import logging
from config import ACTIVE_SYMBOLS, EMA_FAST, EMA_SLOW
from indicators import IndicatorWatcher

logger = logging.getLogger("binance_bot")

class StrategyEngine:
    def __init__(self, client, order_manager):
        self.client    = client
        self.om        = order_manager
        self.watchers  = {s: IndicatorWatcher(s) for s in ACTIVE_SYMBOLS}
        self.last_ema  = {s: (None, None) for s in ACTIVE_SYMBOLS}

    async def handle_kline(self, msg):
        k = msg['k']; sym = msg['s']; iv = k['i']
        if sym not in self.watchers or not k['x']:
            return
        w = self.watchers[sym]
        w.update(k, iv)
        ef = w.get_ema(EMA_FAST)
        es = w.get_ema(EMA_SLOW)
        dh, dl = w.get_donchian()
        atr = w.get_atr()
        logger.info(
            f"Processing {sym} {iv}: EMA {EMA_FAST}={ef}, EMA {EMA_SLOW}={es}, Donchian=({dl},{dh}), ATR={atr}"
        )
        if atr is None or ef is None or es is None:
            logger.info(
                f"Skipping {sym}: ATR={atr}, EMA_fast={ef}, EMA_slow={es} (insufficient history)"
            )
            return
        lf, ls = self.last_ema[sym]
        # EMA cross
        if lf and ls:
            if lf < ls and ef > es:
                res = await self.om.place_trade(sym, 'BUY', float(k['c']), atr)
                logger.info(f"EMA cross BUY {'succeeded' if res else 'failed'} for {sym}")
            elif lf > ls and ef < es:
                res = await self.om.place_trade(sym, 'SELL', float(k['c']), atr)
                logger.info(f"EMA cross SELL {'succeeded' if res else 'failed'} for {sym}")
        self.last_ema[sym] = (ef, es)
        # Donchian breakout
        price = float(k['c'])
        if price > dh:
            res = await self.om.place_trade(sym, 'BUY', price, atr)
            logger.info(f"Donchian breakout BUY {'succeeded' if res else 'failed'} for {sym}")
        elif price < dl:
            res = await self.om.place_trade(sym, 'SELL', price, atr)
            logger.info(f"Donchian breakout SELL {'succeeded' if res else 'failed'} for {sym}")
