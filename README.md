# Touch & Turn

Daily/intraday scanner for a simple manipulation-candle setup.

## Strategy

1. Monitor the **15-minute candle that closes at 09:45 New York time**.
2. Calculate the candle range: **High - Low**.
3. Calculate **Daily ATR(14)** using Wilder/RMA smoothing from completed daily candles.
4. Calculate the manipulation threshold: **25% × Daily ATR(14)**.
5. If **15m candle range > 25% of Daily ATR(14)**, label it **MANIPULATION**.
6. For the touch-and-turn setup:
   - Bullish manipulation candle → LONG limit at the candle **Low**.
   - Bearish manipulation candle → SHORT limit at the candle **High**.
   - Neutral/doji manipulation candle → flagged but no directional limit order is generated.
7. A qualifying signal is sent to Telegram once; repeated workflow runs are deduplicated with `alert_state.json`.

## Timezone

All session logic uses `America/New_York`, so the scanner follows New York daylight-saving changes automatically. The reference candle is the 09:30–09:45 NY 15-minute candle.

## Dashboard

GitHub Actions generates `index.html` and publishes it to GitHub Pages.

## Telegram secrets

Configure these repository secrets:

- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`

The workflow also has a manual **Test Telegram notification** option.

## Data

The scanner uses Yahoo Finance via `yfinance`. The asset universe includes US index futures, metals, oil, major FX pairs, and crypto. Adjust `UNIVERSE` or `ASSET_FILTER` in `touch_turn_scanner.py` if you want a narrower list.
