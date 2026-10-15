"""Prior-board credit stock and new-credit participation, without outcomes."""
from __future__ import annotations
import math

def positive(x):
    return not isinstance(x,bool) and isinstance(x,(float,int)) and math.isfinite(x) and x>0

def credit_crowding(balance, buys, market, float_shares):
    """Both controls share exact inputs; missing roles never become zero.

    Shares are verified against the previously established turnover-percentage
    identity. Traded CNY is checked against the day's low/high times shares.
    These are consistency checks, not proof of vintage/PIT or executions.
    """
    bad=lambda why:{'known':False,'reason':why,'stock_ratio':None,'buy_ratio':None,
                    'stock_pass':None,'buy_pass':None}
    if not positive(balance) or not positive(float_shares):return bad('balance_or_float_missing')
    if len(buys)!=2 or len(market)!=2:return bad('two_board_inputs_required')
    for b,m in zip(buys,market):
        if not isinstance(b,(int,float)) or isinstance(b,bool) or not math.isfinite(b) or b<0:return bad('buy_missing_or_negative')
        if not isinstance(m,dict) or not all(positive(m.get(k)) for k in ['amount','volume','low','high','close','turnover']):return bad('market_role_missing')
        if m['low']>m['high'] or not m['low']<=m['close']<=m['high']:return bad('invalid_price_order')
        if not m['low']*m['volume']*.99<=m['amount']<=m['high']*m['volume']*1.01:return bad('CNY_amount_share_consistency_pending')
        if b>m['amount']*1.01:return bad('financing_buy_exceeds_market_amount')
    if abs(market[1]['turnover']/100*float_shares/market[1]['volume']-1)>.01:return bad('float_share_turnover_identity_pending')
    stock=balance/(float_shares*market[1]['close'])
    flow=sum(buys)/sum(m['amount'] for m in market)
    if not math.isfinite(stock) or not math.isfinite(flow):return bad('nonfinite_ratio')
    return {'known':True,'reason':None,'stock_ratio':stock,'buy_ratio':flow,
            'stock_pass':stock<=.05,'buy_pass':flow<=.10}
