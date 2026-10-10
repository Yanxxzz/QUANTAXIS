from decimal import Decimal as D
import unittest
from panda_alpha.earnings import (ProfitInterval,range_news,event_timing,
    latest_public_guidance,observed_response,unabsorbed_positive_signal,guidance_midpoint_realization)

def interval(a,b,q='1'):
    return ProfitInterval(D(a)-D(q),D(b)+D(q),(D(a),D(b)),(D(q),D(q)))

class EarningsNewsTests(unittest.TestCase):
    def test_growth_from_last_year_is_not_news_inside_prior_guidance(self):
        self.assertEqual('neutral_overlap',range_news(interval('100','200'),interval('160','160'))['sign'])
    def test_within_range_midpoint_resolution_is_separate_assumption(self):
        old,new=interval('100','200'),interval('180','180')
        self.assertEqual('neutral_overlap',range_news(old,new)['sign'])
        r=guidance_midpoint_realization(old,new)
        self.assertEqual('positive',r['sign'])
        self.assertEqual(.15,r['score'])
        self.assertIn('not_market_consensus',r['expectation_proxy'])
    def test_loss_issuer_can_have_positive_new_information(self):
        self.assertEqual('positive',range_news(interval('-200','-150'),interval('-100','-80'))['sign'])
    def test_precision_boundary_is_not_a_false_surprise(self):
        self.assertEqual('neutral_overlap',range_news(interval('100','120'),interval('121','121'))['sign'])
    def test_zero_profit_uses_reported_precision_not_arbitrary_epsilon(self):
        r=range_news(interval('0','0','.01'),interval('1','1','.01'))
        self.assertEqual('0.01',r['scale_yuan']);self.assertEqual('positive',r['sign'])
    def test_invalid_or_unknown_source_amount_rejected(self):
        with self.assertRaises(ValueError):interval('200','100')
        with self.assertRaises(ValueError):interval('NaN','100')
    def test_future_revision_and_same_day_disclosure_not_used(self):
        rows=[{'code':'000001','fiscal_period':'2024-FY','pub_date':day,
            'announcement_id':day,'status':'SOURCE_VERIFIED','profit':{'lower_yuan':str(x),'upper_yuan':str(x)}}
            for day,x in [('2025-01-30',1),('2025-02-10',100),('2025-02-11',200)]]
        r=latest_public_guidance(rows,code='000001',period='2024-FY',publication='2025-02-10')
        self.assertEqual('1',r['profit']['lower_yuan'])
    def test_unknown_latest_revision_blocks_old_value_fallback(self):
        rows=[{'code':'000001','fiscal_period':'2024-FY','pub_date':'2025-01-30','announcement_id':'a',
              'status':'SOURCE_VERIFIED','profit':{'lower_yuan':'1','upper_yuan':'2'}},
              {'code':'000001','fiscal_period':'2024-FY','pub_date':'2025-02-01','announcement_id':'b','status':'SOURCE_PENDING'}]
        self.assertEqual('pending_latest_guidance',latest_public_guidance(rows,code='000001',period='2024-FY',publication='2025-02-10')['status'])
    def test_conflicting_same_day_guidance_stays_pending(self):
        rows=[{'code':'000001','fiscal_period':'2024-FY','pub_date':'2025-01-30','announcement_id':str(x),
              'status':'SOURCE_VERIFIED','profit':{'lower_yuan':str(x),'upper_yuan':str(x+1)}} for x in [1,5]]
        self.assertEqual('pending_same_day_competing_guidance',latest_public_guidance(rows,code='000001',period='2024-FY',publication='2025-02-10')['status'])
    def test_publication_then_decision_close_then_next_open(self):
        cal=['2025-01-01','2025-01-02','2025-01-03','2025-01-06','2025-01-07','2025-01-08','2025-01-09','2025-01-10']
        t=event_timing(cal,'2025-01-03',holding=2,extension=1)
        self.assertEqual('2025-01-02',t['response_reference_date'])
        self.assertEqual('2025-01-06',t['decision_date']);self.assertEqual('2025-01-07',t['entry_date'])
        self.assertEqual('2025-01-09',t['planned_exit_date'])
    def test_incomplete_future_schedule_remains_pending(self):
        with self.assertRaises(ValueError):event_timing(['2025-01-01','2025-01-02','2025-01-03'],'2025-01-02')
    def test_missing_response_cannot_become_underreaction(self):
        with self.assertRaises(ValueError):unabsorbed_positive_signal(1,float('nan'))
        with self.assertRaises(ValueError):observed_response(0,10)
        self.assertTrue(unabsorbed_positive_signal(1,0));self.assertFalse(unabsorbed_positive_signal(1,.01))

if __name__=='__main__':unittest.main()
