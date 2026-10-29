"""The service selector references only complete, same-identity current records."""

import json
from uuid import uuid4

import pytest

from atellagent_client.onboarding.device_activation_receipt import save_activation_receipt
from atellagent_client.onboarding.device_certificate_verify import verify_device_delivery
from atellagent_client.onboarding.device_installation import DeviceInstallation
from atellagent_client.onboarding.errors import OnboardingClientError
from device_certificate_support import certificate_case


def prepared(tmp_path, *, activated=True):
    draft, receipt, trust, payload = certificate_case()
    installation = DeviceInstallation(tmp_path / "installation")
    installation.setups.save_initial(draft)
    verified = verify_device_delivery(draft=draft, receipt=receipt, trust=trust, payload=payload)
    stored = installation.credentials.save(draft=draft, receipt=receipt, trust=trust,
        credential=verified, setup_store=installation.setups)
    if activated:
        save_activation_receipt(draft, stored)
    args = dict(draft=draft, receipt=receipt, trust=trust, fingerprint=verified.certificate_sha256)
    return installation, args


def test_committed_installation_is_idempotent_and_not_a_config_import(tmp_path):
    installation, args = prepared(tmp_path)
    installation.save(**args)
    installation.save(**args)
    draft, receipt, fingerprint = DeviceInstallation(installation.directory).load()
    assert draft.encode() == args["draft"].encode()
    assert receipt == args["receipt"] and fingerprint == args["fingerprint"]
    raw = installation.path.read_text()
    assert "PRIVATE KEY" not in raw and "config_path" not in raw


def test_unactivated_certificate_does_not_select_an_installation(tmp_path):
    installation, args = prepared(tmp_path, activated=False)
    with pytest.raises(OnboardingClientError, match="activation_receipt_missing"):
        installation.save(**args)
    assert not installation.path.exists()


@pytest.mark.parametrize("field,value", [("owner_user_id", str(uuid4())),
    ("revision", True), ("certificate_sha256", "not-a-fingerprint"), ("config_path", "/tmp/import.yaml")])
def test_changed_selector_cannot_relabel_installation(tmp_path, field, value):
    installation, args = prepared(tmp_path)
    installation.save(**args)
    payload = json.loads(installation.path.read_bytes())
    payload[field] = value
    installation.path.write_text(json.dumps(payload))
    with pytest.raises(OnboardingClientError):
        installation.load()
