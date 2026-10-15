# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Safe onboarding errors never include remote response bodies or credentials."""

import re


class OnboardingClientError(RuntimeError):
    def __init__(self, code: str, *, retryable: bool = False, status: int | None = None,
                 retry_after: int | None = None):
        self.code = code if re.fullmatch(r"[a-z][a-z0-9_]{0,95}", code) else "onboarding_request_rejected"
        self.retryable, self.status = retryable, status
        self.retry_after = retry_after
        message = ("Update Atellagent before continuing setup (onboarding_upgrade_required)."
            if self.code == "onboarding_upgrade_required"
            else f"Atellagent request failed: {self.code}")
        super().__init__(message)


class MCPExistingRequestError(OnboardingClientError):
    """A retained target must continue its original request instead of duplicating it."""

    def __init__(self, request_id):
        from uuid import UUID
        if not isinstance(request_id, UUID):
            raise TypeError("typed retained MCP request ID required")
        self.request_id = request_id
        super().__init__("onboarding_mcp_existing_request")
