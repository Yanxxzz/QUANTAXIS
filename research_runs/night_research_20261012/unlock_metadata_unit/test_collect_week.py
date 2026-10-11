from dataclasses import dataclass
from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import unittest

p = Path(__file__).with_name('collect_week.py')
spec = importlib.util.spec_from_file_location('unlock_new_week_tests', p)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

@dataclass
class Row:
    announcement_id: str
    symbol: str = '600001'
    published_at: str = '2026-09-15'
    title: str = '关于限售股上市流通的公告'

class Session:
    def __init__(self, packets):
        self.packets, self.calls = packets, []
    def post(self, url, **kwargs):
        self.calls.append(kwargs)
        packet = self.packets[len(self.calls)-1]
        return SimpleNamespace(status_code=200, raise_for_status=lambda: None,
                               content=json.dumps(packet).encode(), json=lambda: packet)

class MetadataTests(unittest.TestCase):
    def run_source(self, packets, deadline=None):
        source = SimpleNamespace(QUERY_URL='mock', _day=lambda x:x,
                                 _record=lambda r,*_:Row(str(r['announcementId'])))
        session = Session(packets)
        result = m.collect(source,session,deadline or datetime(2099,1,1,tzinfo=timezone.utc))
        return result, session
    def test_one_page_complete(self):
        result, s = self.run_source([{'totalAnnouncement':1,'announcements':[{'announcementId':1}]}])
        self.assertTrue(result['query_complete']); self.assertEqual(len(s.calls),1)
        self.assertFalse(s.calls[0]['allow_redirects'])
        self.assertEqual(s.calls[0]['data']['seDate'],'2026-09-14~2026-09-20')
    def test_total_drift_keeps_first_page(self):
        result,s = self.run_source([{'totalAnnouncement':31,'announcements':[{'announcementId':1}]},
                                   {'totalAnnouncement':32,'announcements':[{'announcementId':2}]}])
        self.assertFalse(result['query_complete']); self.assertEqual(len(result['metadata']),1)
        self.assertEqual(len(s.calls),2)
    def test_deadline_no_request(self):
        result,s = self.run_source([],datetime(2000,1,1,tzinfo=timezone.utc))
        self.assertEqual(len(s.calls),0);self.assertFalse(result['query_complete'])
    def test_intermediary_and_employee_not_primary(self):
        for title in ['保荐机构关于限售股上市流通核查意见','限制性股票激励解除限售股上市流通公告']:
            self.assertNotEqual(m.title_kind({'title':title}),'primary_title_only_body_pending')

if __name__=='__main__':unittest.main()
