"""Socket presence is not readiness: exact selected binding health is required."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4
import asyncio

import pytest

from atellagent_client.cli import native_service_status as module
from atellagent_client.onboarding.native_runtime_selection import NativeRuntimeSelections
from native_runtime_binding_support import runtime_case


@pytest.mark.asyncio
async def test_fresh_install_reports_empty_service(tmp_path, monkeypatch):
    monkeypatch.setattr(module, "installation_directory", lambda: tmp_path / "installation")
    monkeypatch.setattr(module.service, "_require_supported_platform", Mock())
    monkeypatch.setattr(module.service, "_supervisor_installed", lambda: False)
    assert await module.status() == dict(installed=False, running=False, controllers=[])


@pytest.mark.asyncio
async def test_corrupt_selector_is_not_a_fresh_install(tmp_path, monkeypatch):
    from atellagent_client.onboarding.device_installation import DeviceInstallation
    from atellagent_client.onboarding.errors import OnboardingClientError
    directory = tmp_path / "installation"
    installation = DeviceInstallation(directory)
    installation.path.write_text("{}")
    installation.path.chmod(0o600)
    monkeypatch.setattr(module, "installation_directory", lambda: directory)
    monkeypatch.setattr(module.service, "_require_supported_platform", Mock())
    monkeypatch.setattr(module.service, "_supervisor_installed", lambda: False)
    with pytest.raises(OnboardingClientError):
        await module.status()


@pytest.mark.asyncio
@pytest.mark.parametrize("change", [None, "old", "request", "epoch"])
async def test_status_is_exact_local_evidence_not_human_login_state(tmp_path, monkeypatch, change):
    runtime = runtime_case(tmp_path)
    draft, binding = runtime.case.draft, runtime.binding
    slots = NativeRuntimeSelections(tmp_path / "native-runtime-selections")
    slots.select(draft=draft, binding=binding)
    setup = SimpleNamespace(operation_id=draft.setup_id, tenant_id=draft.tenant_id,
                            owner_user_id=draft.owner_user_id, platform_origin=draft.platform_origin)
    installation = SimpleNamespace(directory=tmp_path,
        load=lambda: (setup, SimpleNamespace(device_id=draft.device_id), "unused"))
    monkeypatch.setattr(module, "DeviceInstallation", lambda directory: installation)
    monkeypatch.setattr(module, "NativeSetupStore", lambda directory: runtime.case.draft_store)
    monkeypatch.setattr(module, "installation_directory", lambda: tmp_path)
    for name in ("_require_supported_platform", "_validate_supervisor"):
        monkeypatch.setattr(module.service, name, Mock())
    monkeypatch.setattr(module.service, "_supervisor_installed", lambda: True)
    monkeypatch.setattr(module.service, "_supervisor_running", lambda: True)
    monkeypatch.setattr(module.service, "_endpoint", lambda host: f"/private/{host}.sock")
    health = dict(protocol_version="atellagent.hook-control.v1",
        native_binding=dict(request_id=str(draft.proposal.id), device_id=str(draft.device_id),
            tenant_id=str(draft.tenant_id), owner_user_id=str(draft.owner_user_id), native_host="codex"),
        native_delegation=dict(authorization_id=str(binding.authorization_id), authorization_epoch=1,
                               authority_source="gateway_admission"))
    if change == "old":
        health = dict(local_user_authorization=dict(state="READY"))
    elif change == "request":
        health["native_binding"]["request_id"] = str(uuid4())
    elif change == "epoch":
        health["native_delegation"]["authorization_epoch"] = True
    monkeypatch.setattr(module.service, "HookControlClient", lambda *args, **kwargs:
        SimpleNamespace(call=AsyncMock(return_value=health)))
    status = await module.status()
    assert status["running"] and status["installed"]
    assert len(status["controllers"]) == 1
    controller = status["controllers"][0]
    assert controller["local_controller_ready"] is (change is None)
    assert controller["error"] == (None if change is None else "controller_health_invalid")


@pytest.mark.asyncio
async def test_health_reads_overlap_without_relaxing_exact_binding(monkeypatch, tmp_path):
    hosts = ("codex", "claude-code")
    device_id, tenant_id, owner_id, setup_id = (uuid4() for _ in range(4))
    requests = {host: uuid4() for host in hosts}
    drafts = {request: SimpleNamespace(proposal=SimpleNamespace(id=request), device_id=device_id,
        tenant_id=tenant_id, owner_user_id=owner_id, native_host=host) for host, request in requests.items()}
    setup = SimpleNamespace(operation_id=setup_id, tenant_id=tenant_id,
        owner_user_id=owner_id, platform_origin="https://example.com")
    installation = SimpleNamespace(directory=tmp_path, load=lambda: (
        setup, SimpleNamespace(device_id=device_id), "unused"))
    monkeypatch.setattr(module, "DeviceInstallation", lambda _: installation)
    monkeypatch.setattr(module, "NativeRuntimeSelections", lambda _: SimpleNamespace(
        load=lambda host: dict(request_id=requests[host]), require_draft=Mock()))
    monkeypatch.setattr(module, "NativeSetupStore", lambda _: SimpleNamespace(load=lambda request, **_: drafts[request]))
    monkeypatch.setattr(module, "hook_setup_status", lambda *_: "waiting_for_tool_result")
    for name in ("_require_supported_platform", "_validate_supervisor"):
        monkeypatch.setattr(module.service, name, Mock())
    monkeypatch.setattr(module.service, "_supervisor_installed", lambda: True)
    monkeypatch.setattr(module.service, "_supervisor_running", lambda: True)
    monkeypatch.setattr(module.service, "_endpoint", lambda host: host)
    entered = {host: asyncio.Event() for host in hosts}
    release = asyncio.Event()
    def client(host, **_):
        async def call(*_):
            entered[host].set()
            await release.wait()
            return dict(protocol_version="atellagent.hook-control.v1",
                native_binding=dict(request_id=str(requests[host]), device_id=str(device_id),
                    tenant_id=str(tenant_id), owner_user_id=str(owner_id), native_host=host),
                native_delegation=dict(authorization_id=str(uuid4()), authorization_epoch=1,
                    authority_source="gateway_admission"))
        return SimpleNamespace(call=call)
    monkeypatch.setattr(module.service, "HookControlClient", client)
    task = asyncio.create_task(module.status())
    try:
        await asyncio.wait_for(asyncio.gather(*(event.wait() for event in entered.values())), 1)
        assert not task.done()
    finally:
        release.set()
    result = await task
    assert [row["host"] for row in result["controllers"]] == list(hosts)
    assert all(row["local_controller_ready"] for row in result["controllers"])
