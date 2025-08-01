import logging
import asyncio
from typing import Optional
from binance.helpers import round_step_size

from reporter import log_trade

# precision per symbol for quantity and price rounding
SYMBOL_PRECISION = {
    "BTCUSDT":    {"qty": 0.001, "price": 0.1},
    "ETHUSDT":    {"qty": 0.001, "price": 0.01},
    "NEARUSDT":   {"qty": 0.1,   "price": 0.001},
    "FLOKIUSDT":  {"qty": 1000,  "price": 0.0000001},
    "1000FLOKIUSDT": {"qty": 1000, "price": 0.0000001},
    "DOGEUSDT":   {"qty": 1,     "price": 0.0001},
    "SANDUSDT":   {"qty": 1,     "price": 0.0001},
    "LINKUSDT":   {"qty": 0.1,   "price": 0.001},
    "HBARUSDT":   {"qty": 1,     "price": 0.0001},
    "SUIUSDT":    {"qty": 1,     "price": 0.0001},
    "RENDERUSDT": {"qty": 0.1,   "price": 0.001},
}

from config import (
    LEVERAGE_MAP,
    MARGIN_PER_TRADE,
    SL_MULTIPLIER,
    TP_MULTIPLIER,
    USE_MARKET_ENTRY,
    USE_MARKET_TP,
    MIN_NOTIONAL,
    USE_TRAILING_STOP,
    TRAILING_ACTIVATION_MULTIPLIER,
    TRAILING_CALLBACK,
    BREAK_EVEN_ACTIVATION_MULTIPLIER,
)

logger = logging.getLogger("binance_bot")


class OrderManager:
    """Handle order submission for Binance Futures.

    Supported order types differ from spot trading: Futures does not offer OCO
    orders so take profit and stop loss are submitted individually.
    """

    def __init__(self, client, sheet: Optional[object] = None, ws_manager=None):
        self.client = client
        self.sheet = sheet
        self.ws_manager = ws_manager

    async def _retry(self, func, *args, retries: int = 3, delay: float = 1.0, **kwargs):
        """Execute ``func`` with retries and exponential backoff."""
        for attempt in range(retries):
            try:
                return await func(*args, **kwargs)
            except Exception as e:
                logger.error(f"{func.__name__} failed on attempt {attempt+1}: {e}")
                if attempt == retries - 1:
                    raise
                await asyncio.sleep(delay)
                delay *= 2

    async def place_trade(self, symbol: str, side: str, price: float, atr: float) -> bool:
        """Create entry, take-profit and stop-loss orders.

        Spot-style OCO orders are unsupported on Binance Futures so separate
        reduce-only orders are submitted. Returns ``True`` if all API calls
        succeed, otherwise ``False``.
        """

        lev = LEVERAGE_MAP[symbol]
        notional = MARGIN_PER_TRADE * lev
        qty = notional / price
        if qty * price < MIN_NOTIONAL:
            logger.error(
                f"Order notional {qty * price:.2f} below minimum for {symbol}"
            )
            return False
        sl = price - SL_MULTIPLIER * atr if side == "BUY" else price + SL_MULTIPLIER * atr
        tp = price + TP_MULTIPLIER * atr if side == "BUY" else price - TP_MULTIPLIER * atr

        # round values per symbol precision
        prec = SYMBOL_PRECISION.get(symbol, {"qty": 0.001, "price": 0.01})
        qty = round_step_size(qty, prec["qty"])
        price = round_step_size(price, prec["price"])
        sl = round_step_size(sl, prec["price"])
        tp = round_step_size(tp, prec["price"])
        logger.info(
            f"Rounded: qty={qty}, entry={price}, SL={sl}, TP={tp}"
        )

        # ensure SL/TP trigger prices don't equal or cross the entry
        price_buffer = 0.001 * price  # ~0.1%
        if side == "BUY":
            sl = min(sl, price - price_buffer)
            tp = max(tp, price + price_buffer)
            if sl >= price or tp <= price:
                logger.warning(
                    f"\u26a0\ufe0f BUY TP/SL invalid: SL={sl}, TP={tp}, price={price}"
                )
                return False
        else:
            sl = max(sl, price + price_buffer)
            tp = min(tp, price - price_buffer)
            if sl <= price or tp >= price:
                logger.warning(
                    f"\u26a0\ufe0f SELL TP/SL invalid: SL={sl}, TP={tp}, price={price}"
                )
                return False
        logger.info(
            f"Placing {side} order for {symbol} qty={qty:.6f} price={price:.4f} SL={sl:.4f} TP={tp:.4f}"
        )

        entry_type = "MARKET" if USE_MARKET_ENTRY else "LIMIT"
        opposite = "SELL" if side == "BUY" else "BUY"

        try:
            # skip trade if position already open
            pos = await self.client.futures_position_information(symbol=symbol)
            if isinstance(pos, list):
                for p in pos:
                    amt = float(p.get("positionAmt", 0))
                    if amt != 0:
                        logger.info(
                            f"Existing position {amt} for {symbol}, skipping trade"
                        )
                        return False

            await self._retry(self.client.futures_change_leverage, symbol=symbol, leverage=lev)
            entry = await self._retry(
                self.client.futures_create_order,
                symbol=symbol,
                side=side,
                type=entry_type,
                quantity=qty,
                price=None if entry_type == "MARKET" else price,
                timeInForce="GTC" if entry_type == "LIMIT" else None,
            )

            # confirm entry filled
            filled = False
            for _ in range(5):
                info = await self.client.futures_get_order(symbol=symbol, orderId=entry["orderId"])
                if not isinstance(info, dict) or info.get("status") == "FILLED":
                    filled = True
                    break
                await asyncio.sleep(1)
            if not filled:
                logger.error(f"Entry order for {symbol} not filled")
                return False

            if USE_MARKET_TP:
                tp_order = await self._retry(
                    self.client.futures_create_order,
                    symbol=symbol,
                    side=opposite,
                    type="TAKE_PROFIT_MARKET",
                    stopPrice=tp,
                    quantity=qty,
                    reduceOnly=True,
                    closePosition=True,
                )
            else:
                tp_order = await self._retry(
                    self.client.futures_create_order,
                    symbol=symbol,
                    side=opposite,
                    type="LIMIT",
                    timeInForce="GTC",
                    price=tp,
                    quantity=qty,
                    reduceOnly=True,
                    closePosition=True,
                )

            if self.ws_manager:
                try:
                    self.ws_manager.register_tp_order(symbol, tp_order["orderId"])
                except Exception as e:
                    logger.warning(f"Failed to register TP order for {symbol}: {e}")

            sl_order = await self._retry(
                self.client.futures_create_order,
                symbol=symbol,
                side=opposite,
                type="STOP_MARKET",
                stopPrice=sl,
                reduceOnly=True,
                closePosition=True,
                timeInForce="GTC",
            )

            sl_order_id = sl_order["orderId"]

            if USE_TRAILING_STOP:
                asyncio.create_task(
                    monitor_and_switch_to_trailing(
                        self.client,
                        symbol,
                        price,
                        atr,
                        sl_order_id,
                        side,
                    )
                )

            if self.sheet:
                log_trade(self.sheet, symbol, side, qty, price, tp, sl, "FILLED")

            return True
        except Exception as e:
            logger.error(f"Trade failed for {symbol}: {e}")
            if self.sheet:
                log_trade(self.sheet, symbol, side, qty, price, tp, sl, f"ERROR: {e}")
            return False


async def monitor_and_switch_to_trailing(client, sym, entry_price, atr, sl_order_id, side: str):
    import asyncio

    long_trade = side == "BUY"
    if long_trade:
        activation_price = entry_price + atr * TRAILING_ACTIVATION_MULTIPLIER
        be_activation_price = entry_price + atr * BREAK_EVEN_ACTIVATION_MULTIPLIER
        trailing_side = "SELL"
    else:
        activation_price = entry_price - atr * TRAILING_ACTIVATION_MULTIPLIER
        be_activation_price = entry_price - atr * BREAK_EVEN_ACTIVATION_MULTIPLIER
        trailing_side = "BUY"

    break_even_price = entry_price
    current_sl_id = sl_order_id
    be_triggered = False

    while True:
        try:
            ticker = await client.ticker_price(symbol=sym)
            mark_price = float(ticker["price"])
        except Exception as e:
            logger.warning(f"[{sym}] Error fetching price: {e}")
            await asyncio.sleep(2)
            continue

        if (
            not be_triggered
            and (
                (long_trade and mark_price >= be_activation_price)
                or (not long_trade and mark_price <= be_activation_price)
            )
        ):
            try:
                await client.futures_cancel_order(symbol=sym, orderId=current_sl_id)
                logger.info(
                    f"[{sym}] \ud83d\udd25 Moved stop to break-even after price hit {be_activation_price}"
                )
            except Exception as e:
                logger.warning(f"[{sym}] Failed to cancel SL: {e}")
                break

            try:
                new_sl = await client.futures_create_order(
                    symbol=sym,
                    side=trailing_side,
                    type="STOP_MARKET",
                    stopPrice=break_even_price,
                    reduceOnly=True,
                    closePosition=True,
                    timeInForce="GTC",
                )
                current_sl_id = new_sl["orderId"]
                be_triggered = True
                logger.info(
                    f"[{sym}] \ud83d\udea8 Placed break-even stop at {break_even_price}"
                )
            except Exception as e:
                logger.warning(f"[{sym}] Failed to place break-even stop: {e}")
                break

        if (long_trade and mark_price >= activation_price) or (
            not long_trade and mark_price <= activation_price
        ):
            try:
                await client.futures_cancel_order(symbol=sym, orderId=current_sl_id)
                logger.info(
                    f"[{sym}] \ud83d\udea8 Cancelled {'break-even' if be_triggered else 'fixed'} SL after price hit {activation_price}"
                )
            except Exception as e:
                logger.warning(f"[{sym}] Failed to cancel SL: {e}")
                break

            try:
                await client.futures_create_order(
                    symbol=sym,
                    side=trailing_side,
                    type="TRAILING_STOP_MARKET",
                    activationPrice=activation_price,
                    callbackRate=TRAILING_CALLBACK,
                    reduceOnly=True,
                    closePosition=True,
                    timeInForce="GTC",
                )
                logger.info(f"[{sym}] \ud83e\udde0 Placed trailing stop after cancelling SL")
            except Exception as e:
                logger.warning(f"[{sym}] Failed to place trailing stop: {e}")
            break

        await asyncio.sleep(2)

