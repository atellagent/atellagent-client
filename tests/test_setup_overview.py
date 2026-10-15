"""Onboarding UI reads current proof-bound state without activating or logging in."""

from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import datetime, timezone
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from atellagent_client.onboarding import setup_overview as module
from atellagent_client.onboarding.errors import OnboardingClientError
from atellagent_client.cli import setup_status, setup_device
from native_runtime_binding_support import runtime_case
from test_pending_device_setup import prepared


@pytest.mark.asyncio
async def test_no_saved_setup_is_distinct_from_authority_failure(tmp_path):
    result = await module.overview(tmp_path / "installation")
    assert result == dict(device=None, pending=None, assignments=[], next_after=None, renewal=None)
    with pytest.raises(OnboardingClientError, match="onboarding_device_required"):
        await module.overview(tmp_path / "installation", after=uuid4())


@pytest.mark.asyncio
async def test_pending_server_progress_keeps_retained_keys_and_does_not_consume(tmp_path, monkeypatch):
    pending, draft, receipt = prepared(tmp_path)
    pending.bind_receipt(receipt)
    before = pending.path.read_bytes(), draft.encode()
    class Status:
        def __init__(self, **kwargs):
            assert kwargs["origin"] == draft.platform_origin and kwargs["operation_id"] == draft.operation_id
            assert kwargs["proof_key"].public_key().public_bytes_raw() == draft.keys.proof_key.public_key().public_bytes_raw()
        async def __aenter__(self):
            return self
        async def __aexit__(self, *_):
            pass
        async def status(self):
            return SimpleNamespace(kind="device", device_id=receipt.device_id, status="approved",
                device_issuance=dict(stage="issuing", reason_code=None))
    monkeypatch.setattr(module, "SetupStatusClient", Status)
    result = await module.overview(pending.installation.directory)
    assert result["device"] is None and result["assignments"] == []
    assert result["pending"]["status"] == "approved" and result["pending"]["issuance_stage"] == "issuing"
    assert (pending.path.read_bytes(), pending.load()[0].encode()) == before
    assert "PRIVATE KEY" not in json.dumps(result)


@pytest.mark.asyncio
async def test_unsafe_local_selection_is_an_error_not_a_first_run(tmp_path):
    directory = tmp_path / "installation"
    installation = module.DeviceInstallation(directory)
    installation.path.symlink_to(directory / "missing")
    with pytest.raises(OnboardingClientError):
        await module.overview(directory)


@pytest.mark.asyncio
async def test_current_device_assignments_are_bounded_and_redacted(tmp_path, monkeypatch):
    runtime = runtime_case(tmp_path)
    draft = runtime.case.draft
    assignment = replace(runtime.case.assignment, status="approved", owner_consented_at=None)
    directory = tmp_path / "installation"
    installation = module.DeviceInstallation(directory)
    installation.path.write_bytes(b"fixture selector; context is substituted")
    monkeypatch.setattr(module.DeviceInstallation, "load", lambda self:
        (draft, SimpleNamespace(device_id=draft.device_id), "a" * 64))
    now = datetime.now(timezone.utc)
    current = SimpleNamespace(device_id=draft.device_id, tenant_id=draft.tenant_id, owner_user_id=draft.owner_user_id,
        device_name="My device", tenant_name="Example", status="active", certificate_expires_at=now,
        discovery_reporting_status="never_reported", discovery_fresh=False, discovery_observed_at=None, server_time=now)
    page = SimpleNamespace(assignments=[assignment], next_after=assignment.request_id)
    client = SimpleNamespace(status=AsyncMock(return_value=current), assignments=AsyncMock(return_value=page))
    @asynccontextmanager
    async def context():
        yield SimpleNamespace(client=client, setup=SimpleNamespace(platform_origin=draft.platform_origin))
    monkeypatch.setattr(module.NativeSetupContext, "open", AsyncMock(return_value=context()))
    after = uuid4()
    result = await module.overview(directory, after=after)
    client.assignments.assert_awaited_once_with(after=after)
    assert result["device"]["device_name"] == "My device" and result["pending"] is None
    row = result["assignments"][0]
    assert row["request_id"] == str(assignment.request_id) and row["status"] == "approved"
    assert row["owner_consented_at"] is None and result["next_after"] == str(assignment.request_id)
    assert not any(key in row for key in ("public_key_der", "proposal", "private_key", "configuration"))


@pytest.mark.asyncio
async def test_status_dispatch_emits_only_structured_result_without_login(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(setup_status, "installation_directory", lambda: tmp_path / "installation")
    await setup_device.run(["status", "--json"])
    assert json.loads(capsys.readouterr().out) == dict(device=None, pending=None, assignments=[], next_after=None, renewal=None)


@pytest.mark.asyncio
async def test_recovery_hint_visible_without_network_or_authority_claim(tmp_path, monkeypatch):
    from test_device_installation import prepared as installed
    from atellagent_client.onboarding.device_renewal_state import DeviceRenewalState
    from atellagent_client.onboarding.reporting_scope import ReportingScope
    installation, args = installed(tmp_path)
    installation.save(**args)
    draft, receipt = args["draft"], args["receipt"]
    store = DeviceRenewalState(installation.directory,
        ReportingScope(draft.tenant_id, receipt.device_id, draft.owner_user_id))
    assert store.read() is None
    store.save("device_renewal_recovery_required", certificate_sha256="a" * 64)
    open_context = AsyncMock(side_effect=AssertionError("recovery status must not require live TLS"))
    monkeypatch.setattr(module.NativeSetupContext, "open", open_context)
    result = await module.overview(installation.directory)
    assert result["renewal"]["status"] == "recovery_required"
    assert result["device"]["status"] == "verification_required"
    assert result["assignments"] == []
    open_context.assert_not_awaited()
    assert "PRIVATE KEY" not in json.dumps(result)
    wrong = DeviceRenewalState(installation.directory,
        ReportingScope(uuid4(), receipt.device_id, draft.owner_user_id))
    with pytest.raises(ValueError):
        wrong.read()
    store.clear_if_replaced("a" * 64)
    assert store.read() is not None
    store.clear_if_replaced("b" * 64)
    assert store.read() is None
