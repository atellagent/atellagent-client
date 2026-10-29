"""Supported customer setup APIs; implementation stays shared and UI-independent."""

from atellagent_client.onboarding.attended_device import DeviceSetup
from atellagent_client.onboarding.connection_lifecycle import reconcile_connections
from atellagent_client.onboarding.device_login import DeviceLoginClient
from atellagent_client.onboarding.device_context import DeviceContext
from atellagent_client.onboarding.device_mcp_context import DeviceMCPContext
from atellagent_client.onboarding.errors import OnboardingClientError, MCPExistingRequestError
from atellagent_client.onboarding.native_setup_context import NativeSetupContext
from atellagent_client.onboarding.progress import OnboardingProgress, setup_progress
from atellagent_client.onboarding.setup_identity import SetupIdentity, SetupMembership
from atellagent_client.onboarding.setup_management import SetupManagementClient
from atellagent_client.onboarding.workload_context import WorkloadContext
from atellagent_client.onboarding.workload_local_inputs import WorkloadLocalInputs
from atellagent_client.onboarding.workload_proposal import WorkloadProposal

__all__ = ["DeviceSetup", "DeviceContext", "DeviceMCPContext", "DeviceLoginClient", "OnboardingClientError", "MCPExistingRequestError", "NativeSetupContext",
    "SetupIdentity", "SetupMembership", "SetupManagementClient", "WorkloadContext",
    "WorkloadLocalInputs", "WorkloadProposal", "OnboardingProgress", "setup_progress", "reconcile_connections"]
