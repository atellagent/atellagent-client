"""Offline device-owned preferences and acknowledged online device labels."""

import json
import os
from types import SimpleNamespace
from uuid import uuid4

import pytest

from atellagent_client.cli import discovery_reporting as cli
from atellagent_client.onboarding.device_label_cache import DeviceLabelCache
from atellagent_client.onboarding.errors import OnboardingClientError
from atellagent_client.onboarding.reporting_scope import ReportingScope
from atellagent_client.onboarding.reporting_state import DeviceReportingStore
from test_device_installation import prepared


@pytest.fixture
def installed(tmp_path, monkeypatch):
    installation, args = prepared(tmp_path)
    installation.save(**args)
    scope = ReportingScope(args["draft"].tenant_id, args["receipt"].device_id, args["draft"].owner_user_id)
    store = DeviceReportingStore(installation.directory / "reporting", scope)
    monkeypatch.setattr(cli, "installation_directory", lambda: installation.directory)
    return installation, args, store


@pytest.mark.asyncio
async def test_offline_optout_before_first_service_start_is_preserved(installed, monkeypatch, capsys):
    installation, args, store = installed
    async def no_network(*args):
        pytest.fail("preference changes must work without network or login")
    monkeypatch.setattr(cli.DeviceRuntime, "open", no_network)
    await cli.run(["disable", "--device-id", str(store.scope.device_id)])
    assert store.load().enabled is False and store.load().pending_sync is True
    assert store.initialize(reporting_enabled=True)
    assert store.load().enabled is False
    await cli.run(["list", "--json"])
    value = json.loads(capsys.readouterr().out.splitlines()[-1])["installations"][0]
    assert value["state"] == "removal pending (off)" and value["state_source"] == "local_preference"
    assert value["device_name"] == args["draft"].display_name


@pytest.mark.asyncio
async def test_enable_requires_exact_selected_device_and_retains_pending_sync(installed, capsys):
    _, _, store = installed
    with pytest.raises(SystemExit):
        await cli.run(["enable", "--device-id", str(uuid4())])
    assert not store.path.exists()
    await cli.run(["enable"])
    assert store.load().enabled and store.load().pending_sync
    await cli.run(["status", "--json"])
    assert json.loads(capsys.readouterr().out.splitlines()[-1])["state"] == "enable pending"


@pytest.mark.asyncio
@pytest.mark.parametrize("denied", [False, True])
async def test_rename_caches_only_server_acknowledged_label(installed, monkeypatch, capsys, denied):
    installation, _, store = installed
    calls = []
    async def rename(name):
        calls.append(name)
        if denied:
            raise OnboardingClientError("device_revoked")
    async def close():
        calls.append("closed")
    async def open_device(directory):
        assert directory == installation.directory
        return SimpleNamespace(client=SimpleNamespace(rename=rename, aclose=close))
    monkeypatch.setattr(cli.DeviceRuntime, "open", open_device)
    cache = DeviceLabelCache(installation.directory, store.scope)
    if denied:
        with pytest.raises(SystemExit):
            await cli.run(["rename", "--name", "My workstation"])
        assert cache.load() is None
    else:
        await cli.run(["rename", "--name", "My workstation"])
        assert cache.load() == "My workstation"
    assert calls == ["My workstation", "closed"]


@pytest.mark.asyncio
async def test_local_status_reports_upload_timestamp_not_server_freshness(installed, capsys):
    _, _, store = installed
    store.initialize(reporting_enabled=True)
    from dataclasses import replace
    with store.lease() as held:
        assert held
        store.save(replace(store.load(), uploaded_at=1234567890))
    await cli.run(["status", "--json"])
    value = json.loads(capsys.readouterr().out)
    assert value["last_successful_upload_at"] == "2009-02-13T23:31:30Z"
    assert value["state_source"] == "local_preference" and "discovery_fresh" not in value


@pytest.mark.skipif(os.name != "nt", reason="Native Windows private reporting record acceptance")
def test_windows_preference_uses_current_user_only_acl(installed):
    from atellagent_client.onboarding.windows_credentials import require_current_user_acl
    _, _, store = installed
    assert store.set_enabled(False)
    require_current_user_acl(store.path)
    require_current_user_acl(store.directory)
