"""Exercise admission provenance through a real isolated controller RPC.

No installed hook, production policy, service or quota is modified by these tests.
"""
import asyncio
import sys
from dataclasses import replace
import json
from unittest.mock import AsyncMock
from types import SimpleNamespace

from atellagent_client.governance.actions import RuntimeActionGate, ActionDenied, PolicyActionDenied
from atellagent_client.integrations.agents.control import ExternalAgentGovernance
from atellagent_client.integrations.agents.control_actions import preflight_async
from atellagent_client.integrations.agents.control_gateway_errors import ControlAdmissionError
from atellagent_client.integrations.agents.host_hooks import handle_host_hook
from atellagent_client.pep import RemoteControlDirective
from atellagent_client.sdk.errors import PolicyViolationError, PolicyTransportError
from hook_control_support import HookControlRuntimeCase


class GoalAdmissionRecoveryTests(HookControlRuntimeCase):
    def event(self, *, status='blocked', tool='update_goal', extra=None):
        self.sequence = getattr(self, 'sequence', 0) + 1
        return {'hook_event_name': 'PreToolUse', 'session_id': 'goal-admission-fixture',
                'tool_use_id': f'call-{self.sequence}', 'tool_name': tool,
                'tool_input': {'status': status, **(extra or {})}}

    @staticmethod
    def gateway_failure(status, detail):
        try:
            ExternalAgentGovernance._raise_gateway_error(None, status, {'detail': detail})
        except (PolicyViolationError, PolicyTransportError) as exc:
            return exc
        raise AssertionError('Expected gateway refusal')

    def assert_blocked(self, response):
        self.assertEqual(response.exit_code, 0)
        self.assertEqual(response.stderr, '')
        self.assertEqual(json.loads(response.stdout)['hookSpecificOutput']['permissionDecision'], 'deny')

    async def test_real_quota_coverage_connection_and_transport_failures_release_only_terminal_goals(self):
        cases = (
            (429, {'error': 'entitlement_denied', 'code': 'governed_action_decision_quota_exhausted'}),
            (409, {'error': 'connection_required'}),
            (403, {'code': 'native_host_action_unclassified', 'reason_code': 'native_profile_not_registered'}),
            (403, {'code': 'native_hook_adapter_mismatch'}),
            (403, {'code': 'native_profile_adapter_unsupported'}),
            (503, {}), (422, {}), (429, {}),
        )
        for http_status, detail in cases:
            with self.subTest(status=http_status, code=detail.get('code')):
                failure = self.gateway_failure(http_status, detail)
                self.governance.preflight_async = AsyncMock(side_effect=failure)
                for status in ('blocked', 'paused', 'complete'):
                    response = await handle_host_hook('codex', self.socket_path, self.event(status=status))
                    self.assertEqual((response.exit_code, response.stdout, response.stderr), (0, '', ''))
                for host, event in (
                    ('codex', self.event(tool='view_image')),
                    ('codex', self.event(status='active')),
                    ('codex', self.event(extra={'unexpected': True})),
                    ('codex', self.event(tool='mcp__lookalike__update_goal')),
                    ('claude-code', self.event()),
                ):
                    self.assert_blocked(await handle_host_hook(host, self.socket_path, event))
                self.assertEqual(self.governance.action_gate.calls, [])

    async def test_native_admission_reasons_survive_rpc_to_both_host_denials(self):
        from atellagent_client.integrations.agents.native_rejection_reasons import (
            NATIVE_ADMISSION_REJECTION_CODES, NATIVE_REJECTION_MESSAGES,
        )

        for host in ('codex', 'claude-code'):
            for reason in sorted(NATIVE_REJECTION_MESSAGES):
                with self.subTest(host=host, reason=reason):
                    code = reason if reason in NATIVE_ADMISSION_REJECTION_CODES else 'native_host_action_unclassified'
                    failure = self.gateway_failure(403, {
                        'code': code, 'reason_code': reason, 'message': 'PRIVATE_DIAGNOSTIC',
                    })
                    self.governance.preflight_async = AsyncMock(side_effect=failure)
                    event = {
                        'hook_event_name': 'PreToolUse', 'session_id': 'coverage-reason-fixture',
                        'tool_use_id': host + '-' + reason,
                        'tool_name': 'mcp__codex_app__read_thread' if host == 'codex' else 'Read',
                        'tool_input': ({'threadId': 'fixture', 'turnLimit': 11}
                                       if host == 'codex' else {'file_path': 'fixture'}),
                    }
                    response = await handle_host_hook(host, self.socket_path, event)
                    self.assertEqual(response.exit_code, 0)
                    self.assertEqual(response.stderr, '')
                    self.assertEqual(json.loads(response.stdout), {'hookSpecificOutput': {
                        'hookEventName': 'PreToolUse', 'permissionDecision': 'deny',
                        'permissionDecisionReason': NATIVE_REJECTION_MESSAGES[reason],
                    }})
                    self.assertNotIn('PRIVATE', response.stdout)
        self.assertEqual(self.governance.action_gate.calls, [])

    async def test_actual_launcher_worker_rpc_preserves_native_admission_json_denial(self):
        for host, tool, arguments in (
            ('codex', 'view_image', {'path': r'C:\fixture.png', 'detail': 'high'}),
            ('claude-code', 'Read', {'file_path': r'C:\fixture.txt', 'limit': 1}),
        ):
            with self.subTest(host=host):
                self.governance.preflight_async = AsyncMock(side_effect=self.gateway_failure(403, {
                    'code': 'native_host_action_unclassified',
                    'reason_code': 'native_profile_path_ambiguous', 'message': 'PRIVATE_DIAGNOSTIC',
                }))
                event = {'hook_event_name': 'PreToolUse', 'session_id': 'native-denial-launcher',
                    'tool_use_id': host + '-image-read', 'tool_name': tool, 'tool_input': arguments}
                child = await asyncio.create_subprocess_exec(
                    sys.executable, '-m', 'atellagent_client.hook_launcher',
                    '--host', host, '--socket', self.socket_path,
                    stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                try:
                    stdout, stderr = await asyncio.wait_for(child.communicate(json.dumps(event).encode()), timeout=8)
                finally:
                    if child.returncode is None:
                        child.kill()
                        await child.wait()
                self.assertEqual(child.returncode, 0)
                self.assertEqual(stderr, b'')
                self.assertEqual(json.loads(stdout), {'hookSpecificOutput': {
                    'hookEventName': 'PreToolUse', 'permissionDecision': 'deny',
                    'permissionDecisionReason': 'Atellagent could not determine an unambiguous requested path; request denied.',
                }})
                self.assertNotIn(b'PRIVATE', stdout)
        self.assertEqual(self.governance.action_gate.calls, [])

    async def test_actual_launcher_worker_rpc_structures_operational_refusals(self):
        for host in ('codex', 'claude-code'):
            for status, detail in (
                (503, {}),
                (401, {'code': 'local_host_authorization_revoked'}),
                (429, {'error': 'entitlement_denied', 'code': 'governed_action_decision_quota_exhausted'}),
            ):
                with self.subTest(host=host, status=status):
                    self.governance.preflight_async = AsyncMock(side_effect=self.gateway_failure(status, detail))
                    payload = {'hook_event_name': 'PreToolUse', 'session_id': 'operational-denial-launcher',
                               'tool_use_id': host+'-'+str(status), 'tool_name': 'Read',
                               'tool_input': {'file_path': 'synthetic-fixture'}}
                    child = await asyncio.create_subprocess_exec(
                        sys.executable, '-m', 'atellagent_client.hook_launcher',
                        '--host', host, '--socket', self.socket_path,
                        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE)
                    try:
                        stdout, stderr = await asyncio.wait_for(child.communicate(json.dumps(payload).encode()), timeout=8)
                    finally:
                        if child.returncode is None:
                            child.kill()
                            await child.wait()
                    self.assertEqual(child.returncode, 0)
                    self.assertEqual(stderr, b'')
                    body = json.loads(stdout)['hookSpecificOutput']
                    self.assertEqual((body['hookEventName'], body['permissionDecision']), ('PreToolUse', 'deny'))
                    self.assertNotIn('policy denied', body['permissionDecisionReason'])
        self.assertEqual(self.governance.action_gate.calls, [])

    async def test_authored_policy_envelopes_win_over_all_admission_hints(self):
        cases = (
            (429, {'error': 'entitlement_denied', 'code': 'governed_action_decision_quota_exhausted'}),
            (409, {'error': 'connection_required'}),
            (403, {'code': 'native_host_action_unclassified', 'reason_code': 'native_profile_not_registered'}),
            (403, {'code': 'native_hook_adapter_mismatch'}),
            (403, {'code': 'native_profile_adapter_unsupported'}),
            (401, {'code': 'local_host_authorization_expired'}),
        )
        for http_status, detail in cases:
            with self.subTest(status=http_status, code=detail.get('code')):
                failure = self.gateway_failure(http_status, {**detail, 'violation_type': 'policy_violation'})
                self.assertIsInstance(failure, PolicyViolationError)
                self.assertNotIsInstance(failure, ControlAdmissionError)
                self.governance.preflight_async = AsyncMock(side_effect=failure)
                for status in ('blocked', 'paused', 'complete'):
                    self.assert_blocked(await handle_host_hook('codex', self.socket_path, self.event(status=status)))
        # An unrecognized 403 never acquires admission provenance.
        self.assertNotIsInstance(self.gateway_failure(403, {'message': 'Denied'}), ControlAdmissionError)

    async def test_verification_failure_recovers_but_verified_policy_deny_stays_blocking(self):
        class Verifier:
            mode = 'invalid'

            async def verify(self, encoded, intent):
                if self.mode == 'invalid':
                    raise ValueError('synthetic signature verification failure')
                return RemoteControlDirective(
                    mode='enforce', allowed=False,
                    action=intent.action if self.mode == 'deny' else 'different_action',
                    integration_type=intent.capability.integration_type,
                    audience='fixture', expires_at=9999999999, directive_id='fixture',
                    # A policy may use any reason, including an operational-looking one.
                    reason_code='control_directive_rejected',
                )

        verifier = Verifier()
        gate = RuntimeActionGate(source='cluster_directive', directive_verifier=verifier)
        self.governance.action_gate = gate
        for mode in ('invalid', 'scope_mismatch', 'deny'):
            verifier.mode = mode
            for status in ('blocked', 'paused', 'complete'):
                with self.subTest(mode=mode, status=status):
                    response = await handle_host_hook('codex', self.socket_path, self.event(status=status))
                    if mode == 'deny':
                        self.assert_blocked(response)
                    else:
                        self.assertEqual((response.exit_code, response.stdout, response.stderr), (0, '', ''))
            self.assert_blocked(await handle_host_hook('codex', self.socket_path, self.event(tool='view_image')))
        verifier.mode = 'deny'
        with self.assertRaises(PolicyActionDenied):
            await gate.enforce(action='update_goal', integration_type='agent',
                               correlation_id='fixture', encoded_directive='fixture')
        verifier.mode = 'invalid'
        with self.assertRaises(ActionDenied) as caught:
            await gate.enforce(action='update_goal', integration_type='agent',
                               correlation_id='fixture', encoded_directive='fixture')
        self.assertNotIsInstance(caught.exception, PolicyActionDenied)

    async def test_unsupported_obligation_recovers_terminal_goal_but_not_other_actions(self):
        original = self.governance.preflight_async

        async def with_obligation(context):
            return replace(await original(context), obligations=({'type': 'unsupported-fixture'},))

        self.governance.preflight_async = with_obligation
        for status in ('blocked', 'paused', 'complete'):
            response = await handle_host_hook('codex', self.socket_path, self.event(status=status))
            self.assertEqual((response.exit_code, response.stdout, response.stderr), (0, '', ''))
        self.assert_blocked(await handle_host_hook('codex', self.socket_path, self.event(tool='view_image')))
        self.assertEqual(self.governance.action_gate.calls, [])

    async def test_healthy_receipt_denial_blocks_every_terminal_status(self):
        original = self.governance.preflight_async

        async def denied(context):
            return replace(await original(context), allowed=False, outcome='deny')

        self.governance.preflight_async = denied
        for status in ('blocked', 'paused', 'complete'):
            self.assert_blocked(await handle_host_hook('codex', self.socket_path, self.event(status=status)))
        self.assertEqual(self.governance.action_gate.calls, [])

    async def test_http_success_technical_hold_recovers_but_deny_and_approval_do_not(self):
        # Use the actual HTTP facade, not a preconstructed error, to cover the
        # separate HTTP-200 refusal path that previously lost provenance too.
        transport = SimpleNamespace(
            identity_mode='boundary_identity_only',
            gateway_session=SimpleNamespace(base_url='https://fixture.invalid'),
            config=SimpleNamespace(api_version='v1'),
            _merge_context=lambda **kwargs: {},
        )
        async def facade(context):
            return await preflight_async(transport, context)
        self.governance.preflight_async = facade
        for outcome, reason in (
            ('technical_hold', 'semantic_action_review_timeout'),
            ('technical_hold', 'safety_dependency_unavailable'),
            ('deny', 'semantic_action_review_timeout'),
            ('require_approval', 'governed_action_decision_quota_exhausted'),
        ):
            payload = {'success': False, 'decision': {
                'outcome': outcome, 'violation_type': reason, 'reason': 'synthetic refusal'}}
            response = SimpleNamespace(status_code=200, content=b'{}', json=lambda: payload)
            transport._request_gateway_async = AsyncMock(return_value=response)
            for status in ('blocked', 'paused', 'complete'):
                with self.subTest(outcome=outcome, status=status):
                    actual = await handle_host_hook('codex', self.socket_path, self.event(status=status))
                    if outcome == 'technical_hold':
                        self.assertEqual((actual.exit_code, actual.stdout, actual.stderr), (0, '', ''))
                    else:
                        self.assert_blocked(actual)
            self.assert_blocked(await handle_host_hook('codex', self.socket_path, self.event(tool='view_image')))
        self.assertEqual(self.governance.action_gate.calls, [])

    async def test_actual_launcher_worker_rpc_preserves_quota_and_policy_provenance(self):
        async def launch(event):
            child = await asyncio.create_subprocess_exec(
                sys.executable, '-m', 'atellagent_client.hook_launcher',
                '--host', 'codex', '--socket', self.socket_path,
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout, stderr = await asyncio.wait_for(
                    child.communicate(json.dumps(event).encode()), timeout=8)
            finally:
                if child.returncode is None:
                    child.kill()
                    await child.wait()
            return child.returncode, stdout, stderr

        quota = self.gateway_failure(429, {'error': 'entitlement_denied',
            'code': 'governed_action_decision_quota_exhausted'})
        for status in ('blocked', 'paused', 'complete'):
            self.governance.preflight_async = AsyncMock(side_effect=quota)
            actual = await launch(self.event(status=status))
            self.assertEqual(actual, (0, b'', b''))
            self.governance.preflight_async = AsyncMock(side_effect=
                PolicyViolationError('Authored refusal', 'governed_action_decision_quota_exhausted'))
            code, stdout, stderr = await launch(self.event(status=status))
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(stdout)['hookSpecificOutput']['permissionDecision'], 'deny')
            self.assertEqual(stderr, b'')
        self.governance.preflight_async = AsyncMock(side_effect=quota)
        code, stdout, stderr = await launch(self.event(tool='view_image'))
        self.assertEqual(code, 0)
        self.assertEqual(stderr, b'')
        output = json.loads(stdout)['hookSpecificOutput']
        self.assertEqual(output['permissionDecision'], 'deny')
        self.assertIn('allowance is exhausted', output['permissionDecisionReason'])
