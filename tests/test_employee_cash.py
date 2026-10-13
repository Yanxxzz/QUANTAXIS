"""Specific label and activity-role boundaries, not trading tests."""
import hashlib
import importlib.util
import os
from pathlib import Path

from panda_alpha.employee_cash import parse_current_annual_employee_cash


def run(text):
    parser = parse_current_annual_employee_cash
    if os.environ.get('EMPLOYEE_CASH_TEST_BASELINE') == '1':
        p = Path(__file__).resolve().parents[1] / 'research_runs/day_research_20261013/cash_labor_source4/run_source.py'
        spec = importlib.util.spec_from_file_location('frozen_employee_parser', p)
        old = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(old)
        parser = old.employee
    return parser(text, {'report_date': '2025-12-31'},
                  {'text_hash_reverified': True, 'text_content_sha256': hashlib.sha256(text.encode()).hexdigest()})


def statement(label, *, section='经营', cell='4.00', previous=''):
    return ('===SOURCE_PAGE:1===\n合并现金流量表\n单位：元\n项目 2025年度 2024年度\n'
            '一、经营活动产生的现金流量\n销售商品、提供劳务收到的现金 20.00 19.00\n'
            + ('二、投资活动产生的现金流量\n' if section == '投资' else '')
            + previous + label + ' ' + cell + ' 3.00\n经营活动产生的现金流量净额 2.00 1.00\n'
            '母公司现金流量表\n支付给职工及为职工支付的现金 1.00 1.00\n')


def test_short_and_full_and_labels_bind_same_operating_cash_role():
    a = run(statement('支付给职工以及为职工支付的现金'))
    b = run(statement('支付给职工及为职工支付的现金'))
    assert a['status'] == b['status'] == 'current_annual_employee_cash_amount_bound'
    assert a['current_amount_yuan'] == b['current_amount_yuan'] == '4.00'
    assert a['literal_label'] != b['literal_label']


def test_investing_segment_is_not_operating_payroll():
    assert run(statement('支付给职工以及为职工支付的现金', section='投资'))['status'] == 'pending'


def test_unknown_number_and_dash_are_not_zero():
    assert run(statement('支付给职工以及为职工支付的现金', previous='99.00 98.00\n'))['status'] == 'pending'
    assert run(statement('支付给职工以及为职工支付的现金', cell='—'))['status'] == 'pending'
    z = run(statement('支付给职工以及为职工支付的现金', cell='0.00'))
    assert z['current_amount_yuan'] == '0.00' and z['explicit_numeric_zero']
