# Binance Bot

This bot connects to Binance futures, listens to market data and places trades
based on EMA crosses and Donchian channel breakouts. Logs are written to
`bot.log` so you can inspect activity while the bot runs.

## Setup

1. Install dependencies:

   ```bash
   pip install -r requirements.txt
   ```

2. Export the Google sheet ID where trades will be reported:

   ```bash
   export SHEET_ID=<your-google-sheet-id>
   ```

   AWS credentials must also be configured so the bot can retrieve API keys from
   Secrets Manager.

## Running

Execute the main module:

```bash
python main.py
```

Logs will appear in `bot.log` alongside console output.

## Trade execution

Binance Futures does not support OCO orders. The bot submits separate
reduce-only take-profit and stop-loss orders immediately after the entry order
is placed. Entry, stop loss and take-profit prices are rounded per symbol and
a small 0.1% buffer is applied so trigger prices don't conflict with the entry.
Example log output for a successful trade looks like:

```
2024-01-01 12:00:00 INFO binance_bot Placing BUY order for BTCUSDT qty=0.010000 price=30000.0000 SL=29500.0000 TP=30500.0000
2024-01-01 12:00:00 INFO binance_bot EMA cross BUY succeeded for BTCUSDT
```

If an API call fails an error is logged with the symbol and exception message.
