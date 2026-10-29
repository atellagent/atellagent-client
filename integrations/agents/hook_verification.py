"""Private setup evidence from accepted post-tool delivery, never authorization."""

import json
import math
import os
import sys
from pathlib import Path
import time

from atellagent_client.onboarding.atomic_private_file import read_private_file
from atellagent_client.onboarding.contract_values import canonical_id
from atellagent_client.onboarding.private_record_update import replace_private_record


def receipt_path(endpoint):
    if os.name == "nt":
        from .windows_named_pipe import windows_local_state_path
        outbox = windows_local_state_path(str(endpoint))
    else:
        outbox = Path(endpoint).with_name(f"{Path(endpoint).name}.outcomes.json")
        # macOS exposes these OS-owned aliases to temporary-file callers.
        # Normalize only their root; private-file checks still reject any
        # symlink within the actual user-owned state directory.
        if sys.platform == "darwin" and outbox.is_absolute() and outbox.parts[1] in {"tmp", "var"}:
            outbox = Path("/private").joinpath(*outbox.parts[1:])
    return outbox.with_name(f"{outbox.name}.verified.json")


def load(endpoint):
    try:
        value = json.loads(read_private_file(receipt_path(endpoint), limit=2048))
    except FileNotFoundError:
        return None
    if not isinstance(value, dict) or set(value) != {
        "request_id", "authorization_id", "authorization_epoch", "observed_at", "accepted_at"
    }:
        raise ValueError("bounded hook verification receipt required")
    for field in ("request_id", "authorization_id"):
        canonical_id(value[field])
    if type(value["authorization_epoch"]) is not int or not 1 <= value["authorization_epoch"] <= 2**31 - 1:
        raise ValueError("exact delegation epoch required")
    if any(type(value[field]) not in (int, float) or not math.isfinite(value[field])
           or value[field] <= 0 for field in ("observed_at", "accepted_at")):
        raise ValueError("finite hook receipt times required")
    if not value["observed_at"] <= value["accepted_at"] <= time.time() + 1:
        raise ValueError("ordered hook receipt times required")
    return value


def record_accepted(endpoint, binding, action_key, entry):
    """Call only after Gateway acknowledged this exact postflight successfully."""
    outcome = entry.get("outcome", {})
    if (outcome.get("outcome_observation") != "result_observed" or outcome.get("success") is False
            or not action_key.startswith(f"local:{binding.authorization_id}:{binding.authorization_epoch}:")):
        return
    value = dict(request_id=str(binding.request_id), authorization_id=str(binding.authorization_id),
        authorization_epoch=binding.authorization_epoch, observed_at=entry["received_at"], accepted_at=time.time())
    replace_private_record(receipt_path(endpoint), json.dumps(value, sort_keys=True).encode(), limit=2048)
