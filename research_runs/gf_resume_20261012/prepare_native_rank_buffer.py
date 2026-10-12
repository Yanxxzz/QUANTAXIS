"""Prepare a self-contained backup factor from bound code and protocols only.

No source prices, financial tables, labels, preflights, registry or platform APIs
are read. Verification uses synthetic prices/signals, never completed research.
"""
from __future__ import annotations

import argparse
import ast
import copy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace


HERE = Path(__file__).resolve().parent
DEFAULT_PROTOCOL = HERE / "rank_buffer_v1" / "protocol.json"
DEFAULT_UNIT = HERE / "rank_buffer_v1" / "native_preparation"


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read_protocol(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def bound(binding):
    path = Path(binding["path"])
    if sha(path) != binding["sha256"]:
        raise ValueError("Bound source/protocol changed: " + str(path))
    return path


def class_node(source, name):
    return next(node for node in ast.parse(source).body
                if isinstance(node, ast.ClassDef) and node.name == name)


def function_node(body, name):
    return copy.deepcopy(next(node for node in body
                              if isinstance(node, ast.FunctionDef) and node.name == name))


def parsed_method(source):
    return ast.parse(source).body[0]


def literal_assignment(body, name):
    return copy.deepcopy(next(node for node in body if isinstance(node, ast.Assign)
                              and any(isinstance(t, ast.Name) and t.id == name
                                      for t in node.targets)))


class LocalizeTarget(ast.NodeTransformer):
    """Only localize dependencies; keep the frozen quantile/selection statements."""

    def visit_Call(self, node):
        node = self.generic_visit(node)
        if isinstance(node.func, ast.Name) and node.func.id == "_groups":
            node.func = ast.Attribute(value=ast.Name(id="self", ctx=ast.Load()),
                                      attr="_groups", ctx=ast.Load())
        elif (isinstance(node.func, ast.Attribute)
              and isinstance(node.func.value, ast.Name)
              and node.func.value.id == "study" and node.func.attr == "quantile_groups"):
            node.func.value = ast.Name(id="self", ctx=ast.Load())
            node.func.attr = "_quantile_groups"
        elif isinstance(node.func, ast.Name) and node.func.id == "RankBufferPendingError":
            node.func.id = "ValueError"
            node.args = [ast.BinOp(left=ast.Constant("RANK_BUFFER_PENDING:"), op=ast.Add(),
                                  right=node.args[0])]
            node.keywords = []
        return node


def production_kernel(production):
    kernel = function_node(production.body, "calculate")
    kernel.name = "_production_f"
    operator = ast.parse(
        "close.groupby(level=symbol_level, sort=False).pct_change(periods=1, fill_method=None)",
        mode="eval").body
    replaced = 0
    for node in ast.walk(kernel):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "RETURNS"):
            if ast.dump(node, include_attributes=False) != ast.dump(
                    ast.parse("RETURNS(close, 1)", mode="eval").body,
                    include_attributes=False):
                raise ValueError("Unexpected production RETURNS call")
            node.func = operator.func
            node.args = operator.args
            node.keywords = operator.keywords
            replaced += 1
    if replaced != 1:
        raise ValueError("Expected exactly one declared RETURNS proxy substitution")
    return kernel


def target_methods(target_source, study_source):
    target_tree = ast.parse(target_source)
    groups = function_node(target_tree.body, "_groups")
    groups.args.args.insert(0, ast.arg(arg="self"))
    groups = LocalizeTarget().visit(groups)
    quantile = function_node(ast.parse(study_source).body, "quantile_groups")
    quantile.name = "_quantile_groups"
    quantile.decorator_list = [ast.Name(id="staticmethod", ctx=ast.Load())]
    builder = function_node(target_tree.body, "build_rank_buffer_targets")
    loop = next(node for node in builder.body if isinstance(node, ast.For)
                and isinstance(node.target, ast.Name) and node.target.id == "day"
                and any(isinstance(child, ast.Assign)
                        and isinstance(child.targets[0], ast.Name)
                        and child.targets[0].id == "groups" for child in node.body))
    first = next(i for i, node in enumerate(loop.body) if isinstance(node, ast.Assign)
                 and isinstance(node.targets[0], ast.Name) and node.targets[0].id == "groups")
    last = next(i for i, node in enumerate(loop.body) if isinstance(node, ast.If)
                and isinstance(node.test, ast.Compare)
                and isinstance(node.test.left, ast.Name) and node.test.left.id == "encoded_top")
    step = parsed_method("def _target_step(self, rows, previous, day):\n    pass\n")
    step.body = [LocalizeTarget().visit(copy.deepcopy(node)) for node in loop.body[first:last + 1]]
    step.body.append(ast.Return(value=ast.Tuple(
        elts=[ast.Name(id="rows", ctx=ast.Load()), ast.Name(id="selected", ctx=ast.Load())],
        ctx=ast.Load())))
    return groups, quantile, step


def composition_method(signal_source):
    builder = function_node(ast.parse(signal_source).body, "build_signals")
    loop = next(node for node in builder.body if isinstance(node, ast.For))
    first = next(i for i, node in enumerate(loop.body) if isinstance(node, ast.Assign)
                 and isinstance(node.targets[0], ast.Name) and node.targets[0].id == "f_pct")
    last = next(i for i, node in enumerate(loop.body) if isinstance(node, ast.Assign)
                and isinstance(node.targets[0], ast.Subscript)
                and isinstance(node.targets[0].slice, ast.Constant)
                and node.targets[0].slice.value == "GF")
    method = parsed_method("@staticmethod\ndef _compose_gf(common, day):\n    pass\n")
    method.body = copy.deepcopy(loop.body[first:last + 1])
    method.body.append(ast.Return(value=ast.Attribute(
        value=ast.Name(id="common", ctx=ast.Load()), attr="GF", ctx=ast.Load())))
    return method


PROJECTION = '''
def _gf_projection(self, close):
    original = close.index
    if (not isinstance(original, pd.MultiIndex) or original.nlevels != 2
            or list(original.names) != ['date', 'symbol'] or not original.is_unique):
        raise ValueError('Unique named daily date/symbol index required')
    days = pd.to_datetime(original.get_level_values('date').astype(str), errors='raise')
    if days.tz is not None:
        days = days.tz_localize(None)
    days = days.normalize()
    day_strings = days.strftime('%Y-%m-%d').to_numpy()
    symbols = original.get_level_values('symbol').astype(str).to_numpy()
    prices = pd.to_numeric(pd.Series(close.values), errors='coerce').to_numpy(dtype=float, na_value=np.nan)
    frame = pd.DataFrame({'date': days, 'symbol': symbols, 'close': prices})
    if frame.duplicated(['date', 'symbol']).any():
        raise ValueError('Multiple observations on the same daily native identity')
    canonical_peers = np.asarray([self._peer_code(symbol) for symbol in symbols])
    frame['peer_symbol'] = canonical_peers
    if frame.duplicated(['date', 'peer_symbol']).any():
        raise ValueError('Duplicate canonical native aliases would double-count peer stocks')
    known_codes = {symbol: self._source_code(symbol) for symbol in pd.unique(symbols)}
    price_index = pd.MultiIndex.from_arrays([days, canonical_peers], names=['date', 'symbol'])
    price = pd.Series(prices, index=price_index, name='close').sort_index()
    # Received observations, full peers, before the financial source mask.
    # This declared previous-observed/no-fill proxy does not certify remote RETURNS.
    full_f = self._production_f({'close': price})
    native_f = full_f.reindex(price_index).to_numpy(dtype=float)
    g_values = np.full(len(original), np.nan)
    for symbol, positions in frame.groupby('symbol', sort=False).groups.items():
        code = known_codes[symbol]
        if code not in self.SOURCE:
            continue
        ids = np.asarray(positions, dtype=int)
        current = day_strings[ids]
        for first, last, g in self.SOURCE[code]:
            use = (current >= first) & (current <= last)
            g_values[ids[use]] = g
    projected = ((day_strings >= self.EVALUATION_START)
                 & (day_strings <= self.EVALUATION_END))
    common = projected & np.isfinite(g_values) & np.isfinite(native_f)
    selected = pd.DataFrame({'date': day_strings[common], 'symbol': canonical_peers[common],
                             'G_pct': g_values[common], 'production_F': native_f[common],
                             'row_position': np.flatnonzero(common)})
    pieces = []
    for day, rows in selected.groupby('date', sort=True):
        rows = rows.copy()
        try:
            rows['GF'] = self._compose_gf(rows, pd.Timestamp(day))
        except ValueError:
            if day in self.ANCHORS:
                raise
            continue
        if not np.isfinite(rows.GF).all():
            raise ValueError('Nonfinite unchanged GF composition')
        pieces.append(rows)
    columns = ['date', 'symbol', 'G_pct', 'production_F', 'row_position', 'GF']
    values = pd.concat(pieces, ignore_index=True) if pieces else pd.DataFrame(columns=columns)
    return values, original, projected, day_strings
'''


CALCULATE = '''
def calculate(self, factors):
    values, original, projected, day_strings = self._gf_projection(factors['close'])
    projected_dates = sorted(set(day_strings[projected]))
    if any(day not in projected_dates for day in self.ANCHORS):
        raise ValueError('RANK_BUFFER_PENDING:original_51_anchor_input_missing')
    previous = set()
    output = np.full(len(original), np.nan)
    completed = 0
    for day in projected_dates:
        rows = values.loc[values.date.eq(day)].copy()
        if rows.empty:
            if day in self.ANCHORS:
                raise ValueError('RANK_BUFFER_PENDING:anchor_has_no_legal_GF_cohort:' + day)
            continue
        if day in self.ANCHORS:
            rows, selected = self._target_step(rows, previous, day)
            previous = selected
            completed += 1
        else:
            # Only current legal GF identities receive the preceding target encoding.
            # An absent G source never receives a filled value or a retained position.
            rows['selected_binary'] = rows.symbol.isin(previous).astype(int)
            rows['current_GF_average_percentile'] = rows.GF.rank(method='average', pct=True)
            rows['encoded_signal'] = 2 * rows.selected_binary + rows.current_GF_average_percentile
        output[rows.row_position.to_numpy(dtype=int)] = rows.encoded_signal.to_numpy(dtype=float)
    if completed != len(self.ANCHORS):
        raise ValueError('RANK_BUFFER_PENDING:incomplete_original_anchor_state')
    result = pd.Series(output[projected], index=original[projected], name='value')
    self.diagnostics = {'input_rows': len(original), 'projected_rows': len(result),
                        'completed_anchors': completed,
                        'finite_rows': int(np.isfinite(result.to_numpy()).sum()),
                        'state': 'previous_proposed_targets_not_filled_holdings',
                        'native_RETURNS_certified': False, 'live_source_qualification': 'pending'}
    return result
'''


CALCULATE_GF = '''
def calculate(self, factors):
    values, original, projected, day_strings = self._gf_projection(factors['close'])
    output = np.full(len(original), np.nan)
    if not values.empty:
        output[values.row_position.to_numpy(dtype=int)] = values.GF.to_numpy(dtype=float)
    result = pd.Series(output[projected], index=original[projected], name='value')
    self.diagnostics = {'input_rows': len(original), 'projected_rows': len(result),
                        'finite_rows': int(np.isfinite(result.to_numpy()).sum()),
                        'definition': 'unchanged_GF_without_target_buffer',
                        'native_RETURNS_certified': False, 'live_source_qualification': 'pending'}
    return result
'''


def render(old_source, production_source, signal_source, target_source, study_source, anchors, mode):
    old = class_node(old_source, "WC03GFConservative")
    production = class_node(production_source, "F141ResidualVarianceShareTransfer")
    attrs = [literal_assignment(old.body, name) for name in
             ("SOURCE", "MARKETS", "INPUT_CALENDAR", "EVALUATION_START", "EVALUATION_END")]
    calendar = ast.literal_eval(attrs[2].value)
    positions = {day: i for i, day in enumerate(calendar)}
    if (len(anchors) != 51 or anchors != sorted(set(anchors))
            or any(day not in positions for day in anchors)
            or any(positions[b] - positions[a] != 5 for a, b in zip(anchors, anchors[1:]))
            or positions[anchors[-1]] + 6 >= len(calendar)):
        raise ValueError("Original 51-anchor/five-session input calendar contract changed")
    attrs.append(ast.Assign(targets=[ast.Name(id="ANCHORS", ctx=ast.Store())],
                            value=ast.parse(repr(anchors), mode="eval").body))
    body = [ast.Expr(value=ast.Constant(
        "Fixed historical GF proxy; real source and live qualification pending."))]
    body.extend(attrs)
    body.extend(function_node(old.body, name) for name in ("_peer_code", "_source_code"))
    body.extend(function_node(production.body, name) for name in
                ("_as_series", "_rolling_mean", "_rolling_variance"))
    body.append(production_kernel(production))
    body.append(composition_method(signal_source))
    if mode == "buffer":
        body.extend(target_methods(target_source, study_source))
    body.extend([parsed_method(PROJECTION), parsed_method(CALCULATE if mode == "buffer" else CALCULATE_GF)])
    class_name = "NativeBufferedGF" if mode == "buffer" else "NativeProductionGF"
    generated = ast.Module(body=[ast.Import(names=[ast.alias(name="numpy", asname="np")]),
                                ast.Import(names=[ast.alias(name="pandas", asname="pd")]),
                                ast.ClassDef(name=class_name,
                                             bases=[ast.Name(id="Factor", ctx=ast.Load())],
                                             keywords=[], body=body, decorator_list=[])], type_ignores=[])
    ast.fix_missing_locations(generated)
    source = ast.unparse(generated) + "\n"
    parsed = ast.parse(source)
    classes = [node for node in parsed.body if isinstance(node, ast.ClassDef)]
    imports = [node for node in ast.walk(parsed) if isinstance(node, (ast.Import, ast.ImportFrom))]
    if len(classes) != 1 or any(not isinstance(n, ast.Import)
                               or any(a.name not in {"numpy", "pandas"} for a in n.names)
                               for n in imports):
        raise ValueError("Self-contained one-Factor-class contract failed")
    actual = function_node(classes[0].body, "_production_f")
    if ast.dump(actual, include_attributes=False) != ast.dump(
            production_kernel(production), include_attributes=False):
        raise ValueError("Copied production mathematics changed")
    compile(source, "<prepared-native-rank-buffer>", "exec")
    return source


def synthetic_checks(source, production_source, signal_source, target_source, study_source, anchors, mode):
    import numpy as np
    import pandas as pd

    namespace = {"Factor": object}
    exec(compile(source, "<synthetic-prepared-native>", "exec"), namespace)
    native = namespace["NativeBufferedGF" if mode == "buffer" else "NativeProductionGF"]()
    calendar = list(native.INPUT_CALENDAR)
    codes = [f"{i + 1:06d}" for i in range(40)]
    rng = np.random.default_rng(20261012)
    market = rng.normal(.0002, .008, (len(calendar), 1))
    returns = market + rng.normal(0, .003, (len(calendar), len(codes)))
    prices = 30 * np.cumprod(1 + returns, axis=0)
    index = pd.MultiIndex.from_product([pd.to_datetime(calendar), codes], names=["date", "symbol"])
    close = pd.Series(prices.reshape(-1), index=index, name="close")
    close = close.drop(index[[50 * 40 + 3, 80 * 40 + 7]])  # Received-index gap controls.
    original = {"Factor": object,
                "RETURNS": lambda x, n: x.groupby(level="symbol", sort=False).pct_change(periods=n, fill_method=None)}
    exec(compile(production_source, "<synthetic-owned-production>", "exec"), original)
    expected_f = original["F141ResidualVarianceShareTransfer"]().calculate({"close": close})
    actual_f = native._production_f({"close": close})
    pd.testing.assert_series_equal(actual_f, expected_f, check_exact=True)
    synthetic_g = rng.uniform(.04, .96, len(codes))
    native.SOURCE = {code: [[native.EVALUATION_START, anchors[-1], float(synthetic_g[i])]]
                     for i, code in enumerate(codes)}
    gf_rows, _, _, _ = native._gf_projection(close)
    qualification = gf_rows[["date", "symbol", "G_pct"]].copy()
    qualification["source_qualified"] = True
    signal_namespace = {}
    exec(compile(signal_source, "<synthetic-local-composition>", "exec"), signal_namespace)
    local, _ = signal_namespace["build_signals"](expected_f, qualification, anchors)
    actual_gf = gf_rows[gf_rows.date.isin(anchors)].copy()
    actual_gf["date"] = pd.to_datetime(actual_gf.date)
    actual_gf = actual_gf.set_index(["date", "symbol"]).GF.sort_index()
    pd.testing.assert_series_equal(actual_gf, local.GF.sort_index(), check_exact=True)
    actual = native.calculate({"close": close})
    projected = (close.index.get_level_values("date") >= pd.Timestamp(native.EVALUATION_START)) & (
        close.index.get_level_values("date") <= pd.Timestamp(native.EVALUATION_END))
    if not actual.index.equals(close.index[projected]) or actual.name != "value":
        raise ValueError("Exact date-first evaluation projection changed")
    tail = actual[actual.index.get_level_values("date") > pd.Timestamp(anchors[-1])]
    if not tail.isna().all():
        raise ValueError("Absent holding-tail G source was filled")
    if mode == "gf":
        gf_values = gf_rows.copy()
        gf_values["date"] = pd.to_datetime(gf_values.date)
        wanted = gf_values.set_index(["date", "symbol"]).GF.reindex(actual.index)
        pd.testing.assert_series_equal(actual, wanted, check_names=False, check_exact=True)
        return {"synthetic_only": True, "synthetic_seed": 20261012,
                "production_F_exact_with_declared_proxy_operator": True,
                "GF_component_composition_exact": True, "daily_plain_GF_output_exact": True,
                "buffer_state_or_encoding_used": False, "original_projection_exact": True,
                "missing_G_tail_stays_NaN": True, "real_prices_or_labels_read": False,
                "native_preflight_executed": False}
    quantile_tree = ast.Module(body=[function_node(ast.parse(study_source).body, "quantile_groups")], type_ignores=[])
    quantile_namespace = {"pd": pd}
    exec(compile(ast.fix_missing_locations(quantile_tree), "<synthetic-frozen-qcut>", "exec"), quantile_namespace)
    target_tree = ast.parse(target_source)
    target_tree.body = [node for node in target_tree.body
                        if not (isinstance(node, ast.ImportFrom) and node.module == "panda_alpha")]
    target_namespace = {"study": SimpleNamespace(quantile_groups=quantile_namespace["quantile_groups"])}
    exec(compile(target_tree, "<synthetic-local-target-builder>", "exec"), target_namespace)
    signal_frame = local.reset_index()[["date", "symbol", "GF"]]
    expected = target_namespace["build_rank_buffer_targets"](signal_frame, anchors, calendar)
    tie_rows = pd.DataFrame({'date': anchors[1], 'symbol': codes,
                             'GF': list(range(35)) + [35, 35, 36, 37, 38]})
    seed_values = list(range(40))
    for left, right in ((35, 39), (0, 38), (1, 37), (2, 36)):
        seed_values[left], seed_values[right] = seed_values[right], seed_values[left]
    seed_rows = pd.DataFrame({'date': anchors[0], 'symbol': codes, 'GF': seed_values})
    prior = {codes[i] for i in (35, 0, 1, 2)}
    tie_messages = []
    for check in (lambda: native._target_step(tie_rows.copy(), prior, anchors[1]),
                  lambda: target_namespace['build_rank_buffer_targets'](
                      pd.concat([seed_rows, tie_rows], ignore_index=True), anchors[:2], calendar)):
        try:
            check()
        except ValueError as error:
            tie_messages.append(str(error))
        else:
            raise ValueError('Tie identity mismatch was silently repaired')
    if not all('encoded_top10_identity_mismatch' in message for message in tie_messages):
        raise ValueError('Generated/local tie-pending contract differs')
    encoded = expected["encoded_signals"]
    for day in anchors:
        wanted = encoded[encoded.date.eq(day)].set_index("symbol").encoded_signal.sort_index()
        got = actual.xs(pd.Timestamp(day), level="date").sort_index()
        pd.testing.assert_series_equal(got, wanted, check_names=False, check_exact=True)
    proposed = {}
    nonanchor_days = 0
    for day in sorted(gf_rows.date.unique()):
        if day in expected["anchors"]:
            proposed = set(expected["schedule"][calendar[calendar.index(day) + 1]]["codes"])
        else:
            rows = gf_rows[gf_rows.date.eq(day)].set_index("symbol")
            wanted = 2 * rows.index.isin(proposed).astype(int) + rows.GF.rank(method="average", pct=True)
            got = actual.xs(pd.Timestamp(day), level="date").sort_index()
            pd.testing.assert_series_equal(got, wanted.sort_index(), check_names=False, check_exact=True)
            nonanchor_days += 1
    return {"synthetic_only": True, "synthetic_seed": 20261012,
            "production_F_exact_with_declared_proxy_operator": True,
            "GF_component_composition_exact": True, "all_51_anchor_encoding_exact": True,
            "tie_identity_mismatch_pending_in_both_versions": True,
            "nonanchor_previous_target_encoding_days": nonanchor_days,
            "original_projection_exact": True, "missing_G_tail_stays_NaN": True,
            "real_prices_or_labels_read": False, "native_preflight_executed": False}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--protocol", type=Path, default=DEFAULT_PROTOCOL)
    parser.add_argument("--unit", type=Path, default=DEFAULT_UNIT)
    parser.add_argument("--mode", choices=("both", "gf", "buffer"), default="both",
                        help="Preparation modes, not an economic parameter scan")
    args = parser.parse_args()
    unit = args.unit.resolve()
    if not unit.is_relative_to(HERE.resolve()):
        raise ValueError("Prepared source must stay inside this research unit")
    modes = ["gf", "buffer"] if args.mode == "both" else [args.mode]
    names = {"gf": "NativeProductionGF", "buffer": "NativeBufferedGF"}
    outputs = {mode: unit / (names[mode] + ".py") for mode in modes}
    receipt_path = unit / "generation_receipt.json"
    if any(output.exists() for output in outputs.values()) or receipt_path.exists():
        raise ValueError("Prepared backup already exists; do not overwrite it")
    p = read_protocol(args.protocol)
    base_path = bound(p["bindings"]["base_protocol"])
    base = read_protocol(base_path)
    if p["anchors"] != base["anchors"]:
        raise ValueError("Original formation anchors changed")
    bindings = {name: base["bindings"][name] for name in
                ("old_g_code", "production_code", "signal_builder")}
    bindings.update({name: p["bindings"][name] for name in ("target_builder", "study_engine")})
    sources = {name: bound(item).read_text(encoding="utf-8") for name, item in bindings.items()}
    rendered, checks = {}, {}
    for mode in modes:
        rendered[mode] = render(sources["old_g_code"], sources["production_code"], sources["signal_builder"],
                                sources["target_builder"], sources["study_engine"], p["anchors"], mode)
        checks[mode] = synthetic_checks(rendered[mode], sources["production_code"], sources["signal_builder"],
                                        sources["target_builder"], sources["study_engine"], p["anchors"], mode)
    unit.mkdir(parents=True, exist_ok=True)
    for mode, output in outputs.items():
        output.write_text(rendered[mode], encoding="utf-8", newline="\n")
    receipt = {"status": "BACKUP_SOURCE_PREPARED_NOT_DISPATCHED",
               "created_at_utc": datetime.now(timezone.utc).isoformat(),
               "generator": {"path": str(Path(__file__).resolve()), "sha256": sha(__file__)},
               "protocol": {"path": str(args.protocol.resolve()), "sha256": sha(args.protocol)},
               "base_protocol": {"path": str(base_path), "sha256": sha(base_path)},
               "source_bindings": bindings,
               "generated_codes": {mode: {"path": str(output), "sha256": sha(output)}
                                   for mode, output in outputs.items()},
               "checks": checks, "factor_classes": {mode: [names[mode]] for mode in modes},
               "definitions": {"gf": "Unchanged G; received-index production F141 proxy; original equal-Z GF",
                               "buffer": "Same GF core; fixed 10/20 proposed-target buffer; backup only"},
               "RETURNS": "Per-symbol previous observed close; pct_change(fill_method=None), first NaN; remote implementation unverified",
               "source_and_evaluation": "Copied SOURCE, MARKETS, INPUT_CALENDAR and evaluation boundaries; no source/cohort filling",
               "state": {"gf": "No proposed-target state or encoding",
                         "buffer": "Empty at original first anchor; preceding proposed targets, never actual filled holdings"},
               "live_source_qualification": "pending", "dispatch_ready": False,
               "native_preflight_or_registry_calls": 0, "official_calls": 0,
               "real_source_reads": 0, "economic_replays": 0,
               "limitations": ["Historical static G has no current live updates or new source qualifications",
                               "Native close adjustment, full-peer support, warmup and cleaning remain unverified",
                               "Buffer requires all original 51 anchors; shortened/chunked input cannot seed prior targets",
                               "Nonanchor encoding uses current legal GF cohort and previous proposed target membership; only anchor group identity is certified",
                               "Production worker execution/preflight, full-PIT admission and actual pool increment remain pending",
                               "Prepared as backup even if the local fixed buffer is economically rejected; no dispatch authorization"]}
    receipt_path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps({"generated_codes": receipt["generated_codes"],
                      "generation_receipt": {"path": str(receipt_path), "sha256": sha(receipt_path)},
                      "checks": checks, "dispatch_ready": False}, ensure_ascii=False))


if __name__ == "__main__":
    main()
