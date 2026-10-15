"""Load an exact retained native proposal from the current owned installation."""

from types import SimpleNamespace

from .device_installation import DeviceInstallation
from .native_setup_store import NativeSetupStore


def load_native_local_draft(directory, request_id):
    setup, receipt, _ = DeviceInstallation(directory).load()
    device = SimpleNamespace(tenant_id=setup.tenant_id, owner_user_id=setup.owner_user_id,
                             device_id=receipt.device_id)
    return NativeSetupStore(directory / "native-drafts").load(request_id,
        device=device, setup_id=setup.operation_id, expected_origin=setup.platform_origin)
