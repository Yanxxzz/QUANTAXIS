"""Continuous annual G source selection. No prices, labels or remote dispatch.

This local source ledger is separate from the competition worker's data channel.
Publication, observation and source validation are distinct clocks. New editions
and unresolved revisions block old values; no synthetic financial zero is used.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path
import re
import sqlite3

import numpy as np
import pandas as pd

from .financial_statements import public_availability

TZ = timezone(timedelta(hours=8))
KINDS = {'annual', 'report_metadata', 'blocker', 'resolution', 'refresh', 'industry'}


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def clock(value):
    result = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    if result.tzinfo is None:
        result = result.replace(tzinfo=TZ)
    return result.astimezone(timezone.utc)


def decision_clock(day):
    return datetime.combine(date.fromisoformat(day), datetime.min.time(), TZ).replace(hour=15)


def annual_delta(contract, period):
    """Unchanged (GPcurrent-GPcomparative)/mean(current,opening assets)."""
    year = int(period[:4])
    if period != f'{year}-12-31' or contract.get('amounts_status') != 'three_G_same_report_amounts_bound':
        raise ValueError('annual_G_amount_contract_pending')
    comparison = contract.get('current_comparability', {})
    if (comparison.get('status') != 'current_G_comparability_ready'
            or comparison.get('pending_reasons') or comparison.get('captured_barriers_preserved')
            or comparison.get('actual_change_kinds')):
        raise ValueError('annual_G_comparability_pending')
    amounts = contract['amounts']; numbers = {}
    expected = ([f'{year}-01-01', period], [f'{year-1}-01-01', f'{year-1}-12-31'])
    for field in ['operating_revenue', 'operating_cost']:
        row = amounts[field]
        if (row.get('current_span'), row.get('comparative_span')) != expected:
            raise ValueError('same_full_year_flow_roles_pending')
        numbers[field] = [Decimal(row[k]) for k in ['current_yuan', 'comparative_yuan']]
    asset = amounts['total_assets']
    if (asset.get('current_stock_asof') != period
            or asset.get('opening_stock_asof') not in (f'{year}-01-01', f'{year-1}-12-31')):
        raise ValueError('annual_stock_roles_pending')
    assets = [Decimal(asset[k]) for k in ['current_yuan', 'opening_yuan']]
    if (any(not n.is_finite() or n < 0 for pair in numbers.values() for n in pair)
            or any(not n.is_finite() or n <= 0 for n in assets)):
        raise ValueError('annual_G_amount_domain_pending')
    return float(((numbers['operating_revenue'][0]-numbers['operating_cost'][0])
                  -(numbers['operating_revenue'][1]-numbers['operating_cost'][1]))
                 / ((assets[0]+assets[1])/2))


class GSourceStore:
    """One compact append-only ledger; duplicate sync preserves first observation."""
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path, timeout=30)
        self.db.execute('CREATE TABLE IF NOT EXISTS g_events (event_id TEXT PRIMARY KEY, kind TEXT NOT NULL, code TEXT NOT NULL, observed_at TEXT NOT NULL, payload TEXT NOT NULL)')
        self.db.execute('CREATE TRIGGER IF NOT EXISTS g_events_immutable_update BEFORE UPDATE ON g_events BEGIN SELECT RAISE(ABORT,"G events immutable"); END')
        self.db.execute('CREATE TRIGGER IF NOT EXISTS g_events_immutable_delete BEFORE DELETE ON g_events BEGIN SELECT RAISE(ABORT,"G events immutable"); END')
        self.db.commit()

    def close(self):
        self.db.close()

    def append(self, kind, code, payload, *, observed_at=None):
        if kind not in KINDS or (code != '*' and not re.fullmatch(r'\d{6}', code)):
            raise ValueError('Invalid G source event identity')
        payload = dict(payload)
        proof = payload.get('source_binding', {})
        if not re.fullmatch(r'[0-9a-f]{64}', str(proof.get('sha256', ''))):
            raise ValueError('Source event requires a bound evidence SHA256')
        # A new transport manifest carrying the same financial fact must not
        # reset its first-observed clock or duplicate the monetary payload.
        identity_payload = ({k:v for k,v in payload.items() if k!='source_binding'}
                            if kind=='annual' else payload)
        event_id = digest({'kind':kind, 'code':code, 'payload':identity_payload})
        when = clock(observed_at or datetime.now(timezone.utc).isoformat()).isoformat()
        with self.db:
            self.db.execute('INSERT OR IGNORE INTO g_events VALUES (?,?,?,?,?)',
                            (event_id,kind,code,when,canonical(payload)))
        return event_id

    def events(self, cutoff):
        end = clock(cutoff)
        return [{'event_id':key, 'kind':kind, 'code':code, 'observed_at':observed, **json.loads(raw)}
                for key,kind,code,observed,raw in self.db.execute('SELECT * FROM g_events ORDER BY observed_at,event_id')
                if clock(observed) <= end]

    def asof(self, codes, decision_date, *, knowledge_cutoff=None, max_refresh_age_days=1):
        day = date.fromisoformat(decision_date).isoformat()
        cutoff = knowledge_cutoff or decision_clock(day).isoformat()
        if clock(cutoff) > clock(decision_clock(day)):
            raise ValueError('Knowledge cutoff cannot follow the supplied decision close')
        events = self.events(cutoff)
        def available(e):
            if not e.get('published_at'):
                return False
            return public_availability(e['published_at'],e.get('available_date')) <= day
        rows=[]
        industries=[e for e in events if e['kind']=='industry' and available(e)]
        newest=max((e['publication_date'] for e in industries),default=None)
        editions=[e for e in industries if e['publication_date']==newest]
        edition=editions[0] if len(editions)==1 else None
        # Independently bound scoped fragments of the SAME global document
        # may extend coverage. Different editions or conflicting rows may not.
        fingerprints={(e.get('vintage_id'),e.get('source_sha256') or
                       e.get('source_binding',{}).get('source_sha256')) for e in editions}
        if len(editions)>1 and len(fingerprints)==1 and next(iter(fingerprints))[1]:
            records={};row_events={};conflict=False
            for e in editions:
                for code,sector in e.get('records',{}).items():
                    if code in records and records[code]!=sector:conflict=True
                    records[code]=sector
                    row_events[code]=e['event_id']
            if not conflict:edition=dict(editions[0],records=records,row_event_ids=row_events)
        for code in sorted(set(codes)):
            base={'date':day,'symbol':code,'GP_delta':np.nan,'G_pct':np.nan,
                  'pit_usable':False,'full_pit_certified':False,'economic_rejection':False}
            own=[e for e in events if e['code']==code]
            # Date-only disclosures are eligible only on a later day, so the
            # complete metadata tail must cover yesterday, not tomorrow's news.
            public_tail=(date.fromisoformat(day)-timedelta(days=1)).isoformat()
            checks=[e for e in own if e['kind']=='refresh']
            if not checks:
                rows.append({**base,'source_status':'source_refresh_pending'});continue
            check=max(checks,key=lambda e:e['observed_at'])
            if (check.get('complete') is not True or check.get('through','') < public_tail
                    or check.get('since','9999') > public_tail):
                rows.append({**base,'source_status':'latest_source_refresh_incomplete'});continue
            if (clock(cutoff)-clock(check['observed_at'])).total_seconds()>max_refresh_age_days*86400:
                rows.append({**base,'source_status':'source_refresh_stale'});continue
            reports=[e for e in own if e['kind'] in ('annual','report_metadata') and available(e)
                     and str(e.get('report_date','')).endswith('-12-31')
                     and e['report_date']<=day]
            period=max((e['report_date'] for e in reports),default=None)
            if not period:
                rows.append({**base,'source_status':'annual_report_pending'});continue
            # Do not carry a years-old annual beyond the next normal publication season.
            if day >= f'{int(period[:4])+2}-05-01':
                rows.append({**base,'source_status':'new_annual_disclosure_pending'});continue
            versions=[e for e in reports if e['report_date']==period]
            latest=max(public_availability(e['published_at'],e.get('available_date')) for e in versions)
            versions=[e for e in versions if public_availability(e['published_at'],e.get('available_date'))==latest]
            ids={e['announcement_id'] for e in versions}
            if len(ids)!=1:
                rows.append({**base,'source_status':'ambiguous_latest_annual_editions'});continue
            annid=next(iter(ids))
            facts=[e for e in versions if e['kind']=='annual' and e['announcement_id']==annid]
            if not facts:
                rows.append({**base,'source_status':'latest_annual_fields_pending','announcement_id':annid});continue
            selected=max(facts,key=lambda e:e['observed_at'])
            if len({e.get('source_record_sha256') for e in facts})>1:
                rows.append({**base,'source_status':'conflicting_same_announcement_source_versions'});continue
            approved=selected.get('approved_through',str(selected['published_at'])[:10])
            if check.get('since','9999') > approved:
                rows.append({**base,'source_status':'source_refresh_gap_after_validation'});continue
            # A later incomplete consumer update cannot silently fall back to a ready one.
            try:
                value=annual_delta(selected['contract'],period)
            except (ValueError,KeyError,TypeError,InvalidOperation) as error:
                rows.append({**base,'source_status':str(error),'announcement_id':annid});continue
            blockers=[e for e in own if e['kind']=='blocker' and available(e)
                      and (not e.get('report_dates') or period in e['report_dates']
                           or any(str(p)[:4] in {period[:4],str(int(period[:4])-1)}
                                  for p in e['report_dates']))]
            resolved={e.get('resolves_event_id') for e in own if e['kind']=='resolution' and available(e)
                      and e.get('effect_status')=='unchanged_selected_annual'
                      and e.get('annual_source_id')==annid}
            blockers=[e for e in blockers if e['event_id'] not in resolved]
            pending=selected.get('pending_reasons', [])
            if pending or blockers:
                rows.append({**base,'source_status':'known_annual_revision_or_tail_pending',
                    'pending_reasons':pending,'blocking_event_ids':[e['event_id'] for e in blockers]});continue
            sector=edition.get('records',{}).get(code) if edition else None
            if not sector or not re.fullmatch(r'[A-S]\d{2}',str(sector)):
                rows.append({**base,'source_status':'latest_published_industry_missing'});continue
            if sector.startswith('J'):
                rows.append({**base,'source_status':'financial_industry_G_scope_pending'});continue
            rows.append({**base,'GP_delta':value,'sector':sector,'sector_asof':edition['publication_date'],
                'pit_usable':True,'source_status':'continuous_G_ready_within_bound_sources',
                'announcement_id':annid,'report_date':period,'source_event_id':selected['event_id'],
                'source_record_sha256':selected.get('source_record_sha256'),
                'refresh_event_id':check['event_id'],
                'industry_event_id':edition.get('row_event_ids',{}).get(code,edition['event_id'])})
        frame=pd.DataFrame(rows)
        if frame.empty:return frame
        ready=frame.pit_usable.eq(True)
        if ready.any():
            frame.loc[ready,'G_pct']=frame.loc[ready].groupby('sector').GP_delta.rank(method='average',pct=True)
        return frame


class ContinuousGF:
    """Local framework adapter: live G provider, exact fixed F mathematics.

    Does not claim that this provider is available inside an official worker.
    The caller supplies the full price universe before G qualification.
    """
    def __init__(self, source, codes, f141_calculate):
        self.source,self.codes,self.f141_calculate=source,list(codes),f141_calculate

    def calculate(self, factors):
        close=factors['close']
        if list(close.index.names)!=['date','symbol'] or not close.index.is_unique:
            raise ValueError('Unique date/symbol close index required')
        full_f=self.f141_calculate({'close':close}).reindex(close.index)
        result=pd.Series(np.nan,index=close.index,name='value')
        pending={}
        dates=close.index.get_level_values('date')
        for day in sorted(set(pd.to_datetime(dates).strftime('%Y-%m-%d'))):
            g=self.source.asof(self.codes,day)
            pending[day]=g.source_status.value_counts().to_dict() if not g.empty else {'missing_G_universe':1}
            if g.empty:continue
            # G rank is built across the financial universe BEFORE the F intersection.
            g=g[g.pit_usable].set_index('symbol')
            mask=pd.to_datetime(dates).strftime('%Y-%m-%d')==day
            f=full_f[mask];symbols=f.index.get_level_values('symbol')
            component=pd.DataFrame({'G':g.G_pct.reindex(symbols).to_numpy(),'F':f.to_numpy()},index=f.index)
            component=component[np.isfinite(component).all(axis=1)]
            if len(component)<2:continue
            f_pct=component.F.rank(method='average',pct=True)
            parts=[]
            for x in [component.G,-f_pct]:
                x=x.clip(*x.quantile([.01,.99]).tolist());sd=x.std(ddof=0)
                if not np.isfinite(sd) or sd<=1e-12:break
                parts.append((x-x.mean())/sd)
            if len(parts)==2:result.loc[component.index]=(parts[0]+parts[1])/2
        self.diagnostics={'source_status_by_date':pending,'official_production_channel_certified':False}
        return result
