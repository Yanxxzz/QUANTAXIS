"""Compatibility checks for shared local-script evidence I/O, without studies."""
import hashlib
import importlib

import numpy as np
import pytest

from scripts.research_io import binding, clean, read, write_clean_json


def test_clean_keeps_existing_numpy_and_nonfinite_json_values():
    original = {np.int64(7): (np.int64(9), np.float32(1.25), np.nan,
                             np.inf, -np.inf, np.bool_(True), False),
                "嵌套": {"值": np.float64(2.5)}}
    assert clean(original) == {"7": [9, 1.25, None, None, None, True, False],
                               "嵌套": {"值": 2.5}}
    assert isinstance(original[np.int64(7)], tuple)
    assert np.isnan(original[np.int64(7)][2])


@pytest.mark.parametrize("newline", [False, True])
def test_json_preserves_utf8_format_and_caller_newline(tmp_path, newline):
    path = tmp_path / "evidence.json"
    write_clean_json(path, {"证据": np.int64(3), "缺失": np.inf}, trailing_newline=newline)
    expected = '{\n  "证据": 3,\n  "缺失": null\n}' + ("\n" if newline else "")
    assert path.read_text(encoding="utf-8") == expected
    assert not path.read_bytes().startswith(b"\xef\xbb\xbf")
    assert read(path) == {"证据": 3, "缺失": None}


def test_read_accepts_bom_and_binding_hashes_original_bytes(tmp_path):
    path = tmp_path / "来源.json"
    raw = b"\xef\xbb\xbf" + '{"来源": 1}\r\n'.encode("utf-8")
    path.write_bytes(raw)
    assert read(path) == {"来源": 1}
    assert binding(path) == {"path": str(path.resolve()),
                             "sha256": hashlib.sha256(raw).hexdigest()}
    assert path.read_bytes() == raw


@pytest.mark.parametrize("module_name", [
    "scripts.research_limit_event_factor",
    "scripts.research_limit_stoploss",
    "scripts.research_limit_next_directions",
    "scripts.resume_limit_stoploss",
    "scripts.resume_limit_next_directions",
])
def test_existing_save_entrypoints_keep_local_output_contract(tmp_path, monkeypatch, module_name):
    # These modules guard their study entrypoints; do not import the historical
    # resume scripts that execute immediately at module scope.
    module = importlib.import_module(module_name)
    monkeypatch.setattr(module, "OUT", tmp_path)
    module.save("summary.json", {"指标": (np.int64(2), np.nan)})
    assert read(tmp_path / "summary.json") == {"指标": [2, None]}
    assert not (tmp_path / "summary.json").read_text(encoding="utf-8").endswith("\n")


def test_existing_path_based_save_and_helper_exports_remain_compatible(tmp_path):
    publisher = importlib.import_module("scripts.summarize_limit_next_directions")
    historical = importlib.import_module("scripts.backtest_limit_reversal")
    discovery = importlib.import_module("scripts.research_limit_event_factor")
    path = tmp_path / "comparison.json"
    publisher.save(path, {"指标": np.float64(1.5)})
    assert historical.read(path) == {"指标": 1.5}
    assert historical.binding(path) == binding(path)
    assert discovery.clean is clean
    # The old backtest writer is deliberately strict: it never converted
    # nonfinite values to null and retains its terminal newline.
    historical.save(path, {"指标": 1.5})
    assert path.read_text(encoding="utf-8").endswith("\n")
    with pytest.raises(ValueError):
        historical.save(path, {"指标": float("nan")})
