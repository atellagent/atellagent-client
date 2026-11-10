"""Delivery validates real certificate material before exposing a credential."""

import httpx
import pytest

from atellagent_client.onboarding.device_certificate_delivery import fetch_device_certificate
from atellagent_client.onboarding.device_csr import device_certificate_operation_id
from atellagent_client.onboarding.setup_store import SetupStore
from device_certificate_support import certificate_case


@pytest.mark.asyncio
@pytest.mark.parametrize("progress", ["awaiting_csr", "pending", "in_progress", "failed", "cancelled", "awaiting_activation"])
async def test_delivery_never_treats_progress_as_verified_credentials(tmp_path, monkeypatch, progress):
    draft, receipt, trust, issued = certificate_case()
    store = SetupStore(tmp_path / "setups")
    store.save_initial(draft)
    operation = device_certificate_operation_id(draft.operation_id)
    payload = (issued if progress == "awaiting_activation" else {"status": progress}
        if progress == "awaiting_csr" else {"operation_id": str(operation), "status": progress})
    factory = httpx.AsyncClient
    async def respond(request):
        assert request.method == "GET" and await request.aread() == b""
        assert str(request.url) == draft.platform_origin + f"/api/client-onboarding/sessions/{draft.operation_id}/certificate"
        assert "authorization" not in request.headers
        assert request.headers["X-Atellagent-Onboarding-Signature"]
        return httpx.Response(200, json=payload)
    monkeypatch.setattr("atellagent_client.onboarding.device_certificate_delivery.httpx.AsyncClient",
        lambda **kwargs: factory(**kwargs, transport=httpx.MockTransport(respond)))
    result = await fetch_device_certificate(origin=draft.platform_origin, draft=draft,
        receipt=receipt, store=store, trust=trust)
    assert result.status == progress
    assert (result.credential is not None) == (progress == "awaiting_activation")
