# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Lazy public SDK namespace with no import-time network or runtime activity."""

from __future__ import annotations

from importlib import import_module
from typing import Any


_EXPORT_MODULES = {
    "AtellagentClient": ".client",
    "create_service_account_client": ".client",
    "ServiceAccountConfig": ".config",
    "load_service_account_config_from_yaml": ".config",
    "ConnectedSDKRuntime": ".connected",
    "AuthenticationError": ".errors",
    "PolicyViolationError": ".errors",
    "CertificateEnrollmentError": ".enrollment",
    "CertificateEnrollmentResult": ".enrollment",
    "enroll_service_account_certificate": ".enrollment",
    "TelemetryEmitter": ".telemetry",
    "TelemetryEvent": ".telemetry",
    "make_http_emitter": ".telemetry",
}

__all__ = sorted(_EXPORT_MODULES)


def __getattr__(name: str) -> Any:
    module_name = _EXPORT_MODULES.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(module_name, __name__), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
