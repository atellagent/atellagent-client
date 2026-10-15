# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Bounded native action admission and postflight outcome recording."""

from __future__ import annotations

import asyncio
from hashlib import sha256
import json
import re
from typing import Any, Dict, Mapping, Optional

from atellagent_client.protocol.agent_contracts import GovernanceCallContext
from atellagent_client.pep.contracts import raw_action_fingerprint
from atellagent_client.sdk.errors import PolicyViolationError

from .control_gateway_errors import ControlAdmissionError
from atellagent_client.governance.actions import PolicyActionDenied
from .hook_control_helpers import _identifier, _object
from .hook_control_protocol import HookControlError
from .hook_outbox import HookOutcomeCorrelationUnavailable, HookOutcomeBindingMismatch
from .hook_invocation_binding import expected_postflight_fingerprint
from atellagent_client.onboarding.errors import OnboardingClientError

_HOST_CAPABILITY = "agent.control"
_NATIVE_PREFLIGHT_TIMEOUT_SECONDS = 18.0
_PREFLIGHT_TIMEOUT_SECONDS = 17.0
_DIRECTIVE_VERIFICATION_TIMEOUT_SECONDS = 2.0


class HookControlActionMixin:
    """Admit actions only within the bounded remote decision budget."""

    async def _bounded_preflight(self, raw_params: Any) -> Dict[str, Any]:
        """Bound posture, remote decision, local persistence and verification.

        Leave response margin inside the 19-second adapter deadline. A late
        result, including one that suppresses cancellation, cannot permit work.
        """
        loop = asyncio.get_running_loop()
        deadline = loop.time() + min(
            self.rpc_timeout_seconds, _NATIVE_PREFLIGHT_TIMEOUT_SECONDS
        )
        budget = asyncio.timeout_at(deadline)
        async with budget:
            result = await self._preflight(raw_params)
            task = asyncio.current_task()
            if task is not None and task.cancelling():
                raise asyncio.CancelledError
            if budget.expired() or loop.time() >= deadline:
                raise asyncio.TimeoutError
            return result

    async def _grant_scoped_action_key(self, host_correlation: str) -> str:
        """Namespace correlation; current permission is resolved at Gateway."""
        try:
            result = self._native_binding.action_key(host_correlation)
        except OnboardingClientError as exc:
            raise HookControlError(exc.code) from exc
        if not isinstance(result, str) or not result.startswith("local:") or len(result) > 255:
            raise HookControlError("local_user_authorization_action_key_invalid")
        return result

    async def _preflight(self, raw_params: Any) -> Dict[str, Any]:
        params = _object(
            raw_params,
            "params",
            allowed={
                "host", "session_id", "turn_id", "tool_call_id", "tool_name", "arguments",
                "postflight_required", "adapter_version",
                "host_cwd",
            },
        )
        fields = self._turn_fields(params, include_tool=True)
        tool_name = _identifier(params.get("tool_name"), "tool_name")
        arguments = params.get("arguments")
        if not isinstance(arguments, Mapping):
            raise HookControlError("invalid_arguments")
        postflight_required = params.get("postflight_required", True)
        if not isinstance(postflight_required, bool):
            raise HookControlError("invalid_postflight_required")
        adapter_version = _identifier(params.get("adapter_version"), "adapter_version")
        host_cwd = params.get("host_cwd")
        if host_cwd is not None and (
            fields["host"] != "codex"
            or not isinstance(host_cwd, str)
            or not host_cwd.strip()
            or "\x00" in host_cwd
            or len(host_cwd.encode("utf-8", errors="surrogatepass")) > 4096
        ):
            raise HookControlError("invalid_host_cwd")
        host_correlation = self._action_key(**fields)
        action_key = await self._grant_scoped_action_key(host_correlation)
        async with self._reservation_lock:
            if action_key in self._reserving:
                raise HookControlError("duplicate_tool_call")
            self._reserving.add(action_key)
        context = GovernanceCallContext(
            tool_name=tool_name,
            arguments=dict(arguments),
            task_type="host_tool",
            runtime_mode="hook",
            capabilities=[_HOST_CAPABILITY],
            tool_call_id=fields["tool_call_id"],
            action_key=action_key,
            request_payload={
                "host": fields["host"],
                "session_id": fields["session_id"],
                "turn_id": fields["turn_id"],
                "adapter_version": adapter_version,
                **({"host_cwd": host_cwd} if host_cwd is not None else {}),
            },
        )
        try:
            await self._refresh_native_hook_posture()
            receipt = await asyncio.wait_for(
                self.governance.preflight_async(context),
                timeout=min(self.rpc_timeout_seconds, _PREFLIGHT_TIMEOUT_SECONDS),
            )
            raw_context = {
                field: context.request_payload[field]
                for field in ("host", "adapter_version", "host_cwd")
                if field in context.request_payload
            }
            expected_fingerprint = raw_action_fingerprint(
                action=context.tool_name,
                facts=context.arguments,
                raw_context=raw_context,
            )
            if not receipt.is_executable:
                raise HookControlError("policy_denied")
            if (
                receipt.action_key != action_key
                or receipt.action_binding_fingerprint != expected_fingerprint
            ):
                raise HookControlError("decision_binding_invalid")
            try:
                postflight_fingerprint = expected_postflight_fingerprint(
                    tool_name=context.tool_name, arguments=context.arguments,
                    raw_context=raw_context, assessment_reference=receipt.assessment_reference,
                )
            except ValueError as exc:
                raise HookControlError("control_response_invalid") from exc
            if postflight_required:
                try:
                    await self._outbox.reserve(
                        action_key,
                        receipt.action_binding_fingerprint,
                        host_correlation=host_correlation,
                        postflight_binding_fingerprint=postflight_fingerprint,
                    )
                except ValueError as exc:
                    if "already exists" in str(exc):
                        raise HookControlError("duplicate_tool_call") from exc
                    raise HookControlError("outcome_delivery_unavailable") from exc
            if receipt.obligations:
                if not postflight_required:
                    try:
                        await self._outbox.reserve(
                            action_key,
                            receipt.action_binding_fingerprint,
                            host_correlation=host_correlation,
                        )
                    except ValueError as exc:
                        raise HookControlError("outcome_delivery_unavailable") from exc
                await self._record_local_outcome(
                    action_key,
                    success=False,
                    outcome_observation="local_protocol_rejected",
                    error_type="UnsupportedObligation",
                )
                raise HookControlError("unsupported_obligation")
            try:
                await asyncio.wait_for(
                    self.governance.action_gate.enforce(
                        action=context.tool_name,
                        integration_type="agent",
                        correlation_id=receipt.action_key,
                        encoded_directive=receipt.control_directive,
                        facts=context.arguments,
                        raw_context=raw_context,
                        workflow_context=receipt.workflow_context,
                        policy_decision_id=receipt.decision_id,
                    ),
                    timeout=min(
                        self.rpc_timeout_seconds,
                        _DIRECTIVE_VERIFICATION_TIMEOUT_SECONDS,
                    ),
                )
            except Exception as exc:
                if not postflight_required:
                    try:
                        await self._outbox.reserve(
                            action_key,
                            receipt.action_binding_fingerprint,
                            host_correlation=host_correlation,
                        )
                    except ValueError as reserve_error:
                        raise HookControlError("outcome_delivery_unavailable") from reserve_error
                await self._record_local_outcome(
                    action_key,
                    success=False,
                    outcome_observation=("local_policy_denied" if isinstance(exc, PolicyActionDenied)
                                         else "local_directive_verification_failed"),
                    error_type="ActionDenied",
                )
                if isinstance(exc, PolicyActionDenied):
                    raise HookControlError("policy_denied") from exc
                if isinstance(exc, asyncio.TimeoutError):
                    raise HookControlError("control_directive_timeout") from exc
                raise HookControlError("control_directive_rejected") from exc
        except ControlAdmissionError as exc:
            # Preserve admission provenance across the local RPC. Do not turn
            # quota, native coverage or connection failures into healthy denies.
            # Ordinary tools stay blocked; exact terminal goals can recover.
            raise HookControlError(exc.violation_type) from exc
        except PolicyViolationError as exc:
            return {
                "allowed": False,
                "reason_code": str(exc.violation_type or "policy_denied"),
            }
        finally:
            async with self._reservation_lock:
                self._reserving.discard(action_key)
        return {
            "allowed": True,
            "action_key": receipt.action_key,
            "decision_id": receipt.decision_id,
            "correlation_id": receipt.action_key,
            **({"assessment_reference": receipt.assessment_reference}
               if receipt.assessment_reference else {}),
        }

    async def _postflight(self, raw_params: Any) -> Dict[str, Any]:
        params = _object(
            raw_params,
            "params",
            allowed={
                "host", "session_id", "turn_id", "tool_call_id", "success",
                "result_payload", "error_message", "error_type", "outcome_observation",
                "hook_event_name", "observed_action_fingerprint",
            },
        )
        fields = self._turn_fields(params, include_tool=True)
        success_value = params.get("success")
        if success_value is not None and not isinstance(success_value, bool):
            raise HookControlError("invalid_success")
        outcome_observation = str(params.get("outcome_observation") or "").strip()
        if success_value is None and outcome_observation != "result_observed":
            raise HookControlError("invalid_outcome_observation")
        if not outcome_observation:
            outcome_observation = (
                "result_observed" if success_value is True else "execution_failed"
            )
        host_correlation = self._action_key(**fields)
        try:
            action_key = await self._outbox.action_key_for_host_correlation(
                host_correlation
            )
        except HookOutcomeCorrelationUnavailable as exc:
            raise HookControlError("unknown_tool_call") from exc
        observed_fingerprint = params.get("observed_action_fingerprint")
        if not isinstance(observed_fingerprint, str) or not re.fullmatch(r"[0-9a-f]{64}", observed_fingerprint):
            raise HookControlError("outcome_binding_mismatch")
        error_type = str(params.get("error_type") or "").strip() or None
        if error_type is not None:
            error_type = _identifier(error_type, "error_type")
        result_payload = params.get("result_payload") if success_value is not False else None
        result_byte_length: Optional[int] = None
        result_sha256: Optional[str] = None
        if result_payload is not None:
            try:
                serialized = json.dumps(
                    result_payload, sort_keys=True, separators=(",", ":")
                ).encode("utf-8")
            except (TypeError, ValueError):
                serialized = None
            if serialized is not None:
                result_byte_length = len(serialized)
                result_sha256 = sha256(serialized).hexdigest()
        try:
            await self._record_local_outcome(
                action_key,
                success=success_value,
                outcome_observation=outcome_observation,
                error_type=error_type,
                result_byte_length=result_byte_length,
                result_sha256=result_sha256,
                observed_action_fingerprint=observed_fingerprint,
            )
        except HookOutcomeCorrelationUnavailable as exc:
            raise HookControlError("unknown_tool_call") from exc
        except HookOutcomeBindingMismatch as exc:
            raise HookControlError("outcome_binding_mismatch") from exc
        except (ValueError, OSError) as exc:
            raise HookControlError("outcome_delivery_unavailable") from exc
        return {"recorded": True, "action_key": action_key, "delivery_status": "queued"}

    async def _record_local_outcome(
        self,
        action_key: str,
        *,
        success: Optional[bool],
        outcome_observation: str,
        error_type: Optional[str] = None,
        result_byte_length: Optional[int] = None,
        result_sha256: Optional[str] = None,
        observed_action_fingerprint: Optional[str] = None,
    ) -> None:
        await self._outbox.record_outcome(
            action_key,
            {
                "outcome_observation": outcome_observation,
                "success": success,
                "result_byte_length": result_byte_length,
                "result_sha256": result_sha256,
                "error_type": error_type,
            },
            observed_action_fingerprint=observed_action_fingerprint,
        )
        self._outbox_wakeup.set()
