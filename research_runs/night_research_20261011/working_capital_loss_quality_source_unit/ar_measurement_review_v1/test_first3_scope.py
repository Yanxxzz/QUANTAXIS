"""Synthetic selection metadata only; no real stock amount/body consumption."""
from copy import deepcopy
from pathlib import Path
import sys
import pytest

sys.path.insert(0,str(Path(__file__).resolve().parent))
from review_first3_events import AMOUNT_STATUS,select_first3_whole_AR


def fixtures():
    keys=[{'code':code,'announcement_id':ann,'report_date':'2025-12-31','source_record_sha256':code+'sha'}
        for code,ann in [('000157','1225055609'),('000403','1225245931'),('000409','1225084580')]]
    records=[];cases=[]
    for key in keys:
        tables=[{'asset':'AR','kind':'stock','period_role':period,'population_kind':'whole_AR_classification_total',
            'status':'EXACT_SOURCE_FACTS_ONLY_NOT_ELIGIBILITY','cells':{'gross':object(),'allowance_disclosed':object(),'net':object()},
            'table_id':key['code']+period,'printed_period_label':period,'literal_date_printed':None,
            'period_context':'derived_annual_role_not_literal_calendar_date','unit_status':'pending_in_synthetic'}
            for period in ['closing','opening']]
        records.append({**key,'retained_text_sha256':key['code']+'text','tables':tables})
        cases.append({**key,'retained_text_sha256':key['code']+'text'})
    return keys,{'status':AMOUNT_STATUS,'records':records},{'selected_cases':cases}


def test_exact_first3_pair_uses_metadata_without_amounts_or_eligibility_clearance():
    keys,scope,plan=fixtures()
    cases,metadata=select_first3_whole_AR(scope,keys,plan)
    assert len(cases)==3 and len(metadata)==3
    assert all(m['amounts_or_movement_recomputed'] is False for m in metadata.values())
    assert all(m['currency_calendar_policy_and_version_questions_resolved_here'] is False for m in metadata.values())
    assert all(m['periods']['opening']['literal_date_printed'] is None for m in metadata.values())


def test_missing_role_subgroup_or_text_version_cannot_satisfy_source_selection():
    keys,scope,plan=fixtures()
    bad=deepcopy(scope);del bad['records'][0]['tables'][0]['cells']['net']
    with pytest.raises(ValueError,match='whole_AR_three_printed_roles_pending'):select_first3_whole_AR(bad,keys,plan)
    bad=deepcopy(scope);bad['records'][0]['tables'][0]['population_kind']='AR_single_party_subgroup'
    with pytest.raises(ValueError,match='whole_AR_three_printed_roles_pending'):select_first3_whole_AR(bad,keys,plan)
    bad=deepcopy(scope);bad['records'][0]['retained_text_sha256']='different_version'
    with pytest.raises(ValueError,match='native_text_identity_changed'):select_first3_whole_AR(bad,keys,plan)
