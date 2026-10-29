"""Retain independent request IDs and local selections before any child submission."""

import json
from hashlib import sha256
from uuid import uuid4

from .atomic_private_file import create_private_file, read_private_file
from .contract_values import canonical_id
from .errors import OnboardingClientError
from .setup_request_choices import SetupRequestChoices


class SetupRequestBatchStore:
    def __init__(self, directory):
        self.path = directory / "setup-request-batch.json"
        self.complete_path = directory / "setup-request-batch-submitted.sha256"

    def needs_submission(self):
        try:
            self.path.lstat()
        except FileNotFoundError:
            return False
        digest = sha256(read_private_file(self.path, limit=32768)).hexdigest().encode()
        try:
            saved = read_private_file(self.complete_path, limit=64)
        except FileNotFoundError:
            return True
        if saved != digest:
            raise OnboardingClientError("onboarding_request_batch_changed")
        return False

    def mark_submitted(self):
        digest = sha256(read_private_file(self.path, limit=32768)).hexdigest().encode()
        try:
            create_private_file(self.complete_path, digest)
        except FileExistsError:
            if read_private_file(self.complete_path, limit=64) != digest:
                raise OnboardingClientError("onboarding_request_batch_changed")

    def create(self, setup, receipt, choices):
        if not isinstance(choices, SetupRequestChoices):
            raise TypeError("Validated setup choices required")
        payload = {"setup_id": str(setup.operation_id), "device_id": str(receipt.device_id),
            "choices": {"native": list(choices.native), "mcp": list(choices.mcp)},
            "native_ids": [str(uuid4()) for _ in choices.native],
            "mcp_ids": [str(uuid4()) for _ in choices.mcp],
            "workload_setup_ids": [str(uuid4()) for _ in choices.mcp]}
        try:
            create_private_file(self.path, json.dumps(payload, sort_keys=True).encode())
        except FileExistsError:
            existing, _ = self.load(setup, receipt)
            if existing["choices"] != payload["choices"]:
                raise OnboardingClientError("onboarding_request_batch_changed")
        return self.load(setup, receipt)

    def load(self, setup, receipt):
        try:
            payload = json.loads(read_private_file(self.path, limit=32768))
            if (not isinstance(payload, dict) or set(payload) != {"setup_id", "device_id", "choices",
                "native_ids", "mcp_ids", "workload_setup_ids"}
                or payload["setup_id"] != str(setup.operation_id)
                or payload["device_id"] != str(receipt.device_id)):
                raise ValueError("Exact device selection required")
            choices = SetupRequestChoices.from_payload(payload["choices"])
            ids = []
            for name, count in (("native_ids", len(choices.native)), ("mcp_ids", len(choices.mcp)),
                                ("workload_setup_ids", len(choices.mcp))):
                if not isinstance(payload[name], list) or len(payload[name]) != count:
                    raise ValueError("Exact selection count required")
                ids.extend(canonical_id(value) for value in payload[name])
            if len(set(ids)) != len(ids) or setup.operation_id in ids:
                raise ValueError("Independent request IDs required")
            return payload, choices
        except (OSError, ValueError, TypeError, KeyError) as exc:
            raise OnboardingClientError("onboarding_request_batch_unavailable") from exc
