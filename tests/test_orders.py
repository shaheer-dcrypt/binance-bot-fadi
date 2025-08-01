import unittest
from unittest.mock import AsyncMock, patch, ANY

import config
from orders import OrderManager


class TestOrderManager(unittest.IsolatedAsyncioTestCase):
    async def test_place_trade_creates_orders(self):
        client = AsyncMock()
        ws_manager = unittest.mock.MagicMock()
        with (
            patch("orders.USE_MARKET_ENTRY", True),
            patch("orders.USE_MARKET_TP", True),
            patch("orders.USE_TRAILING_STOP", True),
            patch("orders.monitor_and_switch_to_trailing", new=AsyncMock()) as monitor_mock,
        ):
            om = OrderManager(client, ws_manager=ws_manager)
            await om.place_trade("BTCUSDT", "BUY", 65000.0, 100.0)
            sl_order_id = client.futures_create_order.return_value.__getitem__.return_value
            monitor_mock.assert_called_with(
                client, "BTCUSDT", 65000.0, 100.0, sl_order_id, "BUY"
            )

        # leverage change called
        client.futures_change_leverage.assert_awaited_with(symbol="BTCUSDT", leverage=config.LEVERAGE_MAP["BTCUSDT"])

        # entry order
        client.futures_create_order.assert_any_await(
            symbol="BTCUSDT",
            side="BUY",
            type="MARKET",
            quantity=ANY,
            price=None,
            timeInForce=None,
        )

        # take profit market order
        client.futures_create_order.assert_any_await(
            symbol="BTCUSDT",
            side="SELL",
            type="TAKE_PROFIT_MARKET",
            stopPrice=ANY,
            quantity=ANY,
            reduceOnly=True,
            closePosition=True,
        )

        # stop market order
        client.futures_create_order.assert_any_await(
            symbol="BTCUSDT",
            side="SELL",
            type="STOP_MARKET",
            stopPrice=ANY,
            reduceOnly=True,
            closePosition=True,
            timeInForce="GTC",
        )

        ws_manager.register_tp_order.assert_called_once()

        # ensure no oco order
        self.assertEqual(client.futures_create_oco_order.await_count, 0)


if __name__ == "__main__":
    unittest.main()
