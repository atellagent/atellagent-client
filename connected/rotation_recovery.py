"""Read a committed receipt with the retained replacement key; never reactivate."""

from dataclasses import replace
from datetime import datetime
import json

from atellagent_client.onboarding.atomic_private_file import read_private_file
from atellagent_client.onboarding.contract_values import canonical_id
from atellagent_client.onboarding.service_account_csr import service_account_csr_facts
from .capability import certificate_public_key_sha256
from atellagent_client.sdk.gateway.session import GatewaySession
from .participant_protocol import ConnectedHTTPError, strict_object
from .contracts import ConnectedProtocolError


def validate_rotation_activation(payload, *, staged, operation_id):
    fields = {"operation_id", "status", "certificate_public_key_sha256", "certificate_expires_at",
              "certificate_rotation_due_at", "certificate_rotation_deadline_at"}
    activation = strict_object(payload, fields, "rotation activation receipt")
    try:
        expires = datetime.fromisoformat(str(activation["certificate_expires_at"]).replace("Z", "+00:00"))
        if (set(activation) != fields or activation["operation_id"] != str(operation_id)
                or activation["status"] != "activated"
                or activation["certificate_public_key_sha256"] != staged.public_key_sha256
                or expires != staged.certificate_expires_at):
            raise ValueError("exact retained operation/key/expiry required")
    except (ValueError, TypeError, KeyError) as exc:
        raise ConnectedProtocolError("certificate rotation activation binding mismatch") from exc
    return activation


async def recover_activated_rotation(*, config, staged, device_proof_provider=None):
    record = json.loads(read_private_file(staged.candidate_pointer, limit=4096))
    operation_id = canonical_id(record["operation_id"])
    candidate_config = replace(config, cert_path=str(staged.staged_certificate_path),
                               key_path=str(staged.staged_private_key_path))
    url = candidate_config.gateway_url.rstrip("/") + f"/v1/connected-runtimes/certificate-rotations/{operation_id}/activation"
    async with GatewaySession.from_service_account_config(candidate_config,
        device_proof_provider=device_proof_provider) as session:
        response = await session.request_authenticated("GET", url)
        if response.status_code != 200:
            raise ConnectedHTTPError(response.status_code, "retained rotation has no current activated receipt")
        return validate_rotation_activation(response.json(), staged=staged, operation_id=operation_id)


async def verify_pending_rotation(*, config, staged, device_proof_provider=None):
    record = json.loads(read_private_file(staged.candidate_pointer, limit=4096))
    operation = canonical_id(record["operation_id"])
    url = config.gateway_url.rstrip("/") + f"/v1/connected-runtimes/certificate-rotations/{operation}/pending"
    async with GatewaySession.from_service_account_config(config,
        device_proof_provider=device_proof_provider) as session:
        response = await session.request_authenticated("GET", url)
        if response.status_code != 200:
            raise ConnectedHTTPError(response.status_code, "pending rotation lacks current parent authority")
        fields = {"operation_id", "status", "csr_sha256", "public_key_sha256", "authorization_certificate_public_key_sha256"}
        payload = strict_object(response.json(), fields, "pending rotation receipt")
        if (set(payload) != fields or payload["operation_id"] != str(operation)
                or payload["status"] not in {"pending", "in_progress", "issued"}
                or payload["csr_sha256"] != service_account_csr_facts(staged.prepared.csr_pem).csr_sha256
                or payload["public_key_sha256"] != staged.public_key_sha256
                or payload["authorization_certificate_public_key_sha256"] != certificate_public_key_sha256(config.cert_path)):
            raise ConnectedProtocolError("pending rotation operation/key binding mismatch")
        return payload
