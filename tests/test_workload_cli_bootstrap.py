"""Failed bootstrap can be resubmitted without replacing saved keys or intent."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from atellagent_client.cli import workload_bootstrap as module
from atellagent_client.cli.main import _async_main
from atellagent_client.onboarding.workload_context import WorkloadContext
from test_workload_management import draft
from atellagent_client.onboarding.setup_identity import SetupIdentity, SetupMembership


@pytest.mark.asyncio
async def test_bootstrap_failure_keeps_exact_keys_for_explicit_resubmission(tmp_path, draft, monkeypatch, capsys):
    context = WorkloadContext(tmp_path / "state")
    saved = []
    class Management:
        origin = draft.setup.platform_origin
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *_): pass
        async def identity(self):
            return SetupIdentity(draft.setup.owner_user_id, (SetupMembership(draft.setup.tenant_id, "Fixture", False),))
        async def submit(self, *, draft, store):
            saved.append(store.require_saved(draft, expected_origin=self.origin))
            if len(saved) == 1: raise RuntimeError("Submission unavailable")
            return SimpleNamespace(status="pending")
    monkeypatch.setattr(module, "SetupManagementClient", Management)
    login = AsyncMock(return_value=object())
    monkeypatch.setattr(module, "login", login)
    monkeypatch.setattr(module, "select_membership", AsyncMock(return_value=(draft.setup.owner_user_id, draft.setup.tenant_id)))
    with pytest.raises(RuntimeError):
        await _async_main(["workload", "bootstrap", "--state-directory", str(context.directory),
            "--platform-url", draft.setup.platform_origin, "--name", "Workload host"])
    first = saved[0]
    assert str(first.operation_id) in capsys.readouterr().out
    await _async_main(["workload", "bootstrap-submit", str(first.operation_id), "--state-directory", str(context.directory)])
    assert saved[1].encode() == first.encode()
    assert len(list(context.drafts.directory.iterdir())) == 0
    assert login.await_count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("name,origin", [(" ", "https://atellagent.com"), ("Host", "http://atellagent.com")])
async def test_invalid_bootstrap_input_does_not_start_login(tmp_path, monkeypatch, name, origin):
    login = AsyncMock(side_effect=AssertionError("Invalid input must not login"))
    monkeypatch.setattr(module, "login", login)
    with pytest.raises(ValueError):
        await _async_main(["workload", "bootstrap", "--state-directory", str(tmp_path / "state"),
            "--platform-url", origin, "--name", name])
    login.assert_not_awaited()
