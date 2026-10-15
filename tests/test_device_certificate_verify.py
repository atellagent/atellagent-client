"""Issued chains never authenticate themselves or substitute a device key."""

from dataclasses import replace
from uuid import uuid4

import pytest

from atellagent_client.onboarding.device_certificate_verify import verify_device_delivery, verify_device_material
from atellagent_client.onboarding.device_csr import build_device_csr
from atellagent_client.onboarding.errors import OnboardingClientError
from atellagent_client.onboarding.setup_keys import SetupKeys
from device_certificate_support import certificate_case


def test_device_material_verification_does_not_require_a_setup_session():
    from cryptography.hazmat.primitives import serialization

    draft, receipt, trust, payload = certificate_case()
    result = verify_device_material(tenant_id=draft.tenant_id, device_id=receipt.device_id,
        public_key_der=draft.keys.tls_key.public_key().public_bytes(
            serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo),
        certificate_pem=payload["certificate_pem"], certificate_chain_pem=payload["certificate_chain_pem"],
        trusted_ca_bundle_pem=trust.ca_bundle_pem)
    assert result.certificate_sha256 == payload["certificate_sha256"]


def test_renewal_csr_uses_replacement_key_and_original_device_identity():
    from cryptography import x509
    from cryptography.hazmat.primitives import serialization

    draft, receipt, trust, payload = certificate_case()
    replacement = SetupKeys.generate().tls_key
    csr = x509.load_pem_x509_csr(build_device_csr(tenant_id=draft.tenant_id,
        device_id=receipt.device_id, tls_key=replacement).encode())
    public_der = replacement.public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    assert csr.is_signature_valid
    assert csr.public_key().public_bytes(serialization.Encoding.DER,
        serialization.PublicFormat.SubjectPublicKeyInfo) == public_der
    assert list(csr.extensions.get_extension_for_class(x509.SubjectAlternativeName).value) == [
        x509.DNSName(f"device.{receipt.device_id.hex}.{draft.tenant_id.hex}.identity.invalid")]
    with pytest.raises(OnboardingClientError, match="device_certificate_invalid"):
        verify_device_material(tenant_id=draft.tenant_id, device_id=receipt.device_id,
            public_key_der=public_der, certificate_pem=payload["certificate_pem"],
            certificate_chain_pem=payload["certificate_chain_pem"], trusted_ca_bundle_pem=trust.ca_bundle_pem)


@pytest.mark.parametrize("trusted_intermediate", [False, True])
def test_actual_chain_verifies_against_independent_trust_and_exact_device_key(trusted_intermediate):
    draft, receipt, trust, payload = certificate_case(trusted_intermediate=trusted_intermediate)
    result = verify_device_delivery(draft=draft, receipt=receipt, trust=trust, payload=payload)
    assert result.device_id == receipt.device_id
    assert result.certificate_sha256 == payload["certificate_sha256"]
    assert result.expires_at.isoformat() == payload["expires_at"]


def test_returned_root_does_not_override_independent_trust():
    draft, receipt, _, payload = certificate_case()
    _, _, other_trust, _ = certificate_case()
    with pytest.raises(OnboardingClientError, match="device_certificate_invalid"):
        verify_device_delivery(draft=draft, receipt=receipt, trust=other_trust, payload=payload)


def test_key_substitution_is_rejected_before_persistence():
    draft, receipt, trust, payload = certificate_case()
    with pytest.raises(OnboardingClientError, match="device_certificate_invalid"):
        verify_device_delivery(draft=replace(draft, keys=SetupKeys.generate()), receipt=receipt, trust=trust, payload=payload)


@pytest.mark.parametrize("change", [{"device_id": str(uuid4())}, {"operation_id": str(uuid4())},
    {"certificate_sha256": "0" * 64}, {"expires_at": "2099-01-01T00:00:00Z"},
    {"status": "issued"}, {"private_key_pem": "secret"}])
def test_changed_delivery_metadata_rejected(change):
    draft, receipt, trust, payload = certificate_case()
    with pytest.raises(OnboardingClientError, match="device_certificate_invalid"):
        verify_device_delivery(draft=draft, receipt=receipt, trust=trust, payload={**payload, **change})


@pytest.mark.parametrize("field", ["certificate_pem", "certificate_chain_pem"])
def test_extra_pem_material_is_not_ignored(field):
    draft, receipt, trust, payload = certificate_case()
    payload[field] += "unexpected material\n"
    with pytest.raises(OnboardingClientError, match="device_certificate_invalid"):
        verify_device_delivery(draft=draft, receipt=receipt, trust=trust, payload=payload)


def test_unrelated_chain_certificate_is_rejected():
    draft, receipt, trust, payload = certificate_case()
    _, _, extra, _ = certificate_case()
    payload["certificate_chain_pem"] += extra.ca_bundle_pem
    with pytest.raises(OnboardingClientError, match="device_certificate_invalid"):
        verify_device_delivery(draft=draft, receipt=receipt, trust=trust, payload=payload)


@pytest.mark.parametrize("change", [{"leaf_days": 91}, {"server_auth": True}, {"ca_leaf": True},
    {"wrong_dns": True}, {"expired": True}, {"future": True}])
def test_real_signed_wrong_profile_or_validity_is_rejected(change):
    draft, receipt, trust, payload = certificate_case(**change)
    with pytest.raises(OnboardingClientError, match="device_certificate_invalid"):
        verify_device_delivery(draft=draft, receipt=receipt, trust=trust, payload=payload)
