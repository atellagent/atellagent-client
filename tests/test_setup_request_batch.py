"""Selected requests reuse keys on retry and never activate a pending device."""

from datetime import datetime, timedelta, timezone
import base64
from hashlib import sha256
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
import httpx

from atellagent_client.onboarding.attended_device import DeviceSetup
from atellagent_client.onboarding.errors import OnboardingClientError
from atellagent_client.onboarding.setup_draft import SetupDraft
from atellagent_client.onboarding.setup_keys import SetupKeys
from atellagent_client.onboarding.setup_receipt import SetupReceipt
from atellagent_client.onboarding.setup_request_choices import SetupRequestChoices
from atellagent_client.onboarding.device_login_models import ManagementTokens
from atellagent_client.onboarding.setup_management import SetupManagementClient
from atellagent_client.onboarding.proofs import canonical_json


@pytest.fixture
def setup(tmp_path):
    with DeviceSetup(tmp_path / "installation") as value:
        draft = SetupDraft(uuid4(), uuid4(), uuid4(), "https://platform.example", "device",
            "My Mac", "0.9.1", "macos", SetupKeys.generate())
        value.installation.setups.save_initial(draft)
        value.pending.begin(draft, reporting_enabled=True)
        value.pending.bind_receipt(SetupReceipt(draft.operation_id, "device", "pending", uuid4(), 1,
            datetime.now(timezone.utc) + timedelta(days=1), None))
        yield value


def choices():
    return {"native": [{"host": "codex", "name": "Codex"},
        {"host": "claude-code", "name": "Claude Code"}], "mcp": [
        {"name": "Local MCP", "url": "http://127.0.0.1:8787/mcp", "command": "", "arguments": []}]}


def test_prepare_is_retained_without_network_or_activation(setup, monkeypatch):
    batch = setup.request_batch()
    payload = batch.prepare(choices())
    before = {path: path.read_bytes() for path in setup.installation.directory.rglob("*.json")}
    monkeypatch.setattr(SetupKeys, "generate", lambda: pytest.fail("Retry generated keys"))
    assert batch.prepare(choices()) == payload
    assert before == {path: path.read_bytes() for path in before}
    assert batch.needs_submission()
    assert not setup.installation.path.exists()
    device, receipt, _ = setup.pending.load()
    for value in payload["native_ids"]:
        draft = batch.native.load(UUID(value), device=SimpleNamespace(tenant_id=device.tenant_id,
            owner_user_id=device.owner_user_id, device_id=receipt.device_id),
            setup_id=device.operation_id, expected_origin=device.platform_origin)
        assert draft.proposal.native_host in {"codex", "claude-code"}
    workload = batch.workloads.drafts.load(UUID(payload["mcp_ids"][0]))
    assert workload.proposal.identity_mode == "boundary_identity_only"
    assert workload.proposal.device_id == receipt.device_id
    assert workload.proposal.device_onboarding_session_id == device.operation_id
    assert workload.setup.keys.tls_key.public_key().public_numbers() != device.keys.tls_key.public_key().public_numbers()


@pytest.mark.asyncio
async def test_partial_submission_retry_reuses_ids_and_marks_only_confirmed_batch(setup, monkeypatch):
    batch = setup.request_batch()
    selected = choices(); selected["mcp"] = []
    payload = batch.prepare(selected)
    submit = AsyncMock(side_effect=[OnboardingClientError("onboarding_temporarily_unavailable"),
        None, None, None])
    monkeypatch.setattr("atellagent_client.onboarding.setup_request_batch.submit_native_proposal", submit)
    first = await batch.submit(object())
    assert [item["status"] for item in first] == ["unconfirmed", "submitted"]
    assert batch.needs_submission()
    second = await batch.submit(object())
    assert [item["request_id"] for item in second] == payload["native_ids"]
    assert not batch.needs_submission()
    assert [call.kwargs["draft"].encode() for call in submit.call_args_list[:2]] == [
        call.kwargs["draft"].encode() for call in submit.call_args_list[2:]]
    assert not setup.installation.path.exists()


def test_changed_selection_cannot_replace_requests(setup):
    batch = setup.request_batch(); original = batch.prepare(choices())
    changed = choices(); changed["native"][0]["name"] = "Other"
    with pytest.raises(OnboardingClientError, match="batch_changed"):
        batch.prepare(changed)
    assert batch.prepare(choices()) == original


def test_pending_summary_lists_selections_and_does_not_claim_approval(setup):
    from atellagent_client.onboarding.setup_request_summary import pending_connection_summary
    draft, receipt, _ = setup.pending.load()
    assert pending_connection_summary(setup.installation.directory, draft, receipt) == ([], False)
    batch = setup.request_batch()
    batch.prepare(choices())
    names, submitted = pending_connection_summary(setup.installation.directory, draft, receipt)
    assert len(names) == 3 and not submitted
    batch.store.mark_submitted()
    assert pending_connection_summary(setup.installation.directory, draft, receipt) == (names, True)
    assert not setup.installation.path.exists()


@pytest.mark.parametrize("value", [None, {"native": [{"host": [], "name": "Bad"}], "mcp": []},
    {"native": [], "mcp": [{}]}, {"native": [{"host": "codex", "name": ""}], "mcp": []},
    {"native": [], "mcp": [], "approve": True}])
def test_invalid_choices_rejected(value):
    with pytest.raises(ValueError):
        SetupRequestChoices.from_payload(value)


@pytest.mark.asyncio
async def test_pending_mcp_submission_signs_separate_device_and_workload_proofs(setup, monkeypatch):
    from test_workload_management import receipt as workload_receipt
    batch = setup.request_batch(); selected = choices(); selected["native"] = []
    manifest = batch.prepare(selected)
    draft = batch.workloads.drafts.load(UUID(manifest["mcp_ids"][0]))
    device, device_receipt, _ = setup.pending.load()
    response = workload_receipt(draft)
    response["device_id"] = str(device_receipt.device_id)
    posts = []
    def handler(request):
        assert request.headers["Authorization"] == "Bearer attended-access"
        if request.method == "GET":
            return httpx.Response(200, json={"user_id": str(device.owner_user_id), "selection_required": True,
                "memberships": [{"tenant_id": str(device.tenant_id), "tenant_name": "Customer", "can_approve": True}]})
        payload = json.loads(request.content); posts.append(payload)
        assert "127.0.0.1" not in str(payload) and "PRIVATE KEY" not in str(payload)
        if request.url.path == "/api/client-onboarding/sessions":
            return httpx.Response(200, json={"id": str(draft.setup.operation_id), "kind": "workload",
                "device_id": None, "status": "pending", "revision": 1,
                "expires_at": response["expires_at"], "claim_expires_at": None})
        assert request.url.path == f"/api/client-onboarding/sessions/{draft.setup.operation_id}/workload"
        assert set(payload) == {"tenant_id", "proposal", "proof", "device_proof"}
        body = {"tenant_id": payload["tenant_id"], "setup_operator_id": str(device.owner_user_id),
            "proposal": payload["proposal"]}
        for signer, proof in ((draft.setup, payload["proof"]), (device, payload["device_proof"])):
            material = "\n".join(("atellagent-onboarding-request-v1", str(signer.operation_id),
                "POST", request.url.path, sha256(canonical_json(body)).hexdigest(),
                proof["timestamp"], proof["nonce"])).encode()
            signer.keys.proof_key.public_key().verify(base64.urlsafe_b64decode(proof["signature"] + "=="), material)
        return httpx.Response(200, json=response)
    factory = httpx.AsyncClient
    monkeypatch.setattr("atellagent_client.onboarding.setup_management.httpx.AsyncClient", lambda **kwargs:
        factory(**kwargs, transport=httpx.MockTransport(handler)))
    async with SetupManagementClient(origin=device.platform_origin,
            tokens=ManagementTokens(300, "attended-access", None)) as client:
        assert (await batch.submit(client))[0]["status"] == "submitted"
        assert (await batch.submit(client))[0]["request_id"] == manifest["mcp_ids"][0]
    assert len(posts) == 4 and not batch.needs_submission()
    assert not setup.installation.path.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("confirmed", [False, True])
async def test_desktop_continue_retries_only_unconfirmed_submissions(setup, monkeypatch, confirmed):
    import io
    from atellagent_client.cli import setup_device, setup_request_batch
    from atellagent_client.cli.setup_interaction import SetupInteraction
    batch = setup.request_batch(); selected = choices(); selected["mcp"] = []
    payload = batch.prepare(selected)
    if confirmed: batch.store.mark_submitted()
    monkeypatch.setattr(setup_device, "installation_directory", lambda: setup.installation.directory)
    # Release the fixture's lease before exercising the shipping CLI acquisition.
    setup.__exit__(None, None, None)
    login = AsyncMock(return_value=object())
    monkeypatch.setattr(setup_device, "login", login)
    class Management:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
    monkeypatch.setattr(setup_device, "SetupManagementClient", Management)
    async def failed_batch(*args, interaction, **kwargs):
        rows = [{"kind": "native", "request_id": payload["native_ids"][0],
            "status": "unconfirmed", "code": "onboarding_transport_unavailable"}]
        interaction.emit("connection_requests_submitted", requests=rows)
        return rows
    monkeypatch.setattr(setup_request_batch, "submit_batch", failed_batch)
    resume = AsyncMock(return_value=object())
    monkeypatch.setattr(DeviceSetup, "resume", resume)
    wait = AsyncMock()
    monkeypatch.setattr(setup_device, "wait_for_setup", wait)
    output = io.StringIO()
    await setup_device.run(["resume"], interaction=SetupInteraction(writer=output))
    if confirmed:
        login.assert_not_awaited(); resume.assert_awaited_once(); wait.assert_awaited_once()
    else:
        login.assert_awaited_once(); resume.assert_not_awaited(); wait.assert_not_awaited()
        assert json.loads(output.getvalue().splitlines()[-1]) == {"event": "wait_ended", "state": "retained"}
