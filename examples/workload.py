# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Shared saved-workload selection for runnable customer examples."""

import argparse
from pathlib import Path
from uuid import UUID
from atellagent_client.sdk import WorkloadContext


def add_workload_options(parser):
    parser.add_argument("--state-directory", required=True, type=Path, help="Absolute private workload state directory")
    parser.add_argument("--request-id", required=True, type=UUID, help="Saved approved workload request ID")


def options():
    parser = argparse.ArgumentParser(description="Run this example using an approved saved workload.")
    add_workload_options(parser)
    return parser.parse_args()


async def load_workload_config(directory, request_id, *, integration_type, packaging):
    context = WorkloadContext(directory)
    proposal = context.proposal(request_id)
    kind = "mcp" if proposal.account_type == "mcp_server" else proposal.account_type
    if kind != integration_type or proposal.packaging != packaging:
        raise ValueError("Saved workload profile does not match this example")
    config, _ = await context.runtime(request_id)
    return config
