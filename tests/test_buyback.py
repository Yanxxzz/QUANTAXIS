import hashlib
import unittest

from panda_alpha.buyback import parse_buyback_disclosure, cumulative_execution_increment, parse_buyback_plan_context


# The supported phrases and factual values below were checked against cached
# CNINFO originals 1224188345, 1224162215, 1224188900 and 1224317987. Full PDFs
# and receipt hashes are retained privately, rather than copying full notices.
PLAN = '于2025年4月3日召开的第七届董事会第二十七次会议，审议通过《关于回购公司股份方案的议案》。'
OBS = '截至2025年7月11日，公司通过股份回购专用证券账户以集中竞价交易方式回购公司股份10063100股，占公司总股本的1.3%。'


def extract(body=OBS, *, title='关于回购公司股份比例达到1%的进展公告',
            plan=PLAN, symbol='002133', publication='2025-07-17T00:00:00+08:00', suffix=''):
    text = f'证券代码：{symbol}\n{plan}\n{body}\n{suffix}'
    source = dict(announcement_id='1224188345', symbol=symbol, published_at=publication,
                  title=title, source_url='https://static.cninfo.com.cn/finalpage/2025-07-17/1224188345.PDF',
                  pdf_sha256='a' * 64, text_sha256=hashlib.sha256(text.encode('utf-8')).hexdigest())
    return parse_buyback_disclosure(text, source)


def previous(*, count=5000000, asof='2025-06-30', publication='2025-07-02T00:00:00+08:00',
             status='SOURCE_VERIFIED', plan_status='active', plan_id=None):
    record = extract()
    record.update(announcement_id='prior', cumulative_shares=count, report_asof_date=asof,
                  published_at=publication, status=status, plan_status=plan_status)
    if plan_id is not None:
        record['plan_id'] = plan_id
    return record


class BuybackSourceTests(unittest.TestCase):
    def test_actual_market_buy_for_future_employee_use_is_retained(self):
        r = extract(suffix='本次回购用于员工持股计划或股权激励。')
        self.assertEqual('SOURCE_VERIFIED', r['status'])
        self.assertEqual(10063100, r['cumulative_shares'])
        self.assertIn('future_employee_or_incentive_use', r['purpose'])

    def test_unselected_purpose_checkboxes_are_not_claimed(self):
        r = extract(suffix='□用于转换公司可转债\n√用于员工持股计划或股权激励')
        self.assertNotIn('convertible_bond_conversion', r['purpose'])

    def test_share_units_and_percent_are_separate(self):
        r = extract(body=OBS.replace('10063100股', '1,006.3100万股'))
        self.assertEqual(10063100, r['cumulative_shares'])
        self.assertEqual('SOURCE_VERIFIED', r['status'])
        percent_only = extract(body=OBS.replace('10063100股，', ''))
        self.assertEqual('SOURCE_PENDING', percent_only['status'])

    def test_rounded_wan_shares_are_not_certified_integer_counts(self):
        r = extract(body=OBS.replace('10063100股', '1,209.41万股'))
        self.assertEqual('SOURCE_PENDING', r['status'])
        self.assertEqual(100, r['printed_share_quantum'])
        self.assertIn('rounded_cumulative_shares_not_exact', r['pending_reasons'])

    def test_planned_quantity_and_account_balance_are_not_cumulative_actual(self):
        r = extract(body='截至2025年7月11日，公司计划以集中竞价交易方式拟回购公司股份10063100股。')
        self.assertEqual('SOURCE_PENDING', r['status'])
        r = extract(suffix='回购账户含前次回购102735股，累计313417股。')
        self.assertIn('multiple_or_previous_plan_requires_manual_separation', r['pending_reasons'])

    def test_granted_restricted_stock_redemption_is_not_market_purchase(self):
        r = extract(title='关于回购注销部分已授予限制性股票的公告')
        self.assertIn('granted_restricted_share_redemption', r['pending_reasons'])

    def test_public_metadata_date_is_not_replaced_by_earlier_signing_date(self):
        r = extract(suffix='董事会2025年7月16日')
        self.assertEqual('2025-07-17T00:00:00+08:00', r['published_at'])
        self.assertEqual('2025-07-11', r['report_asof_date'])

    def test_corrected_notice_and_share_count_restatement_remain_pending(self):
        self.assertIn('correction_requires_public_version_chain',
                      extract(title='关于回购进展公告的更正公告')['pending_reasons'])
        self.assertIn('share_count_restatement_requires_version_chain',
                      extract(suffix='已回购股份因每10股转增3股调整。')['pending_reasons'])

    def test_conflicting_asof_counts_and_invalid_date_are_not_resolved_by_first_match(self):
        r = extract(body=OBS + OBS.replace('10063100', '20063100'))
        self.assertEqual('SOURCE_PENDING', r['status'])
        self.assertEqual('SOURCE_PENDING', extract(body=OBS.replace('7月11日', '2月31日'))['status'])

    def test_missing_plan_is_not_filled_with_disclosure_date(self):
        self.assertIn('plan_resolution_ambiguous_or_missing', extract(plan='')['pending_reasons'])

    def test_resale_of_past_buybacks_and_subsidiary_repurchase_right_are_not_buys(self):
        # CNINFO 1224980640 / 1224995197 are these two misleading title classes.
        for title in ('关于首次集中竞价减持已回购股份的进展公告',
                      '关于对杭州德海艾科能源科技有限公司行使回购权的进展公告'):
            r = extract(title=title)
            self.assertIn('not_current_company_market_purchase', r['pending_reasons'])

    def test_multiple_buyback_phases_are_not_a_single_plan_account(self):
        r = extract(suffix='一、第一期回购股份的情况。二、第二期回购股份的情况。')
        self.assertIn('multiple_buyback_phases_require_manual_separation', r['pending_reasons'])

    def test_will_buy_wording_before_auction_method_is_not_actual(self):
        r = extract(body=OBS.replace('公司通过', '公司拟通过'))
        self.assertEqual('SOURCE_PENDING', r['status'])

    def test_a_and_b_share_headers_of_same_issuer_do_not_change_stock_quantity(self):
        r = extract(suffix='证券代码：202133 证券简称：同一发行人B')
        self.assertEqual('SOURCE_VERIFIED', r['status'])
        self.assertEqual(10063100, r['cumulative_shares'])
        self.assertEqual('SOURCE_PENDING', extract(suffix='证券代码：600999')['status'])

    def test_board_and_shareholder_dates_are_not_interchanged(self):
        plan = ('于2025年4月3日召开的第七届董事会第二十七次会议和'
                '于2025年4月22日召开的2025年第二次临时股东大会审议通过《关于回购公司股份方案的议案》。')
        r = extract(plan=plan, suffix='回购期限为自公司股东大会审议通过回购股份方案之日起12个月。')
        self.assertEqual('2025-04-03', r['plan_resolution_date'])
        self.assertEqual('2025-04-22', r['plan_shareholder_resolution_date'])
        self.assertEqual('2026-04-21', r['plan_expiry_date'])

    def test_parallel_board_and_shareholder_date_sentence_uses_correspondence(self):
        plan = ('分别于2025年4月11日、2025年4月29日召开了公司第二届董事会第九次会议、'
                '2025年第二次临时股东大会审议通过了《关于回购公司股份方案的议案》。')
        r = extract(plan=plan)
        self.assertEqual('2025-04-11', r['plan_resolution_date'])
        self.assertEqual('2025-04-29', r['plan_shareholder_resolution_date'])

    def test_source_three_month_term_is_not_assumed_twelve_months(self):
        r = extract(suffix='回购股份的实施期限为自公司董事会审议通过本次回购方案之日起三个月内。')
        self.assertEqual('2025-07-02', r['plan_expiry_date'])
        r = extract(suffix='回购方案实施期限2025年4月16日~2025年10月15日')
        self.assertEqual('2025-10-15', r['plan_expiry_date'])
        self.assertEqual('explicit_public_end_date', r['expiry_basis'])

    def test_unknown_expiry_has_a_distinct_gate_and_no_invented_date(self):
        r = extract()
        self.assertEqual('SOURCE_VERIFIED', r['status'])
        self.assertIsNone(r['plan_expiry_date'])
        self.assertEqual('SOURCE_PENDING', r['expiry_status'])

    def test_explicit_nonexecution_is_zero_but_not_an_implicit_first_execution(self):
        r = extract(body='截至2025年7月11日，公司尚未进行股份回购。', suffix='本次拟以集中竞价交易方式回购。')
        self.assertEqual('SOURCE_VERIFIED', r['status'])
        self.assertEqual(0, r['cumulative_shares'])
        self.assertEqual('SOURCE_PENDING', cumulative_execution_increment(r, [])['status'])

    def test_month_without_execution_does_not_reset_the_plan_cumulative_count(self):
        for period in ('本月', '当月', '本期间'):
            r = extract(body=f'截至2025年7月11日，公司{period}未实施股份回购。',
                        suffix='本次拟以集中竞价交易方式回购。')
            self.assertEqual('SOURCE_PENDING', r['status'])
            self.assertNotIn('cumulative_shares', r)
            self.assertIn('monthly_nonexecution_not_plan_cumulative_zero', r['pending_reasons'])

    def test_plan_context_is_distinct_from_planned_or_actual_quantity(self):
        text = f'证券代码：002133\n{PLAN}回购期限为自董事会审议通过回购方案之日起12个月。'
        source = dict(announcement_id='plan', symbol='002133', published_at='2025-04-04T00:00:00+08:00',
                      title='关于回购公司股份方案的公告', source_url='https://static.cninfo.com.cn/x.PDF',
                      pdf_sha256='a' * 64, text_sha256=hashlib.sha256(text.encode()).hexdigest())
        context = parse_buyback_plan_context(text, source)
        self.assertEqual('SOURCE_VERIFIED', context['status'])
        self.assertEqual('2026-04-02', context['plan_expiry_date'])
        self.assertNotIn('cumulative_shares', context)

    def test_cutoff_spelling_and_cross_page_number_do_not_destroy_actual_count(self):
        # CNINFO 1225434872 uses 截止. 1224838615 splits 集中竞价 across
        # pages with the printed page number 2 between the two word halves.
        self.assertEqual('SOURCE_VERIFIED', extract(body=OBS.replace('截至', '截止'))['status'])
        body = ('截至2025年7月11日，公司通过回购专用证券账户，以集中\n'
                '--- PAGE 2 ---\n2\n竞价方式实施回购公司股份，累计回购A股数量为243,895,500股。')
        self.assertEqual(243895500, extract(body=body)['cumulative_shares'])

    def test_monthly_amount_does_not_replace_explicit_cumulative_completed_purchase(self):
        # 1225456950 distinguishes July's 24,683,800 from the plan's 39,735,900.
        body = ('2025年7月，公司回购股份数量为24,683,800股。'
                '截至2025年7月11日，公司累计完成回购股份数量为39,735,900股。')
        r = extract(body=body, suffix='同意公司以集中竞价交易方式回购公司部分A股股份。')
        self.assertEqual(39735900, r['cumulative_shares'])
        self.assertEqual('active', r['plan_status'])
        self.assertEqual('SOURCE_PENDING', extract(body=body)['status'])

    def test_first_purchase_with_date_before_company_and_no_asof_clause(self):
        # 1225328992 reports first purchase directly on 2026-05-25.
        body = '2025年7月11日，公司首次通过回购专用账户以集中竞价方式回购公司股份1,816,380股。'
        r = extract(body=body, title='关于首次回购股份及回购进展的公告')
        self.assertEqual(1816380, r['cumulative_shares'])
        self.assertEqual('2025-07-11', r['first_execution_date'])
        self.assertEqual('SOURCE_VERIFIED', cumulative_execution_increment(r, [])['status'])

    def test_declared_phase_is_part_of_identity_and_excludes_old_phase_totals(self):
        # 1224992634/1224999320/1225019270 explicitly identify 第六期 shares.
        plan = PLAN.replace('方案的议案', '方案（第六期）的议案')
        body = OBS.replace('回购公司股份10063100', '累计回购（第六期）股份数6031900')
        r = extract(body=body, plan=plan, title='关于回购公司股份（第六期）事项的进展公告')
        self.assertEqual(6031900, r['cumulative_shares'])
        self.assertEqual('第六期', r['plan_phase'])
        self.assertTrue(r['plan_id'].endswith(':第六期'))
        old = body.replace('第六期', '第五期')
        self.assertEqual('SOURCE_PENDING', extract(body=body + old, plan=plan,
                          title='关于回购公司股份（第六期）事项的进展公告')['status'])
        self.assertIn('actual_cumulative_phase_not_explicit', extract(body=OBS, plan=plan,
                      title='关于回购公司股份（第六期）事项的进展公告')['pending_reasons'])

    def test_shareholder_scheme_after_twelve_months_uses_its_own_approval_date(self):
        plan = ('于2025年1月10日召开第十届董事会第五次会议、于2025年2月12日召开临时股东大会，'
                '审议通过了《关于回购公司股份方案的议案》。')
        r = extract(plan=plan, suffix='回购期限为公司临时股东大会审议通过本次回购股份方案后12个月内。')
        self.assertEqual('2025-02-12', r['plan_shareholder_resolution_date'])
        self.assertEqual('2026-02-11', r['plan_expiry_date'])

    def test_a_share_approval_cannot_borrow_later_h_share_approval(self):
        # 1224785936 contains A-share approval May20 and a separate H-share
        # programme approval June26. Only the A-share scope resolves its term.
        plan = ('第八届董事会第三十二次会议于2025年4月25日审议通过了《关于以集中竞价方式回购公司A股股份的议案》，'
                '并经2025年5月20日召开的临时股东大会及A股/H股类别股东会议审议通过。')
        suffix = ('实施期限自公司临时股东大会及A股/H股类别股东会议审议通过回购股份议案之日不超过12个月。'
                  '二、关于回购H股股份的进展情况。并经2025年6月26日召开的股东会审议通过。')
        r = extract(plan=plan, suffix=suffix)
        self.assertEqual('2025-05-20', r['plan_shareholder_resolution_date'])
        self.assertEqual('2026-05-19', r['plan_expiry_date'])

    def test_text_mutation_breaks_its_acquisition_binding(self):
        text = '证券代码：002133\n' + PLAN + OBS
        source = dict(announcement_id='id', symbol='002133', published_at='2025-07-17T00:00:00+08:00',
                      title='回购进展公告', source_url='https://static.cninfo.com.cn/x.PDF',
                      pdf_sha256='a' * 64, text_sha256=hashlib.sha256(text.encode()).hexdigest())
        self.assertIn('text_hash_mismatch', parse_buyback_disclosure(text + 'extra', source)['pending_reasons'])

    def test_explicit_first_market_purchase_has_observed_start(self):
        body = '公司于2025年7月28日首次以集中竞价交易方式实施了回购股份，回购股份数量2,682,700股。'
        r = extract(body=body, title='关于首次回购公司股份的公告', publication='2025-07-29T00:00:00+08:00')
        result = cumulative_execution_increment(r, [])
        self.assertEqual('SOURCE_VERIFIED', result['status'])
        self.assertEqual(2682700, result['increment_shares'])
        self.assertTrue(result['interval_start_inclusive'])
        self.assertEqual('2025-07-28', result['interval_start'])


class BuybackIncrementTests(unittest.TestCase):
    def test_first_monthly_observation_does_not_assume_zero_history(self):
        result = cumulative_execution_increment(extract(), [])
        self.assertEqual('no_strictly_earlier_same_plan_observation', result['reason'])

    def test_strict_prior_same_plan_difference_and_asof_interval(self):
        result = cumulative_execution_increment(extract(), [previous()])
        self.assertEqual(5063100, result['increment_shares'])
        self.assertEqual('2025-06-30', result['interval_start'])
        self.assertFalse(result['interval_start_inclusive'])
        self.assertEqual('2025-07-11', result['interval_end'])

    def test_latest_pending_observation_blocks_an_old_convenient_fallback(self):
        bad = previous(publication='2025-07-10T00:00:00+08:00', status='SOURCE_PENDING')
        result = cumulative_execution_increment(extract(), [previous(), bad])
        self.assertEqual('latest_prior_observation_pending', result['reason'])

    def test_future_report_is_ignored_without_looking_back(self):
        future = previous(publication='2025-08-01T00:00:00+08:00', count=999999999)
        original = cumulative_execution_increment(extract(), [previous()])
        self.assertEqual(original, cumulative_execution_increment(extract(), [previous(), future]))

    def test_same_day_reports_and_equal_asof_are_pending(self):
        p = previous(publication='2025-07-17T00:00:00+08:00')
        self.assertEqual('same_day_prior_observation_ambiguous', cumulative_execution_increment(extract(), [p])['reason'])
        self.assertEqual('report_asof_not_strictly_increasing',
                         cumulative_execution_increment(extract(), [previous(asof='2025-07-11')])['reason'])

    def test_other_plan_does_not_seed_current_plan_with_zero_or_old_count(self):
        p = previous(plan_id='002133:2024-04-03:board_auction_buyback')
        self.assertEqual('no_strictly_earlier_same_plan_observation', cumulative_execution_increment(extract(), [p])['reason'])

    def test_zero_increment_is_distinct_from_decreasing_or_unknown_count(self):
        result = cumulative_execution_increment(extract(), [previous(count=10063100)])
        self.assertEqual('SOURCE_VERIFIED', result['status'])
        self.assertEqual(0, result['increment_shares'])
        self.assertEqual('cumulative_count_decreased_or_restatement',
                         cumulative_execution_increment(extract(), [previous(count=20000000)])['reason'])

    def test_completed_plan_has_no_new_active_signal(self):
        r = extract(title='关于回购股份实施完毕暨实施结果的公告')
        self.assertEqual('completed', r['plan_status'])
        self.assertEqual('plan_inactive', cumulative_execution_increment(r, [previous()])['reason'])

    def test_completion_in_progress_body_stops_new_signal_without_using_future_end(self):
        # Exact completion predicate appears in cached 1225411010; its many
        # historical plans remain separately pending in full-document replay.
        r = extract(suffix='本次回购股份已完成。')
        self.assertEqual('completed', r['plan_status'])
        self.assertEqual('plan_inactive', cumulative_execution_increment(r, [previous()])['reason'])
        self.assertEqual('active', extract(suffix='本次回购已完成资金下限。')['plan_status'])
        self.assertEqual('active', extract(suffix='本次回购已完成证券账户开立。')['plan_status'])
        self.assertEqual('active', extract(suffix='若本次回购方案实施完毕，公司将另行公告。')['plan_status'])


if __name__ == '__main__':
    unittest.main()
