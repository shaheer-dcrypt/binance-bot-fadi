import numpy as np
from collections import deque
from config import DONCHIAN_PERIOD, ATR_PERIOD, EMA_SLOW

class IndicatorWatcher:
    def __init__(self, symbol):
        self.symbol     = symbol
        self.klines_15m = deque(maxlen=DONCHIAN_PERIOD)
        self.klines_1h  = deque(maxlen=EMA_SLOW)

    def update(self, kline, interval):
        high  = float(kline['h'])
        low   = float(kline['l'])
        close = float(kline['c'])
        if interval == '15m':
            self.klines_15m.append((high, low, close))
        else:
            self.klines_1h.append(close)

    def get_donchian(self):
        highs, lows, _ = zip(*self.klines_15m)
        return max(highs), min(lows)

    def get_atr(self):
        if len(self.klines_15m) < ATR_PERIOD: return None
        trs=[]
        kl=list(self.klines_15m)
        for i in range(1,len(kl)):
            h,l,_=kl[i]; _,_,pc=kl[i-1]
            trs.append(max(h-l,abs(h-pc),abs(l-pc)))
        return np.mean(trs[-ATR_PERIOD:])

    def get_ema(self, period):
        if len(self.klines_1h) < period: return None
        data=np.array(self.klines_1h)
        w=np.exp(np.linspace(-1.,0.,period)); w/=w.sum()
        return np.convolve(data, w, mode='valid')[-1]
