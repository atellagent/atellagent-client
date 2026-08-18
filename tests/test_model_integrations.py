# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Credential-free behavior checks for optional model and filter integrations."""

from __future__ import annotations

import asyncio
from pathlib import Path
import tempfile
import unittest

from atellagent_client.pep import RemoteControlDirective
from atellagent_client.governance import ActionDenied, RuntimeActionGate
from atellagent_client.integrations.models import (
    HuggingFaceTextClassificationFilter,
    OllamaModelRuntimeHandler,
)
from atellagent_client.integrations.models.contracts import (
    FilterRuntimeEvaluationRequest,
    ModelRuntimeInvocationRequest,
    coerce_filter_runtime_result,
    coerce_filter_runtime_evaluation_request,
)


class ModelIntegrationTests(unittest.TestCase):
    def _local_manifest(self, mode: str) -> tuple[tempfile.TemporaryDirectory, str]:
        directory = tempfile.TemporaryDirectory()
        path = Path(directory.name) / "local-guardrails.yaml"
        path.write_text(
            "\n".join(
                [
                    "schema_version: v1",
                    f"mode: {mode}",
                    "actions:",
                    "  file.write:",
                    "    writable_roots:",
                    "      - /workspace",
                    "    max_bytes: 16",
                ]
            ),
            encoding="utf-8",
        )
        return directory, str(path)

    def test_limited_local_enforcement_is_explicit_and_has_no_remote_fallback(self) -> None:
        directory, path = self._local_manifest("enforce")
        self.addCleanup(directory.cleanup)

        async def run() -> None:
            gate = RuntimeActionGate.from_local_manifest(path)
            await gate.enforce(
                action="file.write",
                integration_type="mcp",
                correlation_id="message-1",
                facts={"path": "/workspace/file.txt", "access": "write", "bytes": 4},
            )
            with self.assertRaisesRegex(ActionDenied, "path_not_writable"):
                await gate.enforce(
                    action="file.write",
                    integration_type="mcp",
                    correlation_id="message-2",
                    facts={"path": "/outside/file.txt", "access": "write", "bytes": 4},
                )

        asyncio.run(run())

    def test_local_observe_records_but_does_not_block(self) -> None:
        directory, path = self._local_manifest("observe")
        self.addCleanup(directory.cleanup)

        async def run() -> None:
            gate = RuntimeActionGate.from_local_manifest(path)
            await gate.enforce(
                action="file.write",
                integration_type="mcp",
                correlation_id="message-1",
                facts={"path": "/outside/file.txt", "access": "write", "bytes": 4},
            )

        with self.assertLogs("atellagent_client.pep", level="INFO") as captured:
            asyncio.run(run())
        self.assertTrue(
            any(
                "pep_local_decision" in entry and "would_enforce=True" in entry
                for entry in captured.output
            )
        )

    def test_local_manifest_is_fail_closed_for_unknown_actions(self) -> None:
        directory, path = self._local_manifest("enforce")
        self.addCleanup(directory.cleanup)

        async def run() -> None:
            gate = RuntimeActionGate.from_local_manifest(path)
            with self.assertRaisesRegex(ActionDenied, "action_not_configured"):
                await gate.enforce(
                    action="network.connect",
                    integration_type="mcp",
                    correlation_id="message-3",
                    facts={},
                )

        asyncio.run(run())

    def test_malformed_local_manifest_fails_during_gate_construction(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / "local-guardrails.yaml"
        path.write_text(
            "schema_version: v2\nmode: enforce\nactions: {}\n",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(ValueError, "schema_version"):
            RuntimeActionGate.from_local_manifest(str(path))

    def test_local_manifest_mode_must_match_the_provisioned_choice(self) -> None:
        directory, path = self._local_manifest("observe")
        self.addCleanup(directory.cleanup)
        with self.assertRaisesRegex(ValueError, "provisioned mode"):
            RuntimeActionGate.from_local_manifest(path, expected_mode="enforce")

    def test_model_gate_requires_a_matching_remote_directive(self) -> None:
        class Verifier:
            async def verify(self, encoded, intent):
                if encoded != "opaque-directive":
                    raise ValueError("remote_directive_unavailable")
                return RemoteControlDirective(
                    mode="enforce",
                    allowed=True,
                    action=intent.action,
                    integration_type=intent.capability.integration_type,
                    audience="atellagent-client-pep",
                    expires_at=9999999999,
                    directive_id="directive-1",
                    tenant_id=intent.tenant_id,
                    execution_id=intent.execution_id,
                )

        async def run() -> None:
            gate = RuntimeActionGate(
                source="cluster_directive", directive_verifier=Verifier()
            )
            with self.assertRaises(ActionDenied):
                await gate.enforce(
                    action="model.invoke",
                    integration_type="model",
                    correlation_id="model:invoke:request-1",
                    encoded_directive="",
                    workflow_context={"tenant_id": "tenant-1"},
                )
            await gate.enforce(
                action="model.invoke",
                integration_type="model",
                correlation_id="model:invoke:request-1",
                encoded_directive="opaque-directive",
                workflow_context={"tenant_id": "tenant-1"},
            )

        asyncio.run(run())

    def test_ollama_handler_maps_an_admitted_non_streaming_chat(self) -> None:
        class Client:
            def __init__(self) -> None:
                self.kwargs = None

            async def chat(self, **kwargs):
                self.kwargs = kwargs
                return {
                    "model": "llama3.2",
                    "message": {"content": "hello", "tool_calls": []},
                    "prompt_eval_count": 12,
                    "eval_count": 3,
                    "done_reason": "stop",
                }

        async def run() -> None:
            client = Client()
            handler = OllamaModelRuntimeHandler(client=client)
            result = await handler.invoke_model(
                ModelRuntimeInvocationRequest(
                    model="llama3.2",
                    messages=[{"role": "user", "content": "hi"}],
                    options={
                        "temperature": 0.2,
                        "stop_sequences": ["END"],
                        "tool_definitions": [{"type": "function", "function": {}}],
                    },
                )
            )
            self.assertEqual(result["output_text"], "hello")
            self.assertEqual(result["usage"], {"prompt_eval_count": 12, "eval_count": 3})
            self.assertEqual(client.kwargs["options"], {"temperature": 0.2, "stop": ["END"]})
            self.assertEqual(len(client.kwargs["tools"]), 1)
            with self.assertRaisesRegex(ValueError, "coming soon"):
                await handler.invoke_model(
                    ModelRuntimeInvocationRequest(model="llama3.2", stream=True)
                )

        asyncio.run(run())

    def test_huggingface_filter_returns_policy_engine_compatible_scores(self) -> None:
        def classifier(_content, *, truncation):
            self.assertTrue(truncation)
            return [
                {"label": "TOXIC", "score": 0.91},
                {"label": "INSULT", "score": 0.30},
            ]

        async def run() -> None:
            handler = HuggingFaceTextClassificationFilter(
                model_id="customer/example-classifier",
                blocked_labels=("toxic",),
                classifier=classifier,
                threshold=0.7,
            )
            result = await handler.evaluate_filter(
                FilterRuntimeEvaluationRequest(
                    filter_id="customer.classification",
                    execution_boundary="egress",
                    content="synthetic classification fixture",
                )
            )
            self.assertFalse(result["allowed"])
            self.assertEqual(result["score"], 0.91)
            self.assertEqual(result["scores"]["customer.classification"], 0.91)
            self.assertEqual(result["violations"], ["toxic"])
            self.assertEqual(result["metadata"]["execution_boundary"], "egress")

            self.assertFalse(
                (
                    await handler.evaluate_filter(
                        FilterRuntimeEvaluationRequest(
                            filter_id="customer.classification",
                            execution_boundary="tool_response",
                            content="synthetic classification fixture",
                        )
                    )
                )["allowed"]
            )

        asyncio.run(run())

    def test_filter_request_requires_one_explicit_semantic_boundary(self) -> None:
        request = coerce_filter_runtime_evaluation_request(
            {
                "filter_id": "customer.classification",
                "execution_boundary": "model_boundary",
                "content": "synthetic classification fixture",
            }
        )
        self.assertEqual(request.execution_boundary, "model_boundary")
        with self.assertRaisesRegex(ValueError, "unsupported fields: mode"):
            coerce_filter_runtime_evaluation_request(
                {"filter_id": "customer.classification", "mode": "input_check"}
            )
        with self.assertRaisesRegex(ValueError, "execution_boundary"):
            coerce_filter_runtime_evaluation_request(
                {"filter_id": "customer.classification", "execution_boundary": "unknown"}
            )

    def test_filter_result_requires_a_finite_normalized_score(self) -> None:
        self.assertEqual(
            coerce_filter_runtime_result({"score": 0.5}),
            {"score": 0.5, "allowed": False, "violations": []},
        )
        for payload in (
            {},
            {"score": True},
            {"score": "0.5"},
            {"score": float("nan")},
            {"score": float("inf")},
            {"score": -0.01},
            {"score": 1.01},
        ):
            with self.assertRaisesRegex(ValueError, "score"):
                coerce_filter_runtime_result(payload)


if __name__ == "__main__":
    unittest.main()
