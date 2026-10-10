"""Text-only decimal fragments must not masquerade as fiscal amount columns."""
import pytest
from panda_alpha.financial_statements import column_pair


@pytest.mark.parametrize('lines',[
    ['4,335,202,621.','92','3,523,285,791.6'],
    ['22,246,423,224','.05','19,759,215,060.'],
    ['7,486,062,467.','65','8,226,716,564.4'],
    ['1,234,','567.89','100.00'],
])
def test_wrapped_decimal_requires_physical_column_evidence(lines):
    cells,snippet=column_pair('',lines)
    assert cells==[]


def test_two_complete_amount_cells_remain_usable():
    cells,_=column_pair('7,486,062,467.65 8,226,716,564.40',[])
    assert cells==['7,486,062,467.65','8,226,716,564.40']


def test_small_complete_prior_amount_is_not_removed_by_magnitude():
    cells,_=column_pair('100 2',[])
    assert cells==['100','2']


@pytest.mark.parametrize('text',['12,34.00 2','100. 2','100 .05','100.00 200.00 extra','1,2345,678','(100)(200)'])
def test_punctuation_and_residual_text_cannot_disappear_in_findall(text):
    assert column_pair(text,[])[0]==[]


@pytest.mark.parametrize('marker',['（七、5)','(七、5）','七、5(2)','六、49'])
def test_complete_chinese_note_brackets_do_not_leave_currency_punctuation(marker):
    assert column_pair(marker+' 100.00 2.00',[])[0]==['100.00','2.00']
