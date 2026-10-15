"""Device setup available without a desktop, CLI or installed background service."""

from pathlib import Path
from uuid import uuid4

from atellagent_client._version import CLIENT_LIBRARY_VERSION
from .device_installation import DeviceInstallation
from .errors import OnboardingClientError
from .pending_device_resume import resume_pending_device_setup, submit_pending_device_setup
from .pending_device_setup import PendingDeviceSetupStore
from .process_lease import process_lease
from .setup_draft import SetupDraft
from .setup_keys import SetupKeys


class DeviceSetup:
    """Retained device setup; callers explicitly authorize submission and present UI."""

    def __init__(self, directory: Path):
        self.installation = DeviceInstallation(Path(directory))
        self.pending = PendingDeviceSetupStore(self.installation)
        self.lease = None

    def __enter__(self):
        if self.lease is not None:
            raise OnboardingClientError("onboarding_setup_busy", retryable=True)
        lease = process_lease(self.installation.directory.parent / ".device-setup.lock")
        if not lease.__enter__():
            lease.__exit__(None, None, None)
            raise OnboardingClientError("onboarding_setup_busy", retryable=True)
        self.lease = lease
        return self

    def __exit__(self, *exc):
        if self.lease is not None:
            lease, self.lease = self.lease, None
            lease.__exit__(*exc)

    def require_new(self):
        if self.installation.path.exists() or self.installation.path.is_symlink():
            raise OnboardingClientError("device_installation_already_selected")
        if self.pending.path.exists() or self.pending.path.is_symlink():
            raise OnboardingClientError("onboarding_pending_setup_exists")

    async def prepare(self, management, *, tenant_id, owner_user_id, name, platform,
                      reporting_enabled=True):
        if self.lease is None:
            raise OnboardingClientError("onboarding_setup_lease_required")
        self.require_new()
        identity = await management.identity()
        identity.require_selection(expected_user_id=owner_user_id, tenant_id=tenant_id)
        draft = SetupDraft(uuid4(), tenant_id, owner_user_id, management.origin, "device",
            name, CLIENT_LIBRARY_VERSION, platform, SetupKeys.generate())
        self.installation.setups.save_initial(draft)
        self.pending.begin(draft, reporting_enabled=reporting_enabled)
        return draft

    async def submit(self, management):
        if self.lease is None:
            raise OnboardingClientError("onboarding_setup_lease_required")
        return await submit_pending_device_setup(management, self.pending)

    async def resume(self):
        if self.lease is None:
            raise OnboardingClientError("onboarding_setup_lease_required")
        return await resume_pending_device_setup(self.pending)

    async def open_connections(self):
        """Open verified device authority; local metadata alone is not authentication."""
        from .native_setup_context import NativeSetupContext
        return await NativeSetupContext.open(self.installation.directory)

    def request_batch(self):
        """Prepare/submit chosen connections before admission without runtime authority."""
        from .setup_request_batch import SetupRequestBatch
        return SetupRequestBatch(self)

    async def open_context(self):
        """Open shared device admission without selecting any integration."""
        from .device_context import DeviceContext
        return await DeviceContext.open(self.installation.directory)
