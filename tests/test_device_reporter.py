"""Device-only reporting, durable opt-out and process ownership behavior."""

import asyncio
import json
import os
import subprocess
import sys
from types import SimpleNamespace
from uuid import uuid4

import pytest

from atellagent_client.onboarding.device_reporter import DeviceReporter
from atellagent_client.onboarding.errors import OnboardingClientError
from atellagent_client.onboarding.reporting_state import DeviceReportingStore
from atellagent_client.onboarding.reporting_scope import ReportingScope
from onboarding_support import identity


class Connection:
    def __init__(self, device):
        self.device, self.calls, self.disabled, self.fail = device, [], False, False

    async def status(self):
        self.calls.append("status")
        return SimpleNamespace(discovery_reporting_status="disabled" if self.disabled else "enabled")

    async def report_discovery(self):
        self.calls.append("upload")
        if self.fail:
            raise OnboardingClientError("onboarding_transport_unavailable", retryable=True)

    async def remove_discovery(self):
        self.calls.append("delete")
        if self.fail:
            raise OnboardingClientError("onboarding_transport_unavailable", retryable=True)
        self.disabled = True

    async def set_reporting(self, enabled):
        assert enabled is True
        self.calls.append("enable")
        self.disabled = False


@pytest.fixture
def reporter(tmp_path):
    device = identity(tmp_path / "device", tenant=uuid4(), device=uuid4(), owner=uuid4())
    store = DeviceReportingStore(tmp_path / "reporting", ReportingScope.from_device(device))
    assert store.initialize(reporting_enabled=True)
    return DeviceReporter(Connection(device), store)


@pytest.mark.asyncio
async def test_zero_controller_report_and_restart_do_not_duplicate_upload(reporter):
    assert await reporter.tick()
    restarted = DeviceReporter(reporter.client, DeviceReportingStore(reporter.store.directory,
        ReportingScope.from_device(reporter.client.device)))
    assert restarted.store.initialize(reporting_enabled=False)
    assert not await restarted.tick()
    assert reporter.client.calls == ["status", "upload"]
    assert restarted.store.load().enabled


@pytest.mark.asyncio
async def test_offline_optout_survives_restart_and_deletes_before_any_scan(reporter):
    assert reporter.store.set_enabled(False)
    reporter.client.fail = True
    with pytest.raises(OnboardingClientError):
        await reporter.tick()
    assert reporter.store.load().pending_sync
    reporter.client.fail = False
    assert not await reporter.tick()
    assert not reporter.store.load().pending_sync
    assert reporter.client.calls == ["delete", "delete"]
    assert not await reporter.tick()
    assert reporter.store.set_enabled(True)
    assert await reporter.tick()
    assert reporter.client.calls[-3:] == ["enable", "status", "upload"]


@pytest.mark.asyncio
async def test_server_disabled_reporting_is_not_automatically_reenabled(reporter):
    reporter.client.disabled = True
    assert not await reporter.tick()
    assert reporter.client.calls == ["status"]


@pytest.mark.asyncio
async def test_outage_does_not_advance_success_checkpoint(reporter):
    reporter.client.fail = True
    with pytest.raises(OnboardingClientError):
        await reporter.tick()
    assert reporter.store.load().uploaded_at == 0
    reporter.client.fail = False
    assert await reporter.tick()


@pytest.mark.asyncio
async def test_busy_lease_skips_upload_and_preference_mutation(reporter):
    with reporter.store.lease() as held:
        assert held
        assert not await reporter.tick()
        assert not reporter.store.set_enabled(False)
    assert reporter.client.calls == []
    assert await reporter.tick()


@pytest.mark.asyncio
async def test_changed_owner_or_corrupt_record_cannot_report(reporter):
    payload = json.loads(reporter.store.path.read_bytes())
    payload["owner_user_id"] = str(uuid4())
    reporter.store.path.write_text(json.dumps(payload))
    with pytest.raises(ValueError):
        await reporter.tick()
    assert reporter.client.calls == []


@pytest.mark.asyncio
async def test_loop_failure_is_visible_and_does_not_require_login(reporter, caplog):
    stop = asyncio.Event()
    async def failed_tick():
        stop.set()
        raise OnboardingClientError("device_revoked")
    reporter.tick = failed_tick
    await reporter.run(stop)
    assert "code=device_revoked" in caplog.text


@pytest.mark.asyncio
async def test_cancellation_releases_lease(reporter):
    entered = asyncio.Event()
    async def blocked_status():
        entered.set()
        await asyncio.Event().wait()
    reporter.client.status = blocked_status
    task = asyncio.create_task(reporter.tick())
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    with reporter.store.lease() as held:
        assert held


def test_separate_process_handoff_uses_os_lock(reporter):
    code = ("from pathlib import Path; import sys; "
        "from atellagent_client.onboarding.process_lease import process_lease\n"
        "with process_lease(Path(sys.argv[1])) as held: print(int(held))")
    def contender():
        result = subprocess.run([sys.executable, "-c", code, str(reporter.store.lease_path)],
            capture_output=True, text=True, timeout=10, check=True)
        return result.stdout.strip()
    with reporter.store.lease() as held:
        assert held
        assert contender() == "0"
    assert contender() == "1"


@pytest.mark.skipif(os.name == "nt", reason="POSIX path/privacy behavior; native Windows ACL acceptance separate")
def test_unsafe_reporting_files_are_rejected_not_repaired(reporter, tmp_path):
    reporter.store.path.chmod(0o644)
    with pytest.raises(ValueError):
        reporter.store.set_enabled(False)
    assert reporter.store.path.stat().st_mode & 0o777 == 0o644
    reporter.store.path.chmod(0o600)
    reporter.store.lease_path.unlink()
    reporter.store.lease_path.symlink_to(reporter.store.path)
    with pytest.raises(ValueError):
        reporter.store.initialize(reporting_enabled=True)
