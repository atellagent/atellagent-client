"""Exact GUI review precedes fresh attended consent; decline and changed intent make no write."""

import io
import json
from dataclasses import replace
from types import SimpleNamespace
from uuid import uuid4

import pytest

from atellagent_client.cli import setup_native_consent
from atellagent_client.cli.setup_interaction import SetupInteraction
from atellagent_client.onboarding.errors import OnboardingClientError
from atellagent_client.onboarding.native_setup_context import NativeSetupContext
from native_credential_support import stored_case


@pytest.mark.asyncio
@pytest.mark.parametrize("answer", [True, False, "wrong_digest", "wrong_request", "string_bool", "extra"])
async def test_exact_desktop_review_before_human_authority(tmp_path, monkeypatch, capsys, answer):
    case = stored_case(tmp_path)
    assignment = replace(case.assignment, status="approved", owner_consented_at=None)
    before = case.draft.encode()
    async def current(request):
        assert request == assignment.request_id
        return assignment
    context = NativeSetupContext(installation=SimpleNamespace(directory=tmp_path),
        setup=SimpleNamespace(operation_id=case.draft.setup_id, platform_origin=case.draft.platform_origin),
        client=SimpleNamespace(device=case.draft, assignment=current),
        drafts=case.draft_store, credentials=None)
    calls, writer = [], io.StringIO()
    class Reader:
        def readline(self, limit):
            event = json.loads(writer.getvalue().splitlines()[-1])
            review = event["review"]
            assert review["device_id"] == str(case.draft.device_id)
            assert review["service_account_id"] == str(assignment.service_account_id)
            assert review["capabilities"] == ["agent.control"]
            assert review["delegation_lifetime"] == "until_revoked"
            assert "tls_public_key_der_base64" not in review
            calls.append("reviewed")
            response = dict(request_id=review["request_id"], intent_sha256=review["intent_sha256"], approved=answer is True)
            if answer == "wrong_digest": response["intent_sha256"] = "0" * 64
            if answer == "wrong_request": response["request_id"] = str(uuid4())
            if answer == "string_bool": response["approved"] = "true"
            if answer == "extra": response["extra"] = "unreviewed"
            return json.dumps(dict(prompt_id=event["prompt_id"], value=response)) + "\n"
    interaction = SetupInteraction(reader=Reader(), writer=writer)
    async def login(origin, *, interaction):
        assert calls == ["reviewed"] and origin == case.draft.platform_origin
        calls.append("login")
        return object()
    class Management:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *_): pass
        async def consent_native(self, **kwargs):
            assert kwargs["reviewed_intent_sha256"] == assignment.intent_sha256
            assert kwargs["draft"].encode() == before
            calls.append("consented")
    monkeypatch.setattr(setup_native_consent, "login", login)
    monkeypatch.setattr(setup_native_consent, "SetupManagementClient", Management)
    if isinstance(answer, bool):
        assert await setup_native_consent.consent(context, case.draft, interaction=interaction) is answer
        assert calls == (["reviewed", "login", "consented"] if answer else ["reviewed"])
    else:
        with pytest.raises(OnboardingClientError, match="onboarding_native_consent_intent_changed"):
            await setup_native_consent.consent(context, case.draft, interaction=interaction)
        assert calls == ["reviewed"]
    assert context.drafts.require_saved(case.draft).encode() == before
    assert capsys.readouterr().out == ""
