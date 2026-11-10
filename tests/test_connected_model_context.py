"""Optional connected accounting never falls through to generation."""

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from atellagent_client.connected import mount_model_handler
from atellagent_client.connected.contracts import ConnectedDelivery
from atellagent_client.integrations.models.contracts import (
    coerce_model_runtime_invocation_request,
    ModelRuntimeInvocationRequest,
)
from atellagent_client.protocol.model_context import (
    SCHEMA,
    model_context_input,
    model_context_digest,
)


def request(**overrides):
    return coerce_model_runtime_invocation_request(
        {
            "model": "owned-model",
            "provider": "model_boundary",
            "messages": [
                {"role": "system", "content": "Keep the early constraint."},
                {
                    "role": "tool",
                    "tool_call_id": "call-1",
                    "content": "Private exact detail 你好",
                },
            ],
            "tool_definitions": [
                {"function": {"name": "owned_tool", "parameters": {"type": "object"}}}
            ],
            "max_output_tokens": 1000,
            **overrides,
        }
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("messages", [{"role": "user", "content": "Changed"}]),
        ("tool_definitions", []),
        ("structured_output", {"type": "object"}),
        ("max_output_tokens", 999),
        ("model", "other"),
        ("provider", "other"),
    ],
)
def test_digest_covers_whole_handler_input(field, value):
    original = request()
    altered = request(**{field: value})
    assert model_context_digest(original) != model_context_digest(altered)
    assert "你好" in str(model_context_input(original))


def test_routing_and_operation_do_not_change_the_model_token_projection():
    a = request()
    b = request(
        request_id="different",
        memory_thread_id=str(uuid4()),
        context_operation="measure",
    )
    assert model_context_digest(a) == model_context_digest(b)
    assert (
        ModelRuntimeInvocationRequest(
            "owned-model", [{"role": "user", "content": "Existing positional API"}]
        ).messages[0]["role"]
        == "user"
    )


@pytest.mark.parametrize("purpose", ["metadata", "MEASURE", None, True, {}, []])
def test_undeclared_purpose_cannot_select_generation(purpose):
    with pytest.raises(ValueError):
        request(context_operation=purpose)


@pytest.mark.asyncio
@pytest.mark.parametrize("supports_measurement", [True, False])
async def test_optional_measurement_is_separate_from_generation(supports_measurement):
    class Participant:
        def register_handler(self, kind, handler, **kwargs):
            assert kind == "model.invoke" and kwargs["idempotency_mode"] == "target"
            self.handler = handler

    class Handler:
        generations = 0

        async def invoke_model(self, _request):
            self.generations += 1
            return {"output_text": "owned"}

        async def measure_model_context(self, r):
            return {
                "schema": SCHEMA,
                "model": r.model,
                "request_digest": model_context_digest(r),
                "input_tokens": 123,
                "estimated": False,
                "counter_version": "owned.v1",
                "limits": {"max_input_tokens": 32000, "max_output_tokens": 1000},
            }

    handler = Handler()
    if not supports_measurement:
        handler.measure_model_context = None
    participant = Participant()
    mount_model_handler(participant, handler, target_idempotent=True)
    delivery = ConnectedDelivery(
        message_id=str(uuid4()),
        kind="model.invoke",
        operation="invoke",
        execution_id=str(uuid4()),
        execution_attempt_id=str(uuid4()),
        idempotency_key=str(uuid4()),
        payload_schema="atellagent.connected.model-invocation.v1",
        payload={
            "input": {
                "model": "owned-model",
                "messages": [{"role": "user", "content": "Owned"}],
                "context_operation": "measure",
            }
        },
        delivery_attempt=1,
        lease_expires_at=datetime.now(timezone.utc) + timedelta(minutes=1),
    )
    if supports_measurement:
        result = await participant.handler(delivery, None)
        assert (
            result.result_payload["input_tokens"] == 123
            and "output_text" not in result.result_payload
        )
    else:
        with pytest.raises(ValueError, match="measurement is unsupported"):
            await participant.handler(delivery, None)
    assert handler.generations == 0
