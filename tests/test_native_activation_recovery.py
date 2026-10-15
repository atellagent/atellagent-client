"""Missing local acknowledgement is recoverable without a false success."""

import httpx
import pytest
from dataclasses import replace
import json

from atellagent_client.onboarding import native_activation_receipt
from atellagent_client.onboarding.native_activation import activate_stored_native
from atellagent_client.onboarding.errors import OnboardingClientError
from test_native_activation import activation


def test_renewal_resume_reads_exact_activation_history_without_network(activation):
    args = dict(draft=activation.case.draft, assignment=activation.case.assignment, stored=activation.stored)
    result = native_activation_receipt.NativeActivationResult.from_payload(activation.response, stored=activation.stored)
    native_activation_receipt.save_native_activation_receipt(**args, result=result)
    args["assignment"] = replace(args["assignment"], status="installed")
    recovered = native_activation_receipt.load_native_activation_receipt(**args)
    assert recovered == result
    path = activation.stored.certificate_path.parent / "activation.json"
    record = json.loads(path.read_bytes())
    record["certificate_sha256"] = "f" * 64
    path.write_text(json.dumps(record))
    with pytest.raises(OnboardingClientError):
        native_activation_receipt.load_native_activation_receipt(**args)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["response", "receipt"])
async def test_no_success_before_exact_ack_and_durable_local_receipt(activation, monkeypatch, failure):
    response = dict(activation.response)
    if failure == "response":
        response["runtime_status"] = "ready"
    factory = httpx.AsyncClient
    monkeypatch.setattr("atellagent_client.onboarding.service_account_http.httpx.AsyncClient", lambda **kwargs:
        factory(**kwargs, transport=httpx.MockTransport(lambda request: httpx.Response(200, json=response))))
    original = native_activation_receipt.create_private_file
    if failure == "receipt":
        def interrupted(*args, **kwargs):
            raise OSError("local receipt interrupted")
        monkeypatch.setattr(native_activation_receipt, "create_private_file", interrupted)
    with pytest.raises(OnboardingClientError):
        await activate_stored_native(**activation.kwargs)
    assert not (activation.stored.certificate_path.parent / "activation.json").exists()
    response.update(activation.response)
    monkeypatch.setattr(native_activation_receipt, "create_private_file", original)
    result = await activate_stored_native(**activation.kwargs)
    assert str(result.authorization_id) == activation.response["authorization_id"]
    assert (activation.stored.certificate_path.parent / "activation.json").exists()
