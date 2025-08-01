"""
Order management for Binance futures trading bot.

This module provides an ``OrderManager`` class responsible for placing entry, take‑profit,
stop‑loss, and trailing stop orders on Binance Futures. It also exposes a helper
coroutine ``monitor_and_switch_to_trailing`` which watches market prices and
replaces a fixed stop‑loss with a trailing stop once a trade has moved far enough
in the desired direction.

The logic here has been tweaked to close profitable trades more aggressively.
Previously, take‑profit orders were submitted with ``closePosition=False``, which
could leave residual position size open if only part of the quantity was filled
when the market moved quickly. To ensure full closure when the take‑profit is
triggered, the take‑profit order now sets ``closePosition=True``. This instructs
Binance to close the entire position at the stop price regardless of quantity.

Note: When ``closePosition=True`` is used, the ``quantity`` parameter is still
supplied for compatibility, but Binance will ignore it and close the entire
position.
"""

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
        """Create entry, take‑profit and stop‑loss orders.

        Spot‑style OCO orders are unsupported on Binance Futures so separate
        reduce‑only orders are submitted. Returns ``True`` if all API calls
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
                    f"⚠️ BUY TP/SL invalid: SL={sl}, TP={tp}, price={price}"
                )
                return False
        else:
            sl = max(sl, price + price_buffer)
            tp = min(tp, price - price_buffer)
            if sl <= price or tp >= price:
                logger.warning(
                    f"⚠️ SELL TP/SL invalid: SL={sl}, TP={tp}, price={price}"
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
                # For take‑profit market orders we use closePosition=True. When closePosition is True
                # Binance will close the entire position and the quantity parameter must not be
                # specified, otherwise the order will be rejected. See Binance API docs.
                tp_order = await self._retry(
                    self.client.futures_create_order,
                    symbol=symbol,
                    side=opposite,
                    type="TAKE_PROFIT_MARKET",
                    stopPrice=tp,
                    reduceOnly=True,
                    closePosition=True,
                )
            else:
                # For limit take‑profit orders we cannot use closePosition=True because a limit order
                # requires a quantity. Instead we rely on reduceOnly so it will reduce the existing
                # position and not open a new one.
                tp_order = await self._retry(
                    self.client.futures_create_order,
                    symbol=symbol,
                    side=opposite,
                    type="LIMIT",
                    timeInForce="GTC",
                    price=tp,
                    quantity=qty,
                    reduceOnly=True,
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
    """Switch a fixed stop‑loss to a trailing stop when activation price is reached.

    Continuously monitors the mark price via ``client.ticker_price``. Once the price
    moves far enough in the trade's favour (as determined by ``TRAILING_ACTIVATION_MULTIPLIER``),
    the original stop‑loss order is cancelled and replaced with a trailing stop market order.

    Args:
        client: Binance async client.
        sym: Trading symbol (e.g., ``"BTCUSDT"``).
        entry_price: Entry price of the trade.
        atr: Average true range used to compute the activation price.
        sl_order_id: Order ID of the existing stop‑loss to cancel.
        side: "BUY" or "SELL" indicating the direction of the initial trade.
    """
    long_trade = side == "BUY"
    # Determine break-even and trailing activation thresholds based on direction
    if long_trade:
        be_activation_price = entry_price + atr * BREAK_EVEN_ACTIVATION_MULTIPLIER
        trailing_activation_price = entry_price + atr * TRAILING_ACTIVATION_MULTIPLIER
        trailing_side = "SELL"
    else:
        be_activation_price = entry_price - atr * BREAK_EVEN_ACTIVATION_MULTIPLIER
        trailing_activation_price = entry_price - atr * TRAILING_ACTIVATION_MULTIPLIER
        trailing_side = "BUY"

    # Track whether we've moved the stop-loss to break-even
    break_even_set = False

    while True:
        try:
            ticker = await client.ticker_price(symbol=sym)
            mark_price = float(ticker["price"])
        except Exception as e:
            logger.warning(f"[{sym}] Error fetching price: {e}")
            await asyncio.sleep(2)
            continue

        # Step 1: move stop-loss to break-even once initial threshold is hit
        if not break_even_set and (
            (long_trade and mark_price >= be_activation_price)
            or (not long_trade and mark_price <= be_activation_price)
        ):
            try:
                await client.futures_cancel_order(symbol=sym, orderId=sl_order_id)
                logger.info(
                    f"[{sym}] 🚨 Cancelled initial SL after price hit break-even activation {be_activation_price}"
                )
            except Exception as e:
                logger.warning(f"[{sym}] Failed to cancel SL: {e}")
                break

            # Place new stop-loss at entry price to lock in a non-loss
            break_even_price = entry_price
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
                sl_order_id = new_sl["orderId"]
                break_even_set = True
                logger.info(
                    f"[{sym}] 🛡️ Moved stop-loss to break-even at {break_even_price}"
                )
            except Exception as e:
                logger.warning(f"[{sym}] Failed to place break-even stop: {e}")
                break
            await asyncio.sleep(2)
            continue

        # Step 2: switch to trailing stop when trailing activation threshold is reached
        if (
            (long_trade and mark_price >= trailing_activation_price)
            or (not long_trade and mark_price <= trailing_activation_price)
        ):
            try:
                await client.futures_cancel_order(symbol=sym, orderId=sl_order_id)
                logger.info(
                    f"[{sym}] 🚨 Cancelled SL before placing trailing stop (price hit {trailing_activation_price})"
                )
            except Exception as e:
                logger.warning(f"[{sym}] Failed to cancel SL: {e}")
                break

            try:
                await client.futures_create_order(
                    symbol=sym,
                    side=trailing_side,
                    type="TRAILING_STOP_MARKET",
                    activationPrice=trailing_activation_price,
                    callbackRate=TRAILING_CALLBACK,
                    reduceOnly=True,
                    closePosition=True,
                    timeInForce="GTC",
                )
                logger.info(f"[{sym}] 🤠 Placed trailing stop after cancelling SL")
            except Exception as e:
                logger.warning(f"[{sym}] Failed to place trailing stop: {e}")
            break

        await asyncio.sleep(2)
