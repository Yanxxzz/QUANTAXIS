import pytest
import json
import pandas as pd

from panda_alpha.beijing import official_aliases, lifecycles, parse_klines, current_list, official_current_list, parse_official_directory, response_cache, official_exit_record
from panda_alpha.data import adjust_with_price_panel, normalize_code, PendingDataError


def test_bse_select_date_is_not_earlier_a_share_membership():
    page = '<table><tr><td>1</td><td>公司</td><td>2020/7/27</td><td>835185</td><td>920185</td></tr></table>'
    rows = official_aliases(page)
    assert rows[0]['ipo_date'] == '2021-11-15'
    assert rows[0]['old_code'] == '835185'
    current = [{'code': '920185', 'name': '公司', 'ipo_date': '2021-11-15'}]
    life = lifecycles(current, rows)[0]
    assert not life['historical_universe_complete']
    assert life['delisted_date'] is None
    assert normalize_code('bj.920185') == '920185'
    assert normalize_code('920185.BJ') == '920185'
    with pytest.raises(ValueError, match='Conflicting'):
        lifecycles([dict(current[0], ipo_date='2022-01-01')], rows)


def test_hfq_source_prices_preserved_and_qfq_does_not_use_later_action():
    frame = pd.DataFrame({'date': pd.to_datetime(['2024-01-02', '2024-01-03']),
                          'open': [10, 10], 'high': [11, 11], 'low': [9, 9], 'close': [10, 10]})
    panel = pd.DataFrame({'date': ['2024-01-02', '2024-01-03', '2025-01-02'],
                          'open': [10.01, 20.01, 90], 'high': [11, 22, 99],
                          'low': [9, 18, 81], 'close': [10, 20, 90]})
    hfq = adjust_with_price_panel(frame, panel, 'hfq')
    assert hfq['open'].tolist() == [10.01, 20.01]
    qfq = adjust_with_price_panel(frame, panel, 'qfq')
    assert qfq['close'].tolist() == [5, 10]
    with pytest.raises(PendingDataError, match='Missing'):
        adjust_with_price_panel(frame, panel.iloc[:1], 'hfq')


def test_vendor_schema_units_and_identity_required():
    payload = {'code': '920185', 'market': 0,
               'klines': ['2024-01-02,10,10,11,9,100,100000,20,0,0,1']}
    record = parse_klines(payload, '920185', '2024-01-01', '2024-01-03', 'none')[0]
    assert record['volume_shares'] == 10000
    assert record['is_st'] is None
    with pytest.raises(ValueError, match='units'):
        parse_klines(dict(payload, klines=['2024-01-02,10,10,11,9,100,1000,20,0,0,1']), '920185', '2024-01-01', '2024-01-03', 'none')
    with pytest.raises(ValueError, match='different security'):
        parse_klines(payload, '920002', '2024-01-01', '2024-01-03', 'none')


def directory_page(number, codes, *, snapshot='20260930'):
    return 'null(' + json.dumps([{'content': [
        {'xxzqdm': code, 'xxzqjc': '公司', 'fxssrq': '20200727', 'xxjsrq': snapshot,
         'xxfcbj': '2', 'xxzqjb': 'T', 'xxtpbz': 'F', 'xxzrzt': 'N'} for code in codes],
        'number': number, 'numberOfElements': len(codes), 'size': 2,
        'totalElements': 3, 'totalPages': 2}], ensure_ascii=False) + ');'


class DirectorySession:
    def __init__(self, pages):
        self.pages, self.calls = pages, 0

    def post(self, url, data, timeout):
        self.calls += 1
        content = self.pages[data['page']].encode('utf-8')
        return type('Response', (), {'content': content, 'raise_for_status': lambda self: None})()


def test_official_directory_pages_certify_current_scope_only(tmp_path):
    session = DirectorySession([directory_page(0, ['920001', '920002']), directory_page(1, ['920003'])])
    rows, receipt = official_current_list(session, tmp_path)
    assert len(rows) == receipt['total'] == 3
    assert receipt['total_pages'] == 2 and receipt['current_directory_complete']
    assert not receipt['historical_universe_complete']
    assert rows[0]['ipo_date'] == '2021-11-15'
    assert rows[0]['source_snapshot_date'] == '2026-09-30'
    assert len(rows[0]['source_sha256']) == 64
    cached_rows, _ = official_current_list(session, tmp_path)
    assert cached_rows == rows and session.calls == 2


def test_directory_changes_duplicates_and_damaged_names_are_rejected(tmp_path):
    duplicate = DirectorySession([directory_page(0, ['920001', '920002']), directory_page(1, ['920002'])])
    with pytest.raises(ValueError, match='Duplicate'):
        official_current_list(duplicate, tmp_path/'duplicate')
    mixed = DirectorySession([directory_page(0, ['920001', '920002']), directory_page(1, ['920003'], snapshot='20261001')])
    with pytest.raises(ValueError, match='snapshot changed'):
        official_current_list(mixed, tmp_path/'mixed')
    damaged = DirectorySession([directory_page(0, ['920001', '920002']).replace('公司', '\ufffd')])
    with pytest.raises(ValueError, match='damaged'):
        official_current_list(damaged, tmp_path/'damaged')


def test_official_directory_missing_denominator_and_cache_tamper_block(tmp_path):
    with pytest.raises(ValueError, match='denominator'):
        parse_official_directory('null([{"content":[]}])')
    session = DirectorySession([directory_page(0, ['920001', '920002']), directory_page(1, ['920003'])])
    official_current_list(session, tmp_path)
    path = tmp_path/'page_00000.response'
    path.write_bytes(b'{}')
    with pytest.raises(ValueError, match='hash mismatch'):
        official_current_list(session, tmp_path)


def test_missing_current_identity_does_not_invent_delisting_date():
    alias = {'code': '920185', 'old_code': '835185', 'name': '公司', 'ipo_date': '2021-11-15'}
    row = lifecycles([], [alias])[0]
    assert not row['current_directory_present']
    assert row['exit_status'] == 'not_current_exit_date_unverified'
    assert row['delisted_date'] is None
    assert row['listing_status'] == 'historical_identity_pending_exit'


def test_effective_exit_date_is_distinct_from_decision_date():
    text = '证券代码：920305 证券简称：云创退 公告编号：2026-076 公司股票将于2026年7月30日被北京证券交易所终止上市并摘牌。终止上市决定日期：2026年6月9日'
    source = {'pub_date': '2026-07-29', 'source_sha256': 'a'*64, 'source_url': 'https://www.bse.cn/notice.pdf'}
    row = official_exit_record(text, '920305', source, ipo_date='2021-11-15')
    assert row['delisted_date'] == '2026-07-30'
    assert row['listing_status'] == '0' and row['exit_kind'] == 'delisting'
    assert not row['historical_universe_complete']
    with pytest.raises(ValueError, match='effective'):
        official_exit_record('证券代码：920305 证券简称：云创退 公告编号：2026-076 终止上市决定日期：2026年6月9日', '920305', source, ipo_date='2021-11-15')


def test_transfer_exit_retains_old_identity_and_bse_open_membership():
    text = ('证券代码：833874 证券简称：泰祥股份 公告编号：2022-066 关于公司股票因转板在北京证券交易所终止上市 '
            '2021年召开股东大会，同年11月15日北京证券交易所（以下简称“北交所”）设立，公司身份转换为北交所上市公司。'
            '股票终止上市日期为2022年7月18日。')
    row = official_exit_record(text, '833874', {'pub_date': '2022-07-15', 'source_sha256': 'b'*64})
    assert row['code'] == '833874' and row['ipo_date'] == '2021-11-15'
    assert row['delisted_date'] == '2022-07-18' and row['exit_kind'] == 'exchange_transfer'
