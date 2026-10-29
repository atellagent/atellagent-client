"""Native setup uses current selected device credentials, not host YAML or OAuth."""

from datetime import datetime, timezone
from uuid import uuid4

import httpx
import pytest

from atellagent_client.onboarding import native_setup_context
from atellagent_client.onboarding.errors import OnboardingClientError
from atellagent_client.onboarding.native_setup_context import NativeSetupContext
from atellagent_client.onboarding.native_setup_draft import NativeSetupDraft
from test_device_installation import prepared


@pytest.mark.asyncio
async def test_context_verifies_device_authority_and_owns_fixed_private_native_layout(tmp_path, monkeypatch):
    installation, args = prepared(tmp_path)
    installation.save(**args)
    async def trust(origin):
        assert origin == args["draft"].platform_origin
        return args["trust"]
    monkeypatch.setattr("atellagent_client.onboarding.device_context.fetch_device_trust", trust)
    factory, requests = httpx.AsyncClient, []
    def respond(request):
        requests.append(request)
        assert "Authorization" not in request.headers and "Cookie" not in request.headers
        assert request.url.path == "/devices/self/status"
        return httpx.Response(200, json=dict(device_id=str(args["receipt"].device_id),
            tenant_id=str(args["draft"].tenant_id), owner_user_id=str(args["draft"].owner_user_id),
            device_name="Test", tenant_name="Fixture", status="active", discovery_reporting_status="disabled",
            certificate_expires_at=datetime.now(timezone.utc).replace(year=2027).isoformat(),
            server_time=datetime.now(timezone.utc).isoformat(), discovery_fresh=False, discovery_observed_at=None))
    monkeypatch.setattr("atellagent_client.onboarding.device_http.httpx.AsyncClient",
        lambda **kwargs: factory(**kwargs, transport=httpx.MockTransport(respond)))
    async with await NativeSetupContext.open(installation.directory) as context:
        assert context.drafts.directory == installation.directory / "native-drafts"
        draft = NativeSetupDraft.create(device=context.client.device, setup_id=context.setup.operation_id, request_id=uuid4(),
            platform_origin=context.setup.platform_origin, display_name="My host", packaging="sdk",
            native_host="codex", enforcement_mode="enforce", coverage_posture="strict")
        context.drafts.save_initial(draft)
        assert context.load(draft.proposal.id).encode() == draft.encode()
        assert context.flow(draft).device_client is context.client
    assert len(requests) == 1 and context.client._http.is_closed


@pytest.mark.asyncio
async def test_missing_device_selection_cannot_start_native_setup(tmp_path, monkeypatch):
    async def no_trust(*_):
        pytest.fail("no device selector must not contact remote platform")
    monkeypatch.setattr("atellagent_client.onboarding.device_context.fetch_device_trust", no_trust)
    with pytest.raises(OnboardingClientError, match="device_installation_unavailable"):
        await NativeSetupContext.open(tmp_path / "installation")
