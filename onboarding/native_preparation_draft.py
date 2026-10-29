"""Retain the exact prepared integration key before any pickup submission."""

from datetime import datetime, timezone

from .device_preparations import DevicePreparation
from .errors import OnboardingClientError
from .native_settings import NativeSettings
from .native_setup_draft import NativeSetupDraft


def preparation_draft(context, preparation):
    """A server-proposed assignment with missing local key must not generate one."""
    try:
        preparation = DevicePreparation.from_payload(preparation.payload())
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        raise OnboardingClientError("device_preparation_response_invalid") from exc
    if preparation.status == "awaiting_proposal" and preparation.expires_at <= datetime.now(timezone.utc):
        raise OnboardingClientError("onboarding_request_expired")
    arguments = dict(device=context.client.device, setup_id=context.setup.operation_id,
        expected_origin=context.setup.platform_origin)
    draft = context.drafts.load_if_present(preparation.assignment_id, **arguments)
    if draft is None:
        if preparation.status == "proposed":
            raise OnboardingClientError("onboarding_native_preparation_key_unavailable")
        settings = preparation.settings
        draft = NativeSetupDraft.create(device=context.client.device, setup_id=context.setup.operation_id,
            request_id=preparation.assignment_id, platform_origin=context.setup.platform_origin,
            display_name=settings.display_name, packaging=settings.packaging, native_host=settings.native_host,
            enforcement_mode=settings.enforcement_mode, coverage_posture=settings.coverage_posture)
        try:
            context.drafts.save_initial(draft)
        except OnboardingClientError as exc:
            if exc.code != "onboarding_native_setup_already_saved":
                raise
            draft = context.drafts.load(preparation.assignment_id, **arguments)
    saved = context.drafts.require_saved(draft)
    settings = NativeSettings.from_payload({key: saved.proposal.payload()[key] for key in (
        "display_name", "profile", "enforcement_mode", "coverage_posture")})
    if settings != preparation.settings:
        raise OnboardingClientError("onboarding_native_setup_intent_changed")
    return saved
