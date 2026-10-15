"""Require the exact activated identity before opening any native boundary."""

from atellagent_client.protocol.api import strip_api_suffix
from .native_runtime_binding import NativeRuntimeBinding


def require_native_runtime_identity(config, binding):
    if (not isinstance(binding, NativeRuntimeBinding)
            or (str(config.tenant_id), str(config.service_account_id), str(config.integration_id)) != (
                str(binding.device.tenant_id), str(binding.service_account_id), str(binding.integration_id))
            or strip_api_suffix(config.gateway_url) != binding.gateway_origin
            or (config.cert_path, config.key_path) != (str(binding.stored.certificate_path),
                                                      str(binding.stored.private_key_path))):
        raise ValueError("native boundary requires the exact activated native binding")
