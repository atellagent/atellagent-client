"""Renewal uses real retained materials and simulated parent/replacement HTTP."""

from pathlib import Path
import json
from unittest.mock import Mock

import httpx
import pytest

from atellagent_client.onboarding import device as clients
from atellagent_client.onboarding.device_renewal_protocol import require_renewal_progress
from atellagent_client.onboarding.errors import OnboardingClientError
from test_device_renewal_stage import renewal_case


@pytest.mark.parametrize("status", ["failed", "cancelled", "expired"])
def test_exact_terminal_progress_is_actionable_without_replacing_proposal(tmp_path, status):
    from datetime import datetime, timedelta, timezone
    device, trust, prepared, issued = renewal_case(tmp_path)
    progress = {name: issued[name] for name in
        ("operation_id", "device_id", "status", "public_key_sha256", "deadline_at")}
    progress["status"] = "pending" if status == "expired" else status
    if status == "expired":
        progress["deadline_at"] = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    saved = (device.certificate_path.parent / "device-renewal-prepared.json").read_bytes()
    with pytest.raises(OnboardingClientError, match="device_renewal_recovery_required"):
        require_renewal_progress(progress, prepared=prepared, device=device)
    assert (device.certificate_path.parent / "device-renewal-prepared.json").read_bytes() == saved


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["success", "lost_reply", "denied_receipt", "malformed_reply"])
async def test_renewal_activates_with_new_tls_then_selects_and_replaces_pool(tmp_path, monkeypatch, case):
    device, trust, prepared, issued = renewal_case(tmp_path, leaf_days=1)
    calls, pools = [], []
    receipt = {name: issued[name] for name in ("operation_id", "device_id", "certificate_sha256", "public_key_sha256", "expires_at")}
    receipt["status"] = "activated"
    progress = {name: issued[name] for name in ("operation_id", "device_id", "status", "public_key_sha256", "deadline_at")}
    def create_pool(**kwargs):
        certificate = kwargs["certificate_path"]
        assert kwargs["server_ca_path"] == Path("independent-server-ca.pem")
        def respond(request):
            body = json.loads(request.content) if request.content else None
            calls.append((request.method, request.url.path, certificate, dict(request.headers), body))
            if request.url.path.endswith("/activate"):
                assert certificate != device.certificate_path
                assert body == {"certificate_sha256": issued["certificate_sha256"]}
                if case in {"lost_reply", "denied_receipt"}:
                    raise httpx.ReadError("activation committed but reply lost")
                return httpx.Response(200, json={} if case == "malformed_reply" else receipt)
            if request.url.path.endswith("/activation"):
                assert certificate != device.certificate_path
                if case == "denied_receipt":
                    return httpx.Response(403, json={"error": {"code": "onboarding_permission_denied"}})
                return httpx.Response(200, json=receipt)
            if request.url.path.endswith("certificate-renewals"):
                assert certificate == device.certificate_path and body["csr_pem"] == prepared.csr_pem
                return httpx.Response(200, json=progress)
            if request.url.path.endswith(str(prepared.operation_id)):
                assert certificate == device.certificate_path
                return httpx.Response(200, json=issued)
            return httpx.Response(200, json={"success": True})
        pool = httpx.AsyncClient(transport=httpx.MockTransport(respond))
        pools.append(pool)
        return pool
    monkeypatch.setattr(clients, "create_device_http_client", create_pool)
    parent_pair = (device.certificate_path.read_bytes(), device.private_key_path.read_bytes())
    client = clients.DeviceManagementClient(origin=trust.device_api_origin, device=device,
        server_ca_path=Path("independent-server-ca.pem"))
    try:
        if case == "denied_receipt":
            with pytest.raises(OnboardingClientError):
                await client.renew_certificate(trust=trust)
            assert client.device == device
            assert not (device.certificate_path.parent / "device-renewed.json").exists()
            assert not pools[0].is_closed and pools[1].is_closed
        else:
            await client.renew_certificate(trust=trust)
            renewed = client.device
            assert client.device == renewed and renewed.certificate_path != device.certificate_path
            assert renewed.proof_key is device.proof_key
            assert (device.certificate_path.parent / "device-renewed.json").exists()
            assert pools[0].is_closed and pools[1].is_closed and not pools[2].is_closed
            assert await client._request("GET", "/devices/self/status") == {"success": True}
            assert calls[-1][2] == renewed.certificate_path
        assert (device.certificate_path.read_bytes(), device.private_key_path.read_bytes()) == parent_pair
        assert all("authorization" not in headers for _, _, _, headers, _ in calls)
        assert len({headers["x-atellagent-device-nonce"] for _, _, _, headers, _ in calls}) == len(calls)
        assert sum(path.endswith("/activate") for _, path, _, _, _ in calls) == 1
    finally:
        await client.aclose()
    assert all(pool.is_closed for pool in pools)
