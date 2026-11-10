"""Old approved workload drafts keep their bytes and disabled direct access."""
from dataclasses import replace
from hashlib import sha256
import json
from uuid import uuid4
import pytest
from atellagent_client.onboarding.proofs import canonical_json
from atellagent_client.onboarding.setup_draft import SetupDraft
from atellagent_client.onboarding.setup_keys import SetupKeys
from atellagent_client.onboarding.workload_proposal import WorkloadProposal
from atellagent_client.onboarding.workload_setup_draft import WorkloadSetupDraft
from atellagent_client.onboarding.workload_setup_store import WorkloadSetupStore
from atellagent_client.onboarding.workload_request_receipt import WorkloadRequestReceipt
from test_workload_management import receipt

@pytest.mark.parametrize('kind,capability', [('mcp_server','mcp.tools'),('model','fixture-model')])
def test_legacy_saved_draft_receipt_and_intent_bytes_are_unchanged(tmp_path,kind,capability):
    setup=SetupDraft(uuid4(),uuid4(),uuid4(),'https://atellagent.com','workload','Owned fixture','0.9.1',None,SetupKeys.generate())
    proposal=WorkloadProposal(uuid4(),'Owned fixture',kind,'sdk',(capability,))
    wire=json.loads(WorkloadSetupDraft(setup,proposal).encode())
    del wire['proposal']['allow_direct_mcp_clients']
    original=canonical_json(wire);legacy=WorkloadSetupDraft.decode(original)
    assert legacy.proposal.allow_direct_mcp_clients is False
    assert legacy.encode()==original
    assert sha256(legacy.encode()).digest()==sha256(original).digest()
    store=WorkloadSetupStore(tmp_path/'retained');store.save_initial(legacy)
    reopened=store.load(legacy.proposal.id)
    assert reopened.encode()==original
    accepted=receipt(legacy)
    assert WorkloadRequestReceipt.from_payload(accepted,draft=reopened).intent_sha256==accepted['intent_sha256']


def test_new_proposals_remain_explicit_and_enabled_flag_cannot_come_from_legacy():
    new=WorkloadProposal(uuid4(),'Owned MCP','mcp_server','sdk',('mcp.tools',))
    assert new.payload()['allow_direct_mcp_clients'] is False
    enabled=replace(new,allow_direct_mcp_clients=True)
    assert WorkloadProposal.from_payload(enabled.payload()).allow_direct_mcp_clients is True
    old=new.payload();del old['allow_direct_mcp_clients']
    decoded=WorkloadProposal.from_payload(old)
    assert decoded.allow_direct_mcp_clients is False and decoded.payload()==old
    for changed in [old|{'_legacy_direct_mcp_flag_omitted':True},old|{'unreviewed_authority':True},old|{'allow_direct_mcp_clients':None},old|{'profile':old['profile']|{'native_host':'codex'}}]:
        with pytest.raises(ValueError):WorkloadProposal.from_payload(changed)
    for name in set(old):
        bad=dict(old);bad.pop(name)
        with pytest.raises(ValueError):WorkloadProposal.from_payload(bad)
