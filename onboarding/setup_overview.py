"""Read-only onboarding state for desktop controls; never advances or approves setup."""

from .device_installation import DeviceInstallation
from .device_renewal_state import DeviceRenewalState
from .errors import OnboardingClientError
from .native_setup_context import NativeSetupContext
from .pending_device_setup import PendingDeviceSetupStore, submission_is_unconfirmed
from .setup_status import SetupStatusClient
from .reporting_scope import ReportingScope


def exists(path):
    try:
        path.lstat()
        return True
    except FileNotFoundError:
        return False


async def overview(directory, *, after=None):
    installation = DeviceInstallation(directory)
    result = dict(device=None, pending=None, assignments=[], next_after=None, renewal=None)
    if exists(installation.path):
        draft, receipt, _ = installation.load()
        reason = DeviceRenewalState(directory, ReportingScope(draft.tenant_id,
            receipt.device_id, draft.owner_user_id)).read()
        if reason is not None and reason["reason_code"] == "device_renewal_recovery_required":
            result["device"] = dict(device_id=str(receipt.device_id), tenant_id=str(draft.tenant_id),
                tenant_name=None,
                owner_user_id=str(draft.owner_user_id), device_name=draft.display_name,
                platform_origin=draft.platform_origin, status="verification_required")
            result["renewal"] = dict(status="recovery_required", reason_code=reason["reason_code"])
            return result
        async with await NativeSetupContext.open(directory) as context:
            current = await context.client.status()
            result["device"] = dict(device_id=str(current.device_id), tenant_id=str(current.tenant_id),
                tenant_name=current.tenant_name,
                owner_user_id=str(current.owner_user_id), device_name=current.device_name,
                platform_origin=context.setup.platform_origin, status=current.status,
                certificate_expires_at=current.certificate_expires_at.isoformat(),
                discovery_reporting_status=current.discovery_reporting_status,
                discovery_fresh=current.discovery_fresh,
                discovery_observed_at=None if current.discovery_observed_at is None else current.discovery_observed_at.isoformat(),
                server_time=current.server_time.isoformat())
            result["device"]["certificate_renewal_warning"] = None if reason is None else reason["reason_code"]
            page = await context.client.assignments(after=after)
            result["assignments"] = [dict(request_id=str(row.request_id),
                onboarding_session_id=str(row.onboarding_session_id), status=row.status,
                display_name=row.proposal.display_name, native_host=row.proposal.native_host,
                service_account_id=str(row.service_account_id), integration_id=str(row.integration_id),
                agent_principal_id=str(row.agent_principal_id), binding_id=str(row.binding_id),
                controller_profile_id=str(row.controller_profile_id), intent_sha256=row.intent_sha256,
                owner_consented_at=None if row.owner_consented_at is None else row.owner_consented_at.isoformat())
                for row in page.assignments]
            result["next_after"] = None if page.next_after is None else str(page.next_after)
        return result
    if after is not None:
        raise OnboardingClientError("onboarding_device_required")
    pending = PendingDeviceSetupStore(installation)
    if exists(pending.path):
        draft, receipt, _ = pending.load()
        async with SetupStatusClient(origin=draft.platform_origin, operation_id=draft.operation_id,
                                     proof_key=draft.keys.proof_key) as client:
            try:
                progress = await client.status()
            except OnboardingClientError as error:
                if not submission_is_unconfirmed(receipt, error):
                    raise
                # Local retained intent only. Do not infer that the server has no request,
                # bypass approval, or delete keys after an ambiguous submission.
                result["pending"] = dict(setup_id=str(draft.operation_id), platform_origin=draft.platform_origin,
                    tenant_id=str(draft.tenant_id), owner_user_id=str(draft.owner_user_id),
                    device_name=draft.display_name, status="submission_unconfirmed", device_id=None,
                    issuance_stage=None, issuance_reason=None,
                    requested_connections=[], connection_submission_complete=False)
                return result
        if (progress.kind != "device" or (receipt is not None and progress.device_id != receipt.device_id)):
            raise OnboardingClientError("onboarding_setup_identity_changed")
        from .setup_request_summary import pending_connection_summary
        names, submitted = pending_connection_summary(directory, draft, receipt)
        result["pending"] = dict(setup_id=str(draft.operation_id), platform_origin=draft.platform_origin,
            tenant_id=str(draft.tenant_id), owner_user_id=str(draft.owner_user_id),
            device_name=draft.display_name, status=progress.status,
            device_id=None if progress.device_id is None else str(progress.device_id),
            issuance_stage=None if progress.device_issuance is None else progress.device_issuance["stage"],
            issuance_reason=None if progress.device_issuance is None else progress.device_issuance["reason_code"],
            requested_connections=names, connection_submission_complete=submitted)
    return result
