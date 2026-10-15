"""Pre-order participation gates. Unknown is not zero and no t-day data used."""
import math

def _finite(x):
    if isinstance(x,bool): return None
    try: v=float(x)
    except (TypeError, ValueError): return None
    return v if math.isfinite(v) else None

def turnover_gate(first_board, second_board):
    """Fixed continuation of participation: second turnover >= first > 0.

    This is a testable participation hypothesis, not a claim of net inflow,
    identifiable buyers, absorption of offers or executable queue depth.
    Both inputs are completed days preceding the entry decision.
    """
    a,b=_finite(first_board),_finite(second_board)
    if a is None or b is None or a<=0 or b<=0:
        return {'known':False,'score':None,'pass':None,'reason':'two_positive_turnovers_required'}
    return {'known':True,'score':b/a,'pass':b>=a,'reason':None}

def financing_gate(board_rows):
    """Fixed no-new-leverage gate over exactly two known financing observations.

    Requires dated historical financing eligibility and known nonnegative
    monetary inputs in consistent units. Repayment can include corporate-action
    adjustments: this does not identify voluntary selling or forced liquidation.
    """
    if len(board_rows)!=2: return {'known':False,'score':None,'pass':None,'reason':'two_days_required'}
    amounts=[]
    for row in board_rows:
        if row.get('eligible') is not True:
            return {'known':False,'score':None,'pass':None,'reason':'eligibility_unknown_or_inapplicable'}
        x=[_finite(row.get(k)) for k in ['fin_value','fin_buy_value','fin_refund_value']]
        if any(v is None or v<0 for v in x) or x[0]<=0:
            return {'known':False,'score':None,'pass':None,'reason':'amounts_or_units_pending'}
        amounts.append(x)
    gross=sum(x[1]+x[2] for x in amounts)
    net=sum(x[1]-x[2] for x in amounts)
    if gross<=0: return {'known':False,'score':None,'pass':None,'reason':'no_observed_gross_activity'}
    return {'known':True,'score':-net/gross,'pass':net<=0,'reason':None}
