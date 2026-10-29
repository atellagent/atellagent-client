"""Visible attended choice and bounded proof-only CLI continuation."""

from types import SimpleNamespace
from uuid import uuid4

import pytest

from atellagent_client.cli import setup_device, setup_login, setup_progress
from atellagent_client.onboarding.errors import OnboardingClientError
from atellagent_client.onboarding.setup_identity import SetupIdentity, SetupMembership


@pytest.mark.asyncio
async def test_single_membership_is_selected_automatically(monkeypatch, capsys):
    owner, tenant = uuid4(), uuid4()
    identity = SetupIdentity(owner, (SetupMembership(tenant, "Example organization", False),))
    async def current():
        return identity
    prompts = []
    def choose(prompt):
        prompts.append(prompt)
        return "1"
    monkeypatch.setattr("builtins.input", choose)
    assert await setup_login.select_membership(SimpleNamespace(identity=current)) == (owner, tenant)
    assert not prompts and "Example organization" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_desktop_single_membership_does_not_prompt():
    from unittest.mock import AsyncMock
    owner, tenant = uuid4(), uuid4()
    identity = SetupIdentity(owner, (SetupMembership(tenant, "Example", False),))
    management = SimpleNamespace(identity=AsyncMock(return_value=identity))
    interaction = SimpleNamespace(choose_membership=AsyncMock())
    assert await setup_login.select_membership(management, interaction=interaction) == (owner, tenant)
    interaction.choose_membership.assert_not_awaited()


@pytest.mark.asyncio
async def test_multiple_memberships_still_require_the_destination():
    from unittest.mock import AsyncMock
    owner, first, second = uuid4(), uuid4(), uuid4()
    identity = SetupIdentity(owner, (SetupMembership(first, "First", False), SetupMembership(second, "Second", False)))
    management = SimpleNamespace(identity=AsyncMock(return_value=identity))
    interaction = SimpleNamespace(choose_membership=AsyncMock(return_value=(owner, second)))
    assert await setup_login.select_membership(management, interaction=interaction) == (owner, second)
    interaction.choose_membership.assert_awaited_once_with(identity)


@pytest.mark.asyncio
async def test_retry_progress_and_pending_timeout_have_visible_resume_command(monkeypatch, capsys):
    count = 0
    async def advance():
        nonlocal count
        count += 1
        raise OnboardingClientError("onboarding_transport_unavailable", retryable=True)
    async def timeout(_seconds):
        raise TimeoutError
    monkeypatch.setattr("atellagent_client.onboarding.progress.asyncio.sleep", timeout)
    assert not await setup_progress.wait_for_setup(SimpleNamespace(advance=advance), 10)
    output = capsys.readouterr().out
    assert count == 1 and "retrying" in output and "atellagent-cli setup resume" in output


@pytest.mark.asyncio
async def test_terminal_denial_is_not_retried_or_reported_ready(capsys):
    async def advance():
        raise OnboardingClientError("onboarding_setup_denied")
    with pytest.raises(OnboardingClientError):
        await setup_progress.wait_for_setup(SimpleNamespace(advance=advance), 10)
    assert "enrolled" not in capsys.readouterr().out


@pytest.mark.asyncio
async def test_resume_uses_saved_context_without_login(tmp_path, monkeypatch, capsys):
    from test_pending_device_setup import prepared
    pending, _, _ = prepared(tmp_path)
    monkeypatch.setattr(setup_device, "installation_directory", lambda: pending.installation.directory)
    calls = []
    async def resume(store):
        assert store.load()[0].encode() == pending.load()[0].encode()
        calls.append("resume")
        return SimpleNamespace()
    async def wait(flow, seconds):
        assert seconds == 12
        calls.append("wait")
    async def no_login(*args):
        pytest.fail("resume must not initiate browser login")
    monkeypatch.setattr("atellagent_client.onboarding.attended_device.resume_pending_device_setup", resume)
    monkeypatch.setattr(setup_device, "wait_for_setup", wait)
    monkeypatch.setattr(setup_device, "login", no_login)
    await setup_device.run(["resume", "--wait-seconds", "12"])
    assert calls == ["resume", "wait"]


@pytest.mark.asyncio
async def test_pending_request_prevents_new_login_or_key_generation(tmp_path, monkeypatch, capsys):
    from test_pending_device_setup import prepared
    pending, _, _ = prepared(tmp_path)
    monkeypatch.setattr(setup_device, "installation_directory", lambda: pending.installation.directory)
    async def no_login(*args):
        pytest.fail("existing request must be resumed, not relabeled")
    monkeypatch.setattr(setup_device, "login", no_login)
    with pytest.raises(SystemExit):
        await setup_device.run(["device", "--platform-url", "https://platform.example"])
    assert "atellagent-cli setup resume" in capsys.readouterr().err
