"""Desktop selections are explicit, scoped to the live prompt, and contain no OAuth tokens."""

import io
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest

from atellagent_client.cli.setup_interaction import SetupInteraction
from atellagent_client.onboarding.errors import OnboardingClientError
from atellagent_client.onboarding.setup_identity import SetupIdentity, SetupMembership


class RespondingReader:
    def __init__(self, writer, answer):
        self.writer, self.answer = writer, answer

    def readline(self, limit):
        event = json.loads(self.writer.getvalue().splitlines()[-1])
        return self.answer(event)


@pytest.mark.asyncio
async def test_organization_prompt_preserves_membership_scope():
    owner, tenant = uuid4(), uuid4()
    identity = SetupIdentity(owner, (SetupMembership(tenant, "Example", False),))
    writer = io.StringIO()
    reader = RespondingReader(writer, lambda event: json.dumps(dict(prompt_id=event["prompt_id"], value=str(tenant))) + "\n")
    channel = SetupInteraction(reader=reader, writer=writer)
    assert await channel.choose_membership(identity) == (owner, tenant)
    event = json.loads(writer.getvalue())
    assert event["event"] == "choose_organization"
    assert event["organizations"] == [dict(tenant_id=str(tenant), name="Example")]


@pytest.mark.asyncio
async def test_ui_cannot_select_an_unlisted_organization():
    identity = SetupIdentity(uuid4(), (SetupMembership(uuid4(), "Example", False),))
    writer = io.StringIO()
    reader = RespondingReader(writer, lambda event: json.dumps(dict(prompt_id=event["prompt_id"], value=str(uuid4()))) + "\n")
    with pytest.raises(OnboardingClientError, match="onboarding_membership_required"):
        await SetupInteraction(reader=reader, writer=writer).choose_membership(identity)


@pytest.mark.asyncio
@pytest.mark.parametrize("response", ["", "{}\n", "x\n", "x" * 8193,
    '{"prompt_id":"old","value":"yes"}\n',
    '{"prompt_id":"old","prompt_id":"new","value":"yes"}\n'])
async def test_closed_bounded_prompt_response(response):
    channel = SetupInteraction(reader=io.StringIO(response), writer=io.StringIO())
    with pytest.raises(OnboardingClientError, match="onboarding_interaction_invalid"):
        await channel.ask("choose_organization")


def test_sign_in_event_exposes_only_visible_link_code():
    writer = io.StringIO()
    channel = SetupInteraction(writer=writer)
    channel.sign_in(SimpleNamespace(browser_url="https://platform.example/device", user_code="ABCD-EFGH",
        access_token="must-not-cross-pipe"))
    assert json.loads(writer.getvalue()) == dict(event="sign_in", browser_url="https://platform.example/device", user_code="ABCD-EFGH")


@pytest.mark.asyncio
async def test_progress_completion_is_json_only(capsys):
    from atellagent_client.cli.setup_progress import wait_for_setup
    writer = io.StringIO()
    async def advance():
        return SimpleNamespace(stage="device_ready")
    assert await wait_for_setup(SimpleNamespace(advance=advance), 1,
        interaction=SetupInteraction(writer=writer))
    assert json.loads(writer.getvalue()) == dict(event="setup_complete", stage="device_ready")
    assert capsys.readouterr().out == ""


@pytest.mark.asyncio
async def test_desktop_resume_forwards_same_retained_flow(monkeypatch):
    from atellagent_client.cli import setup_device, setup_desktop
    calls = []
    async def run(arguments, *, interaction):
        calls.append((arguments, isinstance(interaction, SetupInteraction)))
    monkeypatch.setattr(setup_device, "run", run)
    await setup_desktop.run(["resume", "--wait-seconds", "12"])
    assert calls == [(["resume", "--wait-seconds", "12"], True)]


@pytest.mark.asyncio
async def test_shipping_device_resume_emits_no_terminal_output(tmp_path, monkeypatch, capsys):
    from test_pending_device_setup import prepared
    from atellagent_client.cli import setup_device
    pending, _, _ = prepared(tmp_path)
    monkeypatch.setattr(setup_device, "installation_directory", lambda: pending.installation.directory)
    async def advance():
        return SimpleNamespace(stage="device_ready")
    async def resume(store):
        assert store.load()[0].encode() == pending.load()[0].encode()
        return SimpleNamespace(advance=advance)
    async def no_login(*args, **kwargs):
        pytest.fail("proof-only continuation cannot start login")
    monkeypatch.setattr("atellagent_client.onboarding.attended_device.resume_pending_device_setup", resume)
    monkeypatch.setattr(setup_device, "login", no_login)
    writer = io.StringIO()
    await setup_device.run(["resume"], interaction=SetupInteraction(writer=writer))
    assert json.loads(writer.getvalue()) == dict(event="setup_complete", stage="device_ready")
    assert capsys.readouterr().out == ""


@pytest.mark.asyncio
async def test_desktop_native_dispatch_keeps_exact_request(monkeypatch):
    from atellagent_client.cli import setup_desktop, setup_native
    request = str(uuid4())
    calls = []
    async def native(arguments, *, interaction):
        calls.append(arguments)
        assert isinstance(interaction, SetupInteraction)
    monkeypatch.setattr(setup_native, "run", native)
    await setup_desktop.run(["native", "resume", request])
    assert calls == [["resume", request]]
    with pytest.raises(SystemExit) as stopped:
        await setup_desktop.run(["native", "run", request])
    assert stopped.value.code == 2 and len(calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["awaiting_owner_consent", "awaiting_registration", "registered"])
async def test_native_transport_does_not_imply_hooks_ready(stage, capsys):
    from atellagent_client.cli.setup_native_progress import wait_for_native
    request = uuid4()
    async def advance(): return SimpleNamespace(stage=stage)
    writer = io.StringIO()
    flow = SimpleNamespace(draft=SimpleNamespace(proposal=SimpleNamespace(id=request)), advance=advance)
    result = await wait_for_native(flow, 1, interaction=SetupInteraction(writer=writer))
    assert result.stage == stage
    assert json.loads(writer.getvalue()) == dict(event="native_progress", request_id=str(request), stage=stage)
    assert capsys.readouterr().out == ""
