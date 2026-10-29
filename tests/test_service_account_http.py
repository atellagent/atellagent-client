"""TLS client purpose remains distinct; rejected credentials never open HTTP."""

from uuid import uuid4
import pytest

from atellagent_client.onboarding.errors import OnboardingClientError
from atellagent_client.onboarding.service_account_http import create_service_account_http_client
from test_native_activation import activation


@pytest.mark.parametrize("change", ["tenant", "account", "fingerprint", "same_path"])
def test_wrong_credential_boundary_stops_before_pool_creation(activation, monkeypatch, change):
    stored = activation.stored
    args = dict(tenant_id=activation.case.draft.tenant_id,
        service_account_id=stored.credential.service_account_id, certificate_path=stored.certificate_path,
        private_key_path=stored.private_key_path, certificate_sha256=stored.credential.certificate_sha256)
    if change == "tenant":
        args["tenant_id"] = uuid4()
    elif change == "account":
        args["service_account_id"] = uuid4()
    elif change == "fingerprint":
        args["certificate_sha256"] = "a" * 64
    else:
        args["private_key_path"] = args["certificate_path"]
    monkeypatch.setattr("atellagent_client.onboarding.service_account_http.httpx.AsyncClient",
                        lambda **kwargs: pytest.fail("rejected credentials opened a pool"))
    with pytest.raises(OnboardingClientError):
        create_service_account_http_client(**args)
