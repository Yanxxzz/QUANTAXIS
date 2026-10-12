"""Market cost-shock protection, fixed research definition, native close/amount.

Amihud-inspired aggregate cost proxy, not the Pastor-Stambaugh reversal factor.
Positive score is the signed cost-shock beta after controlling market return.
There is no financial SOURCE literal or fixed historical output window.
"""
import numpy as np
import pandas as pd


class MarketCostShockProtection(Factor):
    @staticmethod
    def _model_inputs(factors):
        close=factors['close'];amount=factors['amount']
        if (list(close.index.names)!=['date','symbol'] or not close.index.is_unique
                or not amount.index.equals(close.index)):
            raise ValueError('Aligned unique daily date/symbol price and amount required')
        close=pd.to_numeric(close,errors='coerce').sort_index(level=['symbol','date'])
        amount=pd.to_numeric(amount,errors='coerce').reindex(close.index)
        valid=close.where(np.isfinite(close)&close.gt(0))
        r=valid.groupby(level='symbol',sort=False).pct_change(fill_method=None)
        r=r.where(np.isfinite(r))
        count=r.groupby(level='date').transform('count')
        total=r.groupby(level='date').transform('sum')
        market=((total-r)/(count-1)).where(count>1)
        impact=(r.abs()/(amount/1e8)).where(amount.gt(0)&np.isfinite(amount)&r.notna())
        bydate=impact.groupby(level='date')
        median=bydate.median().sort_index().where(bydate.count().ge(30))
        logcost=np.log(median.where(median.gt(0)))
        shock=logcost-logcost.rolling(20,min_periods=20).mean().shift(1)
        shared=pd.Series(shock.reindex(r.index.get_level_values('date')).to_numpy(),index=r.index)
        return pd.DataFrame({'r':r,'market':market,'cost_shock':shared})

    @staticmethod
    def _mean(x):
        out=x.groupby(level='symbol',sort=False).rolling(120,min_periods=120).mean()
        return out.reset_index(level=0,drop=True).reindex(x.index)

    def calculate(self,factors):
        original=factors['close'].index
        x=self._model_inputs(factors)
        common=np.isfinite(x).all(axis=1)
        r=x.r.where(common);m=x.market.where(common);l=x.cost_shock.where(common)
        ar,am,al=self._mean(r),self._mean(m),self._mean(l)
        vm=self._mean(m*m)-am*am;vl=self._mean(l*l)-al*al
        ml=self._mean(m*l)-am*al
        rm=self._mean(r*m)-ar*am;rl=self._mean(r*l)-ar*al
        determinant=vm*vl-ml*ml
        beta=((rl*vm-rm*ml)/determinant).where(
            np.isfinite(determinant)&determinant.gt(1e-12)&vm.gt(0)&vl.gt(0))
        result=beta.where(np.isfinite(beta)).reindex(original)
        result.name='value'
        return result
