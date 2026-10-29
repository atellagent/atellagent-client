"""Thin shell for selected connection submission/retry, not activation."""

import argparse
import json
from pathlib import Path

from atellagent_client.sdk import DeviceSetup, OnboardingClientError, SetupManagementClient
from atellagent_client.onboarding.installation_paths import installation_directory
from atellagent_client.onboarding.setup_request_choices import SetupRequestChoices
from .setup_login import login


async def choices_for_setup(path, interaction):
    if interaction is not None:
        value = await interaction.ask("choose_connections")
    elif path is not None:
        with Path(path).open(encoding="utf-8") as source:
            raw = source.read(8193)
        if len(raw.encode()) > 8192:
            raise ValueError("Setup selections are too large")
        value = json.loads(raw)
    else:
        value = {"native": [], "mcp": []}
    selected = SetupRequestChoices.from_payload(value)
    return {"native": list(selected.native), "mcp": list(selected.mcp)}


async def submit_batch(setup, management, *, selections=None, interaction=None):
    batch = setup.request_batch()
    if selections is not None:
        batch.prepare(selections)
    results = await batch.submit(management)
    if interaction is None:
        print(json.dumps({"requests": results}), flush=True)
    else:
        interaction.emit("connection_requests_submitted", requests=results)
    return results


async def run(arguments):
    parser = argparse.ArgumentParser(prog="atellagent-cli setup requests",
        description="Resubmit the retained device connection requests without replacing IDs or keys.")
    parser.add_argument("action", choices=("submit",))
    parser.parse_args(arguments)
    try:
        with DeviceSetup(installation_directory()) as setup:
            draft, _, _ = setup.pending.load()
            tokens = await login(draft.platform_origin)
            async with SetupManagementClient(origin=draft.platform_origin, tokens=tokens) as management:
                await setup.submit(management)
                results = await submit_batch(setup, management)
                if any(item["status"] != "submitted" for item in results):
                    parser.exit(1, "Some requests are unconfirmed. Review their status, then retry the same retained requests.\n")
    except (OnboardingClientError, OSError, ValueError, EOFError) as exc:
        code = exc.code if isinstance(exc, OnboardingClientError) else "onboarding_request_batch_unavailable"
        parser.exit(1, f"[ERROR] {code}. Existing setup requests were retained.\n")
