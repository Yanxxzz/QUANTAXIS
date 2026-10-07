import json
import unittest
from unittest.mock import patch
import numpy as np
import pandas as pd
from panda_alpha.study import (StudyInputError, normalize_source_panel, prepare_study,
                               validate_study, evaluate_study, replay_portfolio,PotentialQuoteBook)
from tests.study_fixture import synthetic_study


class StudyJourneyTests(unittest.TestCase):
    def setUp(self):
        self.protocol,self.source,self.quotes,self.comparator=synthetic_study()

    def journey(self,source=None,quotes=None,comparator=True,callback=None):
        s=self.source if source is None else source;q=self.quotes if quotes is None else quotes
        other=self.comparator if comparator else None
        prep=prepare_study(self.protocol,s,q,other,source_receipt={"scope":"public_synthetic_fixture"})
        result=evaluate_study(prep,self.protocol,s,q,other,source_receipt={"scope":"public_synthetic_fixture"},quality_callback=callback)
        return prep,result

    def test_nonlegacy_name_full_two_cost_market_pair_allgroups_journey(self):
        prep,r=self.journey(callback=lambda result:{"joint_cost_count":len(result["cost_reviews"]),"candidate":result["candidate_id"]})
        self.assertEqual("ORBIT07",r["candidate_id"])
        self.assertEqual("evaluated_research",r["status"])
        self.assertEqual(42,len(r["runs"]))
        self.assertEqual(2,len(r["cost_reviews"]))
        self.assertEqual(10,len(r["all_groups"]["0.003"]["ORBIT07"]))
        self.assertEqual(2,r["quality_callback_result"]["joint_cost_count"])
        for review in r["comparisons"]:
            self.assertEqual("paired_potential_wealth_evaluated",review["pool_increment_status"])
            candidate=next(run for run in r["runs"] if run["name"]=="ORBIT07_G10" and run["cost"]==review["cost"])
            other=next(run for run in r["runs"] if run["name"]=="REFERENCE11_G01" and run["cost"]==review["cost"])
            for a,b,pair in zip(candidate["daily"],other["daily"],review["pair_daily"]):
                self.assertAlmostEqual(pair["nav"],(a["nav"]+b["nav"])/2,14)
            self.assertLess(candidate["accounting"]["max_absolute_accounting_error"],1e-12)
            self.assertEqual(5,len(candidate["accounting"]["folds"]))
            self.assertIn("positive_contribution_hhi",candidate["accounting"]["concentration"])
            self.assertFalse(candidate["auction_fills_or_capacity_certified"])
        self.assertFalse(r["quality"]["formal_admission"]["eligible"])
        json.dumps(r,allow_nan=False)

    def test_frozen_input_target_and_receipt_changes_cannot_run(self):
        prep=prepare_study(self.protocol,self.source,self.quotes,self.comparator,source_receipt={"v":1})
        changed=self.quotes.copy();changed.loc[0,"open"]+=1
        self.assertFalse(validate_study(prep,self.protocol,self.source,changed,self.comparator,source_receipt={"v":1})["verified"])
        with self.assertRaises(StudyInputError):evaluate_study(prep,self.protocol,self.source,self.quotes,self.comparator,source_receipt={"v":2})
        prep["targets"][0]["candidate_groups"]["10"]=[]
        self.assertFalse(validate_study(prep,self.protocol,self.source,self.quotes,self.comparator,source_receipt={"v":1})["verified"])

    def test_true_value_quantile_ties_do_not_get_split_by_code(self):
        flat=self.source.copy();flat["value"]=1.
        prep,r=self.journey(source=flat)
        self.assertEqual("source_pending",prep["status"])
        self.assertEqual("source_pending",r["status"])
        self.assertFalse(r["quality"]["factor_economically_rejected"])
        self.assertTrue(all(not codes for t in prep["targets"] for codes in t["candidate_groups"].values()))

    def test_unknown_raw_or_trade_status_is_execution_error_not_weak_factor(self):
        for column in ["trade_status","raw_open"]:
            _,r=self.journey(quotes=self.quotes.drop(columns=column),comparator=False)
            self.assertEqual("execution_error",r["status"])
            self.assertFalse(r["quality"]["factor_economically_rejected"])
            self.assertTrue(r["errors"])

    def test_optional_comparator_does_not_fake_pool_increment(self):
        _,r=self.journey(comparator=False)
        self.assertEqual(22,len(r["runs"]))
        self.assertTrue(all(c["pool_increment_status"]=="comparator_not_supplied" for c in r["comparisons"]))
        self.assertTrue(all("pair_metrics" not in c for c in r["comparisons"]))

    def test_same_common_support_and_arbitrary_factor_names(self):
        self.source.loc[self.source.symbol.eq("S00"),"pit_usable"]=False
        self.comparator.loc[self.comparator.symbol.eq("S35"),"value"]=np.nan
        prep=prepare_study(self.protocol,self.source,self.quotes,self.comparator)
        for target in prep["targets"]:
            expected=set(target["common_codes"])
            self.assertEqual(34,len(expected))
            self.assertEqual(expected,set(sum(target["candidate_groups"].values(),[])))
            self.assertEqual(expected,set(sum(target["comparator_groups"].values(),[])))

    def test_formula_wide_panel_and_explicit_boolean_source_flags(self):
        wide=self.source.pivot(index="date",columns="symbol",values="value")
        long=normalize_source_panel(wide)
        self.assertEqual(len(self.source),len(long))
        self.assertTrue(long.pit_usable.all())
        bad=self.source.copy();bad["pit_usable"]="False"
        with self.assertRaises(StudyInputError):normalize_source_panel(bad)

    def test_small_configured_groups_cycle_and_warmup(self):
        p,s,q,_=synthetic_study(names=6,sessions=12);p["warmup_sessions"]=2
        prep=prepare_study(p,s,q)
        self.assertEqual(q.date.unique()[2],prep["anchors"][0])
        self.assertEqual(2,prep["protocol"]["groups"])
        self.assertEqual(1,prep["protocol"]["cycle"])
        self.assertEqual(3,prep["protocol"]["min_assets"])

    def test_independent_calendar_whole_session_loss_does_not_compress_cycle(self):
        missing=self.protocol["calendar"][3]
        quotes=self.quotes[~self.quotes.date.eq(missing)]
        prep=prepare_study(self.protocol,self.source,quotes)
        self.assertEqual("source_pending",prep["status"])
        self.assertEqual([missing],prep["source_coverage"]["missing_whole_sessions"])
        self.assertEqual(self.protocol["calendar"][5],prep["anchors"][1])
        self.assertEqual(self.protocol["calendar"][6],prep["targets"][0]["planned_exit_date"])
        result=evaluate_study(prep,self.protocol,self.source,quotes)
        self.assertEqual("source_pending",result["quality"]["research_state"])
        self.assertEqual([],result["runs"])

    def test_observed_quote_dates_are_not_a_verified_market_calendar(self):
        protocol={k:v for k,v in self.protocol.items() if k not in {"calendar","calendar_verified"}}
        prep=prepare_study(protocol,self.source,self.quotes)
        self.assertFalse(prep["source_coverage"]["calendar_verified"])
        self.assertEqual("source_pending",prep["status"])
        receipt={"calendar":self.protocol["calendar"],"calendar_verified":True,"calendar_evidence_scope":"synthetic_fixture"}
        prep=prepare_study(protocol,self.source,self.quotes,source_receipt=receipt)
        self.assertEqual("source_ready",prep["status"])
        self.assertEqual("source_receipt",prep["source_coverage"]["calendar_origin"])
        with self.assertRaises(StudyInputError):
            prepare_study({**protocol,"calendar":self.protocol["calendar"][::-1]},self.source,self.quotes)

    def test_partial_source_anchors_keep_diagnostics_without_economic_rejection(self):
        p,s,q,_=synthetic_study(names=6,sessions=12)
        # Only half the anchors have usable factor sources. A complete price
        # calendar cannot make missing formation information an economic test.
        s.loc[s.date.isin(p["calendar"][1::2]),"pit_usable"]=False
        for field in ["open","close","raw_open","raw_close"]:q[field]=10.
        for field in ["high","raw_high"]:q[field]=10.1
        for field in ["low","raw_low"]:q[field]=9.9
        prep=prepare_study(p,s,q)
        self.assertEqual("source_partial",prep["status"])
        result=evaluate_study(prep,p,s,q)
        self.assertEqual("source_pending",result["status"])
        self.assertFalse(result["quality"]["source_coverage"]["all_anchors_ready"])
        self.assertFalse(result["quality"]["factor_economically_rejected"])
        self.assertTrue(result["diagnostic_only_partial_source"])
        self.assertEqual(2,len(result["cost_reviews"]))
        self.assertTrue(result["runs"])

    def test_preparation_binds_actual_accounting_and_performance_cores(self):
        prep=prepare_study(self.protocol,self.source,self.quotes)
        self.assertIn("attribution_core",prep["input_hashes"])
        self.assertIn("performance_core",prep["input_hashes"])
        changed={"attribution_core":"f"*64,"performance_core":prep["input_hashes"]["performance_core"]}
        with patch("panda_alpha.study._core_hashes",return_value=changed):
            self.assertFalse(validate_study(prep,self.protocol,self.source,self.quotes)["verified"])
            with self.assertRaises(StudyInputError):evaluate_study(prep,self.protocol,self.source,self.quotes)

    def test_study_does_not_impose_formal_sharpe_or_score_threshold(self):
        _,result=self.journey(comparator=False)
        admission=result["quality"]["formal_admission"]
        self.assertEqual("independent_admission_review_pending",admission["status"])
        self.assertFalse(admission["eligible"])
        self.assertNotIn("sharpe_requirement",admission)

    def test_named_factorseries_reorders_symbol_date_and_unknown_names_reject(self):
        index=pd.MultiIndex.from_tuples([("S01",pd.Timestamp("2024-01-02"))],names=["symbol","date"])
        normalized=normalize_source_panel(pd.Series([.5],index=index))
        self.assertEqual("S01",normalized.iloc[0]["symbol"])
        self.assertEqual("2024-01-02",normalized.iloc[0]["date"])
        with self.assertRaises(StudyInputError):normalize_source_panel(pd.Series([.5],index=index.set_names([None,None])))

    def test_explicit_comparator_cannot_reuse_candidate_identity(self):
        p={**self.protocol,"comparator_id":self.protocol["candidate_id"]}
        with self.assertRaisesRegex(StudyInputError,"Distinct"):
            prepare_study(p,self.source,self.quotes,self.comparator)

    def test_two_low_direction_panels_have_positive_aligned_factor_dependence(self):
        p={**self.protocol,"direction":0,"comparator_direction":0}
        prep=prepare_study(p,self.source,self.quotes,self.comparator)
        self.assertAlmostEqual(1.,prep["factor_dependence"][0]["signed_spearman"])


class ContinuousAccountingTests(unittest.TestCase):
    def setUp(self):
        self.p,self.source,self.frame,_=synthetic_study(names=3,sessions=6)
        self.days=sorted(self.frame.date.unique())[1:]
        self.schedule={self.days[0]:{"decision_date":sorted(self.frame.date.unique())[0],"codes":["S02"]}}

    def replay(self,frame=None,cost=.005,schedule=None):
        return replay_portfolio("ALT47",self.schedule if schedule is None else schedule,self.days,PotentialQuoteBook(self.frame if frame is None else frame),cost)

    def test_constant_marks_exact_entry_exit_fees_and_quantity_chain(self):
        frame=self.frame.copy()
        for field in ["open","close","raw_open","raw_close"]:frame[field]=10.
        for field in ["high","raw_high"]:frame[field]=10.1
        for field in ["low","raw_low"]:frame[field]=9.9
        r=self.replay(frame)
        self.assertAlmostEqual((1-.005)/(1+.005)-1,r["metrics"]["compounded_return"],14)
        self.assertEqual(2,len(r["trades"]))
        self.assertAlmostEqual(r["trades"][0]["quantity_change"],1/(1.005*10),14)
        self.assertFalse(r["final_open_positions"])
        self.assertAlmostEqual(r["accounting"]["nav_change"],-r["accounting"]["fees"],14)

    def test_explicit_suspended_entry_stays_cash_without_fees(self):
        frame=self.frame.copy();mask=frame.date.eq(self.days[0]) & frame.symbol.eq("S02")
        frame.loc[mask,"trade_status"]=0
        r=self.replay(frame)
        self.assertEqual(1.,r["daily"][0]["nav"])
        self.assertEqual(0.,r["accounting"]["fees"])
        self.assertFalse(r["trades"])
        self.assertEqual(1,r["blocked_orders"]["buy_blocked_suspension"])

    def test_blocked_rotation_keeps_stock_and_does_not_spend_unsold_value(self):
        frame=self.frame.copy();rotation=self.days[1]
        frame.loc[frame.date.eq(rotation)&frame.symbol.eq("S02"),"trade_status"]=0
        schedule={**self.schedule,rotation:{"decision_date":self.days[0],"codes":["S00"]}}
        r=self.replay(frame,schedule=schedule)
        self.assertIn("S02",r["daily"][1]["positions_after"])
        self.assertNotIn("S00",r["daily"][1]["positions_after"])
        self.assertEqual(1,r["blocked_orders"]["sell_blocked_suspension"])

    def test_terminal_known_suspension_retains_marked_position_not_fake_cash(self):
        frame=self.frame.copy();mask=frame.date.eq(self.days[-1])&frame.symbol.eq("S02")
        frame.loc[mask,"trade_status"]=0
        frame.loc[mask,["open","close"]]=np.nan
        r=self.replay(frame)
        self.assertTrue(r["terminal_liquidation_pending"])
        self.assertIn("S02",r["final_open_positions"])
        self.assertTrue(r["daily"][-1]["known_suspension_mark"])
        self.assertLess(r["daily"][-1]["cash_after"],1e-12)

    def test_unknown_held_price_does_not_become_zero_return(self):
        frame=self.frame.copy();mask=frame.date.eq(self.days[1])&frame.symbol.eq("S02")
        frame.loc[mask,"close"]=np.nan
        with self.assertRaisesRegex(ValueError,"unknown_quote"):self.replay(frame)

    def test_absent_risk_metadata_is_unknown_not_zero_or_fake_industry(self):
        r=self.replay()
        exposure=r["exposures"]
        self.assertEqual("risk_metadata_pending",exposure["status"])
        self.assertIsNone(exposure["summary"]["mean_beta_exposure"])
        self.assertIsNone(exposure["summary"]["maximum_industry_wealth_fraction"])
        first=exposure["daily"][0]
        self.assertAlmostEqual(1.,first["unknown_beta_wealth_fraction"])
        self.assertAlmostEqual(1.,first["unknown_sector_wealth_fraction"])
        self.assertEqual({},first["known_sector_wealth"])

    def test_declared_pit_asof_risk_metadata_tracks_real_position_weights(self):
        frame=self.frame.copy();frame["beta"]=1.5;frame["sector"]="MANUFACTURING"
        frame["beta_asof"]="2023-12-29";frame["sector_asof"]="2023-12-29"
        frame["beta_pit_usable"]=True;frame["sector_pit_usable"]=True
        r=self.replay(frame);exposure=r["exposures"]
        self.assertEqual("supplied_risk_metadata_available",exposure["status"])
        first=exposure["daily"][0]
        self.assertAlmostEqual(1.5,first["beta_exposure"])
        self.assertAlmostEqual(1.,first["known_sector_wealth"]["MANUFACTURING"])
        self.assertEqual(0.,first["unknown_beta_wealth_fraction"])
        self.assertAlmostEqual(r["accounting"]["fees"],exposure["summary"]["total_fees_initial_equity_units"])

    def test_current_snapshot_without_pit_asof_and_future_metadata_stays_pending(self):
        frame=self.frame.copy();frame["sector"]="CURRENT_ONLY";frame["beta"]=100.
        self.assertEqual("risk_metadata_pending",self.replay(frame)["exposures"]["status"])
        frame["sector_pit_usable"]=True;frame["beta_pit_usable"]=True
        frame["sector_asof"]="2030-01-01";frame["beta_asof"]="2030-01-01"
        self.assertIsNone(self.replay(frame)["exposures"]["summary"]["mean_beta_exposure"])

    def test_new_public_unknown_sector_supersedes_older_classification(self):
        frame=self.frame.copy();frame["sector"]="MANUFACTURING"
        frame["sector_asof"]="2023-12-29";frame["sector_pit_usable"]=True
        mask=frame.date.ge(self.days[1])
        frame.loc[mask,"sector"]="UNKNOWN"
        frame.loc[mask,"sector_asof"]=self.days[1]
        frame.loc[mask,"sector_pit_usable"]=False
        exposure=self.replay(frame)["exposures"]["daily"]
        self.assertEqual(0.,exposure[0]["unknown_sector_wealth_fraction"])
        self.assertAlmostEqual(1.,exposure[1]["unknown_sector_wealth_fraction"])
        self.assertEqual({},exposure[1]["known_sector_wealth"])

    def test_new_declared_missing_beta_does_not_carry_stale_estimate(self):
        frame=self.frame.copy();frame["beta"]=1.5
        frame["beta_asof"]="2023-12-29";frame["beta_pit_usable"]=True
        mask=frame.date.ge(self.days[1])
        frame.loc[mask,"beta"]=np.nan
        frame.loc[mask,"beta_asof"]=self.days[1]
        frame.loc[mask,"beta_pit_usable"]=False
        exposure=self.replay(frame)["exposures"]["daily"]
        self.assertAlmostEqual(1.5,exposure[0]["beta_exposure"])
        self.assertIsNone(exposure[1]["beta_exposure"])
        self.assertAlmostEqual(1.,exposure[1]["unknown_beta_wealth_fraction"])


if __name__=="__main__":unittest.main()
