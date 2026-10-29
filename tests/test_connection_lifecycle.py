"""Approval prepares credentials, never starts an agent or MCP process."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

from atellagent_client.onboarding import connection_lifecycle as lifecycle
from atellagent_client.onboarding.errors import OnboardingClientError


class Context:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        pass


@pytest.mark.asyncio
async def test_reconcile_provisions_each_consented_request_without_enabling(tmp_path, monkeypatch):
    approved, waiting, mcp_id = uuid4(), uuid4(), uuid4()
    native, mcp = Context(), Context()
    native.load = lambda identifier: identifier
    advance = AsyncMock(return_value=SimpleNamespace(stage="issuing"))
    native.flow = lambda _: SimpleNamespace(advance=advance)
    native.select = Mock(side_effect=AssertionError("approval must not enable"))
    mcp.saved = AsyncMock(return_value={"requests": [dict(request_id=str(mcp_id), selected=False,
        server_status="approved", server_error=None)], "next_after": None})
    mcp.flow = lambda _: SimpleNamespace(advance=advance)
    mcp.select = Mock(side_effect=AssertionError("approval must not launch"))
    monkeypatch.setattr(lifecycle.NativeSetupContext, "open", AsyncMock(return_value=native))
    monkeypatch.setattr(lifecycle.DeviceMCPContext, "open", AsyncMock(return_value=mcp))
    monkeypatch.setattr(lifecycle, "NativeRuntimeSelections", lambda _: SimpleNamespace(load=lambda _: None))
    monkeypatch.setattr(lifecycle, "native_page", AsyncMock(return_value={"requests": [
        dict(request_id=str(approved), native_host="codex", server_status="approved", owner_consented_at="now"),
        dict(request_id=str(waiting), native_host="claude-code", server_status="approved", owner_consented_at=None)],
        "next_after": None}))
    result = await lifecycle.reconcile_connections(tmp_path)
    assert advance.await_count == 2
    assert all(not row["enabled"] for row in result["connections"])
    assert result["connections"][1]["stage"] == "approved"


@pytest.mark.asyncio
async def test_disable_removes_hooks_before_stopping_exact_controller(tmp_path, monkeypatch, capsys):
    from atellagent_client.cli import setup_connections as command, setup_native_hooks as hooks
    identifier = uuid4()
    calls = []
    monkeypatch.setattr(command, "installation_directory", lambda: tmp_path)
    monkeypatch.setattr(hooks, "preview", lambda *a, **k: {"review_sha256": "review"})
    monkeypatch.setattr(hooks, "apply", lambda *a, **k: calls.append("remove hooks"))
    monkeypatch.setattr(command.NativeSetupContext, "stop_selected", lambda *a: calls.append("stop controller"))
    await command.run(["disable", "native", str(identifier)])
    assert calls == ["remove hooks", "stop controller"]


@pytest.mark.asyncio
async def test_configuration_conflict_does_not_leave_new_selection(tmp_path, monkeypatch):
    from atellagent_client.cli import setup_connections as command, setup_native_hooks as hooks
    identifier = uuid4()
    context = Context()
    context.select = AsyncMock()
    stopped = Mock()
    monkeypatch.setattr(command, "installation_directory", lambda: tmp_path)
    monkeypatch.setattr(command.NativeSetupContext, "open", AsyncMock(return_value=context))
    monkeypatch.setattr(command.NativeSetupContext, "stop_selected", stopped)
    monkeypatch.setattr(hooks, "preview", Mock(side_effect=OnboardingClientError("config_conflict")))
    with pytest.raises(SystemExit) as failed:
        await command.run(["enable", "native", str(identifier)])
    assert failed.value.code == 1
    stopped.assert_called_once_with(tmp_path, identifier)
