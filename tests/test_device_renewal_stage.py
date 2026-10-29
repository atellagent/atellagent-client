"""Real signed replacements are immutable and selected only by exact receipts."""

from datetime import datetime, timedelta, timezone
from hashlib import sha256
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
import pytest

from atellagent_client.onboarding.device_renewal_preparation import prepare_device_renewal
from atellagent_client.onboarding.device_renewal_stage import stage_device_renewal, select_device_renewal
from atellagent_client.onboarding.errors import OnboardingClientError
from test_device_renewal_preparation import admitted_case


def renewal_case(tmp_path, **parent_options):
    device, trust, (root, root_key) = admitted_case(tmp_path, include_authority=True, **parent_options)
    prepared = prepare_device_renewal(device=device, trust=trust)
    csr = x509.load_pem_x509_csr(prepared.csr_pem.encode())
    now = datetime.now(timezone.utc)
    builder = (x509.CertificateBuilder().subject_name(csr.subject).issuer_name(root.subject)
        .public_key(csr.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1)).not_valid_after(now + timedelta(days=30)))
    for extension in csr.extensions:
        builder = builder.add_extension(extension.value, extension.critical)
    leaf = (builder.add_extension(x509.KeyUsage(True, False, True, False, False, False, False, False, False), True)
        .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(root_key.public_key()), False)
        .sign(root_key, hashes.SHA256()))
    payload = dict(operation_id=str(prepared.operation_id), status="issued", device_id=str(device.device_id),
        public_key_sha256=prepared.public_key_sha256, deadline_at=(now + timedelta(minutes=10)).isoformat(),
        certificate_pem=leaf.public_bytes(serialization.Encoding.PEM).decode(), certificate_chain_pem=trust.ca_bundle_pem,
        certificate_sha256=sha256(leaf.public_bytes(serialization.Encoding.DER)).hexdigest(), expires_at=leaf.not_valid_after_utc.isoformat())
    return device, trust, prepared, payload


@pytest.mark.parametrize("case", ["valid", "wrong_receipt", "tampered_key", "tampered_pointer"])
def test_staging_preserves_parent_and_selection_requires_exact_material(tmp_path, case):
    device, trust, prepared, payload = renewal_case(tmp_path)
    parent = (device.certificate_path.read_bytes(), device.private_key_path.read_bytes())
    staged = stage_device_renewal(device=device, trust=trust, prepared=prepared, payload=payload)
    retried = stage_device_renewal(device=device, trust=trust, prepared=prepared, payload=payload)
    assert staged.certificate_path == retried.certificate_path
    assert (device.certificate_path.read_bytes(), device.private_key_path.read_bytes()) == parent
    selection = staged.pointer.parent / "device-renewed.json"
    assert not selection.exists()
    receipt = {name: payload[name] for name in ("operation_id", "device_id", "certificate_sha256", "public_key_sha256", "expires_at")}
    receipt["status"] = "activated"
    if case == "wrong_receipt": receipt["certificate_sha256"] = "0" * 64
    elif case == "tampered_key": staged.private_key_path.write_bytes(b"corrupt")
    elif case == "tampered_pointer": staged.pointer.write_bytes(b"{}")
    if case == "valid":
        select_device_renewal(staged=staged, receipt=receipt)
        select_device_renewal(staged=staged, receipt=receipt)
        assert selection.read_bytes() == staged.record
    else:
        with pytest.raises(OnboardingClientError):
            select_device_renewal(staged=staged, receipt=receipt)
        assert not selection.exists()
    assert (device.certificate_path.read_bytes(), device.private_key_path.read_bytes()) == parent
