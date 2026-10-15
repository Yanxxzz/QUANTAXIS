"""Synthetic identities and routing only; no live registry/account or PnL."""
from copy import deepcopy
import json

import pytest

from panda_alpha.platform import PandaClient
from panda_alpha.registry import normalize_definition, strategy_definition, trial_key
from panda_alpha.research_budget import request


WINDOW = {"start": "2024-01-02", "end": "2026-09-18"}
STATEFUL = {"direction": 1, "economic_evaluation_type": "stateful_account",
            "strategy_spec": {"entry": "two_limit_closes", "exit": "tp10", "slots": 5}}
POLICY = {"study_id": "synthetic", "max_total_credits": 60, "max_runs": 20,
          "review_interval_credits": 20, "max_initial_runs_per_mechanism": 2,
          "max_runs_per_mechanism": 4}


def test_legacy_factor_bytes_and_three_prechange_identity_hashes_are_unchanged():
    cases = [
        ({"formula": "MA(CLOSE,20)", "direction": 1},
         "cf430dbc527efe991e2df33455e2312e3cc3dc953ad6f8eca53369c0c4d8d812",
         "ecbe37ff37caedcf8ca3fa6606aece21beee11fa060060e3d785940cef38ea85"),
        ({"code": 'def calculate(factors):\n    return factors["close"]\n', "direction": 0},
         "e4cf299f7b61a3a0a7317f25f1520a599c7ad75d13729c558ffe661792d9da29",
         "f3b8c10908d3b778b677265d2a25d9ea9e9981f85772f69b28fbde69c53bbde2"),
        ({k: v for k, v in STATEFUL.items() if k != "economic_evaluation_type"},
         "fe62839f3aecdbc433207d0a575e9352210b33787866f29decb117a69f7dce19",
         "7dace5f7c7cd95dfcb0e8c29bf1fb285933e2cadc552a0a3f39c10e2c3201dfc"),
    ]
    for candidate, signature, old_hash in cases:
        expected = {"signature": signature, "direction": candidate["direction"],
                    "window": WINDOW, "cycle": 5, "groups": 10}
        actual = strategy_definition(candidate, WINDOW, 5, 10)
        assert json.dumps(actual, sort_keys=True) == json.dumps(expected, sort_keys=True)
        assert trial_key(actual) == old_hash
        assert "evaluation_type" not in actual
        assert strategy_definition(dict(candidate, economic_evaluation_type="factor"),
                                   WINDOW, 5, 10) == actual


def test_factor_cannot_obtain_one_group_by_omitting_or_renaming_its_type():
    candidate = {"formula": "RANK(CLOSE)", "direction": 1}
    for groups in (1, True, 0):
        with pytest.raises(ValueError, match="at least two groups"):
            strategy_definition(candidate, WINDOW, 1, groups)
    definition = strategy_definition(candidate, WINDOW, 1, 2)
    with pytest.raises(ValueError, match="at least two groups"):
        normalize_definition(dict(definition, groups=1))


def test_explicit_stateful_account_has_distinct_typed_non_grouped_identity():
    original = deepcopy(STATEFUL)
    definition = strategy_definition(STATEFUL, WINDOW, 1, 1)
    assert definition["evaluation_type"] == "stateful_account"
    assert (definition["cycle"], definition["groups"]) == (1, 1)
    assert normalize_definition(definition) == definition
    assert STATEFUL == original
    legacy = strategy_definition({k: v for k, v in STATEFUL.items()
                                  if k != "economic_evaluation_type"}, WINDOW, 1, 2)
    assert definition["signature"] == legacy["signature"]
    assert trial_key(definition) != trial_key(legacy)


def test_stateful_account_requires_exact_one_cycle_one_group_and_frozen_window():
    for cycle, groups in ((5, 1), (1, 2), (True, 1), (1, True)):
        with pytest.raises(ValueError, match="cycle 1 and groups 1"):
            strategy_definition(STATEFUL, WINDOW, cycle, groups)
    first = strategy_definition(STATEFUL, WINDOW, 1, 1)
    changed = strategy_definition(STATEFUL, dict(WINDOW, end="2026-09-17"), 1, 1)
    assert trial_key(first) != trial_key(changed)
    for window in ({}, {"start": WINDOW["start"]}, {"start": "", "end": WINDOW["end"]}):
        with pytest.raises(ValueError, match="start and end window"):
            strategy_definition(STATEFUL, window, 1, 1)


def test_unknown_economic_types_are_rejected_without_silent_legacy_fallback():
    definition = strategy_definition(STATEFUL, WINDOW, 1, 1)
    for unknown in (None, "", "account", "factor_with_one_group", False):
        with pytest.raises(ValueError, match="Unknown economic evaluation type"):
            strategy_definition(dict(STATEFUL, economic_evaluation_type=unknown), WINDOW, 1, 1)
        with pytest.raises(ValueError, match="Unknown economic evaluation type"):
            normalize_definition(dict(definition, evaluation_type=unknown))


def test_stateful_budget_task_is_exploration_with_unchanged_shared_limits():
    candidate = dict(STATEFUL, code="def initialize(context):\n    pass\n",
                     official_task="stateful_strategy", budget_mechanism_id="synthetic_account",
                     research_question="Compare the frozen account rules",
                     decision_if_pass="Review risk and source evidence",
                     decision_if_fail="Close this exact version",
                     official_information_gap="Official account fills and fees")
    result = request(POLICY, candidate, "exploration")
    assert result["task"] == "stateful_strategy"
    assert result["study_id"] == "synthetic" and result["limits"] == POLICY
    with pytest.raises(ValueError, match="matching research category"):
        request(POLICY, candidate, "validation")


def test_factor_create_rejects_both_stateful_markers_before_any_cli_call():
    class NoCLI(PandaClient):
        def cli(self, *args, **kwargs):
            raise AssertionError("The factor CLI must never be reached")
    client = NoCLI({})
    for candidate in ({"official_task": "stateful_strategy"},
                      {"economic_evaluation_type": "stateful_account"}):
        with pytest.raises(ValueError, match="never factor_create"):
            client.create(candidate, WINDOW, 1, 1, "synthetic-no-run")
