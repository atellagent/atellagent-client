"""Retained activation can recover after parent expiry without using old TLS."""

import httpx
import pytest

from atellagent_client.onboarding import device as clients
from atellagent_client.onboarding.device_renewal_recovery import reconcile_device_renewal_candidate
from atellagent_client.onboarding.device_renewal_loading import load_selected_device_renewals
from atellagent_client.onboarding.errors import OnboardingClientError
from test_device_renewal_loading import selected_case, future_clock


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["committed", "denied", "mismatch", "outage"])
async def test_recovery_selects_only_exact_current_replacement_receipt(tmp_path, monkeypatch, case):
    device, trust, staged = selected_case(tmp_path, select=False)
    future_clock(monkeypatch, 2)
    parent = (device.certificate_path.read_bytes(), device.private_key_path.read_bytes())
    retained = staged.pointer.read_bytes()
    calls, pools = [], []
    receipt = dict(operation_id=str(staged.prepared.operation_id), status="activated",
        device_id=str(device.device_id), certificate_sha256=staged.credential.certificate_sha256,
        public_key_sha256="0" * 64 if case == "mismatch" else staged.prepared.public_key_sha256,
        expires_at=staged.credential.expires_at.isoformat())
    def factory(**kwargs):
        assert kwargs["certificate_path"] == staged.certificate_path
        assert kwargs["private_key_path"] == staged.private_key_path
        def reply(request):
            calls.append(request)
            assert request.method == "GET" and request.url.path.endswith("/activation")
            assert "authorization" not in request.headers
            if case in {"denied", "outage"}:
                return httpx.Response(403 if case == "denied" else 503,
                    json={"error": {"code": "onboarding_permission_denied"}})
            return httpx.Response(200, json=receipt)
        pool = httpx.AsyncClient(transport=httpx.MockTransport(reply))
        pools.append(pool)
        return pool
    monkeypatch.setattr(clients, "create_device_http_client", factory)
    if case in {"mismatch", "outage"}:
        with pytest.raises(OnboardingClientError):
            await reconcile_device_renewal_candidate(device=device, trust=trust)
    else:
        recovered = await reconcile_device_renewal_candidate(device=device, trust=trust)
        if case == "committed":
            assert recovered.certificate_path == staged.certificate_path
            assert load_selected_device_renewals(device=device, trust=trust) == recovered
        else:
            assert recovered == device
            with pytest.raises(OnboardingClientError):
                load_selected_device_renewals(device=recovered, trust=trust)
    assert (staged.pointer.parent / "device-renewed.json").exists() is (case == "committed")
    assert len(calls) == 1 and all(pool.is_closed for pool in pools)
    assert (device.certificate_path.read_bytes(), device.private_key_path.read_bytes()) == parent
    assert staged.pointer.read_bytes() == retained
