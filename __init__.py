# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""
Atellagent client library.

`atellagent_client` is the public Python entrypoint for:
- authenticated SDK access to the gateway
- external runtime participation surfaces
- external-agent wedge integrations

Hosted agent/workflow runtime semantics are platform-owned and are not exposed
as reusable public engine kits from this package.

Imports are resolved lazily so package consumers do not pull in the full SDK
and CLI bootstrap stack merely by touching the top-level package namespace.
"""

from __future__ import annotations

from typing import Any

from .protocol.api import CLIENT_LIBRARY_VERSION

_PUBLIC_EXPORTS = {
    "AtellagentClient",
    "create_service_account_client",
    "ServiceAccountConfig",
    "ConnectedSDKRuntime",
    "load_service_account_config_from_yaml",
    "CertificateEnrollmentError",
    "CertificateEnrollmentResult",
    "enroll_service_account_certificate",
    "PolicyViolationError",
    "AuthenticationError",
    "TelemetryEvent",
    "TelemetryEmitter",
    "make_http_emitter",
}

__version__ = CLIENT_LIBRARY_VERSION

__all__ = [
    *_PUBLIC_EXPORTS,
    "cli_main",
]


def __getattr__(name: str) -> Any:
    if name in _PUBLIC_EXPORTS:
        from . import sdk as _sdk

        return getattr(_sdk, name)
    if name == "cli_main":
        from .cli import main as _cli_main

        return _cli_main
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
