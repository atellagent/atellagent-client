"""Device CSR construction uses the retained key and exact assigned identity."""

from dataclasses import replace
from datetime import datetime, timezone
from uuid import uuid4

from cryptography import x509
from cryptography.hazmat.primitives import serialization
from cryptography.x509.oid import ExtendedKeyUsageOID
import pytest

from atellagent_client.onboarding.device_csr import device_csr, device_certificate_operation_id
from atellagent_client.onboarding.setup_draft import SetupDraft
from atellagent_client.onboarding.setup_keys import SetupKeys
from atellagent_client.onboarding.setup_receipt import SetupReceipt


def case():
    draft = SetupDraft(uuid4(), uuid4(), uuid4(), "https://platform.example", "device",
        "My Mac", "0.9.1", "macos", SetupKeys.generate())
    now = datetime.now(timezone.utc)
    return draft, SetupReceipt(draft.operation_id, "device", "approved", uuid4(), 1, now, now)


def test_csr_has_exact_device_san_client_only_purpose_and_same_public_key():
    draft, receipt = case()
    encoded = device_csr(draft, receipt)
    csr = x509.load_pem_x509_csr(encoded.encode())
    assert csr.is_signature_valid
    assert list(csr.extensions.get_extension_for_class(x509.SubjectAlternativeName).value) == [
        x509.DNSName(f"device.{receipt.device_id.hex}.{draft.tenant_id.hex}.identity.invalid")]
    assert list(csr.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value) == [ExtendedKeyUsageOID.CLIENT_AUTH]
    assert csr.extensions.get_extension_for_class(x509.BasicConstraints).value.ca is False
    assert csr.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo) == (
        draft.keys.tls_key.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo))
    assert encoded == device_csr(draft, receipt)
    assert device_certificate_operation_id(draft.operation_id) != draft.operation_id


@pytest.mark.parametrize("change", [{"id": uuid4()}, {"kind": "workload"}, {"device_id": None}])
def test_csr_requires_exact_device_assignment(change):
    draft, receipt = case()
    with pytest.raises(ValueError):
        device_csr(draft, replace(receipt, **change))
