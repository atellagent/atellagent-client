# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Public sequencing contracts for provider-neutral governed sessions."""

from __future__ import annotations

import asyncio
import unittest

from atellagent_client.integrations.providers import (
    DecisionModelTransport,
    GovernedProviderSession,
    ModelGovernanceMode,
    RouteModelTransport,
)
from atellagent_client.protocol.agent_contracts import ModelDecision, ModelDecisionRequest
from atellagent_client.sdk.errors import PolicyTransportError, PolicyViolationError


def _decision(
    request: ModelDecisionRequest | None = None, outcome: str = "allow"
) -> ModelDecision:
    return ModelDecision(
        outcome=outcome,  # type: ignore[arg-type]
        enforcement="enforced",
        input_scope="full_model_request",
        evaluated={"content": "evaluated"},
        reason_code="policy.test",
        reason="test decision",
        obligations=(),
        valid_until=None,
        decision_id="decision-1",
        correlation_id="correlation-1",
        request_fingerprint=(request.request_fingerprint if request else "a" * 64),
    )


class GovernedProviderSessionTests(unittest.TestCase):
    def test_decision_transport_checks_each_native_turn_and_never_routes(self) -> None:
        class Governance:
            def __init__(self):
                self.decisions = 0

            async def model_decision_async(self, request, **_kwargs):
                self.decisions += 1
                return _decision(request)

            async def governed_model_call_async(self, **_kwargs):
                raise AssertionError("decision transport must not route")

        async def run():
            governance = Governance()
            session = GovernedProviderSession(
                governance=governance,  # type: ignore[arg-type]
                mode=ModelGovernanceMode.DECISION,
            )
            request = ModelDecisionRequest(
                input_scope="full_model_request",
                messages=[{"role": "user", "content": "hello"}],
                model="test-model",
                provider="test-provider",
            )
            calls = []
            result = None
            for _ in range(2):
                result = await session.native_turn(
                    decision_request=request,
                    invoke=lambda: calls.append("native") or {"id": "response"},
                )
            return governance.decisions, calls, result

        decisions, calls, result = asyncio.run(run())
        self.assertEqual(decisions, 2)
        self.assertEqual(calls, ["native", "native"])
        self.assertEqual(result.provider_payload, {"id": "response"})
        self.assertEqual(result.payload["status"], "native_response")

    def test_each_provider_has_a_decision_for_each_inference_round(self) -> None:
        class Governance:
            def __init__(self):
                self.requests = []

            async def model_decision_async(self, request, **_kwargs):
                self.requests.append(request)
                return _decision(request)

        async def run(provider: str):
            governance = Governance()
            session = GovernedProviderSession(
                governance=governance,  # type: ignore[arg-type]
                mode=ModelGovernanceMode.DECISION,
            )
            result = None
            for round_number in (1, 2):
                messages = (
                    [{"role": "user", "content": "request a tool"}]
                    if round_number == 1
                    else [{"role": "tool", "content": "governed tool result"}]
                )
                result = await session.native_turn(
                    decision_request=ModelDecisionRequest(
                        input_scope="full_model_request",
                        messages=messages,
                        model="test-model",
                        provider=provider,
                    ),
                    invoke=lambda: {"provider": provider},
                )
            return governance.requests, result

        for provider in ("openai", "anthropic", "google"):
            requests, result = asyncio.run(run(provider))
            self.assertEqual([request.provider for request in requests], [provider, provider])
            self.assertEqual(requests[1].messages[0]["role"], "tool")
            self.assertEqual(len(result.decisions), 2)
            self.assertEqual(result.decisions[0].correlation_id, "correlation-1")

    def test_denied_decision_prevents_native_provider_turn(self) -> None:
        class Governance:
            async def model_decision_async(self, request, **_kwargs):
                return _decision(request, "deny")

        async def run():
            session = GovernedProviderSession(
                governance=Governance(),  # type: ignore[arg-type]
                mode=ModelGovernanceMode.DECISION,
            )
            with self.assertRaises(PolicyViolationError):
                await session.native_turn(
                    decision_request=ModelDecisionRequest(
                        input_scope="full_model_request",
                        messages=[{"role": "user", "content": "blocked"}],
                        model="test-model",
                        provider="openai",
                    ),
                    invoke=lambda: (_ for _ in ()).throw(
                        AssertionError("native provider must not run")
                    ),
                )

        asyncio.run(run())

    def test_advisory_allow_executes_native_turn_and_records_decision(self) -> None:
        class Governance:
            async def model_decision_async(self, request, **_kwargs):
                return ModelDecision(
                    **{**_decision(request).__dict__, "enforcement": "advisory"}
                )

        async def run():
            session = GovernedProviderSession(
                governance=Governance(),  # type: ignore[arg-type]
                mode=ModelGovernanceMode.DECISION,
            )
            return await session.native_turn(
                decision_request=ModelDecisionRequest(
                    input_scope="full_model_request",
                    messages=[{"role": "user", "content": "observe"}],
                    model="test-model",
                    provider="openai",
                ),
                invoke=lambda: {"id": "native-response"},
            )
        result = asyncio.run(run())
        self.assertEqual(result.decisions[0].enforcement, "advisory")
        self.assertEqual(result.provider_payload, {"id": "native-response"})

    def test_transport_failure_or_unfulfilled_obligation_never_calls_native_provider(self) -> None:
        class TransportFailureGovernance:
            async def model_decision_async(self, request, **_kwargs):
                raise PolicyTransportError("decision unavailable")

        class ObligatingGovernance:
            async def model_decision_async(self, request, **_kwargs):
                return ModelDecision(
                    **{**_decision(request).__dict__, "obligations": ({"type": "approval"},)}
                )

        async def run(governance, error_type):
            session = GovernedProviderSession(
                governance=governance,  # type: ignore[arg-type]
                mode=ModelGovernanceMode.DECISION,
            )
            with self.assertRaises(error_type):
                await session.native_turn(
                    decision_request=ModelDecisionRequest(
                        input_scope="full_model_request",
                        messages=[{"role": "user", "content": "blocked"}],
                        model="test-model",
                        provider="openai",
                    ),
                    invoke=lambda: (_ for _ in ()).throw(
                        AssertionError("native provider must not run")
                    ),
                )

        asyncio.run(run(TransportFailureGovernance(), PolicyTransportError))
        asyncio.run(run(ObligatingGovernance(), PolicyViolationError))

    def test_provider_error_propagates_after_recording_the_admission(self) -> None:
        class Governance:
            async def model_decision_async(self, request, **_kwargs):
                return _decision(request)

        async def run():
            session = GovernedProviderSession(
                governance=Governance(),  # type: ignore[arg-type]
                mode=ModelGovernanceMode.DECISION,
            )
            with self.assertRaisesRegex(RuntimeError, "native provider failed"):
                await session.native_turn(
                    decision_request=ModelDecisionRequest(
                        input_scope="full_model_request",
                        messages=[{"role": "user", "content": "provider error"}],
                        model="test-model",
                        provider="openai",
                    ),
                    invoke=lambda: (_ for _ in ()).throw(RuntimeError("native provider failed")),
                )
            return session.decisions

        decisions = asyncio.run(run())
        self.assertEqual(decisions[0].correlation_id, "correlation-1")

    def test_scope_mismatch_is_a_control_failure_before_native_transport(self) -> None:
        class Governance:
            async def model_decision_async(self, request, **_kwargs):
                return ModelDecision(
                    **{**_decision(request).__dict__, "input_scope": "turn_entry"}
                )

        async def run():
            session = GovernedProviderSession(
                governance=Governance(),  # type: ignore[arg-type]
                mode=ModelGovernanceMode.DECISION,
            )
            with self.assertRaisesRegex(PolicyTransportError, "scope did not match"):
                await session.native_turn(
                    decision_request=ModelDecisionRequest(
                        input_scope="full_model_request",
                        messages=[{"role": "user", "content": "scope"}],
                        model="test-model",
                        provider="openai",
                    ),
                    invoke=lambda: (_ for _ in ()).throw(
                        AssertionError("native provider must not run")
                    ),
                )

        asyncio.run(run())

    def test_request_fingerprint_mismatch_prevents_native_transport(self) -> None:
        async def run():
            request = ModelDecisionRequest(
                input_scope="full_model_request",
                messages=[{"role": "user", "content": "bound"}],
                model="test-model",
                provider="openai",
            )
            mismatched = ModelDecisionRequest(
                input_scope="full_model_request",
                messages=[{"role": "user", "content": "different"}],
                model="test-model",
                provider="openai",
            )

            class Governance:
                async def model_decision_async(self, _request, **_kwargs):
                    return _decision(mismatched)

            session = GovernedProviderSession(
                governance=Governance(),  # type: ignore[arg-type]
                mode=ModelGovernanceMode.DECISION,
            )
            with self.assertRaisesRegex(PolicyTransportError, "not bound"):
                await session.native_turn(
                    decision_request=request,
                    invoke=lambda: (_ for _ in ()).throw(
                        AssertionError("native provider must not run")
                    ),
                )

        asyncio.run(run())

    def test_route_transport_never_calls_native_provider(self) -> None:
        class Governance:
            def __init__(self):
                self.calls = []

            async def governed_model_call_async(self, **kwargs):
                self.calls.append(kwargs)
                return {"output_text": "routed", "messages": kwargs["messages"]}

        async def run():
            governance = Governance()
            session = GovernedProviderSession(
                governance=governance,  # type: ignore[arg-type]
                mode=ModelGovernanceMode.ROUTE,
            )
            results = []
            for provider in ("openai", "anthropic", "google"):
                results.append(
                    await session.route_turn(
                        messages=[{"role": "user", "content": "hello"}],
                        memory_thread_id="thread-1",
                        model="model-1",
                        provider=provider,
                    )
                )
            return governance.calls, results

        calls, results = asyncio.run(run())
        self.assertEqual([call["provider"] for call in calls], ["openai", "anthropic", "google"])
        for result in results:
            self.assertEqual(result.mode, ModelGovernanceMode.ROUTE)
            self.assertEqual(result.payload["output_text"], "routed")
            self.assertIsNone(result.provider_payload)

    def test_route_error_does_not_change_transport_strategy(self) -> None:
        class Governance:
            async def governed_model_call_async(self, **_kwargs):
                raise PolicyTransportError("route unavailable")

        async def run():
            session = GovernedProviderSession(
                governance=Governance(),  # type: ignore[arg-type]
                mode=ModelGovernanceMode.ROUTE,
            )
            with self.assertRaisesRegex(PolicyTransportError, "route unavailable"):
                await session.route_turn(
                    messages=[{"role": "user", "content": "route error"}],
                    memory_thread_id="thread-1",
                    provider="openai",
                    model="test-model",
                )
            self.assertIsInstance(session.transport, RouteModelTransport)

        asyncio.run(run())

    def test_strategy_exports_match_the_configured_mode(self) -> None:
        class Governance:
            pass

        decision = GovernedProviderSession(
            governance=Governance(),  # type: ignore[arg-type]
            mode=ModelGovernanceMode.DECISION,
        )
        route = GovernedProviderSession(
            governance=Governance(),  # type: ignore[arg-type]
            mode=ModelGovernanceMode.ROUTE,
        )
        self.assertIsInstance(decision.transport, DecisionModelTransport)
        self.assertIsInstance(route.transport, RouteModelTransport)


if __name__ == "__main__":
    unittest.main()
