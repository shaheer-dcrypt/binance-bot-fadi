SYMBOLS = [
    "BTCUSDT", "ETHUSDT", "NEARUSDT", "SANDUSDT", "RENDERUSDT",
    "SUIUSDT", "HBARUSDT", "LINKUSDT", "DOGEUSDT", "1000FLOKIUSDT"
]
# Pairs listed here will be ignored by the bot. Useful if a symbol
# consistently errors during bootstrap or trading.
EXCLUDED_SYMBOLS: list[str] = []

ACTIVE_SYMBOLS = [s for s in SYMBOLS if s not in EXCLUDED_SYMBOLS]
LEVERAGE_MAP = {
    "BTCUSDT": 5,  "ETHUSDT": 5,
    "NEARUSDT": 8, "SANDUSDT": 8, "RENDERUSDT": 8,
    "SUIUSDT": 10, "HBARUSDT": 10, "LINKUSDT": 10,
    "DOGEUSDT": 10,"1000FLOKIUSDT":10
}
MARGIN_PER_TRADE = 200  # USD
# tuned indicator parameters
ATR_PERIOD         = 14
EMA_FAST, EMA_SLOW = 8, 21
DONCHIAN_PERIOD    = 55
TP_MULTIPLIER      = 1.5
SL_MULTIPLIER      = 1.0

# use market orders for entries and take profit for better fills
USE_MARKET_ENTRY   = True
USE_MARKET_TP      = True

# orders below this notional will be skipped to avoid exchange rejections
MIN_NOTIONAL      = 5.0

# trailing stop configuration
USE_TRAILING_STOP = True
TRAILING_ACTIVATION_MULTIPLIER = 1.0  # trailing activates after +1 ATR
TRAILING_CALLBACK = 0.5               # 0.5% trailing distance

# break-even stop activates after price moves this many ATR in favour
BREAK_EVEN_ACTIVATION_MULTIPLIER = 0.5
