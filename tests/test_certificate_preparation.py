"""Issuance retries retain one durable, exact key/CSR per credential generation."""

import json
from uuid import uuid4
import pytest

from atellagent_client.sdk import certificate_preparation
from atellagent_client.sdk.certificate_rotation import prepare_certificate_rotation
from atellagent_client.sdk.certificate_errors import CertificateMaterialError


def arguments(tmp_path):
    return dict(service_account_id=str(uuid4()), tenant_id=str(uuid4()),
                certificate_path=tmp_path / "certificate.pem", private_key_path=tmp_path / "private-key.pem")


def test_reopening_keeps_exact_key_and_csr_before_server_request(tmp_path):
    args = arguments(tmp_path)
    first = prepare_certificate_rotation(**args)
    reopened = prepare_certificate_rotation(**args)
    assert first.csr_pem == reopened.csr_pem
    assert first.private_key.private_numbers() == reopened.private_key.private_numbers()
    assert not args["certificate_path"].exists() and not args["private_key_path"].exists()
    assert "PRIVATE KEY" not in repr(reopened)


def test_lost_local_commit_ack_recovers_same_preparation(tmp_path, monkeypatch):
    args = arguments(tmp_path)
    write = certificate_preparation.create_private_file
    def interrupted(path, content):
        write(path, content)
        raise OSError("commit acknowledgement lost")
    monkeypatch.setattr(certificate_preparation, "create_private_file", interrupted)
    with pytest.raises(CertificateMaterialError):
        prepare_certificate_rotation(**args)
    retained = (tmp_path / "rotation-prepared.json").read_bytes()
    monkeypatch.setattr(certificate_preparation, "build_csr", lambda *_: pytest.fail("must not create another key"))
    recovered = prepare_certificate_rotation(**args)
    assert recovered.csr_pem == json.loads(retained)["csr_pem"]
    assert (tmp_path / "rotation-prepared.json").read_bytes() == retained


@pytest.mark.parametrize("change", ["account", "tenant", "key", "csr", "unknown-field"])
def test_corrupt_preparation_is_not_replaced(tmp_path, change):
    args = arguments(tmp_path)
    prepare_certificate_rotation(**args)
    pointer = tmp_path / "rotation-prepared.json"
    record = json.loads(pointer.read_bytes())
    if change == "account": record["service_account_id"] = str(uuid4())
    elif change == "tenant": record["tenant_id"] = str(uuid4())
    elif change == "key": record["private_key_pem"] = "not a key"
    elif change == "csr": record["csr_pem"] = "not the retained CSR"
    else: record["new_field"] = True
    pointer.write_text(json.dumps(record))
    retained = pointer.read_bytes()
    with pytest.raises(CertificateMaterialError):
        prepare_certificate_rotation(**args)
    assert pointer.read_bytes() == retained
