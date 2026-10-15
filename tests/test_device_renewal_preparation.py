"""Device replacement proposals survive retries without substituting keys."""

from dataclasses import replace
import json
from uuid import uuid4

import pytest

from atellagent_client.onboarding import device_renewal_preparation as preparation
from atellagent_client.onboarding.atomic_private_file import create_private_file, read_private_file
from atellagent_client.onboarding.device_identity import AdmittedDevice
from atellagent_client.onboarding.errors import OnboardingClientError
from atellagent_client.onboarding.private_paths import create_private_directory
from device_certificate_support import certificate_case


def admitted_case(tmp_path, **kwargs):
    result = certificate_case(**kwargs)
    draft, receipt, trust, payload = result[:4]
    directory = tmp_path / "device-generation"
    create_private_directory(directory)
    certificate, key = directory / "certificate.pem", directory / "private-key.pem"
    create_private_file(certificate, (payload["certificate_pem"] + payload["certificate_chain_pem"]).encode())
    create_private_file(key, preparation.private_key_pem(draft.keys.tls_key).encode())
    device = AdmittedDevice(draft.tenant_id, receipt.device_id, draft.owner_user_id,
        draft.keys.proof_key, certificate, key)
    return (device, trust, result[4]) if kwargs.get("include_authority") else (device, trust)


def test_retained_device_proposal_reopens_exact_operation_key_and_csr(tmp_path):
    device, trust = admitted_case(tmp_path)
    before = (device.certificate_path.read_bytes(), device.private_key_path.read_bytes())
    first = preparation.prepare_device_renewal(device=device, trust=trust)
    second = preparation.prepare_device_renewal(device=device, trust=trust)
    assert first.operation_id == second.operation_id
    assert first.csr_pem == second.csr_pem
    assert preparation.private_key_pem(first.private_key) == preparation.private_key_pem(second.private_key)
    assert first.public_key_sha256 == second.public_key_sha256
    assert "PRIVATE KEY" not in repr(first)
    assert (device.certificate_path.read_bytes(), device.private_key_path.read_bytes()) == before
    assert len(list(device.certificate_path.parent.glob("device-renewal-prepared.json"))) == 1


def test_lost_local_commit_acknowledgement_recovers_retained_proposal(tmp_path, monkeypatch):
    device, trust = admitted_case(tmp_path)
    original = preparation.create_private_file
    def lost_ack(path, content):
        original(path, content)
        raise OSError("lost acknowledgement")
    monkeypatch.setattr(preparation, "create_private_file", lost_ack)
    with pytest.raises(OnboardingClientError):
        preparation.prepare_device_renewal(device=device, trust=trust)
    pointer = device.certificate_path.parent / "device-renewal-prepared.json"
    retained = read_private_file(pointer, limit=16384)
    monkeypatch.setattr(preparation, "create_private_file", original)
    result = preparation.prepare_device_renewal(device=device, trust=trust)
    assert str(result.operation_id) == json.loads(retained)["operation_id"]
    assert read_private_file(pointer, limit=16384) == retained


@pytest.mark.parametrize("case", ["owner", "parent", "csr", "unknown", "operation_type"])
def test_changed_scope_or_proposal_fails_without_repair(tmp_path, case):
    device, trust = admitted_case(tmp_path)
    preparation.prepare_device_renewal(device=device, trust=trust)
    pointer = device.certificate_path.parent / "device-renewal-prepared.json"
    record = json.loads(read_private_file(pointer, limit=16384))
    if case == "owner": device = replace(device, owner_user_id=uuid4())
    elif case == "parent": record["parent_certificate_sha256"] = "0" * 64
    elif case == "csr": record["csr_pem"] = "invalid CSR"
    elif case == "unknown": record["extra"] = True
    else: record["operation_id"] = 1
    pointer.write_bytes(json.dumps(record).encode())
    before = pointer.read_bytes()
    with pytest.raises(OnboardingClientError):
        preparation.prepare_device_renewal(device=device, trust=trust)
    assert pointer.read_bytes() == before


def test_expired_parent_cannot_initiate_renewal(tmp_path):
    device, trust = admitted_case(tmp_path, expired=True)
    with pytest.raises(OnboardingClientError):
        preparation.prepare_device_renewal(device=device, trust=trust)
    assert not (device.certificate_path.parent / "device-renewal-prepared.json").exists()
