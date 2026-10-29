"""Actual local RPC distinguishes native admission errors from policy refusals."""
import json
from unittest.mock import AsyncMock

from atellagent_client.integrations.agents.control import ExternalAgentGovernance
from atellagent_client.integrations.agents.control_gateway_errors import NativeConnectionAuthorizationError
from atellagent_client.integrations.agents.host_hooks import handle_host_hook
from atellagent_client.sdk.errors import PolicyViolationError
from hook_control_support import HookControlRuntimeCase

CODES = ('local_host_authorization_required', 'local_host_authorization_expired',
         'local_host_authorization_revoked', 'local_device_user_mismatch',
         'device_binding_invalid', 'device_inactive', 'device_proof_invalid',
         'device_proof_expired', 'device_proof_replayed')


class NativeAuthorityGoalRecoveryTests(HookControlRuntimeCase):
    def event(self, *, status='blocked', tool='update_goal', extra=None):
        return {'hook_event_name': 'PreToolUse', 'session_id': 'fixture-session',
                'tool_use_id': 'fixture-call', 'tool_name': tool,
                'tool_input': {'status': status, **(extra or {})}}

    async def test_real_authority_error_envelope_recovers_only_exact_codex_stop(self):
        for http_status in (401, 403):
            for code in CODES:
                with self.subTest(http_status=http_status, code=code):
                    try:
                        ExternalAgentGovernance._raise_gateway_error(None, http_status,
                            {'detail': {'code': code, 'message': 'private server prose'}})
                    except NativeConnectionAuthorizationError as failure:
                        self.assertEqual(failure.details, {})
                        self.governance.preflight_async = AsyncMock(side_effect=failure)
                    else:
                        self.fail('Native admission provenance was lost')
                    for status in ('blocked', 'paused', 'complete'):
                        response = await handle_host_hook('codex', self.socket_path,
                            self.event(status=status))
                        self.assertEqual(response.exit_code, 0)
                        self.assertEqual(response.stdout, '')
                        self.assertEqual(response.stderr, '')
                    ordinary = await handle_host_hook('codex', self.socket_path,
                        self.event(tool='view_image'))
                    self.assertEqual(ordinary.exit_code, 0)
                    self.assertEqual(ordinary.stderr, '')
                    self.assertEqual(json.loads(ordinary.stdout)['hookSpecificOutput']['permissionDecision'], 'deny')
                    self.assertNotIn('private server prose', ordinary.stdout)
                    self.assertEqual(self.governance.action_gate.calls, [])

    async def test_authored_policy_reason_collision_stays_a_deny(self):
        for code in (*CODES, 'policy_denied', 'deny', 'technical_hold'):
            self.governance.preflight_async = AsyncMock(side_effect=
                PolicyViolationError('Authored refusal', code))
            for status in ('blocked', 'paused', 'complete'):
                with self.subTest(code=code, status=status):
                    response = await handle_host_hook('codex', self.socket_path,
                        self.event(status=status))
                    self.assertEqual(json.loads(response.stdout)['hookSpecificOutput']['permissionDecision'], 'deny')

    async def test_authority_failure_does_not_release_other_goals_or_hosts(self):
        self.governance.preflight_async = AsyncMock(side_effect=
            NativeConnectionAuthorizationError('Current device revoked', 'device_binding_invalid'))
        for host, event in (
            ('codex', self.event(status='active')),
            ('codex', self.event(extra={'unexpected': True})),
            ('codex', self.event(tool='mcp__lookalike__update_goal')),
            ('claude-code', self.event()),
        ):
            with self.subTest(host=host, event=event):
                response = await handle_host_hook(host, self.socket_path, event)
                self.assertEqual(response.exit_code, 0)
                self.assertEqual(response.stderr, '')
                self.assertEqual(json.loads(response.stdout)['hookSpecificOutput']['permissionDecision'], 'deny')

    def test_policy_envelope_cannot_claim_native_error_provenance_by_reason(self):
        for code in CODES:
            with self.subTest(code=code):
                with self.assertRaises(PolicyViolationError) as caught:
                    ExternalAgentGovernance._raise_gateway_error(None, 403,
                        {'detail': {'violation_type': code, 'message': 'Authored denial'}})
                self.assertNotIsInstance(caught.exception, NativeConnectionAuthorizationError)

    async def test_explicit_policy_envelope_wins_over_native_code_hint(self):
        for http_status in (401, 403):
            for wrapper in ('detail', 'error'):
                for code in CODES:
                    with self.subTest(http_status=http_status, wrapper=wrapper, code=code):
                        try:
                            ExternalAgentGovernance._raise_gateway_error(None, http_status,
                                {wrapper: {'code': code, 'violation_type': 'policy_violation',
                                           'message': 'Authored policy refusal'}})
                        except PolicyViolationError as failure:
                            self.assertNotIsInstance(failure, NativeConnectionAuthorizationError)
                            self.governance.preflight_async = AsyncMock(side_effect=failure)
                        else:
                            self.fail('Explicit policy refusal was lost')
                        for status in ('blocked', 'paused', 'complete'):
                            response = await handle_host_hook('codex', self.socket_path,
                                self.event(status=status))
                            self.assertEqual(json.loads(response.stdout)['hookSpecificOutput']['permissionDecision'], 'deny')
