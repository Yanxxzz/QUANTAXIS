"""Evidence-led next-open execution audit, separate from research return proxies.

Daily OHLC can disprove an assumed fill but cannot establish our auction queue
position or fill quantity. A missing receipt therefore remains pending even on
an ordinary, liquid trading day. Suspended marks never create a tradable exit.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass
from decimal import Decimal, ROUND_HALF_UP
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Iterable, Mapping
from urllib.parse import urlparse

import numpy as np
import pandas as pd

from .diversity import as_factor_panel


@dataclass(frozen=True)
class ExecutionPolicy:
    cycle: int = 5
    groups: int = 10
    direction: int = 1
    one_way_cost: float = 0.003
    min_assets: int = 30

    def __post_init__(self):
        if not 1 <= self.cycle <= 10 or not 2 <= self.groups <= 10 or self.direction not in (0, 1):
            raise ValueError("Invalid frozen cycle/group/direction")
        if self.min_assets < self.groups or not math.isfinite(self.one_way_cost) or not 0 <= self.one_way_cost < 1:
            raise ValueError("Invalid execution coverage or transaction cost")


@dataclass(frozen=True)
class RuleEvidence:
    rule_id: str
    board: str
    effective_start: str
    effective_end: str
    normal_limit: float
    st_limit: float
    source_url: str
    source_section: str
    reviewed_on: str
    ipo_unlimited_sessions: int | None = None
    tick_size: float = 0.01
    relisting_first_day_unlimited: bool | None = None
    delisting_first_day_unlimited: bool | None = None
    evidence_status: str = "primary_reviewed"

    def to_dict(self):
        result = asdict(self)
        result["reviewed_summary_sha256"] = hashlib.sha256(
            json.dumps(result, sort_keys=True).encode()).hexdigest()
        result["hash_scope"] = "reviewed rule summary, not the original exchange document bytes"
        return result

    def valid_source(self) -> bool:
        host = (urlparse(self.source_url).hostname or "").lower()
        domain = {"sse_main": "sse.com.cn", "star": "sse.com.cn", "szse_main": "szse.cn",
                  "chinext": "szse.cn", "bse": "bse.cn"}.get(self.board)
        official = domain is not None and (host == domain or host.endswith("." + domain))
        return (official and self.evidence_status == "primary_reviewed" and bool(self.source_section)
                and bool(self.reviewed_on) and 0 < self.normal_limit < 1 and 0 < self.st_limit < 1
                and self.tick_size == 0.01)


class TradingRuleBook:
    def __init__(self, rules: Iterable[RuleEvidence] = ()):
        self.rules = list(rules)

    def resolve(self, row: Mapping[str, Any]) -> dict[str, Any]:
        date = _day(row.get("date"))
        board = row.get("board")
        if row.get("security_status_verified") is not True:
            return _pending("Point-in-time board/ST/IPO/relisting status has not been verified")
        matches = [r for r in self.rules if r.board == board and r.effective_start <= date <= r.effective_end and r.valid_source()]
        if len(matches) != 1:
            return _pending("Exactly one applicable, primary-source trading rule is required")
        rule = matches[0]
        st = _binary(row.get("is_st", row.get("isST")))
        if st is None:
            return _pending("Point-in-time ST status is missing; do not infer it from today's name")
        state = row.get("listing_state")
        unlimited = False
        if state == "seasoned":
            pass
        elif state == "ipo":
            session = row.get("listing_session_number")
            if type(session) is not int or session < 1 or rule.ipo_unlimited_sessions is None:
                return _pending("IPO session count or this version's IPO exemption is unverified")
            unlimited = session <= rule.ipo_unlimited_sessions
        elif state == "relisting_first_day" and rule.relisting_first_day_unlimited is True:
            unlimited = True
        elif state == "delisting_first_day" and rule.delisting_first_day_unlimited is True:
            unlimited = True
        else:
            return _pending("Special listing/retirement regime is unverified")
        ratio = rule.st_limit if st else rule.normal_limit
        if unlimited:
            return {"status": "verified", "unlimited": True, "upper_limit": None, "lower_limit": None,
                    "rule": rule.to_dict(), "ratio": None}
        reference = _number(row.get("raw_preclose", row.get("preclose")))
        if reference is None or reference <= 0 or row.get("limit_reference_verified") is not True:
            return _pending("Verified raw exchange reference price is required; adjusted close is not a limit reference")
        tick, price, fraction = Decimal(str(rule.tick_size)), Decimal(str(reference)), Decimal(str(ratio))
        raw_upper, raw_lower = price * (1 + fraction), price * (1 - fraction)
        upper = raw_upper.quantize(tick, rounding=ROUND_HALF_UP)
        lower = raw_lower.quantize(tick, rounding=ROUND_HALF_UP)
        if rule.board == "bse":
            # The reviewed BSE clauses establish 30% and the quotation tick,
            # but do not establish rounding at fractional ticks. Do not copy
            # SSE/SZSE's minimum-one-tick adjustment into a different market.
            if raw_upper != upper or raw_lower != lower or lower < tick:
                return _pending("BSE fractional-tick limit rounding requires independently verified exchange limit prices/rule")
        else:
            # SSE 2026 3.3.17; SZSE 2023/2026 3.3.19.
            upper = max(upper, price + tick)
            lower = max(tick, min(lower, price - tick))
        return {"status": "verified", "unlimited": False, "upper_limit": float(upper), "lower_limit": float(lower),
                "rule": rule.to_dict(), "ratio": ratio}


def reviewed_2026_rulebook() -> TradingRuleBook:
    """Primary-reviewed rules scoped to the requested 2026-06-18..09-18 study.

    This snapshot is deliberately bounded, not a guessed historical rule engine.
    IPO/relisting exemptions not established in the reviewed version stay None.
    SSE and SZSE main-board ST limits changed on 2026-07-06, inside this study.
    """
    sse_old = "https://star.sse.com.cn/aboutus/mediacenter/hotandd/c/c_20260424_10816474.shtml"
    sse_new = "https://www.sse.com.cn/lawandrules/sselawsrules2025/fund/trading/c/c_20260424_10817739.shtml"
    sz_old = "https://docs.static.szse.cn/www/disclosure/notice/W020201231711823519859.pdf"
    sz_new = "https://docs.static.szse.cn/www/lawrules/rule/trade/current/W020260424690713155663.pdf"
    star_old = "https://www.sse.com.cn/aboutus/mediacenter/hotandd/c/c_20190719_4866745.shtml"
    bse_old = "https://www.bse.cn/uploads/6/file/public/202109/20210909101516_y5wn2y3ft9.pdf"
    bse_new = "https://www.bse.cn/jygl_list/200028217.html"
    specifications = [
        ("sse_main_pre_20260706", "sse_main", "2026-06-18", "2026-07-05", .10, .05, sse_old, "2026 revision notice: prior ST5%, effective change 2026-07-06", None, None, None),
        ("sse_main_20260706", "sse_main", "2026-07-06", "2026-09-18", .10, .10, sse_new, "3.3 price limits and risk warning board; revision effective 2026-07-06", 5, True, True),
        ("szse_main_pre_20260706", "szse_main", "2026-06-18", "2026-07-05", .10, .05, sz_old, "2020 revision explanation, ST and ChiNext exceptions", None, None, None),
        ("szse_main_20260706", "szse_main", "2026-07-06", "2026-09-18", .10, .10, sz_new, "3.3.13-3.3.19 and 4.5; effective 2026-07-06", 5, True, True),
        ("star_pre_20260706", "star", "2026-06-18", "2026-07-05", .20, .20, star_old, "STAR initial trading mechanisms; five IPO sessions,20% thereafter", 5, None, None),
        ("star_20260706", "star", "2026-07-06", "2026-09-18", .20, .20, sse_new, "6.6,6.14; STAR remains outside main-board risk warning board", 5, None, True),
        ("chinext_pre_20260706", "chinext", "2026-06-18", "2026-07-05", .20, .20, sz_old, "2020 revision explanation: ChiNext ST20%; IPO exemption requires separate evidence", None, None, None),
        ("chinext_20260706", "chinext", "2026-07-06", "2026-09-18", .20, .20, sz_new, "3.3.13,3.3.15,3.3.19", 5, True, True),
        ("bse_pre_20260706", "bse", "2026-06-18", "2026-07-05", .30, .30, bse_old, "3.3.11-3.3.12", 1, None, None),
        ("bse_20260706", "bse", "2026-07-06", "2026-09-18", .30, .30, bse_new, "3.3.10-3.3.12; public IPO first day and delisting first day", 1, None, True),
    ]
    return TradingRuleBook(RuleEvidence(*item[:8], reviewed_on="2026-10-05",
                                        ipo_unlimited_sessions=item[8], relisting_first_day_unlimited=item[9],
                                        delisting_first_day_unlimited=item[10]) for item in specifications)


def _number(value) -> float | None:
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except (ValueError, TypeError):
        return None


def _binary(value) -> bool | None:
    if not isinstance(value, (str, bool, int, float, np.integer, np.floating, np.bool_)):
        return None
    if value in (0, "0", False):
        return False
    if value in (1, "1", True):
        return True
    return None


def _day(value) -> str:
    if isinstance(value, (int, np.integer)):
        date = pd.to_datetime(str(value), format="%Y%m%d", errors="coerce")
    else:
        date = pd.to_datetime(value, errors="coerce")
    if pd.isna(date):
        raise ValueError("A valid trading date is required")
    return date.strftime("%Y-%m-%d")


def _pending(reason: str) -> dict[str, Any]:
    return {"status": "pending", "reason": reason}


def _verified_fill(receipt: Mapping[str, Any] | None, date: str, symbol: str, side: str,
                   open_price: float, quantity: float | None) -> dict[str, Any]:
    if receipt is None:
        return _pending("Daily bars do not prove opening-auction fill price, queue position or quantity")
    try:
        receipt_date = _day(receipt.get("date"))
    except ValueError:
        return _pending("Receipt date is missing or invalid")
    if (receipt.get("verified") is not True or receipt.get("source_kind") not in {"broker_execution", "exchange_execution"}
            or receipt.get("phase") != "opening_auction" or receipt.get("symbol") != symbol
            or receipt_date != date or receipt.get("side") != side):
        return _pending("A verified matching opening-auction execution receipt is required")
    path, expected = receipt.get("evidence_path"), receipt.get("evidence_sha256")
    if not path or not expected or not Path(path).is_file():
        return _pending("Execution receipt artifact and SHA256 are required")
    try:
        content = Path(path).read_bytes()
    except OSError:
        return _pending("Execution receipt artifact cannot be read")
    if hashlib.sha256(content).hexdigest() != expected:
        return _pending("Execution receipt artifact SHA256 mismatch")
    try:
        parsed = json.loads(content)
        records = parsed.get("fills", [parsed]) if isinstance(parsed, dict) else parsed
        matching = any(isinstance(record, dict) and record.get("symbol") == symbol and record.get("side") == side
                       and _day(record.get("date")) == date and record.get("phase") == "opening_auction"
                       and _number(record.get("price")) == _number(receipt.get("price"))
                       and _number(record.get("quantity")) == _number(receipt.get("quantity")) for record in records)
    except (ValueError, TypeError):
        matching = False
    if not matching:
        return _pending("Hashed receipt artifact does not contain the claimed matching fill")
    price, amount = _number(receipt.get("price")), _number(receipt.get("quantity"))
    if price is None or amount is None or amount <= 0 or abs(price - open_price) > .000001:
        return _pending("Receipt fill must match the raw opening price and state a positive quantity")
    if quantity is not None and amount != quantity:
        return _pending("Partial/unmatched quantity cannot certify the requested full fill")
    return {"status": "confirmed", "price": price, "quantity": amount,
            "evidence_path": str(Path(path).resolve()), "evidence_sha256": expected}


def assess_next_open_order(row: Mapping[str, Any] | None, side: str, rulebook: TradingRuleBook,
                           fill_receipt: Mapping[str, Any] | None = None,
                           quantity: float | None = None, one_way_cost: float = .003) -> dict[str, Any]:
    if side not in {"buy", "sell"}:
        raise ValueError("side must be buy or sell")
    if not math.isfinite(one_way_cost) or not 0 <= one_way_cost < 1:
        raise ValueError("Invalid one-way execution cost assumption")
    if row is None:
        return _pending("Missing stock-day: source loss, suspension and delisting must be distinguished")
    date, symbol = _day(row.get("date")), str(row.get("symbol", row.get("code", "")))
    base = {"date": date, "symbol": symbol, "side": side, "requested_quantity": quantity,
            "economic_status": "pending"}
    if row.get("listing_state") == "delisted":
        return {**base, **_pending("Delisted terminal settlement/exit is unresolved; do not impute a zero return")}
    traded = _binary(row.get("trade_status", row.get("tradestatus")))
    if traded is False:
        return {**base, "status": "blocked", "reason": "Known suspension prevents opening-auction execution", "fill_price": None}
    if traded is None:
        return {**base, **_pending("Trading/suspension status is unverified")}
    opening = _number(row.get("raw_open"))
    high, low, close = (_number(row.get("raw_" + field)) for field in ("high", "low", "close"))
    if any(value is None or value <= 0 for value in (opening, high, low, close)) or not low <= min(opening, close) <= max(opening, close) <= high:
        return {**base, **_pending("Raw positive OHLC and consistent opening range are required")}
    volume = _number(row.get("volume"))
    if volume is None or volume <= 0:
        return {**base, **_pending("No verified traded volume; a daily quote cannot establish execution")}
    limits = rulebook.resolve(row)
    if limits["status"] != "verified":
        return {**base, **limits}
    upper, lower = limits["upper_limit"], limits["lower_limit"]
    if not limits["unlimited"] and (opening > upper + .000001 or opening < lower - .000001):
        return {**base, "status": "blocked", "reason": "Raw open is outside verified daily exchange price limits",
                "limits": limits, "fill_price": None}
    fill = _verified_fill(fill_receipt, date, symbol, side, opening, quantity)
    queue_constrained = not limits["unlimited"] and ((side == "buy" and opening >= upper - .000001)
                                                    or (side == "sell" and opening <= lower + .000001))
    result = {**base, **fill, "limits": limits, "queue_constrained": queue_constrained,
              "daily_bar_price_admissible": True, "fill_price": fill.get("price")}
    if fill["status"] == "pending" and queue_constrained:
        result["reason"] = "Limit-side opening auction queue is unresolved; later daily high/low cannot establish our fill"
    if fill["status"] == "confirmed":
        result["cost_stress_estimate"] = fill["price"] * fill["quantity"] * one_way_cost
        result["cost_status"] = "research stress assumption; actual fee receipt remains separate"
    return result


def mark_position(row: Mapping[str, Any] | None, session_date: str,
                  previous_close: float | None = None, previous_close_date: str | None = None) -> dict[str, Any]:
    date = _day(session_date)
    if row is None:
        return _pending("Missing stock-day cannot be silently marked as suspension or a zero return")
    if _day(row.get("date")) != date:
        return _pending("Valuation row belongs to a different session; future marks are forbidden")
    if row.get("listing_state") == "delisted":
        return _pending("Delisted terminal cashflow and position recovery are unresolved")
    traded = _binary(row.get("trade_status", row.get("tradestatus")))
    if traded is False:
        previous = _number(previous_close)
        if previous is None or previous <= 0 or previous_close_date is None or _day(previous_close_date) >= date:
            return _pending("Suspended valuation requires an already known earlier trading close")
        return {"status": "marked_stale", "mark": previous, "mark_source_date": _day(previous_close_date),
                "executable_exit": False, "reason": "Carried valuation only; suspension has no executable exit",
                "corporate_action_cashflow_status": "pending_unless_independently_reconciled"}
    close = _number(row.get("raw_close"))
    if traded is not True or close is None or close <= 0:
        return _pending("No valid closing mark; do not replace missing values with zero")
    return {"status": "marked", "mark": close, "mark_source_date": date, "executable_exit": False}


def audit_next_open(frame: pd.DataFrame, signal_values: pd.DataFrame | pd.Series,
                    rulebook: TradingRuleBook, policy: ExecutionPolicy | None = None,
                    calendar: Iterable[str] | None = None,
                    fill_receipts: Mapping[tuple[str, str, str], Mapping[str, Any]] | None = None) -> dict[str, Any]:
    """Audit intended equal-weight decile transitions; never infer actual holdings.

    Sell orders refer to the preceding *intended* cohort, not assumed fills. The
    report contains no NAV/Sharpe because holdings, cashflows and actual fees need
    an independent reconciled execution ledger. Signals stay on t; orders use t+1.
    """
    policy = policy or ExecutionPolicy()
    if calendar is None:
        return {"status": "pending", "reason": "Explicit verified exchange calendar is required", "orders": []}
    dates = [_day(value) for value in calendar]
    if len(set(dates)) != len(dates) or dates != sorted(dates):
        raise ValueError("Calendar must contain unique ordered verified sessions")
    required = {"date", "symbol"}
    if not required.issubset(frame) or frame.duplicated(["date", "symbol"]).any():
        raise ValueError("Unique date/security source observations are required")
    bars = frame.copy()
    bars["date"] = bars["date"].map(_day)
    if set(bars["date"]) - set(dates):
        raise ValueError("Source observations contain off-calendar dates")
    bars["symbol"] = bars["symbol"].astype(str)
    observations = {(r["date"], r["symbol"]): r for r in bars.to_dict(orient="records")}
    scores = as_factor_panel(signal_values)
    scores.index = scores.index.strftime("%Y-%m-%d")
    scores.columns = scores.columns.astype(str)
    scores *= 1 if policy.direction else -1
    previous_intended: set[str] = set()
    orders, rebalances = [], []
    receipts = fill_receipts or {}
    for offset in range(1, len(dates), policy.cycle):
        signal_date, execution_date = dates[offset - 1], dates[offset]
        if signal_date not in scores.index:
            rebalances.append({"signal_date": signal_date, "execution_date": execution_date,
                               "status": "pending", "reason": "Signal values are missing on this verified session"})
            continue
        signal = scores.loc[signal_date].replace([np.inf, -np.inf], np.nan).dropna()
        if len(signal) < policy.min_assets:
            rebalances.append({"signal_date": signal_date, "execution_date": execution_date,
                               "status": "pending", "reason": "Minimum cross-sectional signal coverage is missing"})
            continue
        target = set(signal.sort_values(ascending=False, kind="mergesort").head(max(1, len(signal) // policy.groups)).index)
        planned = [(symbol, "sell") for symbol in sorted(previous_intended - target)] + [(symbol, "buy") for symbol in sorted(target - previous_intended)]
        for symbol, side in planned:
            result = assess_next_open_order(observations.get((execution_date, symbol)), side, rulebook,
                                            receipts.get((execution_date, symbol, side)), one_way_cost=policy.one_way_cost)
            result.update(signal_date=signal_date, execution_date=execution_date, symbol=symbol, side=side,
                          transition_scope="intended targets; not a claim of actual holdings")
            orders.append(result)
        rebalances.append({"signal_date": signal_date, "execution_date": execution_date, "status": "audited_intention",
                           "intended_symbols": sorted(target), "intended_weight": 1 / len(target)})
        previous_intended = target
    counts = dict(Counter(order["status"] for order in orders))
    pending = any(r["status"] == "pending" for r in rebalances) or counts.get("pending", 0) or counts.get("blocked", 0)
    return {"status": "pending" if pending or not orders else "order_receipts_verified",
            "admission_status": "pending_reconciled_positions_cashflows_and_actual_fees",
            "policy": asdict(policy), "orders": orders, "rebalances": rebalances, "order_counts": counts,
            "rules": [rule.to_dict() for rule in rulebook.rules],
            "limitations": ["Daily volume/OHLC never proves our opening-auction queue or fill",
                            "Suspensions and delisting gaps are not filled with zero returns",
                            "Intended cohort transitions are not an execution or NAV ledger",
                            "Corporate actions, lot sizes, capacity and actual fee receipts require independent reconciliation"]}
