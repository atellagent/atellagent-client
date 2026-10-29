"""One local MCP participant owner per admitted device/integration."""

from contextlib import contextmanager
from uuid import UUID

from .errors import OnboardingClientError
from .process_lease import process_lease


@contextmanager
def runtime_ownership(device_context, request_id):
    if not isinstance(request_id, UUID):
        raise ValueError("Exact retained runtime request required")
    if device_context is None:
        yield  # Standalone workloads may intentionally scale receiver instances.
        return
    path = device_context.installation.directory / f".mcp-runtime-{request_id}.lock"
    with process_lease(path) as acquired:
        if not acquired:
            raise OnboardingClientError("onboarding_mcp_runtime_busy", retryable=True)
        yield
