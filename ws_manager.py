import asyncio
import aiohttp
import logging

logger = logging.getLogger("binance_ws")


class BinanceWSManager:
    def __init__(self, client):
        self.client = client
        self.listen_key = None
        self.session = None
        self.ws = None
        self.tp_orders = {}  # {symbol: tp_order_id}

    async def start(self):
        self.listen_key = await self.client.stream_get_listen_key()
        url = f"wss://fstream.binance.com/ws/{self.listen_key}"
        self.session = aiohttp.ClientSession()
        self.ws = await self.session.ws_connect(url)
        logger.info("\ud83d\dd0c Connected to Binance User Stream")
        asyncio.create_task(self._listen())

    async def _listen(self):
        async for msg in self.ws:
            if msg.type == aiohttp.WSMsgType.TEXT:
                await self._handle_event(msg.json())

    async def _handle_event(self, data):
        if data.get("e") != "ORDER_TRADE_UPDATE":
            return

        order = data.get("o", {})
        symbol = order.get("s")
        order_type = order.get("o")
        status = order.get("X")

        if order_type == "TRAILING_STOP_MARKET" and status == "FILLED":
            if symbol in self.tp_orders:
                tp_id = self.tp_orders[symbol]
                try:
                    await self.client.futures_cancel_order(symbol=symbol, orderId=tp_id)
                    logger.info(f"\ud83d\udd91\ufe0f Canceled TP for {symbol} after trailing stop filled")
                except Exception as e:
                    logger.warning(f"\u274c Failed to cancel TP on {symbol}: {e}")
                finally:
                    del self.tp_orders[symbol]

    def register_tp_order(self, symbol, order_id):
        self.tp_orders[symbol] = order_id
