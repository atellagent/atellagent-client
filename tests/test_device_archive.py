"""Local archive is explicit, recoverable and excluded while runtime/setup owns state."""

from uuid import uuid4

import pytest

from atellagent_client.onboarding.device_archive import archive_device_installation
from atellagent_client.onboarding.device_installation import DeviceInstallation
from atellagent_client.onboarding.errors import OnboardingClientError
from atellagent_client.onboarding.process_lease import process_lease
from test_device_installation import prepared


def test_archive_preserves_exact_state_and_fresh_setup_has_no_old_selection(tmp_path):
    installation, args = prepared(tmp_path)
    installation.save(**args)
    selector = installation.path.read_bytes()
    archived = archive_device_installation(installation.directory,
        expected_device_id=args["receipt"].device_id, service_running=False)
    assert not installation.directory.exists()
    retained = DeviceInstallation(archived / "installation")
    assert retained.path.read_bytes() == selector
    assert retained.load()[0].encode() == args["draft"].encode()
    fresh = DeviceInstallation(installation.directory)
    assert not fresh.path.exists()


@pytest.mark.parametrize("case", ["service", "runtime", "setup", "wrong_device"])
def test_archive_refuses_unsafe_transition_without_moving_state(tmp_path, case):
    from contextlib import nullcontext
    installation, args = prepared(tmp_path)
    installation.save(**args)
    original = installation.path.read_bytes()
    lock = {"runtime": ".device-runtime.lock", "setup": ".device-setup.lock"}.get(case)
    lease = process_lease(installation.directory.parent / lock) if lock else nullcontext()
    with lease:
        with pytest.raises(OnboardingClientError):
            archive_device_installation(installation.directory,
                expected_device_id=uuid4() if case == "wrong_device" else args["receipt"].device_id,
                service_running=case == "service")
    assert installation.path.read_bytes() == original
    assert not list(tmp_path.glob("installation-archived-*"))


@pytest.mark.asyncio
async def test_cli_archive_dispatch_requires_exact_confirmation_and_discloses_retained_keys(tmp_path, monkeypatch, capsys):
    import json
    from atellagent_client.cli import setup_archive, setup_device
    installation, args = prepared(tmp_path)
    installation.save(**args)
    monkeypatch.setattr(setup_archive, "installation_directory", lambda: installation.directory)
    monkeypatch.setattr(setup_archive.service, "_supervisor_loaded", lambda: False)
    await setup_device.run(["archive", "--confirm-device", str(args["receipt"].device_id), "--json"])
    output = json.loads(capsys.readouterr().out)
    assert output["status"] == "archived" and output["device_id"] == str(args["receipt"].device_id)
    assert "private keys were retained" in output["summary"] and "require approval" in output["summary"]
    assert "PRIVATE KEY" not in json.dumps(output)


def test_lost_durability_acknowledgement_does_not_delete_retained_state(tmp_path, monkeypatch):
    from atellagent_client.onboarding import device_archive
    installation, args = prepared(tmp_path)
    installation.save(**args)
    original = installation.path.read_bytes()
    def failed_sync(_path):
        raise OSError("simulated durability acknowledgement failure")
    monkeypatch.setattr(device_archive, "sync_directory", failed_sync)
    with pytest.raises(OnboardingClientError, match="device_archive_outcome_uncertain"):
        archive_device_installation(installation.directory,
            expected_device_id=args["receipt"].device_id, service_running=False)
    archives = list(tmp_path.glob("installation-archived-*"))
    assert len(archives) == 1 and (archives[0] / "installation" / "installation.json").read_bytes() == original


def test_lost_local_keys_do_not_prevent_explicit_archive(tmp_path):
    installation, args = prepared(tmp_path)
    installation.save(**args)
    # Real damaged proposal state, not a substituted archive implementation.
    installation.setups._path(args["draft"].operation_id).write_bytes(b"unreadable local proposal")
    with pytest.raises(OnboardingClientError):
        installation.load()
    archive = archive_device_installation(installation.directory,
        expected_device_id=args["receipt"].device_id, service_running=False)
    assert (archive / "installation" / "installation.json").exists()
