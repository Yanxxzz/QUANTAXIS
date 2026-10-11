"""Only synthetic conditioning/tie math, no source/price/label reads."""
from pathlib import Path
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from run_conditional_information import partial_rank, restore_checked_G


def test_true_ties_preserved_despite_floating_cancellation():
    G = np.repeat([-.5, .5], 40)
    F = np.linspace(-1.3, 1.3, 80)
    GF = (G - F) / 2
    canonical, error = restore_checked_G(GF, F, G)
    assert error <= 2e-12
    assert pd.Series(canonical).nunique() == 2
    np.testing.assert_array_equal(pd.Series(canonical).rank(method="average"), np.repeat([20.5, 60.5], 40))
    with pytest.raises(ValueError): restore_checked_G(GF, F, G + 1e-4)


def test_two_collinear_rank_controls_stay_pending_not_zero():
    x = np.arange(80); y = np.sin(x); control = np.arange(80)
    out = partial_rank(x, y, [control, control * 2 + 7])
    assert out["value"] is None and out["status"] == "rank_control_design_degenerate_pending"


def test_exact_explained_target_has_no_residual_information():
    x = np.arange(80); y = np.cos(x); other = np.sin(x)
    out = partial_rank(x, y, [x, other])
    assert out["value"] is None and out["status"] == "zero_residual_rank_variance_pending"


def test_nondegenerate_observed_partial_is_affine_scale_invariant():
    rng = np.random.default_rng(1234)
    x, G, F = rng.normal(size=(3, 100))
    y = x + G + F + rng.normal(scale=.15, size=100)
    a = partial_rank(x, y, [G, F]); b = partial_rank(x * 100 + 5, y * 7, [G * 3 + 2, F * .1 - 3])
    assert a["status"] == "descriptive_partial_rank_observed" and a["value"] > .5
    assert a["value"] == pytest.approx(b["value"], abs=1e-14)
