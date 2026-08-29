# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""
Public exceptions for the Atellagent SDK
"""

from typing import Dict, Any


class PolicyViolationError(Exception):
    """Exception raised when a policy violation is detected"""

    def __init__(
        self, message: str, violation_type: str, details: Dict[str, Any] = None
    ):
        super().__init__(message)
        self.violation_type = violation_type
        self.details = details or {}


class AuthenticationError(Exception):
    """Exception raised when authentication fails"""

    pass


class PolicyTransportError(RuntimeError):
    """A required remote policy decision could not be obtained or parsed."""

    pass
