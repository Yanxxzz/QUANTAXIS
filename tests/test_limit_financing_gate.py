from panda_alpha.limit_financing_gate import financing_report_gate as gate

def rows():
    return [dict(SCODE='000001',DATE=d,RZYE=100,RZMRE=b,RZCHE=10,RZJME=b-10) for d,b in [('2025-01-02',5),('2025-01-03',15)]]

def test_zero_net_boundary():
    assert gate(rows())=={'known':True,'pass':True,'score':0,'reason':None}

def test_missing_not_zero_leverage():
    assert not gate([None,rows()[1]])['known']

def test_date_and_issuer_mismatch():
    r=rows();r[1]['DATE']=r[0]['DATE'];assert not gate(r)['known']
    r=rows();r[1]['SCODE']='000002';assert not gate(r)['known']

def test_unit_scaling_and_counterflow():
    r=rows();r[1].update(RZMRE=20,RZJME=10)
    assert gate(r)['pass'] is False
    scaled=[{k:(v*10000 if k.startswith('RZ') else v) for k,v in x.items()} for x in r]
    assert gate(r)==gate(scaled)

def test_net_reconciliation_and_invalid_value():
    r=rows();r[0]['RZJME']=99;assert gate(r)['reason']=='reported_net_mismatch'
    r=rows();r[0]['RZYE']=float('nan');assert not gate(r)['known']
