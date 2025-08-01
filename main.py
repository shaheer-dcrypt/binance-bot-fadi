import asyncio
from binance import AsyncClient, BinanceSocketManager
from ws_manager import BinanceWSManager
# from config import *

# from logger import setup_logger
from our_secrets import get_secrets
from strategy import StrategyEngine
from orders import OrderManager
from reporter import setup_reporter
import logging

# logger = setup_logger()
# ——— LOGGING SETUP ———
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    filename="bot.log",
)
logger = logging.getLogger("binance-bot")


async def main():
    api_key, api_secret, google_creds, sheet_id = get_secrets()
    sheet = setup_reporter(google_creds, sheet_id)
    # Connect to the global Binance Futures API instead of the U.S. endpoint
    # which does not serve futures data.
    client = await AsyncClient.create(api_key, api_secret, tld="com")

    ws_manager = BinanceWSManager(client)
    await ws_manager.start()

    omgr = OrderManager(client, sheet, ws_manager)
    strat = StrategyEngine(client, omgr)
    # === history bootstrap – seed indicators with recent klines ===
    from config import (
        EMA_FAST,
        EMA_SLOW,
        ATR_PERIOD,
        DONCHIAN_PERIOD,
        ACTIVE_SYMBOLS,
    )

    symbols = ACTIVE_SYMBOLS
    limit = max(EMA_SLOW, EMA_FAST, ATR_PERIOD, DONCHIAN_PERIOD) + 1
    for sym in symbols:
        for interval in ("15m", "1h"):
            try:
                bars = await client.futures_klines(
                    symbol=sym, interval=interval, limit=limit
                )
            except Exception as e:
                logger.error(f"Bootstrap klines fetch failed for {sym} {interval}: {e}")
                continue
            for bar in bars:
                msg = {
                    "s": sym,
                    "k": {
                        "h": bar[2],  # high
                        "l": bar[3],  # low
                        "c": bar[4],  # close
                        "i": interval,
                        "x": True,
                    },
                }
                await strat.handle_kline(msg)
    # === end bootstrap ===
    bm = BinanceSocketManager(client)

    streams = [f"{s.lower()}@kline_1h" for s in symbols] + [
        f"{s.lower()}@kline_15m" for s in symbols
    ]
    async with bm.multiplex_socket(streams) as tsm:
        while True:
            msg = await tsm.recv()
            data = msg.get("data", msg)
            if data.get("e") == "kline":
                logger.info(
                    f"Received {data['s']} {data['k']['i']} closed at {data['k']['c']}"
                )
                await strat.handle_kline(data)

    await client.close_connection()


if __name__ == "__main__":
    asyncio.run(main())
