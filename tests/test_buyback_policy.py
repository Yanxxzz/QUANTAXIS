import unittest
from panda_alpha.buyback_policy import expanding_participation_groups


class PolicyTests(unittest.TestCase):
    def events(self):
        return [{'announcement_id':str(n),'symbol':f'A{n:02d}',
                 'decision_date':f'2025-07-{n+1:02d}','score':n/100} for n in range(12)]

    def test_later_peer_changes_do_not_change_past_groups(self):
        e=self.events();old=expanding_participation_groups(e,3)
        for r in e[8:]:r['score']=1000
        new=expanding_participation_groups(e,3)
        self.assertEqual(old[:8],new[:8])

    def test_same_day_peers_are_not_used_to_set_each_others_cutoffs(self):
        e=self.events();e[5]['decision_date']=e[4]['decision_date']
        result=expanding_participation_groups(e,3)
        rows=[r for r in result if r['decision_date']==e[4]['decision_date']]
        self.assertEqual(rows[0]['historical_event_ids'],rows[1]['historical_event_ids'])
        self.assertTrue(all(r['history_last_day']<r['decision_date'] for r in rows))

    def test_insufficient_or_flat_history_does_not_invent_groups(self):
        e=self.events()
        self.assertEqual(expanding_participation_groups(e,3)[0]['policy_arm'],'CALIBRATION_PENDING')
        for r in e:r['score']=0
        self.assertEqual(expanding_participation_groups(e,3)[-1]['policy_arm'],'NONDISCRIMINATING_HISTORY')

    def test_input_order_does_not_change_classification(self):
        e=self.events()
        self.assertEqual(expanding_participation_groups(e,3),expanding_participation_groups(list(reversed(e)),3))

    def test_duplicate_event_ids_are_rejected(self):
        e=self.events();e[1]['announcement_id']=e[0]['announcement_id']
        with self.assertRaises(ValueError):expanding_participation_groups(e,3)


if __name__=='__main__':unittest.main()
