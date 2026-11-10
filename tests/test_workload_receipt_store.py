"""Initial workload identity is immutable while server progress can advance."""

from dataclasses import replace
import json

import pytest
from atellagent_client.onboarding.errors import OnboardingClientError
from atellagent_client.onboarding.workload_receipt_store import WorkloadReceiptStore
from atellagent_client.onboarding.workload_request_receipt import WorkloadRequestReceipt
from atellagent_client.onboarding.workload_setup_store import WorkloadSetupStore
from test_workload_management import draft, receipt


def test_receipt_is_bound_once_and_progress_does_not_overwrite_it(tmp_path, draft):
    drafts = WorkloadSetupStore(tmp_path / "setup")
    drafts.save_initial(draft)
    receipts = WorkloadReceiptStore(drafts)
    first = WorkloadRequestReceipt.from_payload(receipt(draft), draft=draft)
    with pytest.raises(OnboardingClientError, match="receipt_recovery_required"):
        receipts.load(draft)
    receipts.bind(draft=draft, receipt=first)
    assert receipts.bind(draft=draft, receipt=replace(first, status="approved")) == first
    assert receipts.load(draft) == first
    other = WorkloadRequestReceipt.from_payload(receipt(draft), draft=draft)
    with pytest.raises(OnboardingClientError):
        receipts.bind(draft=draft, receipt=other)
    assert receipts.load(draft) == first


@pytest.mark.parametrize("change", ["hash", "revision", "extra", "intent"])
def test_changed_receipt_record_is_not_a_missing_record(tmp_path, draft, change):
    drafts = WorkloadSetupStore(tmp_path / "setup")
    drafts.save_initial(draft)
    receipts = WorkloadReceiptStore(drafts)
    receipts.bind(draft=draft, receipt=WorkloadRequestReceipt.from_payload(receipt(draft), draft=draft))
    path = receipts._path(draft)
    record = json.loads(path.read_bytes())
    if change == "hash": record["draft_sha256"] = "f" * 64
    elif change == "revision": record["revision"] = True
    elif change == "extra": record["unexpected"] = True
    else: record["receipt"]["intent_sha256"] = "f" * 64
    path.write_text(json.dumps(record))
    with pytest.raises(OnboardingClientError) as failure:
        receipts.load(draft)
    assert failure.value.code != "onboarding_workload_receipt_recovery_required"
