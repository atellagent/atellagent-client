# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Own-device control independent of agent controllers and browser sessions."""

import asyncio
from time import monotonic
from pathlib import Path

from .device_http import create_device_http_client
from .device_identity import AdmittedDevice
from .device_requests import device_request
from .device_status import DeviceStatus
from .device_assignments import DeviceAssignmentPage
from uuid import UUID
from .discovery_snapshot import discovery_snapshot
from .errors import OnboardingClientError
from .origins import https_origin


class DeviceManagementClient:
    def __init__(self, *, origin: str, device: AdmittedDevice, server_ca_path: Path | None = None):
        if not isinstance(device, AdmittedDevice):
            raise ValueError("exact admitted device material required")
        self.origin, self.device = https_origin(origin), device
        self._server_ca_path = server_ca_path
        self._transport_lock = asyncio.Lock()
        self._closed = False
        self._next_request_at = 0.0
        self._http = create_device_http_client(tenant_id=device.tenant_id, device_id=device.device_id,
            certificate_path=device.certificate_path, private_key_path=device.private_key_path,
            server_ca_path=server_ca_path)

    async def _request(self, method: str, path: str, body=None):
        async with self._transport_lock:
            if self._closed:
                raise OnboardingClientError("device_runtime_stopped")
            # Match the own-device sustained budget (one operation/second).
            # Inventory reads share this transport with setup operations.
            delay = self._next_request_at - monotonic()
            if delay > 0:
                await asyncio.sleep(delay)
            self._next_request_at = monotonic() + 1.0
            return await device_request(self._http, origin=self.origin, device_id=self.device.device_id,
                proof_key=self.device.proof_key, method=method, path=path, body=body)

    async def replace_credentials(self, device: AdmittedDevice) -> None:
        """Replace only TLS material after confirmed activation; keep exact owner/proof."""
        from cryptography.hazmat.primitives import serialization
        if (not isinstance(device, AdmittedDevice) or (device.tenant_id, device.device_id, device.owner_user_id) != (
                self.device.tenant_id, self.device.device_id, self.device.owner_user_id)
                or device.proof_key.public_key().public_bytes(serialization.Encoding.Raw,
                    serialization.PublicFormat.Raw) != self.device.proof_key.public_key().public_bytes(
                        serialization.Encoding.Raw, serialization.PublicFormat.Raw)):
            raise OnboardingClientError("device_identity_mismatch")
        replacement = create_device_http_client(tenant_id=device.tenant_id, device_id=device.device_id,
            certificate_path=device.certificate_path, private_key_path=device.private_key_path,
            server_ca_path=self._server_ca_path)
        try:
            async with self._transport_lock:
                if self._closed:
                    raise OnboardingClientError("device_runtime_stopped")
                previous = self._http
                self._http, self.device = replacement, device
                replacement = None
                await previous.aclose()
        finally:
            if replacement is not None:
                await replacement.aclose()

    async def status(self) -> DeviceStatus:
        current = DeviceStatus.from_payload(await self._request("GET", "/devices/self/status"))
        if (current.tenant_id, current.device_id, current.owner_user_id) != (
            self.device.tenant_id, self.device.device_id, self.device.owner_user_id):
            raise OnboardingClientError("device_identity_mismatch")
        return current

    async def renew_certificate(self, *, trust):
        from .device_renewal_protocol import renew_device_certificate
        return await renew_device_certificate(self, trust=trust)

    async def rename(self, name: str) -> None:
        if (not isinstance(name, str) or not 1 <= len(name) <= 128 or name != name.strip()
            or any(ord(item) < 32 or ord(item) == 127 for item in name)):
            raise OnboardingClientError("device_name_invalid")
        result = await self._request("PUT", "/devices/self/name", {"device_name": name})
        if set(result) != {"success", "device_name"} or result["success"] is not True or result["device_name"] != name:
            raise OnboardingClientError("device_response_invalid")

    async def assignments(self, *, after: UUID | None = None) -> DeviceAssignmentPage:
        if after is not None and not isinstance(after, UUID):
            raise ValueError("typed assignment cursor required")
        path = "/devices/self/assignments" + (f"?after={after}" if after is not None else "")
        result = await self._request("GET", path)
        return DeviceAssignmentPage.from_payload(result, device=self.device, after=after)

    async def computer_use_permissions(self, *, after: UUID | None = None):
        """Current display status only; never an execution grant or enrollment intent."""
        from .contract_values import canonical_id
        if after is not None and not isinstance(after, UUID):
            raise ValueError("typed permission cursor required")
        payload = await self._request("GET", "/devices/self/computer-use" + (f"?after={after}" if after else ""))
        try:
            if (not isinstance(payload, dict) or set(payload) != {"permissions", "next_after"}
                    or not isinstance(payload["permissions"], list) or len(payload["permissions"]) > 8):
                raise ValueError("bounded permission status required")
            result, ids = {}, []
            for row in payload["permissions"]:
                if not isinstance(row, dict) or set(row) != {"request_id", "service_account_id", "computer_use"}:
                    raise ValueError("exact permission binding required")
                request = canonical_id(row["request_id"])
                account = canonical_id(row["service_account_id"])
                status = row["computer_use"]
                if status is not None:
                    if (not isinstance(status, dict) or set(status) != {"status", "effective_mode"}
                            or status["status"] not in {"permitted", "would_block", "blocked_by_organization", "blocked_by_connection"}
                            or status["effective_mode"] not in {"observe", "enforce"}
                            or status["status"] == "would_block" and status["effective_mode"] != "observe"
                            or status["status"].startswith("blocked_") and status["effective_mode"] != "enforce"):
                        raise ValueError("consistent effective permission required")
                ids.append(request)
                result[str(request)] = {"service_account_id": str(account), "computer_use": status}
            if ids != sorted(set(ids)) or after is not None and any(item <= after for item in ids):
                raise ValueError("advancing permission page required")
            cursor = None if payload["next_after"] is None else canonical_id(payload["next_after"])
            if cursor is not None and (len(ids) != 8 or cursor != ids[-1]):
                raise ValueError("exact permission cursor required")
            return result
        except (ValueError, TypeError, KeyError) as exc:
            raise OnboardingClientError("device_computer_use_response_invalid") from exc

    async def assignment(self, request_id: UUID):
        if not isinstance(request_id, UUID):
            raise ValueError("typed exact assignment identity required")
        result = await self._request("GET", f"/devices/self/assignments?request_id={request_id}")
        page = DeviceAssignmentPage.from_payload(result, device=self.device)
        if (len(page.assignments) > 1 or page.next_after is not None
                or any(item.request_id != request_id for item in page.assignments)):
            raise OnboardingClientError("device_assignment_response_invalid")
        return page.assignments[0] if page.assignments else None

    async def runtime_metadata(self, *, platform_origin, gateway_origin):
        from .runtime_metadata import fetch_runtime_metadata
        return await fetch_runtime_metadata(self, platform_origin=platform_origin, gateway_origin=gateway_origin)

    async def preparations(self, *, after: UUID | None = None, assignment_id: UUID | None = None):
        from .device_preparations import DevicePreparationPage
        if (after is not None and not isinstance(after, UUID)
                or assignment_id is not None and not isinstance(assignment_id, UUID)
                or after is not None and assignment_id is not None):
            raise ValueError("One typed preparation selector required")
        path = "/devices/self/preparations"
        if after is not None: path += f"?after={after}"
        if assignment_id is not None: path += f"?assignment_id={assignment_id}"
        result = await self._request("GET", path)
        return DevicePreparationPage.from_payload(result, after=after, assignment_id=assignment_id)

    async def set_reporting(self, enabled: bool) -> None:
        if type(enabled) is not bool:
            raise OnboardingClientError("device_reporting_preference_invalid")
        result = await self._request("PUT", "/devices/self/reporting", {"reporting_enabled": enabled})
        if (set(result) != {"success", "reporting_enabled"} or result["success"] is not True
            or result["reporting_enabled"] is not enabled):
            raise OnboardingClientError("device_response_invalid")

    async def aclose(self):
        async with self._transport_lock:
            self._closed = True
            await self._http.aclose()

    async def report_discovery(self) -> None:
        payload = await asyncio.to_thread(discovery_snapshot)
        result = await self._request("POST", "/devices/self/discovery", payload)
        self._require_discovery_ack(result)

    async def remove_discovery(self) -> None:
        result = await self._request("DELETE", "/devices/self/discovery")
        self._require_discovery_ack(result)

    @staticmethod
    def _require_discovery_ack(result):
        if set(result) != {"success"} or result["success"] is not True:
            raise OnboardingClientError("device_response_invalid")

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        await self.aclose()
