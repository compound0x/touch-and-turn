from __future__ import annotations

import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from urllib import request

import touch_turn_scanner as scanner

STATE_FILE = Path("alert_state.json")
DASHBOARD_FILE = "index.html"
USER_AGENT = "Mozilla/5.0 (compatible; TouchTurnScanner/1.0)"


def post_json(url: str, payload: dict) -> None:
    data = json.dumps(payload).encode("utf-8")
    req = request.Request(url, data=data, headers={"Content-Type": "application/json", "User-Agent": USER_AGENT}, method="POST")
    with request.urlopen(req, timeout=15) as response:
        if response.status >= 300:
            raise RuntimeError(f"HTTP {response.status}")


def telegram_send(message: str) -> None:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        print("Telegram secrets not configured; skipping Telegram notification.")
        return
    post_json(f"https://api.telegram.org/bot{token}/sendMessage", {"chat_id": chat_id, "text": message, "disable_web_page_preview": False})
    print("Telegram notification sent.")


def load_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8")) if STATE_FILE.exists() else {}
    except Exception:
        return {}


def save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def signal_key(sig: dict) -> str:
    return f"{sig['asset']}|{sig['time']}|{sig['side']}|{sig['limit_price']}"


def format_alert(sig: dict) -> str:
    asset = scanner.UNIVERSE[sig["asset"]]
    ts = pd_timestamp(sig["time"]).strftime("%Y-%m-%d %H:%M EST")
    return "\n".join([
        "🟠 TOUCH & TURN — MANIPULATION CANDLE", "",
        f"Asset: {sig['asset']}",
        f"09:45 Candle: {ts}",
        f"Direction: {sig['direction']}",
        f"High: {asset.fmt(float(sig['high']))}",
        f"Low: {asset.fmt(float(sig['low']))}",
        f"Delta: {asset.fmt(float(sig['delta']))}",
        f"Daily ATR(14): {asset.fmt(float(sig['daily_atr14']))}",
        f"25% ATR: {asset.fmt(float(sig['atr_25pct']))}",
        f"Range / 25% ATR: {float(sig['range_multiple']):.2f}x",
        f"Limit setup: {sig['side']}",
        f"Limit price: {asset.fmt(float(sig['limit_price']))}",
        "",
        "Manipulation candle confirmed. Ready for touch-and-turn limit order.",
        "",
        "Dashboard: https://compound0x.github.io/touch-and-turn/",
    ])


def pd_timestamp(value):
    import pandas as pd
    return pd.Timestamp(value)


def main() -> None:
    now = datetime.now(scanner.NY)
    signals = scanner.scan_history(verbose=False)
    scanner.export_html(signals, DASHBOARD_FILE)
    print(f"Dashboard generated: {DASHBOARD_FILE}")

    if signals.empty:
        print("No manipulation candles found.")
        save_state(load_state())
        return

    # Alert only for today's 09:45 candle. The workflow may run several times after
    # 09:45; state prevents duplicate Telegram messages.
    today = now.date()
    current = signals[signals["session_date"] == today].copy()
    if current.empty:
        print("No manipulation candle for today's 09:45 session.")
        return

    state = load_state()
    cutoff = (now - timedelta(days=7)).isoformat()
    state = {k: v for k, v in state.items() if v >= cutoff}

    for _, row in current.sort_values("time").iterrows():
        sig = row.to_dict()
        key = signal_key(sig)
        if key in state:
            continue
        message = format_alert(sig)
        print("\n" + message)
        telegram_send(message)
        state[key] = now.isoformat()
    save_state(state)


if __name__ == "__main__":
    main()
