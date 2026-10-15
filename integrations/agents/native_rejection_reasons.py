# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
"""Reviewed native-denial codes and fixed local messages, without server input."""

NATIVE_ADMISSION_REJECTION_CODES = frozenset({
    "native_host_action_unclassified", "native_hook_adapter_mismatch", "native_profile_adapter_unsupported",
})

NATIVE_REJECTION_MESSAGES = {
    "native_host_action_unclassified": "Atellagent could not classify this native tool input against a reviewed contract; request denied.",
    "native_hook_adapter_mismatch": "Atellagent hook host or adapter does not match this connection's authorization. Check the selected connection and client version; request denied.",
    "native_profile_adapter_unsupported": "Atellagent has no reviewed tool contract for this hook adapter version; request denied.",
    "native_profile_ambiguous": "Atellagent could not determine an unambiguous native action; request denied.",
    "native_profile_argument_shape_unsupported": "Atellagent does not support this tool argument shape or its extra fields; request denied.",
    "native_profile_argument_type_unsupported": "Atellagent received an unsupported tool argument type; request denied.",
    "native_profile_command_missing": "Atellagent received a native command without its required command text; request denied.",
    "native_profile_detail_unsupported": "Atellagent does not support this image detail value; request denied.",
    "native_profile_host_or_tool_unknown": "Atellagent has no reviewed contract for this native host or tool; request denied.",
    "native_profile_not_registered": "Atellagent has no registered profile for this native action; request denied.",
    "native_profile_patch_context_ambiguous": "Atellagent could not determine the patch workspace context; request denied.",
    "native_profile_patch_grammar_unsupported": "Atellagent does not support this patch input variant; request denied.",
    "native_profile_patch_input_invalid": "Atellagent could not validate the complete patch input; request denied.",
    "native_profile_path_ambiguous": "Atellagent could not determine an unambiguous requested path; request denied.",
    "native_profile_path_invalid": "Atellagent received an invalid requested path; request denied.",
    "native_profile_projection_mismatch": "Atellagent could not validate the native argument projection; request denied.",
    "native_profile_required_argument_missing": "Atellagent received a tool input without a required argument; request denied.",
    "native_profile_sandbox_bypass_unsupported": "Atellagent does not support this native sandbox bypass option; request denied.",
    "native_profile_tool_unsupported": "Atellagent does not support this native tool variant; request denied.",
    "native_profile_url_ambiguous": "Atellagent could not validate an unambiguous requested URL; request denied.",
}


def native_rejection_code(detail: dict) -> str | None:
    """Accept a fixed reason only within a reviewed native-denial envelope."""
    code = detail.get("code")
    if not isinstance(code, str) or code not in NATIVE_ADMISSION_REJECTION_CODES:
        return None
    reason = detail.get("reason_code")
    if isinstance(reason, str) and reason in NATIVE_REJECTION_MESSAGES:
        if code == "native_host_action_unclassified" and reason.startswith("native_profile_"):
            return reason
    return code
