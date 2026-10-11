"""Real observed numbering/chapter shapes, with synthetic text only."""
from pathlib import Path
import sys
import pytest

sys.path.insert(0,str(Path(__file__).resolve().parent))
from consumer_geometry import geometry,sha


def fixture(chapter='七',next_chapter='八',ar=4,inv=9,extra=''):
    parent=chapter+'、合并财务报表项目注释';following=next_chapter+'、研发支出'
    text=('===SOURCE_PAGE:1===\n2025年年度报告\n'+parent+'\n'+str(ar)+'、应收账款\n'
          '（1）应收账款分类披露\n'+extra+'期末余额\n期初余额\n账面余额 坏账准备 账面价值\n金额 比例（%）金额 计提比例（%）\n'
          '===SOURCE_PAGE:2===\n（2）坏账准备的情况\n期初余额 本期计提 收回或转回 转销或核销 其他 期末余额\n'
          '一、合营企业\n七.30）文字引用\n'+str(ar+1)+'、未知同级项目\n'+str(inv)+'、存货\n'
          '(1)存货分类\n账面余额 跌价准备 账面价值\n(2)存货跌价准备\n期初余额 计提 转回或转销 其他 期末余额\n'
          '一、账面原值\n'+str(inv+1)+'、下一同级项目\n'+following+'\n')
    def item(caption):
        start=text.index(caption);return {'start':start,'end':start+len(caption),'sha256':sha(caption.encode())}
    a,b=item(parent),item(following)
    contract={'chapter_start':a,'chapter_end':b,'chapter_range':{'start':a['start'],'end':b['start'],'sha256':sha(text[a['start']:b['start']].encode())},
              'chapter_start_caption_dense':parent,'chapter_end_caption_dense':following,
              'notes':[{'asset':'AR','ordinal':ar,'next_ordinal':ar+1,'title':'应收账款','start':text.index(str(ar)+'、应收账款'),'next_parent_start':text.index(str(ar+1)+'、未知同级项目')},
                       {'asset':'INV','ordinal':inv,'next_ordinal':inv+1,'title':'存货','start':text.index(str(inv)+'、存货'),'next_parent_start':text.index(str(inv+1)+'、下一同级项目')}]}
    return text,contract


@pytest.mark.parametrize('chapter,next_chapter,ar,inv',[('七','八',4,9),('五','六',4,8),('七','八',5,10)])
def test_observed_three_parent_shapes_keep_bracket_children_and_true_RnD_chapter(chapter,next_chapter,ar,inv):
    text,c=fixture(chapter,next_chapter,ar,inv);result=geometry(text,c)
    assert len(result['notes'])==2 and result['chapter_end']['start']==text.index(next_chapter+'、研发支出')
    for note in result['notes']:
        assert note['geometry_status']=='exact_diagnosed_parent_note_range_bound'
        assert len(note['known_bracket_child_captions'])==2
        assert note['native_header_clues'] and note['note_range']['end']==note['next_same_level_caption']['start']
        assert note['amounts_solved']==0 and note['financial_eligibility'] is False
        assert note['combined_movements_split'] is False and note['percentage_columns_removed'] is False
    clues=[row['brief_native_header'] for row in result['notes'][0]['native_header_clues']]
    assert '期末余额' in clues and any('比例（%）' in line for line in clues)
    assert any('收回或转回' in line and '转销或核销' in line for line in clues)


def test_unknown_bare_numbered_caption_still_terminates_without_borrowing_header():
    text,c=fixture(extra='7、未诊断同级标题\n')
    result=geometry(text,c)['notes'][0]
    assert result['geometry_status']=='unknown_same_level_boundary_pending'
    assert result['note_range']['end']==text.index('7、未诊断同级标题')
    assert result['native_header_clues']==[]


def test_named_chapter_hash_and_exact_report_note_ordinals_cannot_be_relaxed():
    text,c=fixture();c['chapter_end']['sha256']='0'*64
    with pytest.raises(ValueError,match='chapter_span'):geometry(text,c)
    text,c=fixture();c['notes'][0]['next_ordinal']=99
    with pytest.raises(ValueError,match='same_level'):geometry(text,c)
