"""CLI continuation preserves the key; consent is visible and separately attended."""

from dataclasses import replace
from types import SimpleNamespace

import pytest

from atellagent_client.cli import setup_device, setup_native, setup_native_consent, setup_native_progress
from atellagent_client.onboarding.errors import OnboardingClientError
from native_credential_support import stored_case


@pytest.fixture
def context(tmp_path, monkeypatch):
    case = stored_case(tmp_path)
    calls = []
    from atellagent_client.onboarding.native_setup_context import NativeSetupContext
    class Context:
        prepare_request = NativeSetupContext.prepare_request
        submit_request = NativeSetupContext.submit_request
        pickup_preparation = NativeSetupContext.pickup_preparation
        review = NativeSetupContext.review
        consent = NativeSetupContext.consent
        setup = SimpleNamespace(operation_id=case.draft.setup_id, platform_origin=case.draft.platform_origin)
        client = SimpleNamespace(device=case.draft)
        drafts = case.draft_store
        installation = object()
        async def __aenter__(self):
            return self
        async def __aexit__(self, *_):
            calls.append("closed")
        def load(self, identifier):
            return self.drafts.load(identifier, device=case.draft, setup_id=case.draft.setup_id,
                                    expected_origin=case.draft.platform_origin)
        def flow(self, draft):
            return SimpleNamespace(draft=draft)
    selected = Context()
    async def open_context(directory):
        return selected
    async def wait(flow, seconds):
        calls.append(("wait", flow.draft.encode(), seconds))
    async def no_login(*_):
        pytest.fail("proof-only continuation must not sign in")
    monkeypatch.setattr(setup_native.NativeSetupContext, "open", open_context)
    monkeypatch.setattr(setup_native, "wait_for_native", wait)
    monkeypatch.setattr(setup_native, "login", no_login)
    return SimpleNamespace(case=case, selected=selected, calls=calls)


@pytest.mark.asyncio
async def test_resume_dispatches_exact_saved_request_without_sign_in(context):
    await setup_device.run(["native", "resume", str(context.case.draft.proposal.id), "--wait-seconds", "12"])
    assert context.calls == [("wait", context.case.draft.encode(), 12), "closed"]


@pytest.mark.asyncio
async def test_pickup_dispatches_saved_identity_without_browser_login(context, monkeypatch):
    from atellagent_client.onboarding import native_preparation_pickup
    async def pickup(selected, identifier):
        assert selected is context.selected and identifier == context.case.draft.proposal.id
        context.calls.append("picked-up")
    monkeypatch.setattr(native_preparation_pickup, "pickup_preparation", pickup)
    await setup_native.run(["pickup", str(context.case.draft.proposal.id), "--wait-seconds", "12"])
    assert context.calls == ["picked-up", ("wait", context.case.draft.encode(), 12), "closed"]


@pytest.mark.asyncio
async def test_desktop_dispatcher_accepts_only_the_explicit_pickup_action(monkeypatch):
    from atellagent_client.cli import setup_desktop
    from unittest.mock import AsyncMock
    run = AsyncMock()
    monkeypatch.setattr(setup_native, "run", run)
    identifier = "11111111-1111-4111-8111-111111111111"
    await setup_desktop.run(["native", "pickup", identifier])
    assert run.await_args.args == (["pickup", identifier],)
    assert run.await_args.kwargs["interaction"] is not None


@pytest.mark.asyncio
async def test_request_saved_before_login_and_interrupted_submission_reuses_key(context, monkeypatch, capsys):
    async def interrupted(origin):
        assert len(list(context.case.draft_store.directory.glob("*.json"))) == 2
        raise OnboardingClientError("onboarding_transport_unavailable", retryable=True)
    monkeypatch.setattr(setup_native, "login", interrupted)
    with pytest.raises(SystemExit):
        await setup_native.run(["request", "--host", "claude-code", "--name", "My Claude"])
    identifiers = [path.stem for path in context.case.draft_store.directory.glob("*.json")
                   if path.stem != str(context.case.draft.proposal.id)]
    assert len(identifiers) == 1
    from uuid import UUID
    retained = context.selected.load(UUID(identifiers[0]))
    assert retained.proposal.native_host == "claude-code"
    before = retained.encode()
    async def login(origin):
        assert origin == retained.platform_origin
        return object()
    class Management:
        def __init__(self, **kwargs):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *_):
            pass
        async def submit_native(self, **kwargs):
            assert kwargs["store"].require_saved(kwargs["draft"]).encode() == before
            context.calls.append("submitted")
    monkeypatch.setattr(setup_native, "login", login)
    monkeypatch.setattr(setup_native, "SetupManagementClient", Management)
    await setup_native.run(["submit", identifiers[0]])
    assert context.selected.load(UUID(identifiers[0])).encode() == before
    assert "submitted" in context.calls
    assert f"native submit {identifiers[0]}" in capsys.readouterr().err


@pytest.mark.asyncio
@pytest.mark.parametrize("answer,expected", [("no", False), ("yes", True)])
async def test_owner_reviews_exact_connection_before_login_and_consent(context, monkeypatch, capsys, answer, expected):
    assignment = replace(context.case.assignment, status="approved")
    async def current(identifier):
        return assignment
    context.selected.client.assignment = current
    monkeypatch.setattr("builtins.input", lambda _: answer)
    async def login(origin):
        assert expected
        assert "Authority: agent.control" in capsys.readouterr().out
        context.calls.append("login")
        return object()
    class Management:
        def __init__(self, **kwargs):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *_):
            pass
        async def consent_native(self, **kwargs):
            assert kwargs["reviewed_intent_sha256"] == assignment.intent_sha256
            context.calls.append("consented")
    monkeypatch.setattr(setup_native_consent, "login", login)
    monkeypatch.setattr(setup_native_consent, "SetupManagementClient", Management)
    assert await setup_native_consent.consent(context.selected, context.case.draft) is expected
    assert context.calls == (["login", "consented"] if expected else [])


@pytest.mark.asyncio
async def test_consent_and_registration_progress_do_not_claim_hooks_ready(context, capsys):
    for stage in ("awaiting_owner_consent", "awaiting_registration", "registered"):
        async def advance():
            return SimpleNamespace(stage=stage)
        await setup_native_progress.wait_for_native(SimpleNamespace(draft=context.case.draft, advance=advance), 5)
    output = capsys.readouterr().out
    assert f"native consent {context.case.draft.proposal.id}" in output
    assert "not complete" in output and "verified separately" in output


@pytest.mark.asyncio
async def test_timeout_preserves_exact_native_resume_command(context, monkeypatch, capsys):
    async def advance():
        return SimpleNamespace(stage="awaiting_approval")
    async def interrupted(_):
        raise TimeoutError
    from atellagent_client.onboarding import progress
    monkeypatch.setattr(progress.asyncio, "sleep", interrupted)
    await setup_native_progress.wait_for_native(SimpleNamespace(draft=context.case.draft, advance=advance), 5)
    assert f"native resume {context.case.draft.proposal.id}" in capsys.readouterr().out


@pytest.mark.asyncio
@pytest.mark.parametrize("arguments", [["request", "--host", "codex", "--name", "Test", "--config", "old.yaml"],
    ["resume", "AAAAAAAA-AAAA-AAAA-AAAA-AAAAAAAAAAAA"], ["resume", "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", "--wait-seconds", "0"]])
async def test_invalid_or_import_arguments_fail_before_opening_device(monkeypatch, arguments):
    async def no_open(*_):
        pytest.fail("invalid command must not access device state")
    monkeypatch.setattr(setup_native.NativeSetupContext, "open", no_open)
    with pytest.raises(SystemExit) as stopped:
        await setup_native.run(arguments)
    assert stopped.value.code == 2
