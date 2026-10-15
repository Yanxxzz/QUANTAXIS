"""Source-supported two-board financing pressure, available before entry."""
import math

def financing_report_gate(rows):
    """Use two explicit daily monetary reports; no historical eligibility claim.

    Cash-equity selection does not require permission to borrow the stock.
    Its information comparison is limited to two positive financing balances
    and nonzero gross reported activity. Absence is not zero leverage.
    """
    if len(rows)!=2 or any(r is None for r in rows):
        return {'known':False,'pass':None,'score':None,'reason':'two_reports_required'}
    if len({r.get('DATE') for r in rows})!=2 or len({r.get('SCODE') for r in rows})!=1:
        return {'known':False,'pass':None,'score':None,'reason':'identity_mismatch'}
    totals=[]
    for row in rows:
        try:
            if any(isinstance(row.get(k),bool) for k in ['RZYE','RZMRE','RZCHE','RZJME']): raise ValueError()
            bal,buy,repay,net=[float(row[k]) for k in ['RZYE','RZMRE','RZCHE','RZJME']]
        except (KeyError,TypeError,ValueError):
            return {'known':False,'pass':None,'score':None,'reason':'invalid_amount'}
        if not all(math.isfinite(v) for v in [bal,buy,repay,net]) or bal<=0 or min(buy,repay)<0:
            return {'known':False,'pass':None,'score':None,'reason':'invalid_amount'}
        if not math.isclose(buy-repay,net,rel_tol=1e-9,abs_tol=.01):
            return {'known':False,'pass':None,'score':None,'reason':'reported_net_mismatch'}
        totals.append((buy,repay))
    gross=sum(a+b for a,b in totals);net=sum(a-b for a,b in totals)
    if gross<=0: return {'known':False,'pass':None,'score':None,'reason':'zero_gross_activity'}
    return {'known':True,'pass':net<=0,'score':-net/gross,'reason':None}
