"""Synthetic HTTP pages only; no real endpoint/PDF/source body access."""
from datetime import datetime
from pathlib import Path
import json,sys
import pytest

sys.path.insert(0,str(Path(__file__).resolve().parent));sys.path.insert(0,r'D:\PandaAI-Alpha')
import collect_metadata as c
from factor_workflow import filings_market_index as source


def row(i,code='000001',day='2026-09-21'):
    return {'secCode':code,'announcementId':str(i),'announcementTime':int(datetime.fromisoformat(day+'T00:00:00+08:00').timestamp()*1000),'adjunctUrl':f'finalpage/{day}/{i}.PDF','orgId':'synthetic','announcementTitle':'关于限售股上市流通的公告'}


class Response:
    status_code=200
    def __init__(self,total,rows):self.packet={'totalAnnouncement':total,'announcements':rows};self.content=json.dumps(self.packet).encode()
    def raise_for_status(self):pass
    def json(self):return self.packet


class Session:
    def __init__(self,responses):self.responses=list(responses);self.calls=[]
    def post(self,url,*,data,timeout,allow_redirects):
        assert allow_redirects is False
        self.calls.append(data)
        r=self.responses.pop(0)
        if isinstance(r,Exception):raise r
        return r


def test_search_payload_and_stable_metadata_first3_without_PDF():
    s=Session([Response(3,[row(3,'000002'),row(2,'000001'),row(1,'000001')])]);got=c.keyword_metadata(source,s)
    assert got['query_complete'] and [r['announcement_id'] for r in got['planned_body_first3']]==['1','2','3']
    assert s.calls[0]['searchkey']==c.KEYWORD and s.calls[0]['stock']==s.calls[0]['plate']==s.calls[0]['category']==''
    assert got['body_consumed'] is False and len(s.calls)==1


def test_first_total_above90_stops_without_second_page_or_split():
    s=Session([Response(91,[row(i) for i in range(30)])]);got=c.keyword_metadata(source,s)
    assert got['status']=='SOURCE_PENDING_COVERAGE_PARTIAL_FIRST_PAGE_METADATA_RETAINED' and got['total_announcements']==91
    assert len(s.calls)==1 and len(got['metadata'])==30 and not got['date_window_split_or_keyword_variants_attempted']
    assert not got['query_complete'] and got['body_sample_scope']=='validated_returned_first_page_only_not_whole_window_first3'
    assert len(got['planned_body_first3'])==3 and got['body_consumed'] is False


def test_duplicate_ID_does_not_create_complete_sampling_manifest():
    s=Session([Response(31,[row(i) for i in range(30)]),Response(31,[row(1)])]);got=c.keyword_metadata(source,s)
    assert got['error_type']=='PaginationUnstable' and not got['planned_body_first3']
    assert len(s.calls)==2


def test_transport_failure_is_source_pending_no_inherited_three_retries():
    ConnectionError=type('ConnectionError',(Exception,),{})
    s=Session([ConnectionError('synthetic transport failure')]);got=c.keyword_metadata(source,s)
    assert len(s.calls)==1 and got['error_type']=='SourceTransportPending'
    assert not got['query_complete'] and not got['planned_body_first3']


def test_maximum90_exact_three_pages_and_no_fourth():
    s=Session([Response(90,[row(i) for i in range(a,a+30)]) for a in (1,31,61)])
    got=c.keyword_metadata(source,s)
    assert got['query_complete'] and len(got['metadata'])==90 and len(s.calls)==3


def test_supporting_employee_revision_titles_retained_but_not_same_primary_supply():
    rows=[row(i) for i in range(1,5)]
    rows[0]['announcementTitle']='证券公司关于限售股上市流通的核查意见'
    rows[1]['announcementTitle']='关于股权激励限制性股票限售股上市流通的公告'
    rows[2]['announcementTitle']='关于限售股上市流通的更正公告'
    got=c.keyword_metadata(source,Session([Response(4,rows)]))
    assert len(got['metadata'])==4 and [r['announcement_id'] for r in got['planned_body_first3']]==['4']
    assert [r['kind'] for r in got['title_classification_inventory']]==['supporting_intermediary_opinion_title','employee_or_incentive_unlock_title_separate_scope','revision_cancellation_or_reschedule_title_pending','company_primary_notice_title_candidate_body_pending']
    assert all(not r['actual_circulating_supply_or_tradability_inferred'] for r in got['title_classification_inventory'])
    for title in ('律师关于限售股上市流通的公告','保荐机构关于限售股上市流通的公告'):
        assert c.title_classification({'title':title})['kind']=='supporting_intermediary_opinion_title'


def test_redirect_is_pending_no_followed_POST_or_body_selection():
    r=Response(1,[row(1)]);r.status_code=307
    s=Session([r]);got=c.keyword_metadata(source,s)
    assert len(s.calls)==1 and got['error_type']=='SourceTransportPending' and got['planned_body_first3']==[]
    assert 'metadata_redirect_response_not_accepted' in got['error']
