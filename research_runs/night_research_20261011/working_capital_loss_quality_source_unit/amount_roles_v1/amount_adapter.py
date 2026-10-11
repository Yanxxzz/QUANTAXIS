"""Exact annotated native table cells; source facts, never cash/alpha labels."""
from __future__ import annotations
from decimal import Decimal
import hashlib
import re
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[4]
sys.path.insert(0,str(ROOT))
from panda_alpha.financial_statements import money,UNIT_MULTIPLIERS

TOKENS=re.compile(r'[（(]?[-−－]?(?:\d{1,3}(?:[,，]\d{3})+|\d+)(?:\.\d+)?[）)]?[%％]?|[—–]')


def sha(raw):return hashlib.sha256(raw).hexdigest()
def dense(text):return re.sub(r'\s+','',text)
def evidence(text,ref):
    start,end=ref['start'],ref['end']
    if not 0<=start<end<=len(text):raise ValueError('source_span_bounds_pending')
    raw=text[start:end]
    # All offsets are additionally bound to the exact complete original text.
    # An absent local hash never permits content/role guessing.
    if ref.get('sha256') and sha(raw.encode('utf-8'))!=ref['sha256']:raise ValueError('source_span_hash_pending')
    return raw,{'start':start,'end':end,'span_sha256':sha(raw.encode('utf-8'))}


def reconciliation(cells):
    if any(cells[role].get('amount_printed_unit') is None for role in ['gross','allowance_disclosed','net']):
        return {'status':'missing_stock_role_pending'}
    residual=Decimal(cells['gross']['amount_printed_unit'])-Decimal(cells['allowance_disclosed']['amount_printed_unit'])-Decimal(cells['net']['amount_printed_unit'])
    tolerance=sum((Decimal(cells[role]['quantum_printed_unit'])/2 for role in ['gross','allowance_disclosed','net']),Decimal(0))
    return {'status':'source_display_intervals_overlap' if abs(residual)<=tolerance else 'source_arithmetic_conflict_pending',
            'residual_printed_unit':str(residual),'display_rounding_tolerance_printed_unit':str(tolerance),
            'cash_release_or_population_proved':False}


def original_row_tokens(text,table):
    row,r=evidence(text,table['row_span'])
    if not dense(row).startswith('合计'):raise ValueError('original_total_row_label_pending')
    if table.get('cell_spans'):
        found=[];previous=table['row_span']['start']
        for cell in table['cell_spans']:
            if not previous<=cell['start']<cell['end']<=table['row_span']['end']:raise ValueError('same_row_disjoint_cell_bounds_pending')
            literal,_=evidence(text,cell)
            if dense(literal)!=cell['printed']:raise ValueError('exact_single_cell_print_pending')
            found.append(cell['printed']);previous=cell['end']
    else:found=TOKENS.findall(row)
    if found!=table['expected_tokens']:raise ValueError('complete_original_row_token_order_pending')
    return found,r


def table_facts(text,table):
    """Independent table/block; a pending movement cannot erase stock facts."""
    if table.get('status')!='EXACT_NATIVE_ROLES_ANNOTATED':
        if table.get('row_span'):
            found,r=original_row_tokens(text,table);_,h=evidence(text,table['header_span'])
            return {'table_id':table['table_id'],'asset':table['asset'],'kind':table['kind'],'status':'EXACT_UNMAPPED_PRINTED_ROW_FACTS_ROLES_PENDING',
                    'reason':table['pending_reason'],'header':h,'row':r,'unmapped_printed_tokens':found,
                    'columns_not_shifted_to_fill_blank':True,'missing_cell_zero_imputation':False,'financial_eligibility':False,
                    'combined_role_semantics':table.get('combined_role_semantics',{}),'cause_components_split':False}
        return {'table_id':table['table_id'],'status':'source_role_annotation_pending','reason':table.get('pending_reason','actual_columns_or_total_scope_unbound')}
    header,h=evidence(text,table['header_span']);row,r=evidence(text,table['row_span'])
    if any(dense(word) not in dense(header) for word in table['required_header_literals']):
        raise ValueError('annotated_original_column_header_pending')
    if any(dense(label) not in dense(header) for label in table['original_role_labels'].values()):
        raise ValueError('printed_original_role_label_missing_from_header')
    if not dense(row).startswith('合计'):raise ValueError('original_total_row_label_pending')
    if table.get('printed_period_label') and dense(table['printed_period_label']) not in dense(header):
        raise ValueError('printed_closing_opening_header_role_pending')
    found,r=original_row_tokens(text,table)
    roles=table['role_indices'];pct=table.get('percentage_indices',[])
    if len(set(roles.values()))!=len(roles) or set(roles.values())&set(pct) or any(i<0 or i>=len(found) for i in [*roles.values(),*pct]):
        raise ValueError('money_percent_or_role_index_overlap_pending')
    if any(not found[i].endswith(('%','％')) for i in pct):raise ValueError('original_percentage_column_pending')
    population,pop=evidence(text,table['population_span'])
    if table['population_kind'] not in {'whole_AR_classification_total','whole_inventory_disclosed_classification_total','whole_AR_provision_total','whole_inventory_disclosed_provision_total'}:
        raise ValueError('overall_asset_population_pending')
    unit=table.get('unit_contract',{});normalized=False;unit_proof=None
    if unit.get('status')=='SOURCE_CNY_UNIT_BOUND':
        literal,unit_proof=evidence(text,unit['span'])
        if unit['currency']!='CNY' or unit['unit'] not in UNIT_MULTIPLIERS or dense(unit['required_literal']) not in dense(literal):
            raise ValueError('actual_CNY_unit_source_pending')
        local,_=evidence(text,unit['local_unit_span'])
        if dense(local)!='单位：'+unit['unit'] or unit.get('table_specific_currency_override_absent_reviewed') is not True:
            raise ValueError('table_local_unit_or_currency_exception_pending')
        normalized=True
    cells={}
    for role,index in roles.items():
        token=found[index]
        if token.endswith(('%','％')):raise ValueError('percentage_is_not_amount_role')
        value=money(token)
        cell={'printed':token,'original_token_index':index,'original_header_role':table['original_role_labels'][role],
              'status':'printed_blank_or_dash_unknown' if value is None else 'exact_printed_money_fact_bound',
              'amount_printed_unit':str(value) if value is not None else None,
              'quantum_printed_unit':str(Decimal(1).scaleb(value.as_tuple().exponent)) if value is not None else None,
              'amount_yuan':str(value*UNIT_MULTIPLIERS[unit['unit']]) if value is not None and normalized else None,
              'quantum_yuan':str(Decimal(1).scaleb(value.as_tuple().exponent)*UNIT_MULTIPLIERS[unit['unit']]) if value is not None and normalized else None}
        cells[role]=cell
    # Empty cells must be explicitly annotated as unknown, never inferred zero
    # from a shifted token index or a rollforward closing balance.
    for role in table.get('blank_roles',[]):
        if role in cells:raise ValueError('blank_role_overlaps_printed_role')
        cells[role]={'printed':None,'status':'empty_native_cell_unknown','amount_printed_unit':None,'amount_yuan':None,
                     'original_header_role':table['original_role_labels'][role]}
    result={'table_id':table['table_id'],'asset':table['asset'],'kind':table['kind'],'period_role':table.get('period_role'),
            'printed_period_label':table.get('printed_period_label'),'literal_date_printed':table.get('literal_date_printed'),
            'period_context':table.get('period_context'),'status':'EXACT_SOURCE_FACTS_ONLY_NOT_ELIGIBILITY',
            'population_kind':table['population_kind'],'population':pop,'header':h,'row':r,'unit_source':unit_proof,
            'unit_status':'bound_CNY_unit' if normalized else 'unit_or_currency_pending',
            'cells':cells,'percentage_columns_retained':[{ 'original_token_index':i,'printed':found[i]} for i in pct],
            'combined_role_semantics':table.get('combined_role_semantics',{}),'cause_components_split':False,
            'actual_cash_release_certified':False,'policy_scope_version_barriers_cleared':False,'financial_eligibility':False}
    if table['kind']=='stock':result['gross_allowance_net_reconciliation']=reconciliation(cells)
    else:result['movement_rollforward_status']='partial_or_combined_or_other_direction_pending_no_forced_closure'
    return result
