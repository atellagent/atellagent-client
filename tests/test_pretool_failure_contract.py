# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Recognized pre-tool failures must be explicit denials, never hook errors."""
import asyncio
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch
from atellagent_client import hook_launcher
from atellagent_client.integrations.agents.hook_control import HookControlError
from atellagent_client.integrations.agents.host_hooks import handle_host_hook


def event(tool='Read', arguments=None, checkpoint='PreToolUse'):
    return {'hook_event_name': checkpoint, 'session_id': 'synthetic-session',
            'tool_use_id': 'synthetic-call', 'tool_name': tool,
            'tool_input': {} if arguments is None else arguments, 'prompt': 'synthetic'}


def assert_denial(test, response):
    test.assertEqual(response.exit_code, 0)
    test.assertEqual(response.stderr, '')
    value = json.loads(response.stdout)
    test.assertEqual(set(value), {'hookSpecificOutput'})
    body = value['hookSpecificOutput']
    test.assertEqual(body['hookEventName'], 'PreToolUse')
    test.assertEqual(body['permissionDecision'], 'deny')
    test.assertNotIn('PRIVATE', response.stdout)
    return body['permissionDecisionReason']


class PretoolFailureContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_operational_and_unexpected_errors_deny_without_private_details(self):
        errors = [HookControlError(code) for code in (
            'control_unavailable', 'control_socket_missing', 'control_socket_access_denied',
            'local_host_authorization_revoked', 'governed_action_decision_quota_exhausted',
            'control_directive_rejected', 'control_response_invalid', 'PRIVATE_DIAGNOSTIC')]
        errors += [RuntimeError('PRIVATE_DETAIL'), ValueError('PRIVATE_DETAIL'), TypeError('PRIVATE_DETAIL')]
        for host in ('codex', 'claude-code'):
            for error in errors:
                with self.subTest(host=host, error=type(error).__name__, code=getattr(error, 'code', None)):
                    call = AsyncMock(side_effect=error)
                    with patch('atellagent_client.integrations.agents.host_hook_transport._call', call):
                        response = await handle_host_hook(host, '/private/tmp/not-opened.sock', event())
                    reason = assert_denial(self, response)
                    self.assertNotIn('policy denied', reason)
                    call.assert_awaited_once()

    async def test_malformed_rpc_codes_and_post_only_codes_cannot_break_pretool_denial(self):
        for host in ('codex', 'claude-code'):
            for code in (None, {}, [], 17, 'unknown_tool_call', 'outcome_binding_mismatch'):
                with self.subTest(host=host, code=code):
                    with patch('atellagent_client.integrations.agents.host_hook_transport._call', AsyncMock(side_effect=HookControlError(code))):
                        response = await handle_host_hook(host, '/private/tmp/not-opened.sock', event())
                    reason = assert_denial(self, response)
                    self.assertNotIn('already have executed', reason)
                    self.assertNotIn('post-tool', reason)

    async def test_invalid_endpoint_and_malformed_pretool_deny_before_rpc(self):
        for host in ('codex', 'claude-code'):
            for endpoint, payload in [('relative.sock', event()), ('/private/tmp/not-opened.sock', {'hook_event_name': 'PreToolUse'})]:
                with self.subTest(host=host, endpoint=endpoint):
                    call = AsyncMock()
                    with patch('atellagent_client.integrations.agents.host_hook_transport._call', call):
                        response = await handle_host_hook(host, endpoint, payload)
                    assert_denial(self, response)
                    call.assert_not_awaited()

    async def test_only_exact_terminal_goals_recover_and_policy_denials_remain_blocking(self):
        for status in ('blocked', 'paused', 'complete'):
            for code in ('control_unavailable', 'policy_denied'):
                with self.subTest(status=status, code=code):
                    with patch('atellagent_client.integrations.agents.host_hook_transport._call', AsyncMock(side_effect=HookControlError(code))):
                        response = await handle_host_hook('codex', '/private/tmp/not-opened.sock', event('update_goal', {'status': status}))
                    if code == 'control_unavailable':
                        self.assertEqual((response.exit_code, response.stdout, response.stderr), (0, '', ''))
                    else:
                        self.assertIn('policy denied', assert_denial(self, response))
        for host, payload in [('claude-code', event('update_goal', {'status': 'blocked'})), ('codex', event('mcp__other__update_goal', {'status': 'blocked'})), ('codex', event('update_goal', {'status': 'active'})), ('codex', event('update_goal', {'status': 'blocked', 'extra': True}))]:
            with patch('atellagent_client.integrations.agents.host_hook_transport._call', AsyncMock(side_effect=HookControlError('control_unavailable'))):
                assert_denial(self, await handle_host_hook(host, '/private/tmp/not-opened.sock', payload))

    async def test_prompt_and_postflight_failures_keep_their_existing_checkpoint_contract(self):
        for host in ('codex', 'claude-code'):
            for checkpoint in ('UserPromptSubmit', 'PostToolUse'):
                with patch('atellagent_client.integrations.agents.host_hook_transport._call', AsyncMock(side_effect=HookControlError('control_unavailable'))):
                    response = await handle_host_hook(host, '/private/tmp/not-opened.sock', event(checkpoint=checkpoint))
                self.assertEqual(response.exit_code, 2)
                self.assertEqual(response.stdout, '')


class LauncherPretoolFailureContractTests(unittest.TestCase):
    def launch(self, source, host='codex', payload=None):
        with tempfile.TemporaryDirectory(prefix='pretool-failure-contract-') as folder:
            Path(folder, 'synthetic_worker.py').write_text('def main():\n'+''.join('    '+line+'\n' for line in source.splitlines()))
            out, err = io.BytesIO(), io.BytesIO()
            stderr = io.TextIOWrapper(err, write_through=True)
            data = json.dumps(event() if payload is None else payload).encode()
            with patch.object(hook_launcher, '_ADAPTER_MODULE', 'synthetic_worker'), patch.dict(os.environ, {'PYTHONPATH': folder}), patch.object(hook_launcher.sys, 'stdin', SimpleNamespace(buffer=io.BytesIO(data))), patch.object(hook_launcher.sys, 'stdout', SimpleNamespace(buffer=out)), patch.object(hook_launcher.sys, 'stderr', stderr):
                with self.assertRaises(SystemExit) as caught:
                    hook_launcher.main(['--host', host, '--socket', '/private/tmp/not-opened.sock'])
            return SimpleNamespace(exit_code=caught.exception.code, stdout=out.getvalue().decode(), stderr=err.getvalue().decode())

    def test_real_worker_import_or_refusal_errors_become_explicit_generic_denials(self):
        for host in ('codex', 'claude-code'):
            for source in ('if True:', "raise RuntimeError('PRIVATE_DETAIL')", "import sys\nsys.stderr.write('PRIVATE_DETAIL')\nraise SystemExit(2)"):
                with self.subTest(host=host, source=source):
                    reason = assert_denial(self, self.launch(source, host))
                    self.assertNotIn('policy denied', reason)

    def test_worker_failure_still_recovers_only_exact_terminal_goals(self):
        for status in ('blocked', 'paused', 'complete'):
            response = self.launch('if True:', payload=event('update_goal', {'status': status}))
            self.assertEqual((response.exit_code, response.stdout, response.stderr), (0, '', ''))
            assert_denial(self, self.launch('raise SystemExit(2)', payload=event('update_goal', {'status': status})))

    def test_healthy_explicit_policy_denial_is_never_rewritten(self):
        text = json.dumps({'hookSpecificOutput': {'hookEventName': 'PreToolUse', 'permissionDecision': 'deny', 'permissionDecisionReason': 'Authored refusal.'}})
        response = self.launch('import sys\nsys.stdout.write('+repr(text)+')')
        self.assertEqual((response.exit_code, response.stdout, response.stderr), (0, text, ''))

    def test_worker_start_oserror_does_not_leak_or_bypass_pretool_denial(self):
        with patch.object(hook_launcher.subprocess, 'run', side_effect=OSError('PRIVATE_STARTUP_DETAIL')):
            for host in ('codex', 'claude-code'):
                assert_denial(self, self.launch('return', host=host))
            result = self.launch('return', payload=event('update_goal', {'status': 'blocked'}))
            self.assertEqual((result.exit_code, result.stdout, result.stderr), (0, '', ''))
