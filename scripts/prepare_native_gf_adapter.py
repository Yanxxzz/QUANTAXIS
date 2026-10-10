"""Build/replay GF on a read-only StockDB proxy; no API, labels or ledger writes."""
from pathlib import Path
from datetime import datetime, timezone
import hashlib
import json
import sys

import numpy as np
import pandas as pd
from pymongo import MongoClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from panda_alpha.native_gf_transfer import (
    frozen_g_source, compress_g_intervals, render_native_gf,
    read_mongo_gf_fixture, file_sha256,
)
from panda_alpha.risk_relation import relation_states
from panda_alpha.factor_blend import equal_signal

OUT = ROOT / 'research_runs/night_research_20261011/native_gf_adapter_unit'
read = lambda p: json.loads(Path(p).read_text(encoding='utf-8-sig'))


def save(name, value):
    path = OUT / name
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')


def main():
    OUT.mkdir(exist_ok=True)
    if (OUT/'adapter_review.json').exists():
        raise ValueError('Completed adapter unit exists; do not blindly replay or overwrite it')
    protocol = ROOT/'research_runs/wc03_equal_blend_20261010/protocol.json'
    g, eval_dates, anchors, source_receipt = frozen_g_source(protocol)
    print(json.dumps({'stage':'literal_G_rebound','rows':len(g),'formation_exact':True}),flush=True)
    cfgpath = ROOT/'config/panda-alpha.local.json';cfg=read(cfgpath)
    with MongoClient(cfg['data']['mongo_uri'],serverSelectionTimeoutMS=5000) as client:
        calendar=[r['date'] for r in client.quantaxis.trade_calendar.find(
            {'source':'baostock_trade_dates','date':{'$gte':'2024-06-03','$lte':'2026-09-03'}},
            {'_id':0,'date':1}).sort('date',1)]
    if [d for d in calendar if d>='2025-08-14'] != eval_dates:
        raise ValueError('Independent input calendar disagrees with original evaluation dates')
    save('input_calendar.json',{'dates':calendar,'source':'quantaxis.trade_calendar:baostock_trade_dates',
         'read_only':True,'not_a_new_exchange_census':True})
    peer_protocol=ROOT/'research_runs/stockdb_research_20261008_tail/protocol.json'
    adjustment=ROOT/'research_runs/stockdb_research_20261008_tail/source_ready.json'
    repair=ROOT/'research_runs/wc03_repaired_research_20261009/native_minute_repair_receipt.json'
    descriptor={'format':'native_gf_readonly_mongo_v1','config_path':str(cfgpath),
        'source':'stockdb','database':'quantaxis_stockdb_research',
        'input_window':{'start':'2024-06-03','end':'2026-09-03'},
        'price_basis':'stockdb_asof_hfq_proxy',
        'peer_scope_protocol':{'path':str(peer_protocol),'sha256':file_sha256(peer_protocol)},
        'existing_adjustment_contract':{'path':str(adjustment),'sha256':file_sha256(adjustment)},
        'existing_price_repairs':[{'path':str(repair),'sha256':file_sha256(repair)}],
        'proxy_not_actual_production_export':True,'no_source_lock_or_native_snapshot_implied':True}
    save('fixture_descriptor.json',descriptor)
    source=compress_g_intervals(g,eval_dates)
    life=read(ROOT/'research_runs/wc03_history_2022_20261009/lifecycle_snapshot.json')['records']
    markets={r['code']:r['sse'] for r in life if r['code'] in source}
    code=render_native_gf(source,markets,calendar,{'start':'2025-08-14','end':'2026-09-03'})
    codepath=OUT/'WC03NativeGF.py';codepath.write_text(code,encoding='utf-8',newline='\n')
    print(json.dumps({'stage':'code_ready','code_bytes':codepath.stat().st_size,'intervals':sum(map(len,source.values()))}),flush=True)
    fixture=read_mongo_gf_fixture(OUT/'fixture_descriptor.json')
    print(json.dumps({'stage':'source_view_read','rows':len(fixture),'dates':fixture.date.nunique(),'symbols':fixture.symbol.nunique()}),flush=True)
    # Reference is the unchanged original mathematical implementation. Prices
    # are a named source proxy, and source eligibility remains a literal mask.
    wide=fixture.pivot(index='date',columns='symbol',values='close').reindex(calendar)
    wide.index=pd.to_datetime(wide.index)
    state=relation_states(wide)['F141']
    global_rank=state.rank(axis=1,pct=True,method='average')
    projected=fixture[fixture.date.between('2025-08-14','2026-09-03')].copy().reset_index(drop=True)
    times=pd.to_datetime(projected.date)
    xi=state.index.get_indexer(times);yi=state.columns.get_indexer(projected.symbol)
    rank_values=global_rank.to_numpy()[xi,yi]
    source_values=g[['date','symbol','G_pct']]
    projected=projected.merge(source_values,on=['date','symbol'],how='left',validate='one_to_one')
    projected['source_qualified']=np.isfinite(projected.G_pct)
    projected['F141']=rank_values
    usable=projected.source_qualified & np.isfinite(projected.F141)
    selected=projected[usable][['date','symbol','G_pct','F141']].copy()
    selected['F_pct']=selected.groupby('date').F141.rank(method='average',pct=True)
    selected['expected']=equal_signal(selected,{'G_pct':1,'F_pct':0})
    expected=np.full(len(projected),np.nan);expected[selected.index.to_numpy()]=selected.expected.to_numpy()
    source_mask=projected[['date','symbol','source_qualified']].copy()
    source_mask['expected_finite']=np.isfinite(expected)
    maskpath=OUT/'source_qualification.parquet';source_mask.to_parquet(maskpath,index=False,compression='zstd')
    del global_rank,wide
    # Isolated self-contained code evaluation only; no workflow, API or charge.
    namespace={'Factor':object,'__name__':'private_local_GF_software_replay'}
    exec(compile(code,str(codepath),'exec'),namespace)
    native=namespace['WC03NativeGF']()
    index=pd.MultiIndex.from_arrays([pd.to_datetime(fixture.date),fixture.symbol],names=['date','symbol'])
    output=native.calculate({'close':pd.Series(fixture.close.to_numpy(),index=index)})
    expected_index=pd.MultiIndex.from_arrays([times,projected.symbol],names=['date','symbol'])
    if not output.index.equals(expected_index):raise AssertionError('Projection index mismatch')
    finite_equal=np.array_equal(np.isfinite(output.to_numpy()),np.isfinite(expected))
    if not finite_equal:raise AssertionError('Self-contained/reference finite mask mismatch')
    finite=np.isfinite(expected);error=float(np.max(np.abs(output.to_numpy()[finite]-expected[finite]))) if finite.any() else None
    if error is None or error>2e-13:raise AssertionError('Original mathematical formula changed')
    print(json.dumps({'stage':'independent_math_equal','max_error':error,'finite':int(finite.sum())}),flush=True)
    # Read only already published old signal values for two known control dates.
    old_f=ROOT/'research_runs/wc03_repaired_research_20261009/F141_comparator_panel.parquet'
    old=pd.read_parquet(old_f,columns=['date','symbol','value','pit_usable'])
    comparisons=[]
    for day in ('2026-08-26','2026-09-03'):
        prior=old[old.date.eq(day)];current=projected[projected.date.eq(day)][['symbol','F141']]
        join=prior.merge(current,on='symbol',how='left',validate='one_to_one')
        a=join.value.to_numpy(dtype=float);b=join.F141.to_numpy(dtype=float)
        same=np.isfinite(a)&np.isfinite(b)
        comparisons.append({'date':day,'original_rows':len(join),'finite_mask_exact':bool(np.array_equal(np.isfinite(a),np.isfinite(b))),
            'joint_finite':int(same.sum()),'maximum_global_percentile_error':float(np.max(np.abs(a[same]-b[same]))) if same.any() else None})
    decision_dates=eval_dates[:-6]
    if decision_dates[::5]!=anchors:raise AssertionError('Fixed 51 formation lattice changed')
    grouping=[]
    for day in decision_dates:
        values=output.to_numpy()[projected.date.eq(day).to_numpy()];values=values[np.isfinite(values)]
        ready=False
        try:ready=len(np.unique(pd.qcut(values,10,labels=False)))==10
        except ValueError:pass
        grouping.append({'date':day,'finite':len(values),'distinct':len(np.unique(values)),'no_jitter_ten_groups':ready})
    peers=fixture.symbol.unique().tolist();declared=read(peer_protocol)['codes']
    peer_sha=lambda a:hashlib.sha256(json.dumps(sorted(a),separators=(',',':')).encode()).hexdigest()
    native_old=ROOT/'research_runs/wc03_arinv_increment_20261009/native_close_source_snapshot.parquet'
    older=pd.read_parquet(native_old,columns=['code']).code.unique().tolist()
    review={'status':'SELF_CONTAINED_GF_MATH_VERIFIED_ON_DECLARED_STOCKDB_PROXY_SOURCE_SCOPE_PENDING',
        'frozen_at_utc':datetime.now(timezone.utc).isoformat(),'code':{'path':str(codepath),'sha256':file_sha256(codepath)},
        'generator':{'path':str(Path(__file__).resolve()),'sha256':file_sha256(__file__)},
        'reader':{'path':str(ROOT/'panda_alpha/native_gf_transfer.py'),'sha256':file_sha256(ROOT/'panda_alpha/native_gf_transfer.py'),
                  'qualname':'read_mongo_gf_fixture'},
        'fixture_descriptor':{'path':str(OUT/'fixture_descriptor.json'),'sha256':file_sha256(OUT/'fixture_descriptor.json')},
        'source_qualification':{'path':str(maskpath),'sha256':file_sha256(maskpath),'rows':len(source_mask)},
        'input_calendar':{'path':str(OUT/'input_calendar.json'),'sha256':file_sha256(OUT/'input_calendar.json')},
        'native_evaluation_contract':{'input_window':descriptor['input_window'],
             'evaluation_window':{'start':'2025-08-14','end':'2026-09-03'},'formation_dates':anchors,
             'holding_end':'2026-09-03','warmup':{'minimum_prior_price_rows':258},
             'input_calendar':{'path':str(OUT/'input_calendar.json'),'sha256':file_sha256(OUT/'input_calendar.json')},
             'source_qualification':{'path':str(maskpath),'sha256':file_sha256(maskpath)}},
        'original_G_source':source_receipt,'price_proxy':descriptor['price_basis'],
        'input_rows':len(fixture),'input_dates':len(calendar),'prior_calendar_dates':sum(x<'2025-08-14' for x in calendar),
        'peer_scope':{'declared_full_acquisition_count':len(declared),'declared_set_sha256':peer_sha(declared),
             'received_source_time_count':len(peers),'received_set_sha256':peer_sha(peers),
             'old_shorter_native_snapshot_count':len(older),'old_shorter_set_sha256':peer_sha(older),
             'received_not_in_declared':sorted(set(peers)-set(declared)),
             'additional_price_peers_relative_to_shorter_history':len(set(peers)-set(older)),
             'never_filtered_by_financial_G_source':True,'official_native_peer_scope_unverified':True},
        'mathematical_validation':{'original_relation_states_and_equal_signal_reference':True,
             'max_absolute_error':error,'finite_mask_exact':finite_equal,'finite_rows':int(finite.sum()),
             'complete_original_eval_projection':True,'precalculated_F141_lookup_used':False},
        'known_date_original_F141_comparisons':comparisons,
        'public_lattice':{'decision_dates':len(decision_dates),'formation_count':len(anchors),
             'last_formation':anchors[-1],'holding_only_tail':eval_dates[-6:],
             'no_Sep2_extra_formation':'public 5D label uses shift(-6)/shift(-1); missing future label excludes last six dates before group sampling',
             'phase_requirement':'every preceding decision date must survive grouping and native tradeability; public day_count samples available groups, gaps shift phase',
             'all_decision_dates_no_jitter_ten_groups':all(r['no_jitter_ten_groups'] for r in grouping),
             'min_decision_finite':min(r['finite'] for r in grouping),
             'min_decision_distinct':min(r['distinct'] for r in grouping),
             'not_verified_remote_tradeability':'can still change grouped date support and actual formation phase',
             'label_price_semantics':'public hfq_open uses first_open*cum(close/pre_close), not certification of actual StockDB adjusted open executions'},
        'source_scope_warning':'Original literal strict mask is frozen for software replay. Root found5488/38393 formation identities conflicting with six-field parent scope; this is NOT current whole-G/PIT certification. Conservative intersection/field repair would require a separately frozen root context and explicit G rank-base explanation.',
        'holding_end_finite':int(source_mask[source_mask.date.eq('2026-09-03')].expected_finite.sum()),
        'holding_tail_G_unavailable_remains_NaN':True,
        'actual_production_field_price_basis_not_verified':True,'not_a_real_server_export':True,
        'not_formal_admission_or_pool_increment':True,'dispatch_ready':False,
        'new_labels_or_returns_read':0,'new_economic_trials':0,'official_spent':0,'raw_price_snapshot_copies':0}
    save('adapter_review.json',review)
    print(json.dumps({'status':review['status'],'review_sha256':file_sha256(OUT/'adapter_review.json'),
                      'max_error':error,'all_decision_groups':review['public_lattice']['all_decision_dates_no_jitter_ten_groups'],
                      'known_dates':comparisons}),flush=True)


if __name__=='__main__':
    main()
