"""Exact admitted device association for machine-authority MCP integrations."""

from .errors import OnboardingClientError


def require_device_association(context, draft):
    """Verify retained installation facts; server proof/admission is independent."""
    if context is None:
        raise OnboardingClientError("onboarding_mcp_device_required")
    setup, receipt, _ = context.installation.load()
    device = context.client.device
    expected = (draft.setup.tenant_id, draft.setup.owner_user_id,
                draft.proposal.device_id, draft.proposal.device_onboarding_session_id)
    if (draft.proposal.account_type != "mcp_server" or setup.kind != "device"
            or receipt.kind != "device" or receipt.id != setup.operation_id
            or setup.platform_origin != draft.setup.platform_origin
            or (setup.tenant_id, setup.owner_user_id, receipt.device_id, setup.operation_id) != expected
            or (device.tenant_id, device.owner_user_id, device.device_id, context.setup.operation_id) != expected
            or setup.encode() != context.setup.encode()
            or setup.keys.proof_key.public_key().public_bytes_raw()
               != device.proof_key.public_key().public_bytes_raw()):
        raise OnboardingClientError("onboarding_mcp_device_changed")
    return setup


def device_request_provider(context, draft):
    """Bind fresh request proofs to exact retained placement, never a user grant."""
    require_device_association(context, draft)

    def provider(method, url, body):
        from .proofs import sign_device_request
        require_device_association(context, draft)
        device = context.client.device
        return sign_device_request(private_key=device.proof_key, device_id=device.device_id,
                                   method=method, url=url, body=body)

    return provider
