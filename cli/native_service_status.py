"""Local service evidence, distinct from current server admission or discovery freshness."""

from types import SimpleNamespace
import asyncio

from . import service_platform as service
from atellagent_client.onboarding.installation_paths import installation_directory
from atellagent_client.onboarding.device_installation import DeviceInstallation
from atellagent_client.onboarding.native_runtime_selection import NativeRuntimeSelections, HOSTS
from atellagent_client.onboarding.native_setup_store import NativeSetupStore
from atellagent_client.onboarding.contract_values import canonical_id
from atellagent_client.onboarding.errors import OnboardingClientError
from .native_hook_setup_status import status as hook_setup_status


async def status():
    service._require_supported_platform()
    installed = service._supervisor_installed()
    if installed:
        service._validate_supervisor()
    result = dict(installed=installed, running=installed and service._supervisor_running(), controllers=[])
    installation = DeviceInstallation(installation_directory())
    # A fresh install has no device selector or controllers. Do not turn that
    # normal empty state into an enrollment error. Existing unsafe/corrupt
    # selectors and registered supervisors still require full validation.
    if not installed and not installation.path.exists() and not installation.path.is_symlink():
        return result
    setup, receipt, _ = installation.load()
    device = SimpleNamespace(tenant_id=setup.tenant_id, owner_user_id=setup.owner_user_id,
                             device_id=receipt.device_id)
    selections = NativeRuntimeSelections(installation.directory / "native-runtime-selections")
    drafts = NativeSetupStore(installation.directory / "native-drafts")
    async def observe(host):
        selection = selections.load(host)
        if selection is None:
            return None
        draft = drafts.load(selection["request_id"], device=device, setup_id=setup.operation_id,
                            expected_origin=setup.platform_origin)
        selections.require_draft(selection, draft)
        endpoint = service._endpoint(host)
        entry = dict(host=host, request_id=str(selection["request_id"]), endpoint=endpoint,
                     local_controller_ready=False, error=None, native_delegation=None,
                     hook_setup="unavailable")
        if result["running"]:
            try:
                health = await service.HookControlClient(endpoint, timeout_seconds=2).call("health", {})
                delegation = health.get("native_delegation")
                if (health.get("protocol_version") != "atellagent.hook-control.v1"
                        or health.get("native_binding") != dict(request_id=str(draft.proposal.id),
                            device_id=str(draft.device_id), tenant_id=str(draft.tenant_id),
                            owner_user_id=str(draft.owner_user_id), native_host=host)
                        or not isinstance(delegation, dict)
                        or set(delegation) != {"authorization_id", "authorization_epoch", "authority_source"}
                        or delegation["authority_source"] != "gateway_admission"
                        or type(delegation["authorization_epoch"]) is not int
                        or not 1 <= delegation["authorization_epoch"] <= 2**31 - 1):
                    raise ValueError("exact selected controller health required")
                canonical_id(delegation["authorization_id"])
                entry["native_delegation"] = delegation
                entry["local_controller_ready"] = True
            except service.HookControlError as exc:
                entry["error"] = exc.code
            except (ValueError, TypeError, AttributeError, KeyError):
                entry["error"] = "controller_health_invalid"
        try:
            entry["hook_setup"] = hook_setup_status(installation.directory, draft, endpoint, entry["native_delegation"])
        except (OSError, ValueError, TypeError, KeyError, OnboardingClientError):
            pass
        return entry
    result["controllers"] = [entry for entry in await asyncio.gather(*(observe(host) for host in HOSTS))
                             if entry is not None]
    return result
