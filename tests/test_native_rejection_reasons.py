# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
"""Gateway rejection envelopes reach both host adapters without private diagnostics."""
import json
import unittest

from atellagent_client.integrations.agents.control import ExternalAgentGovernance
from atellagent_client.integrations.agents.hook_control import _safe_error_code
from atellagent_client.integrations.agents.host_hook_responses import _pretool_result
from atellagent_client.integrations.agents.native_rejection_reasons import (
    NATIVE_ADMISSION_REJECTION_CODES, NATIVE_REJECTION_MESSAGES, native_rejection_code,
)
from atellagent_client.sdk.errors import PolicyTransportError, PolicyViolationError


class NativeRejectionReasonTests(unittest.TestCase):
    def test_fixed_reasons_reach_both_hosts_through_all_gateway_envelopes(self):
        for reason in NATIVE_REJECTION_MESSAGES:
            code = reason if reason in NATIVE_ADMISSION_REJECTION_CODES else 'native_host_action_unclassified'
            detail = {'code': code, 'reason_code': reason, 'message': 'PRIVATE_DIAGNOSTIC',
                'arguments': {'content': 'PRIVATE_CONTENT'}}
            for payload in (detail, {'detail': detail}, {'error': detail}):
                for host in ('codex', 'claude-code'):
                    with self.subTest(reason=reason, payload_kind=list(payload)[0], host=host):
                        with self.assertRaises(PolicyViolationError) as caught:
                            ExternalAgentGovernance._raise_gateway_error(None, 403, payload)
                        exc = caught.exception
                        self.assertEqual(_safe_error_code(exc), reason)
                        self.assertNotIn('PRIVATE', str(exc))
                        self.assertEqual(exc.details, {})
                        response = _pretool_result(host, False, _safe_error_code(exc))
                        output = json.loads(response.stdout)['hookSpecificOutput']
                        self.assertEqual(output['permissionDecision'], 'deny')
                        self.assertEqual(output['permissionDecisionReason'], NATIVE_REJECTION_MESSAGES[reason])
                        self.assertNotIn('PRIVATE', response.stdout)
                        self.assertEqual(response.exit_code, 0)

    def test_unknown_rpc_error_cannot_expose_diagnostics_as_a_fixed_reason(self):
        from atellagent_client.integrations.agents.host_hook_responses import _failure
        from atellagent_client.integrations.agents.hook_control_protocol import HookControlError

        response = _failure(HookControlError('PRIVATE_DIAGNOSTIC'))
        self.assertEqual(response.exit_code, 2)
        self.assertEqual(response.stdout, '')
        self.assertNotIn('PRIVATE', response.stderr)
        self.assertIn('local control could not complete', response.stderr)

    def test_unknown_or_malformed_reason_stays_generic_and_denied(self):
        for reason in ('PRIVATE_DIAGNOSTIC', {'private': 'value'}, ['private'], None, 1):
            detail = {'code': 'native_host_action_unclassified', 'reason_code': reason}
            self.assertEqual(native_rejection_code(detail), 'native_host_action_unclassified')
        for code in ('PRIVATE_DIAGNOSTIC', {}, [], None, 1):
            self.assertIsNone(native_rejection_code({'code': code, 'reason_code': 'native_profile_path_invalid'}))
        self.assertIsNone(native_rejection_code({'reason_code': 'native_profile_path_invalid'}))
        for host in ('codex', 'claude-code'):
            response = _pretool_result(host, False, 'PRIVATE_DIAGNOSTIC')
            self.assertNotIn('PRIVATE', response.stdout)
            self.assertIn('Atellagent policy denied this request.', response.stdout)

    def test_adapter_mismatch_cannot_be_relabelled_as_input_error(self):
        detail = {'code': 'native_hook_adapter_mismatch', 'reason_code': 'native_profile_path_invalid'}
        self.assertEqual(native_rejection_code(detail), 'native_hook_adapter_mismatch')

    def test_reason_on_a_server_error_never_masquerades_as_an_input_denial(self):
        with self.assertRaises(PolicyTransportError) as caught:
            ExternalAgentGovernance._raise_gateway_error(None, 503, {'error': {
                'code': 'native_host_action_unclassified', 'reason_code': 'native_profile_path_invalid'}})
        self.assertEqual(_safe_error_code(caught.exception), 'control_gateway_server_status')

    def test_legacy_string_denial_and_success_remain_compatible(self):
        with self.assertRaises(PolicyViolationError) as caught:
            ExternalAgentGovernance._raise_gateway_error(None, 403, {'error': 'old gateway denial'})
        self.assertEqual(_safe_error_code(caught.exception), 'policy_violation')
        for host in ('codex', 'claude-code'):
            self.assertEqual(_pretool_result(host, True, 'native_profile_path_invalid').stdout, '')
