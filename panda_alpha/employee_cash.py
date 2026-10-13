"""Current FY25 operating employee-cash facts, not efficiency or native proof."""
from decimal import Decimal
import hashlib
import re
from .cash_collection import _annual_columns, _text_pair
from .financial_statements import PREFIX, TABLE, amount_unit, money, amount_text, printed_quantum_yuan, source_page
LABEL = '支付给职工以及为职工支付的现金'

def _tables(source, name):
    heads = list(TABLE.finditer(source)); result = []
    for i, hit in enumerate(heads):
        if hit['scope'] != '合并' or hit['name'] != name or hit['continued']:
            continue
        end = next((h.start() for h in heads[i+1:] if not
                    (h['scope'] == '合并' and h['name'] == name and h['continued'])), len(source))
        result.append((hit, end, source[hit.end():end]))
    return result

def parse_current_annual_employee_cash(text, stock, provenance):
    if (stock['report_date'] != '2025-12-31' or not provenance['text_hash_reverified']
            or hashlib.sha256(text.encode()).hexdigest() != provenance['text_content_sha256']):
        raise ValueError('Bound annual retained text required')
    source = text.replace('\r\n', '\n'); found = _tables(source, '现金流量表')
    if len(found) != 1:
        return {'status': 'pending', 'reason': 'unique_consolidated_cashflow_table_required', 'table_count': len(found)}
    hit, end, body = found[0]
    heading = re.search(r'(?m)^\s*'+PREFIX+r'经营\s*活动', body)
    if not heading:
        return {'status': 'pending', 'reason': 'operating_activity_header_boundary_missing'}
    columns, unit = _annual_columns(body[:heading.start()], '2025-12-31'), amount_unit(body[:heading.start()])
    pattern = '(?:' + '|'.join(r'\s*'.join(map(re.escape, label)) for label in (LABEL, '支付给职工及为职工支付的现金')) + ')'
    rows = list(re.finditer(r'(?m)^[^\S\n]*'+PREFIX+r'(?P<label>'+pattern+r')(?P<rest>[^\n]*)$', body))
    field = {'status': 'pending', 'canonical_label': LABEL, 'current_amount_yuan': None, 'row_count': len(rows),
             'new_fact': False, 'no_alias_outsourcing_payroll_substitution': True}
    if len(rows) != 1:
        field['reason'] = 'unique_exact_employee_cash_row_required'; return field
    row = rows[0]
    sections = list(re.finditer(r'(?m)^\s*'+PREFIX+r'(?P<activity>经营|投资|筹资)\s*活动\s*(?:产生的)?\s*现金流量[^\n]*', body[:row.start()]))
    role = sections[-1]['activity'] if sections else None
    cells, snippet, proof = _text_pair(row['rest'], body[row.end():].splitlines(), columns.get('note_column_explicit', False))
    field.update(literal_label=re.sub(r'\s+', '', row['label']), source_label=row.group().strip(),
                 source_page=source_page(source, hit.end()+row.start()), source_row_offset=hit.end()+row.start(),
                 row_sha256=hashlib.sha256((row.group()+'\n'+snippet).encode()).hexdigest(),
                 preceding_activity=role, column_parse_evidence=proof, printed_column_count=len(cells),
                 current_printed=cells[0] if len(cells)==2 else None, comparative_amount_claimed=False,
                 statement_scope='consolidated', statement_name='现金流量表', columns=columns, unit=unit)
    if role != '经营':
        field['reason'] = 'original_operating_activity_role_pending'; return field
    previous = body[:row.start()].rstrip().splitlines()
    if previous and re.fullmatch(r'[\d,，.\s+\-−－—–/()（）]+', previous[-1].strip()):
        field['reason'] = 'adjacent_numeric_fragment_unresolved'; return field
    if (len(cells)!=2 or columns['status']!='explicit_annual_column_pair' or unit['status']!='unit_verified'):
        field['reason'] = 'explicit_current_columns_unit_or_pair_pending'; return field
    current = money(cells[0])
    if current is None:
        field['reason'] = 'current_blank_dash_never_zero'; return field
    field.update(status='current_annual_employee_cash_amount_bound', current_amount_yuan=amount_text(current*Decimal(unit['multiplier'])),
                 current_start=columns['spans']['current_start'], current_end=columns['spans']['current_end'],
                 currency=unit['currency'], printed_unit=unit['printed_unit'],
                 current_printed_quantum_yuan=printed_quantum_yuan(cells[0],unit['printed_unit']),
                 explicit_numeric_zero=current==0, negative_printed_amount=current<0,
                 negative_outflow_role_requires_review=current<0, original_sign_preserved=True, new_fact=True)
    return field
