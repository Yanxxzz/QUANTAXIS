"""Synthetic continuous-source regression checks; no market/PIT/alpha claims."""
import numpy as np
import pandas as pd
import pytest

from panda_alpha.g_continuity import ContinuousGF, GSourceStore, annual_delta


OBSERVED = "2026-10-10T00:00:00+00:00"
DAY = "2026-10-12"
CUTOFF = "2026-10-12T07:00:00+00:00"
PROOF = {"sha256": "a" * 64, "path": "synthetic-source-contract"}


def amount_contract(year=2025, current_revenue="100"):
    return {
        "amounts_status": "three_G_same_report_amounts_bound",
        "current_comparability": {
            "status": "current_G_comparability_ready", "pending_reasons": [],
            "captured_barriers_preserved": [], "actual_change_kinds": [],
        },
        "amounts": {
            "operating_revenue": {
                "current_yuan": current_revenue, "comparative_yuan": "80",
                "current_span": [f"{year}-01-01", f"{year}-12-31"],
                "comparative_span": [f"{year-1}-01-01", f"{year-1}-12-31"],
            },
            "operating_cost": {
                "current_yuan": "70", "comparative_yuan": "60",
                "current_span": [f"{year}-01-01", f"{year}-12-31"],
                "comparative_span": [f"{year-1}-01-01", f"{year-1}-12-31"],
            },
            "total_assets": {
                "current_yuan": "200", "opening_yuan": "100",
                "current_stock_asof": f"{year}-12-31", "opening_stock_asof": f"{year}-01-01",
            },
        },
    }


def annual_payload(code="000001", year=2025, annid="annual-2025", **changes):
    value = {
        "announcement_id": annid, "report_date": f"{year}-12-31",
        "published_at": f"{year+1}-04-15", "source_record_sha256": "b" * 64,
        "source_binding": PROOF, "contract": amount_contract(year), "pending_reasons": [],
    }
    value.update(changes)
    return value


def refresh(store, codes=("000001",), *, observed="2026-10-12T00:00:00+00:00",
            since="2026-04-15", through="2026-10-12", complete=True, **extra):
    for code in codes:
        store.append("refresh", code, {
            "source_binding": PROOF, "since": since, "through": through,
            "complete": complete, **extra,
        }, observed_at=observed)


def industry(store, records=None, *, publication="2026-04-01", observed=OBSERVED, **extra):
    return store.append("industry", "*", {
        "source_binding": PROOF, "published_at": publication, "publication_date": publication,
        "records": records if records is not None else {"000001": "C30"}, **extra,
    }, observed_at=observed)


@pytest.fixture
def store(tmp_path):
    value = GSourceStore(tmp_path / "sources.sqlite3")
    yield value
    value.close()


def ready(store, code="000001", **annual_changes):
    store.append("annual", code, annual_payload(code, **annual_changes), observed_at=OBSERVED)
    industry(store, {code: "C30"})
    refresh(store, (code,))


def row(store, code="000001", day=DAY, **kwargs):
    return store.asof([code], day, **kwargs).iloc[0]


def test_original_scalar_uses_gp_change_and_mean_assets():
    assert annual_delta(amount_contract(), "2025-12-31") == pytest.approx(10 / 150)
    assert annual_delta(amount_contract(current_revenue="70"), "2025-12-31") < 0
    revised = amount_contract()
    revised["amounts"]["total_assets"]["opening_stock_asof"] = "2024-12-31"
    assert annual_delta(revised, "2025-12-31") == pytest.approx(10 / 150)


@pytest.mark.parametrize("mutate", [
    lambda c: c["amounts"]["operating_revenue"].update(current_span=["2025-01-01", "2025-06-30"]),
    lambda c: c["amounts"]["operating_cost"].update(comparative_span=["2023-01-01", "2023-12-31"]),
    lambda c: c["amounts"]["total_assets"].update(opening_stock_asof="2025-06-30"),
    lambda c: c["amounts"]["total_assets"].update(opening_yuan="0"),
    lambda c: c["amounts"]["operating_revenue"].update(current_yuan="NaN"),
    lambda c: c["current_comparability"].update(pending_reasons=["policy_pending"]),
    lambda c: c["current_comparability"].update(captured_barriers_preserved=[{"kind": "consolidation_scope"}]),
    lambda c: c["current_comparability"].update(actual_change_kinds=["accounting_policy"]),
])
def test_bad_source_contract_does_not_become_scalar(mutate):
    contract = amount_contract(); mutate(contract)
    with pytest.raises(ValueError):
        annual_delta(contract, "2025-12-31")


def test_source_binding_sha_required_and_duplicate_sync_preserves_first_observation(store):
    with pytest.raises(ValueError):
        store.append("annual", "000001", {"source_binding": {"sha256": "unbound"}})
    first = store.append("annual", "000001", annual_payload(), observed_at=OBSERVED)
    second = store.append("annual", "000001", annual_payload(), observed_at="2026-10-11T00:00:00Z")
    assert first == second
    events = store.events(CUTOFF)
    assert len(events) == 1
    assert events[0]["observed_at"] == OBSERVED


def test_new_year_automatically_selects_new_annual_without_fixed_end_date(store):
    ready(store)
    store.append("annual", "000001", annual_payload(year=2026, annid="annual-2026",
                 contract=amount_contract(2026, "120")), observed_at="2027-04-16T00:00:00Z")
    refresh(store, observed="2027-04-16T00:00:00Z", since="2027-04-15", through="2027-04-16")
    result = row(store, day="2027-04-16")
    assert result.pit_usable
    assert result.announcement_id == "annual-2026"
    assert result.GP_delta == pytest.approx(30 / 150)


def test_latest_disclosed_metadata_blocks_fallback_to_previous_annual(store):
    ready(store)
    store.append("report_metadata", "000001", annual_payload(year=2026, annid="new-unparsed"),
                 observed_at="2027-04-16T00:00:00Z")
    refresh(store, observed="2027-04-16T00:00:00Z", since="2027-04-15", through="2027-04-16")
    result = row(store, day="2027-04-16")
    assert not result.pit_usable
    assert result.source_status == "latest_annual_fields_pending"
    assert np.isnan(result.GP_delta)


def test_same_day_two_report_versions_remain_pending(store):
    ready(store)
    store.append("annual", "000001", annual_payload(annid="alternative-2025"),
                 observed_at="2026-10-11T00:00:00Z")
    result = row(store)
    assert not result.pit_usable
    assert result.source_status == "ambiguous_latest_annual_editions"


def test_conflicting_same_announcement_records_are_not_amount_good_tiebroken(store):
    ready(store)
    store.append("annual", "000001", annual_payload(source_record_sha256="c" * 64,
                 contract=amount_contract(current_revenue="130")), observed_at="2026-10-11T00:00:00Z")
    result = row(store)
    assert not result.pit_usable
    assert "ambiguous" in result.source_status or "conflict" in result.source_status


def test_late_discovery_never_rewrites_past_decision(store):
    ready(store)
    before = row(store)
    store.append("annual", "000001", annual_payload(annid="late-revised", published_at="2026-04-18"),
                 observed_at="2026-10-13T00:00:00Z")
    after = row(store)
    pd.testing.assert_series_equal(before, after)
    refresh(store, observed="2026-10-13T00:00:00Z", through="2026-10-13")
    assert row(store, day="2026-10-13").announcement_id == "late-revised"
    with pytest.raises(ValueError, match="Knowledge cutoff"):
        row(store, knowledge_cutoff="2026-10-13T00:00:00Z")


def test_unresolved_correction_only_exact_resolution_restores_selected_leaf(store):
    ready(store)
    blocker = store.append("blocker", "000001", {
        "source_binding": PROOF, "published_at": "2026-10-09", "report_dates": ["2025-12-31"],
        "pending_reasons": ["income_restatement_scope_pending"],
    }, observed_at=OBSERVED)
    assert not row(store).pit_usable
    store.append("resolution", "000001", {
        "source_binding": PROOF, "published_at": "2026-10-10", "resolves_event_id": blocker,
        "annual_source_id": "another-annual", "effect_status": "unchanged_selected_annual",
    }, observed_at="2026-10-11T00:00:00Z")
    assert not row(store).pit_usable
    store.append("resolution", "000001", {
        "source_binding": PROOF, "published_at": "2026-10-10", "resolves_event_id": blocker,
        "annual_source_id": "annual-2025", "effect_status": "unchanged_selected_annual",
    }, observed_at="2026-10-11T01:00:00Z")
    assert row(store).pit_usable


def test_unknown_same_year_h1_income_correction_blocks_annual_delta(store):
    ready(store)
    store.append("blocker", "000001", {
        "source_binding": PROOF, "published_at": "2026-10-09", "report_dates": ["2025-06-30"],
        "affected_fields": ["operating_revenue"], "scope_verified": False,
    }, observed_at=OBSERVED)
    assert not row(store).pit_usable


@pytest.mark.parametrize("period", ["2024-12-31", "2024-06-30"])
def test_prior_comparative_year_revision_also_blocks_g_delta(store, period):
    ready(store)
    store.append("blocker", "000001", {
        "source_binding": PROOF, "published_at": "2026-10-09", "report_dates": [period],
        "affected_fields": ["operating_cost"], "scope_verified": False,
    }, observed_at=OBSERVED)
    assert not row(store).pit_usable


def test_announced_future_fiscal_year_is_not_a_completed_annual(store):
    ready(store)
    store.append("report_metadata", "000001", {
        "source_binding": PROOF, "published_at": "2026-10-09",
        "report_date": "2026-12-31", "announcement_id": "future-report-work-plan",
    }, observed_at=OBSERVED)
    assert row(store).announcement_id == "annual-2025"


def test_latest_industry_edition_omission_does_not_fall_back(store):
    ready(store)
    industry(store, {"000002": "C31"}, publication="2026-10-09")
    result = row(store)
    assert not result.pit_usable
    assert result.source_status == "latest_published_industry_missing"


def test_date_only_publication_not_usable_same_day(store):
    ready(store, published_at=DAY)
    assert not row(store).pit_usable
    refresh(store, observed="2026-10-13T00:00:00Z", through="2026-10-13")
    assert row(store, day="2026-10-13").pit_usable


@pytest.mark.parametrize("field,value", [("current_yuan", None), ("comparative_yuan", None)])
def test_missing_operand_is_pending_instead_of_crashing_complete_date(store, field, value):
    contract = amount_contract()
    contract["amounts"]["operating_revenue"][field] = value
    ready(store, contract=contract)
    result = row(store)
    assert not result.pit_usable
    assert np.isnan(result.GP_delta)


def test_explicit_scope_barrier_preserved_even_with_amount_equality(store):
    contract = amount_contract()
    contract["current_comparability"]["captured_barriers_preserved"] = [{"kind": "consolidation_scope"}]
    ready(store, contract=contract)
    result = row(store)
    assert not result.pit_usable
    assert result.source_status == "annual_G_comparability_pending"
    assert not result.economic_rejection


def test_missing_and_stale_refresh_cannot_continue_old_numeric_source(store):
    store.append("annual", "000001", annual_payload(), observed_at=OBSERVED)
    industry(store)
    assert row(store).source_status == "source_refresh_pending"
    refresh(store, observed=OBSERVED)
    assert row(store).source_status == "source_refresh_stale"


def test_incomplete_newest_refresh_does_not_fall_back_to_older_complete(store):
    ready(store)
    refresh(store, observed="2026-10-12T01:00:00Z", complete=False, pending_reasons=["latest_query_unfinished"])
    result = row(store)
    assert not result.pit_usable
    assert "refresh" in result.source_status


def test_metadata_refresh_only_needs_last_publication_day_not_future_news(store):
    ready(store)
    refresh(store, observed="2026-10-12T01:00:00Z", through="2026-10-11")
    assert row(store).pit_usable


def test_refresh_gap_after_source_validation_stays_pending(store):
    ready(store)
    refresh(store, observed="2026-10-12T01:00:00Z", since="2026-10-01", through="2026-10-11")
    result = row(store)
    assert not result.pit_usable
    assert result.source_status == "source_refresh_gap_after_validation"


def test_later_consumer_pending_never_selects_previous_ready_fact(store):
    ready(store)
    store.append("annual", "000001", annual_payload(pending_reasons=["latest_H1_version_pending"]),
                 observed_at="2026-10-11T00:00:00Z")
    result = row(store)
    assert not result.pit_usable
    assert result.source_status == "known_annual_revision_or_tail_pending"


def test_fresh_refresh_does_not_certify_years_old_annual_amount(store):
    ready(store, year=2023, annid="old-annual-2023", contract=amount_contract(2023))
    assert row(store).source_status == "new_annual_disclosure_pending"


def test_same_day_ambiguous_global_industry_editions_are_pending(store):
    ready(store)
    industry(store, {"000001": "C31"}, observed="2026-10-11T00:00:00Z")
    assert row(store).source_status == "latest_published_industry_missing"


def test_same_global_industry_edition_scoped_fragments_merge(store):
    codes = ["000001", "000002"]
    for code in codes:
        store.append("annual", code, annual_payload(code, annid=f"annual-{code}"), observed_at=OBSERVED)
    edition = {"vintage_id": "synthetic-2026H1", "source_sha256": "c" * 64}
    first = industry(store, {"000001": "C30"}, **edition)
    second = industry(store, {"000002": "C31"}, observed="2026-10-11T00:00:00Z", **edition)
    refresh(store, codes)
    output = store.asof(codes, DAY)
    assert output.pit_usable.all()
    assert output.sector.tolist() == ["C30", "C31"]
    assert output.industry_event_id.tolist() == [first, second]


def test_conflicting_rows_in_same_global_industry_fragments_stay_pending(store):
    store.append("annual", "000001", annual_payload(), observed_at=OBSERVED)
    edition = {"vintage_id": "synthetic-2026H1", "source_sha256": "c" * 64}
    industry(store, {"000001": "C30"}, **edition)
    industry(store, {"000001": "C31"}, observed="2026-10-11T00:00:00Z", **edition)
    refresh(store)
    assert not row(store).pit_usable


@pytest.mark.parametrize("second_edition", [
    {"vintage_id": "different-version", "source_sha256": "c" * 64},
    {"vintage_id": "synthetic-2026H1", "source_sha256": "d" * 64},
])
def test_different_global_industry_versions_are_not_merged(store, second_edition):
    store.append("annual", "000001", annual_payload(), observed_at=OBSERVED)
    industry(store, {"000001": "C30"}, vintage_id="synthetic-2026H1", source_sha256="c" * 64)
    industry(store, {"000002": "C31"}, observed="2026-10-11T00:00:00Z", **second_edition)
    refresh(store)
    assert not row(store).pit_usable


def test_annual_transport_manifest_change_preserves_fact_and_first_observation(store):
    first = store.append("annual", "000001", annual_payload(), observed_at=OBSERVED)
    second = store.append("annual", "000001", annual_payload(
        source_binding={"sha256": "c" * 64, "path": "new-transport-manifest"}),
        observed_at="2026-10-11T00:00:00Z")
    assert first == second
    annuals = [e for e in store.events(CUTOFF) if e["kind"] == "annual"]
    assert len(annuals) == 1
    assert annuals[0]["observed_at"] == OBSERVED
    assert annuals[0]["source_binding"] == PROOF


def test_financial_sector_remains_outside_original_g_domain(store):
    store.append("annual", "000001", annual_payload(), observed_at=OBSERVED)
    industry(store, {"000001": "J66"})
    refresh(store)
    assert not row(store).pit_usable


def test_continuous_gf_ranks_full_g_before_finite_f_intersection(store):
    codes = ["000001", "000002", "000003", "000004"]
    for n, code in enumerate(codes):
        store.append("annual", code, annual_payload(code, annid=f"annual-{code}",
                     contract=amount_contract(current_revenue=str(100 + n * 10))), observed_at=OBSERVED)
    industry(store, {code: "C30" for code in codes})
    refresh(store, codes)
    g = store.asof(codes, DAY)
    assert g.G_pct.tolist() == [.25, .5, .75, 1]
    index = pd.MultiIndex.from_product([[DAY], codes], names=["date", "symbol"])
    close = pd.Series([10, 11, 12, 13], index=index, name="close")
    observed = []

    def calculate_f(factors):
        observed.append(factors["close"].index.tolist())
        return pd.Series([4., np.nan, 2., 1.], index=index)

    adapter = ContinuousGF(store, codes, calculate_f)
    output = adapter.calculate({"close": close})
    assert observed == [index.tolist()]
    assert np.isnan(output.loc[(DAY, "000002")])
    use = ["000001", "000003", "000004"]
    g_values = pd.Series([.25, .75, 1.])
    f_values = -pd.Series([4., 2., 1.]).rank(method="average", pct=True)

    def z(values):
        clipped = values.clip(*values.quantile([.01, .99]).tolist())
        return (clipped - clipped.mean()) / clipped.std(ddof=0)

    expected = (z(g_values) + z(f_values)) / 2
    assert output.loc[[(DAY, code) for code in use]].to_numpy() == pytest.approx(expected.to_numpy())
    reranked = (z(pd.Series([1/3, 2/3, 1.])) + z(f_values)) / 2
    assert not np.allclose(expected, reranked)
    assert adapter.diagnostics["official_production_channel_certified"] is False


def test_persisted_ledger_append_only_database_trigger(store):
    store.append("annual", "000001", annual_payload(), observed_at=OBSERVED)
    with pytest.raises(Exception, match="immutable"):
        store.db.execute("UPDATE g_events SET observed_at='2000-01-01'")
    with pytest.raises(Exception, match="immutable"):
        store.db.execute("DELETE FROM g_events")
