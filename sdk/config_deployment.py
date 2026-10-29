# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Strict local packaging configuration for outbound connected participants."""

from __future__ import annotations

from typing import Any, Dict, Optional

from .config_coercion import _coerce_headers, _coerce_string_list
from .config_models import (
    BridgeDeploymentConfig,
    DeploymentConfig,
    SDKDeploymentConfig,
)


def _optional_text(value: Any) -> Optional[str]:
    text = str(value or "").strip()
    return text or None


def _reject_unknown_deployment_fields(
    data: Dict[str, Any],
    allowed_fields: set[str],
) -> None:
    unsupported = sorted(set(data) - allowed_fields)
    if unsupported:
        raise ValueError("Unsupported deployment fields: " + ", ".join(unsupported))


def _coerce_env_map(value: Any) -> Dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError("deployment.target_env_map must be an object")
    result: Dict[str, str] = {}
    for target_name, source_name in value.items():
        target = str(target_name or "").strip()
        source = str(source_name or "").strip()
        if not target or not source:
            raise ValueError(
                "deployment.target_env_map keys and source environment names are required"
            )
        result[target] = source
    return result


def _parse_deployment_config(
    raw: Optional[Dict[str, Any]],
    *,
    packaging: str,
) -> DeploymentConfig:
    if raw is not None and not isinstance(raw, dict):
        raise ValueError("deployment must be an object")
    data = dict(raw or {})
    normalized_packaging = str(packaging or "").strip().lower()
    deployment_type = str(data.get("type") or normalized_packaging).strip().lower()
    if deployment_type not in {"sdk", "bridge"}:
        raise ValueError("deployment.type must be 'sdk' or 'bridge'")
    if deployment_type != normalized_packaging:
        raise ValueError("deployment.type must match top-level packaging")

    if deployment_type == "sdk":
        _reject_unknown_deployment_fields(data, {"type"})
        return SDKDeploymentConfig()

    allowed_fields = {
        "type",
        "target_transport",
        "target_url",
        "target_unix_socket",
        "target_command",
        "target_args",
        "target_env_map",
        "upstream_headers",
        "upstream_auth_header",
        "upstream_auth_token_env",
        "upstream_cert_path",
        "upstream_key_path",
        "upstream_ca_path",
    }
    _reject_unknown_deployment_fields(data, allowed_fields)
    transport = str(data.get("target_transport") or "handler").strip().lower()
    if transport not in {"handler", "http", "stdio"}:
        raise ValueError(
            "deployment.target_transport must be 'handler', 'http', or 'stdio'"
        )

    target_url = _optional_text(data.get("target_url"))
    target_socket = _optional_text(data.get("target_unix_socket"))
    target_command = _optional_text(data.get("target_command"))
    http_fields = {
        "target_url",
        "target_unix_socket",
        "upstream_headers",
        "upstream_auth_header",
        "upstream_auth_token_env",
        "upstream_cert_path",
        "upstream_key_path",
        "upstream_ca_path",
    }
    stdio_fields = {"target_command", "target_args", "target_env_map"}
    if transport == "handler" and any(data.get(name) for name in http_fields | stdio_fields):
        raise ValueError("handler bridge must not configure a network or stdio target")
    if transport == "http":
        if not target_url:
            raise ValueError("HTTP bridge requires deployment.target_url")
        if any(data.get(name) for name in stdio_fields):
            raise ValueError("HTTP bridge must not configure stdio target fields")
    if transport == "stdio":
        if not target_command:
            raise ValueError("stdio bridge requires deployment.target_command")
        if any(data.get(name) for name in http_fields):
            raise ValueError("stdio bridge must not configure HTTP target fields")

    has_upstream_cert = bool(data.get("upstream_cert_path"))
    has_upstream_key = bool(data.get("upstream_key_path"))
    if has_upstream_cert != has_upstream_key:
        raise ValueError(
            "deployment.upstream_cert_path and upstream_key_path must be set together"
        )
    if data.get("upstream_auth_header") and not data.get("upstream_auth_token_env"):
        raise ValueError(
            "deployment.upstream_auth_header requires upstream_auth_token_env"
        )

    return BridgeDeploymentConfig(
        target_transport=transport,
        target_url=target_url,
        target_unix_socket=target_socket,
        target_command=target_command,
        target_args=_coerce_string_list(data.get("target_args")),
        target_env_map=_coerce_env_map(data.get("target_env_map")),
        upstream_headers=_coerce_headers(data.get("upstream_headers")),
        upstream_auth_header=_optional_text(data.get("upstream_auth_header")),
        upstream_auth_token_env=_optional_text(data.get("upstream_auth_token_env")),
        upstream_cert_path=_optional_text(data.get("upstream_cert_path")),
        upstream_key_path=_optional_text(data.get("upstream_key_path")),
        upstream_ca_path=_optional_text(data.get("upstream_ca_path")),
    )


def deployment_config_to_dict(deployment: DeploymentConfig) -> Dict[str, Any]:
    """Return only the active clean-cutover packaging fields."""
    if isinstance(deployment, SDKDeploymentConfig):
        return {"type": "sdk"}
    return {
        "type": "bridge",
        "target_transport": deployment.target_transport,
        "target_url": deployment.target_url,
        "target_unix_socket": deployment.target_unix_socket,
        "target_command": deployment.target_command,
        "target_args": list(deployment.target_args),
        "target_env_map": dict(deployment.target_env_map),
        "upstream_headers": dict(deployment.upstream_headers),
        "upstream_auth_header": deployment.upstream_auth_header,
        "upstream_auth_token_env": deployment.upstream_auth_token_env,
        "upstream_cert_path": deployment.upstream_cert_path,
        "upstream_key_path": deployment.upstream_key_path,
        "upstream_ca_path": deployment.upstream_ca_path,
    }


__all__ = ["_parse_deployment_config", "deployment_config_to_dict"]
