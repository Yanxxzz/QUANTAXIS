"""One source-only fixed-label completion; no PnL replay or factor calculation."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import sys

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from panda_alpha.quote_labels import fixed_forward_open_labels


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--protocol", type=Path, required=True)
    parser.add_argument("--sha", required=True)
    args = parser.parse_args()
    if sha(args.protocol) != args.sha:
        raise ValueError("Frozen source-repair protocol changed")
    protocol = json.loads(args.protocol.read_text(encoding="utf-8"))
    out = args.protocol.parent
    if (out / "receipt.json").exists() or (out / "repaired_fixed_open_labels.parquet").exists():
        raise ValueError("Source label repair output already exists; no implicit replay")
    for binding in protocol["bindings"].values():
        if sha(binding["path"]) != binding["sha256"]:
            raise ValueError("Frozen label source changed: " + binding["path"])
    db = sqlite3.connect((ROOT / "research_runs/research_trials.sqlite3").as_uri() + "?mode=ro", uri=True)
    try:
        registered = db.execute("SELECT kind FROM research_events WHERE event_id=?",
                                ("lq01-fixed-label-source-repair:" + args.sha,)).fetchone()
    finally:
        db.close()
    if registered != ("source_repair",):
        raise ValueError("Root source-repair registration required before numeric opens")
    bindings = protocol["bindings"]
    # Only retained, already calculated factor values; no candidate recomputation.
    signals = pd.read_parquet(bindings["frozen_signals"]["path"], columns=["date", "symbol", "LQ01"])
    signals["date"] = pd.to_datetime(signals.date).dt.strftime("%Y-%m-%d")
    if len(signals) != 77510 or sorted(signals.date.unique()) != protocol["anchors"]:
        raise ValueError("Original frozen formation-key denominator/anchors changed")
    old = pd.read_parquet(bindings["old_labels"]["path"], columns=["date", "symbol", "label_available", "forward_return"])
    old["date"] = pd.to_datetime(old.date).dt.strftime("%Y-%m-%d")
    # First numeric-open read is below, after all source/code/protocol/registration checks.
    quotes = pd.read_parquet(bindings["quotes"]["path"], columns=["date", "symbol", "open"])
    labels = fixed_forward_open_labels(signals, quotes, protocol["calendar"], preserve_unavailable=old)
    joined = labels.merge(old.rename(columns={"label_available": "old_available", "forward_return": "old_return"}),
                          on=["date", "symbol"], how="left", validate="one_to_one")
    matched = joined.old_available.notna()
    old_true = joined.old_available.eq(True)
    old_false = joined.old_available.eq(False)
    equal = np.array_equal(joined.loc[old_true, "forward_return"].to_numpy(), joined.loc[old_true, "old_return"].to_numpy())
    if (not joined.loc[old_true, "label_available"].all() or not equal
            or joined.loc[old_false, "label_available"].any() or int(old_false.sum()) != 32):
        raise ValueError("Old matched labels/availability not exactly preserved")
    path = out / "repaired_fixed_open_labels.parquet"
    labels.to_parquet(path, index=False, compression="zstd")
    complete = signals.merge(labels[["date", "symbol", "label_available", "forward_return"]],
                             on=["date", "symbol"], how="left", validate="one_to_one")
    information = []
    for day, formed in complete.groupby("date", sort=True):
        observed = formed[formed.label_available & np.isfinite(formed.forward_return)]
        ic = observed.LQ01.corr(observed.forward_return, method="spearman") if len(observed) >= 30 else np.nan
        information.append({"date": day, "formed_keys": len(formed), "available_labels": len(observed),
                            "unavailable_labels": len(formed) - len(observed),
                            "RankIC": float(ic) if np.isfinite(ic) else None})
    receipt = {
        "status": "COMPLETED_UNCHANGED_FIXED_LABEL_SOURCE_REPAIR", "completed_at_utc": datetime.now(timezone.utc).isoformat(),
        "protocol_sha256": args.sha, "runtime_sha256": sha(__file__), "original_formed_denominator": len(labels),
        "old_matched_keys": int(matched.sum()), "old_true_labels_exact_equal": equal, "old_true_labels": int(old_true.sum()),
        "old_false_labels_preserved": int(old_false.sum()), "absent_old_label_keys": int((~matched).sum()),
        "newly_completed_label_keys": int((~matched & joined.label_available).sum()),
        "new_pending_label_keys": int((~matched & ~joined.label_available).sum()),
        "available_label_keys": int(labels.label_available.sum()), "unavailable_label_keys": int((~labels.label_available).sum()),
        "coverage": float(labels.label_available.mean()), "source_pending_reasons": labels.loc[~labels.label_available, "availability_reason"].value_counts().to_dict(),
        "formation_periods": len(information), "information_supplement": information,
        "mean_period_RankIC": float(np.mean([v["RankIC"] for v in information if v["RankIC"] is not None])),
        "label_artifact": {"path": str(path), "sha256": sha(path), "bytes": path.stat().st_size},
        "unchanged_definition": "Existing direction1LQ01 and reference t+1 adjusted open to t+6 adjusted open; no fills or costs in these labels.",
        "source_repair_only": True, "new_economic_contexts": 0, "PnL_paths_replayed": 0, "new_factor_values_calculated": 0,
        "definition_direction_window_weights_changed": False, "old_outputs_modified": False,
        "previously_exposed": True, "independent_OOS": False, "new_BH_or_admission_claim": False,
        "original_label_false_not_reinterpreted": True, "official_runs": 0, "official_compute": 0, "downloads": 0,
    }
    (out / "receipt.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps({k: receipt[k] for k in ["status", "original_formed_denominator", "old_true_labels_exact_equal", "old_false_labels_preserved", "newly_completed_label_keys", "new_pending_label_keys", "coverage", "mean_period_RankIC"]}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
