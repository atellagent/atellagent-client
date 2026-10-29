"""Private stored certificate/delegation fixtures for route-facade contracts."""

from hook_control_support import _config
from native_runtime_binding_support import runtime_case


def identity(tmp_path):
    config = _config()
    binding = runtime_case(tmp_path).binding
    config.tenant_id = str(binding.device.tenant_id)
    config.service_account_id = str(binding.service_account_id)
    config.integration_id = str(binding.integration_id)
    config.gateway_url = binding.gateway_origin
    config.cert_path = str(binding.stored.certificate_path)
    config.key_path = str(binding.stored.private_key_path)
    return config, binding
