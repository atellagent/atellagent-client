# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Recover a lost submission response for the same persisted proposal/key."""

import httpx

from .http_response import request_json
from .setup_proofs import sign_setup_request
from .workload_request_receipt import WorkloadRequestReceipt


async def recover_workload_receipt(*, draft, draft_store, receipt_store):
    saved = draft_store.require_saved(draft)
    path = f"/api/client-onboarding/requests/{saved.proposal.id}/status"
    headers = sign_setup_request(private_key=saved.setup.keys.proof_key,
        operation_id=saved.setup.operation_id, method="GET", path=path)
    async with httpx.AsyncClient(http2=True, trust_env=False, follow_redirects=False, timeout=15) as client:
        result = await request_json(client, method="GET", url=saved.setup.platform_origin + path,
                                    headers=headers, error_prefix="onboarding")
    receipt = WorkloadRequestReceipt.from_payload(result, draft=saved)
    return receipt_store.bind(draft=saved, receipt=receipt)
