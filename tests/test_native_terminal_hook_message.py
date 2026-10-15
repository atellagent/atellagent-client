# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.
"""Terminal native gate denials survive SDK/controller/adapter transport."""
from types import SimpleNamespace
import json
import unittest
import httpx

from atellagent_client.integrations.agents.control_actions import preflight_async
from atellagent_client.integrations.agents.control_gateway_errors import ControlGatewayErrorMixin
from atellagent_client.integrations.agents.host_hooks import handle_host_hook
from atellagent_client.integrations.agents.host_hook_responses import _pretool_result
from atellagent_client.integrations.agents.hook_control_helpers import _safe_error_code
from atellagent_client.sdk.errors import PolicyTransportError
from hook_control_support import HookControlRuntimeCase

CODE = "native_hook_continuation_unsupported"

class _Gateway(ControlGatewayErrorMixin):
    identity_mode = "boundary_identity_only"
    config = SimpleNamespace(api_version="v1")
    gateway_session = SimpleNamespace(base_url="https://fixture.invalid")

    def __init__(self):
        self.requests = []

    def _merge_context(self, **kwargs):
        return kwargs.get("explicit_context") or {}

    async def _request_gateway_async(self, method, url, **kwargs):
        self.requests.append((method, url, kwargs))
        return httpx.Response(403, json={"detail": {
            "error": "Policy violation", "violation_type": CODE,
            "message": "PRIVATE_REMOTE_DIAGNOSTIC", "violations": [CODE],
        }})

class NativeTerminalMessageSocketTests(HookControlRuntimeCase):
    async def test_both_hosts_deny_with_terminal_guidance_through_sdk_and_socket(self):
        gateway = _Gateway()
        async def denied(context):
            return await preflight_async(gateway, context)
        self.governance.preflight_async = denied
        for host in ("claude-code", "codex"):
            with self.subTest(host=host):
                event = {"hook_event_name": "PreToolUse", "session_id": "fixture-session",
                         "tool_use_id": "fixture-" + host, "tool_name": "Read",
                         "tool_input": {"file_path": "/fixture/synthetic.txt", "limit": 1}}
                response = await handle_host_hook(host, self.socket_path, event)
                self.assertEqual(response.exit_code, 0)
                output = json.loads(response.stdout)["hookSpecificOutput"]
                self.assertEqual(output["permissionDecision"], "deny")
                reason = output["permissionDecisionReason"]
                self.assertIn("No approval is pending", reason)
                self.assertIn("cannot pause and resume", reason)
                self.assertIn("managed execution route", reason)
                self.assertNotIn("retry", reason.lower())
                self.assertNotIn("PRIVATE", response.stdout)
                self.assertNotIn("updatedInput", output)
                self.assertEqual(response.stderr, "")
        self.assertEqual(len(gateway.requests), 2)
        self.assertEqual(self.governance.action_gate.calls, [])
        self.assertEqual(self.governance.outcomes, [])
        self.assertEqual(self.runtime._outbox.diagnostics()["awaiting_host"], 0)

class NativeTerminalMessageBoundaryTests(unittest.TestCase):
    def test_unknown_reason_and_success_do_not_acquire_terminal_denial_guidance(self):
        for host in ("claude-code", "codex"):
            denied = _pretool_result(host, False, "untrusted_" + CODE)
            self.assertNotIn("No approval is pending", denied.stdout)
            self.assertIn('"permissionDecision":"deny"', denied.stdout)
            allowed = _pretool_result(host, True, CODE)
            self.assertEqual(allowed.exit_code, 0)
            self.assertEqual(allowed.stdout, "")

    def test_server_error_cannot_be_relabelled_as_native_terminal_policy_denial(self):
        gateway = _Gateway()
        with self.assertRaises(PolicyTransportError) as caught:
            gateway._raise_gateway_error(503, {"detail": {"violation_type": CODE}})
        self.assertEqual(_safe_error_code(caught.exception), "control_gateway_server_status")
        for host in ("claude-code", "codex"):
            response = _pretool_result(host, False, _safe_error_code(caught.exception))
            self.assertNotIn("No approval is pending", response.stdout)
            self.assertIn('"permissionDecision":"deny"', response.stdout)
