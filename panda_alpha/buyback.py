"""Narrow, evidence-carrying extraction of actual company auction buybacks.

Unsupported or ambiguous public disclosures remain pending. This module reads
neither prices nor returns and never invents a zero before a first observation.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
import calendar
from decimal import Decimal, InvalidOperation
import hashlib
import re
from typing import Iterable, Mapping


_DATE = r"(?P<year>20\d{2})年(?P<month>\d{1,2})月(?P<day>\d{1,2})日"
_PLAIN_DATE = r"20\d{2}年\d{1,2}月\d{1,2}日"
_ASOF = r"(?:截至|截止)"
_PHASE = r"第[一二三四五六七八九十\d]+期"
_PROPOSAL = r"(?P<proposal>[^》]{0,90}?(?:回购[^》]{0,12}?股份|股份回购)[^》]{0,70}?)"
_MEETING = r"第[一二三四五六七八九十百零〇\d]+届董事会第[一二三四五六七八九十百零〇\d]+次会议"
_NUMBER = r"(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?"
_ACTUAL = re.compile(
    _ASOF + _DATE + r"[^。；]{0,180}?集中竞价(?:交易)?(?:方式)?"
    r"[^。；]{0,60}?回购(?:了)?(?:[（(](?P<phase>" + _PHASE + r")[）)])?(?:公司)?(?:A股(?:股份)?|股份|股票)"
    r"(?:,(?:累计回购(?:A股|股份)|回购))?"
    r"(?:数量|数)?(?:为|共计|共|累计|达|合计|总计|了)?"
    r"(?P<amount>" + _NUMBER + r")(?P<unit>万股|股)"
)
_ZERO = re.compile(_ASOF + _DATE + r"[^。；]{0,70}?(?:尚未|未)(?:开始)?(?:实施|进行)(?:本次)?(?:股份)?回购")
_ZERO_DIRECT = re.compile(_ASOF + _DATE + r"[^。；]{0,70}?(?:尚未回购A股股份|暂未通过[^。；]{0,60}?集中竞价(?:交易)?方式回购公司股份)")
_PLAN = re.compile(
    r"(?:于)?" + _DATE + r",?召开(?:了|的)?[^。；]{0,110}?董事会"
    r"[^。；]{0,170}?审议通过(?:了)?《" + _PROPOSAL + r"》"
)
_PLAN_MEETING = re.compile(r"(?P<meeting>" + _MEETING + r")[^。；]{0,140}?审议通过(?:了)?《" + _PROPOSAL + r"》")
_PARALLEL_PLAN = re.compile(
    r"分别于" + _DATE + r"、(?P<shareholder_date>" + _PLAIN_DATE + r")召开(?:了)?"
    r"[^。；]{0,110}?董事会[^。；]{0,110}?股东(?:大)?会[^。；]{0,60}?审议通过(?:了)?《" + _PROPOSAL + r"》"
)
_FIRST_ACTUAL = re.compile(
    r"(?:于)?" + _DATE + r"(?:,公司)?首次(?:以|通过[^。；]{0,50}?以)集中竞价(?:交易)?(?:方式)?(?:实施了|实施)?"
    r"回购(?:公司)?股份(?:,回购股份数量)?"
    r"(?P<amount>" + _NUMBER + r")(?P<unit>万股|股)"
)
_FIRST_METHOD_BEFORE = re.compile(
    _DATE + r",公司通过集中竞价(?:交易)?方式,首次回购股份数量"
    r"(?P<amount>" + _NUMBER + r")(?P<unit>万股|股)"
)
_DIRECT_CUMULATIVE = re.compile(
    _ASOF + _DATE + r",公司(?:已)?累计(?:完成)?回购(?:公司)?(?:A股)?股份(?:数量)?(?:为)?"
    r"(?P<amount>" + _NUMBER + r")(?P<unit>万股|股)"
)


def _date(match: re.Match) -> str:
    return date(int(match['year']), int(match['month']), int(match['day'])).isoformat()


def _expiry(compact: str, board_day: str | None, shareholder_day: str | None) -> dict:
    """Use explicit printed end dates or a certified date/basis/month clause."""
    term = r"(?:回购(?:股份|方案)?(?:的)?(?:实施)?期限|实施期限)"
    ranges = list(re.finditer(
        term + r"[^。；]{0,65}?"
        r"(?P<start>" + _PLAIN_DATE + r")[^。；]{0,16}?(?:至|~|～|-)(?P<end>" + _PLAIN_DATE + r")", compact))
    try:
        ends = {_date(re.fullmatch(_DATE, m['end'])) for m in ranges}
    except ValueError:
        return dict(plan_expiry_date=None, expiry_status='SOURCE_PENDING', expiry_reason='invalid_explicit_expiry')
    if len(ends) == 1:
        return dict(plan_expiry_date=next(iter(ends)), expiry_status='SOURCE_VERIFIED',
                    expiry_basis='explicit_public_end_date', expiry_evidence=[m.group(0) for m in ranges])
    if len(ends) > 1:
        return dict(plan_expiry_date=None, expiry_status='SOURCE_PENDING', expiry_reason='conflicting_public_end_dates')
    clauses = list(re.finditer(r"(?:" + term + r"[^。；]{0,35}?)?自(?P<basis>[^。；]{0,140}?)"
                              r"(?:之日起|之日)(?:不超过)?(?P<months>\d{1,2}|十二|三|六)个?月", compact))
    clauses += list(re.finditer(term + r"为(?P<basis>[^。；]{0,140}?(?:董事会|股东)[^。；]{0,70}?审议通过[^。；]{0,30}?)"
                               r"后(?P<months>\d{1,2}|十二|三|六)个?月", compact))
    resolved = []
    for m in clauses:
        basis = m['basis']
        if '回购' not in basis and not re.match(term, m.group(0)):
            continue
        start = shareholder_day if '股东' in basis else board_day if '董事会' in basis else None
        if start:
            initial = date.fromisoformat(start)
            months = {'十二': 12, '三': 3, '六': 6}.get(m['months'])
            months = int(m['months']) if months is None else months
            offset = initial.year * 12 + initial.month - 1 + months
            year, month0 = divmod(offset, 12)
            anniversary = date(year, month0 + 1, min(initial.day, calendar.monthrange(year, month0 + 1)[1]))
            resolved.append(((anniversary - timedelta(days=1)).isoformat(), m.group(0)))
    if len({r[0] for r in resolved}) == 1:
        return dict(plan_expiry_date=resolved[0][0], expiry_status='SOURCE_VERIFIED',
                    expiry_basis='conservative_anniversary_minus_one_day_from_explicit_approval_month_clause',
                    expiry_evidence=[r[1] for r in resolved])
    return dict(plan_expiry_date=None, expiry_status='SOURCE_PENDING', expiry_reason='expiry_or_approval_basis_missing')


def _printed_shares(amount: str, unit: str) -> tuple[int, int]:
    """Return printed nominal shares and its quantum; rounded counts are not exact."""
    value = Decimal(amount.replace(',', ''))
    multiplier = 10000 if unit == '万股' else 1
    shares = value * multiplier
    quantum = Decimal(10) ** value.as_tuple().exponent * multiplier
    if not value.is_finite() or value < 0 or shares != shares.to_integral_value():
        raise ValueError('Invalid integer share amount')
    return int(shares), max(1, int(quantum))


def _body_plan_status(compact: str) -> tuple[str | None, list[str]]:
    patterns = {
        'terminated': (r'本次回购(?:公司)?(?:股份)?(?:方案|计划)?(?:已)?(?:终止|停止实施)(?=[。；,]|$)',),
        'completed': (r'本次回购(?:公司)?(?:股份)?(?:方案|计划)?(?:已)?(?:实施完毕|实施完成|已完成)(?=[。；,]|$)',),
    }
    found = []
    for status, expressions in patterns.items():
        for expression in expressions:
            for match in re.finditer(expression, compact):
                sentence_start = max(compact.rfind('。', 0, match.start()), compact.rfind('；', 0, match.start())) + 1
                prefix = compact[sentence_start:match.start()]
                if not re.search(r'若|如果|如公司|待|拟|将|尚未|未能', prefix):
                    found.append((status, match.group(0)))
    statuses = {s for s, _ in found}
    if len(statuses) == 1:
        return next(iter(statuses)), [e for _, e in found]
    if len(statuses) > 1:
        return 'ambiguous', [e for _, e in found]
    return None, []


def parse_buyback_disclosure(text: str, source: Mapping) -> dict:
    """Extract supported actual-execution statements with source spans.

    ``source`` is a verified acquisition receipt, not guessed PDF metadata. It
    supplies announcement_id, symbol, published_at, title, source_url and both
    SHA256 hashes. Publication metadata is used conservatively instead of the
    (occasionally earlier) document's printed signing date.
    """
    result = {k: source.get(k) for k in (
        'announcement_id', 'symbol', 'published_at', 'title', 'source_url',
        'pdf_sha256', 'text_sha256')}
    result.update(status='SOURCE_PENDING', pending_reasons=[], evidence={})

    def pending(reason):
        result['pending_reasons'].append(reason)
        return result

    if any(not result[k] for k in result if k not in ('status', 'pending_reasons', 'evidence')):
        return pending('incomplete_acquisition_receipt')
    if any(not re.fullmatch(r'[0-9a-f]{64}', str(result[k]))
           for k in ('pdf_sha256', 'text_sha256')):
        return pending('invalid_source_hash')
    if hashlib.sha256(text.encode('utf-8')).hexdigest() != result['text_sha256']:
        return pending('text_hash_mismatch')
    try:
        publication = datetime.fromisoformat(str(result['published_at']))
        if publication.tzinfo is None:
            return pending('publication_timezone_missing')
        publication_day = publication.date().isoformat()
    except (TypeError, ValueError):
        return pending('invalid_publication_time')
    # Source extraction page markers may be followed by a printed page number.
    # Remove that exact page-number line, not arbitrary numbers inside data.
    compact = re.sub(r'---\s*PAGE\s*(\d+)\s*---[\r\n]+[ \t]*(?:-?\1-?|\1/\d{1,2})[ \t]*(?=[\r\n])', '', text)
    compact = re.sub(r'---\s*PAGE\s*\d+\s*---', '', compact)
    compact = re.sub(r'\s+', '', compact).replace('，', ',')
    codes = set(re.findall(r'(?:证券|股票)代码[：:](\d{6})(?!\d)', compact))
    permitted = {str(result['symbol'])}
    if str(result['symbol']).startswith('00'):
        permitted.add('2' + str(result['symbol'])[1:])  # same issuer's B-share header
    if str(result['symbol']) not in codes or not codes <= permitted:
        return pending('issuer_code_not_certified')
    title = re.sub(r'\s+', '', str(result['title']))
    if '回购' not in title:
        return pending('not_company_buyback_disclosure')
    if '回购权' in title or '减持' in title or '出售' in title:
        return pending('not_current_company_market_purchase')
    if re.search(r'限制性(?:股票|股份)|回购注销(?:部分|已授予)', title):
        return pending('granted_restricted_share_redemption')
    if '更正' in title or '修订' in title:
        return pending('correction_requires_public_version_chain')
    if re.search(r'前次回购|前期回购|上次回购|两(?:个|次)(?:股份)?回购(?:计划|方案)', compact):
        return pending('multiple_or_previous_plan_requires_manual_separation')
    phase_labels = set(re.findall(r'第[一二三四五六七八九十\d]+期(?=(?:股份)?回购)', compact))
    if len(phase_labels) > 1:
        return pending('multiple_buyback_phases_require_manual_separation')
    if re.search(r'累计(?:已)?回购[^。；]{0,40}(?:调整为|重述|送股|转增)|'
                 r'(?:每10股|每十股)[^。；]{0,30}(?:送股|转增)', compact):
        return pending('share_count_restatement_requires_version_chain')

    original = lambda m: not re.search(r'调整|变更|增加|延长', m['proposal'])
    plans = [m for m in _PLAN.finditer(compact) if original(m)]
    parallel = [m for m in _PARALLEL_PLAN.finditer(compact) if original(m)]
    # In "respectively DATE1, DATE2 ... board, shareholder meeting", DATE2
    # immediately precedes "convened" but belongs to the shareholder meeting.
    plans = [m for m in plans if not any(p.start() <= m.start() and m.end() <= p.end() for p in parallel)]
    meetings = [m for m in _PLAN_MEETING.finditer(compact) if original(m)]
    if len({m['proposal'] for m in plans + parallel + meetings}) > 1:
        return pending('multiple_original_proposals_require_manual_separation')
    try:
        plan_dates = {_date(m) for m in plans + parallel}
        # A reversed "board meeting on DATE approved proposal" has the date
        # inside, rather than before, the board-meeting clause.
        for m in meetings:
            inside = list(re.finditer(_DATE, m.group(0)))
            if len(inside) == 1 and '股东' not in m.group(0):
                plan_dates.add(_date(inside[0]))
    except ValueError:
        return pending('invalid_plan_resolution_date')
    meeting_ids = {m['meeting'] for m in meetings}
    if len(plan_dates) > 1 or len(meeting_ids) > 1 or (not plan_dates and not meeting_ids):
        return pending('plan_resolution_ambiguous_or_missing')
    plan_day = next(iter(plan_dates)) if plan_dates else None
    if plan_day and plan_day > publication_day:
        return pending('plan_resolution_after_publication')
    result['plan_resolution_date'] = plan_day
    identity = next(iter(meeting_ids)) if meeting_ids else plan_day
    result['plan_id'] = f"{result['symbol']}:{identity}:board_auction_buyback"
    plan_phases = set(re.findall(_PHASE, title))
    plan_phases.update(phase for m in plans + parallel + meetings for phase in re.findall(_PHASE, m['proposal']))
    if len(plan_phases) > 1:
        return pending('multiple_current_plan_phases_ambiguous')
    current_phase = next(iter(plan_phases)) if plan_phases else None
    if current_phase:
        result['plan_phase'] = current_phase
        result['plan_id'] += ':' + current_phase
    result['evidence']['plan'] = list(dict.fromkeys(m.group(0) for m in plans + parallel + meetings))
    shareholder_days = set()
    for m in plans:
        if '股东' in m.group(0):
            before_shareholder = m.group(0).split('股东')[0]
            dates = list(re.finditer(_DATE, before_shareholder))
            if len(dates) >= 2:
                shareholder_days.add(_date(dates[-1]))
    shareholder_days.update(_date(re.fullmatch(_DATE, m['shareholder_date'])) for m in parallel)
    # A-share annual/category approval after the board proposal is a distinct
    # dated assertion. Do not borrow the following H-share programme's date.
    a_scope = re.split(r'二、关于回购H股', compact, maxsplit=1)[0]
    for m in re.finditer(r'并经' + _DATE + r'召开(?:的)?[^。；]{0,170}?股东[^。；]{0,100}?审议通过', a_scope):
        shareholder_days.add(_date(m))
    shareholder_day = next(iter(shareholder_days)) if len(shareholder_days) == 1 else None
    if shareholder_day and shareholder_day > publication_day:
        shareholder_day = None  # a scheduled future meeting is not an approval
    result['plan_shareholder_resolution_date'] = shareholder_day
    result.update(_expiry(a_scope, plan_day, shareholder_day))
    # A cited original plan can certify these context fields even when it
    # contains no actual execution count. Cross-document recovery still needs
    # an explicit public citation link and a strictly earlier publication.
    result['plan_context_status'] = 'SOURCE_VERIFIED'

    actual = list(_ACTUAL.finditer(compact))
    first_actual = list(_FIRST_ACTUAL.finditer(compact)) + list(_FIRST_METHOD_BEFORE.finditer(compact)) if '首次回购' in title else []
    ordinary_plan_method = bool(re.search(r'(?:同意公司|公司(?:拟|将)?)[^。；]{0,80}?(?:以|通过)集中竞价[^。；]{0,45}?回购', a_scope))
    direct = list(_DIRECT_CUMULATIVE.finditer(compact)) if ordinary_plan_method and not current_phase else []
    observations = []
    for m in actual + first_actual + direct:
        if any(word in m.group(0) for word in ('拟', '预计', '计划回购', '计划通过', '将通过', '将回购')):
            continue
        observed_phase = m.groupdict().get('phase')
        if observed_phase and observed_phase != current_phase:
            return pending('actual_phase_does_not_match_current_plan')
        if current_phase and m in actual and not observed_phase:
            return pending('actual_cumulative_phase_not_explicit')
        try:
            shares, quantum = _printed_shares(m['amount'], m['unit'])
            observations.append((_date(m), shares, quantum, m.group(0)))
        except (ValueError, InvalidOperation):
            return pending('invalid_reported_share_amount_or_asof_date')
    if not observations:
        zero = list(_ZERO.finditer(compact)) + list(_ZERO_DIRECT.finditer(compact))
        monthly_only = [m for m in zero if re.search(
            r'本月|当月|月份|本(?:报告)?期间|本期',
            re.sub(r'^' + _ASOF + _PLAIN_DATE, '', m.group(0)))]
        zero = [m for m in zero if m not in monthly_only]
        if monthly_only and not zero:
            result['evidence']['monthly_nonexecution'] = [m.group(0) for m in monthly_only]
            return pending('monthly_nonexecution_not_plan_cumulative_zero')
        try:
            if len({_date(m) for m in zero}) == 1 and '集中竞价' in compact:
                observations = [(_date(zero[0]), 0, 1, zero[0].group(0))]
        except ValueError:
            return pending('invalid_zero_execution_asof_date')
    values = {(r[0], r[1], r[2]) for r in observations}
    if len(values) != 1:
        return pending('actual_cumulative_count_ambiguous_or_missing')
    report_day, shares, quantum = next(iter(values))
    result.update(report_asof_date=report_day, cumulative_shares=shares,
                  printed_share_quantum=quantum, execution_method='concentrated_auction',
                  ordinary_company_market_purchase=True)
    result['evidence']['actual'] = [r[3] for r in observations]
    if (plan_day and plan_day > report_day) or report_day > publication_day:
        return pending('inconsistent_plan_asof_publication_dates')
    if quantum > 1:
        return pending('rounded_cumulative_shares_not_exact')

    # Empty checkboxes list possible uses; they are not the selected purpose.
    purpose_text = re.sub(r'\s+', '', re.sub(r'□[^\n]*', '', text))
    result['purpose'] = []
    for label, words in (
        ('future_employee_or_incentive_use', ('用于员工持股', '用于公司员工持股', '用于股权激励', '实施股权激励')),
        ('convertible_bond_conversion', ('可转换公司债券转股', '用于转换公司可转债')),
        ('capital_reduction', ('用于减少注册资本', '注销并减少注册资本')),
    ):
        if any(word in purpose_text for word in words):
            result['purpose'].append(label)
    if re.search(r'终止|停止实施', title):
        result['plan_status'] = 'terminated'
    elif re.search(r'完成|实施完毕|实施结果', title):
        result['plan_status'] = 'completed'
    elif re.search(r'进展|进度|首次回购', title):
        result['plan_status'] = 'active'
    else:
        return pending('plan_status_not_certified')
    body_status, status_evidence = _body_plan_status(compact)
    if body_status == 'ambiguous' or (body_status and result['plan_status'] not in ('active', body_status)):
        return pending('conflicting_public_plan_status')
    if body_status:
        result['plan_status'] = body_status
        result['evidence']['plan_status'] = status_evidence
    if first_actual:
        if len({_date(m) for m in first_actual}) != 1:
            return pending('first_execution_date_ambiguous')
        first_day = _date(first_actual[0])
        if (plan_day and plan_day > first_day) or first_day > report_day:
            return pending('first_execution_date_inconsistent')
        result['first_execution_date'] = first_day
        result['evidence']['first_execution'] = [m.group(0) for m in first_actual]
    result['status'] = 'SOURCE_VERIFIED'
    return result


def parse_buyback_plan_context(text: str, source: Mapping) -> dict:
    """Extract cited-plan context without pretending an intention is execution.

    SOURCE_VERIFIED here certifies the uniquely parsed plan identity only.
    ``expiry_status`` remains a separate gate. The caller must certify the
    current announcement's literal citation link, plan correspondence and
    strictly earlier public time before attaching this context to an event.
    """
    parsed = parse_buyback_disclosure(text, source)
    keys = ('announcement_id', 'symbol', 'published_at', 'title', 'source_url',
            'pdf_sha256', 'text_sha256', 'plan_id', 'plan_resolution_date',
            'plan_shareholder_resolution_date', 'plan_expiry_date', 'expiry_status',
            'expiry_basis', 'expiry_reason', 'expiry_evidence')
    output = {k: parsed[k] for k in keys if k in parsed}
    output['evidence'] = {'plan': parsed.get('evidence', {}).get('plan', [])}
    output['status'] = parsed.get('plan_context_status', 'SOURCE_PENDING')
    output['context_only_no_actual_share_count'] = True
    if output['status'] != 'SOURCE_VERIFIED':
        output['pending_reasons'] = parsed['pending_reasons']
    return output


def cumulative_execution_increment(current: Mapping, history: Iterable[Mapping]) -> dict:
    """Difference the latest strictly earlier known observation of the same plan.

    A newer pending disclosure blocks an older convenient fallback. Same-day
    competing observations cannot be ordered from date-only publication records.
    Completed/terminated reports certify closure, never an active new signal.
    """
    output = {'status': 'SOURCE_PENDING', 'current_id': current.get('announcement_id')}
    if current.get('status') != 'SOURCE_VERIFIED':
        return dict(output, reason='current_source_pending')
    if current.get('plan_status') != 'active':
        return dict(output, reason='plan_inactive', plan_status=current.get('plan_status'))
    publication_day = str(current['published_at'])[:10]
    peers = [r for r in history if r.get('symbol') == current['symbol']
             and r.get('announcement_id') != current['announcement_id']
             and str(r.get('published_at', ''))[:10] <= publication_day
             and (r.get('plan_id') == current['plan_id'] or not r.get('plan_id'))]
    if any(str(r['published_at'])[:10] == publication_day for r in peers):
        return dict(output, reason='same_day_prior_observation_ambiguous')
    if not peers:
        if current.get('first_execution_date'):
            return dict(output, status='SOURCE_VERIFIED', increment_shares=current['cumulative_shares'],
                        interval_start=current['first_execution_date'], interval_end=current['report_asof_date'],
                        interval_start_inclusive=True, comparator='explicit_first_execution_zero_before_first_buy')
        return dict(output, reason='no_strictly_earlier_same_plan_observation')
    latest_day = max(str(r['published_at'])[:10] for r in peers)
    latest = [r for r in peers if str(r['published_at'])[:10] == latest_day]
    if any(r.get('status') != 'SOURCE_VERIFIED' or r.get('plan_id') != current['plan_id'] for r in latest):
        return dict(output, reason='latest_prior_observation_pending')
    prior_values = {(r.get('report_asof_date'), r.get('cumulative_shares'), r.get('plan_status')) for r in latest}
    if len(prior_values) != 1:
        return dict(output, reason='competing_latest_prior_observations')
    prior_day, prior_count, prior_status = next(iter(prior_values))
    if prior_status != 'active':
        return dict(output, reason='same_plan_already_inactive')
    if prior_day >= current['report_asof_date']:
        return dict(output, reason='report_asof_not_strictly_increasing')
    if current['cumulative_shares'] < prior_count:
        return dict(output, reason='cumulative_count_decreased_or_restatement')
    return dict(output, status='SOURCE_VERIFIED', previous_ids=[r['announcement_id'] for r in latest],
                increment_shares=current['cumulative_shares'] - prior_count,
                interval_start=prior_day, interval_end=current['report_asof_date'],
                interval_start_inclusive=False, comparator='strictly_earlier_same_plan_cumulative')
