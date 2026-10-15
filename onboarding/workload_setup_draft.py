# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""One retained workload bootstrap and proposal, with no desktop dependency."""

from dataclasses import dataclass, field
import json

from .proofs import canonical_json
from .setup_draft import SetupDraft
from .workload_proposal import WorkloadProposal


@dataclass(frozen=True)
class WorkloadSetupDraft:
    setup: SetupDraft = field(repr=False)
    proposal: WorkloadProposal

    def __post_init__(self):
        if (not isinstance(self.setup, SetupDraft) or self.setup.kind != "workload"
                or self.setup.platform is not None or not isinstance(self.proposal, WorkloadProposal)):
            raise ValueError("workload-owned bootstrap and exact proposal required")
        self.setup.proposal()
        if WorkloadProposal.from_payload(self.proposal.payload()) != self.proposal:
            raise ValueError("canonical workload proposal required")

    def encode(self):
        return canonical_json({"revision": 1, "setup": json.loads(self.setup.encode()),
                               "proposal": self.proposal.payload()})

    @classmethod
    def decode(cls, raw):
        if not isinstance(raw, bytes) or not 1 <= len(raw) <= 65536:
            raise ValueError("bounded workload draft required")
        payload = json.loads(raw)
        if (not isinstance(payload, dict) or set(payload) != {"revision", "setup", "proposal"}
                or type(payload["revision"]) is not int or payload["revision"] != 1):
            raise ValueError("closed workload draft required")
        return cls(SetupDraft.decode(canonical_json(payload["setup"])),
                   WorkloadProposal.from_payload(payload["proposal"]))
