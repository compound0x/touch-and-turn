from __future__ import annotations

import importlib
import subprocess
import sys
import time
import warnings
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

for pkg, mod in [("yfinance", "yfinance"), ("pandas", "pandas"), ("numpy", "numpy")]:
    try:
        importlib.import_module(mod)
    except ImportError:
        subprocess.run([sys.executable, "-m", "pip", "install", "-q", pkg], check=False)

import numpy as np
import pandas as pd
import yfinance as yf

warnings.filterwarnings("ignore")
NY = ZoneInfo("America/New_York")
UTC = ZoneInfo("UTC")

# Strategy settings
REFERENCE_CLOSE_HOUR = 9
REFERENCE_CLOSE_MINUTE = 45
ATR_LENGTH = 14
ATR_THRESHOLD_FRACTION = 0.25
SCAN_DAYS = 7
ASSET_FILTER = None
SAVE_HTML = "index.html"

@dataclass
class Asset:
    name: str
    symbol: str
    kind: str
    digits: int = 2
    weekends: bool = False

    def fmt(self, value: float) -> str:
        return f"{value:,.{self.digits}f}"

UNIVERSE_CORE = {
    "NAS100": Asset("NAS100", "NQ=F", "index", 2),
    "US500": Asset("US500", "ES=F", "index", 2),
    "US30": Asset("US30", "YM=F", "index", 0),
    "XAUUSD": Asset("XAUUSD", "GC=F", "metal", 2),
    "XAGUSD": Asset("XAGUSD", "SI=F", "metal", 3),
    "OIL": Asset("OIL", "CL=F", "energy", 2),
}
UNIVERSE_FX = {
    "EURUSD": Asset("EURUSD", "EURUSD=X", "fx", 5),
    "GBPUSD": Asset("GBPUSD", "GBPUSD=X", "fx", 5),
    "USDJPY": Asset("USDJPY", "USDJPY=X", "fx", 3),
    "AUDUSD": Asset("AUDUSD", "AUDUSD=X", "fx", 5),
    "USDCAD": Asset("USDCAD", "USDCAD=X", "fx", 5),
    "USDCHF": Asset("USDCHF", "USDCHF=X", "fx", 5),
    "NZDUSD": Asset("NZDUSD", "NZDUSD=X", "fx", 5),
    "EURJPY": Asset("EURJPY", "EURJPY=X", "fx", 3),
    "GBPJPY": Asset("GBPJPY", "GBPJPY=X", "fx", 3),
}
UNIVERSE_CRYPTO = {
    "BTCUSD": Asset("BTCUSD", "BTC-USD", "crypto", 2, True),
    "ETHUSD": Asset("ETHUSD", "ETH-USD", "crypto", 2, True),
    "SOLUSD": Asset("SOLUSD", "SOL-USD", "crypto", 3, True),
    "XRPUSD": Asset("XRPUSD", "XRP-USD", "crypto", 5, True),
    "BNBUSD": Asset("BNBUSD", "BNB-USD", "crypto", 2, True),
    "DOGEUSD": Asset("DOGEUSD", "DOGE-USD", "crypto", 6, True),
}
UNIVERSE = {**UNIVERSE_CORE, **UNIVERSE_FX, **UNIVERSE_CRYPTO}

_CACHE: dict[tuple, tuple[float, pd.DataFrame]] = {}


def _normalise(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    d = df.copy()
    if isinstance(d.columns, pd.MultiIndex):
        d.columns = d.columns.get_level_values(0)
    d.columns = [str(c).title() for c in d.columns]
    keep = [c for c in ["Open", "High", "Low", "Close", "Volume"] if c in d.columns]
    d = d[keep]
    if d.index.tz is None:
        d.index = d.index.tz_localize(UTC)
    d.index = d.index.tz_convert(NY)
    for c in d.columns:
        d[c] = pd.to_numeric(d[c], errors="coerce")
    return d.sort_index()[~d.index.duplicated(keep="last")].dropna(subset=["Open", "High", "Low", "Close"])


def fetch(symbol: str, interval: str, period: str = "60d", ttl: int = 60) -> pd.DataFrame:
    key = (symbol, interval, period)
    now = time.time()
    if key in _CACHE and now - _CACHE[key][0] < ttl:
        return _CACHE[key][1]
    try:
        df = yf.Ticker(symbol).history(period=period, interval=interval, prepost=True,
                                       auto_adjust=False, raise_errors=False)
        out = _normalise(df)
    except Exception as exc:
        print(f"  ! {symbol} {interval}: {exc}")
        out = pd.DataFrame()
    _CACHE[key] = (now, out)
    return out


def wilder_atr(daily: pd.DataFrame, length: int = ATR_LENGTH) -> pd.Series:
    """TradingView-style ATR: True Range smoothed with Wilder's RMA."""
    prev = daily["Close"].shift(1)
    tr = pd.concat([
        daily["High"] - daily["Low"],
        (daily["High"] - prev).abs(),
        (daily["Low"] - prev).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / length, adjust=False, min_periods=length).mean()


def selected_assets():
    if not ASSET_FILTER:
        return list(UNIVERSE.values())
    wanted = {x.upper() for x in ASSET_FILTER}
    return [a for a in UNIVERSE.values() if a.name in wanted]


def reference_date(now: datetime) -> datetime.date:
    return now.date()


def is_weekend(asset: Asset, day) -> bool:
    return (not asset.weekends) and day.weekday() >= 5


def find_reference_bar(intraday: pd.DataFrame, day) -> tuple[pd.Timestamp, pd.Series] | None:
    if intraday.empty:
        return None
    target = intraday[(intraday.index.date == day) &
                      (intraday.index.hour == REFERENCE_CLOSE_HOUR) &
                      (intraday.index.minute == 30)]
    if target.empty:
        return None
    # 09:30 NY bar closes at 09:45. Using the bar timestamp rather than a later candle
    # prevents accidentally evaluating an incomplete/future bar.
    row = target.iloc[-1]
    return target.index[-1], row


def scan_history(assets=None, days: int = SCAN_DAYS, verbose: bool = True):
    assets = assets or selected_assets()
    now = datetime.now(NY)
    dates = [(now - timedelta(days=i)).date() for i in range(days)]
    rows = []

    for asset in assets:
        if verbose:
            print(f"Scanning {asset.name} ({asset.symbol})")
        intraday = fetch(asset.symbol, "15m", period="60d")
        daily = fetch(asset.symbol, "1d", period="1y")
        if intraday.empty or daily.empty:
            continue
        daily = daily.copy()
        daily["ATR14"] = wilder_atr(daily)

        for day in reversed(dates):
            if is_weekend(asset, day):
                continue
            ref = find_reference_bar(intraday, day)
            if ref is None:
                continue
            ts, bar = ref
            # Only closed reference candles are valid. For today's live run, 09:45 has
            # to have passed; historical candles are already closed.
            if day == now.date() and now < ts + timedelta(minutes=15):
                continue

            # Use the latest completed daily ATR available BEFORE the reference session.
            prior_daily = daily[daily.index.date < day]
            if prior_daily.empty:
                continue
            atr = float(prior_daily["ATR14"].dropna().iloc[-1]) if not prior_daily["ATR14"].dropna().empty else np.nan
            if not np.isfinite(atr) or atr <= 0:
                continue

            candle_high = float(bar["High"])
            candle_low = float(bar["Low"])
            delta = candle_high - candle_low
            threshold = atr * ATR_THRESHOLD_FRACTION
            if delta <= threshold:
                continue

            o = float(bar["Open"])
            c = float(bar["Close"])
            direction = "BULLISH" if c > o else "BEARISH" if c < o else "NEUTRAL"

            # Touch-and-turn interpretation: bullish manipulation candle -> long limit at
            # its low; bearish manipulation candle -> short limit at its high. Neutral
            # candles are still flagged but have no directional limit order.
            if direction == "BULLISH":
                side, limit_price = "LONG", candle_low
            elif direction == "BEARISH":
                side, limit_price = "SHORT", candle_high
            else:
                side, limit_price = "WAIT", np.nan

            rows.append({
                "asset": asset.name,
                "time": ts,
                "session_date": day,
                "open": o,
                "high": candle_high,
                "low": candle_low,
                "close": c,
                "delta": delta,
                "daily_atr14": atr,
                "atr_25pct": threshold,
                "range_multiple": delta / threshold if threshold else np.nan,
                "direction": direction,
                "side": side,
                "limit_price": limit_price,
                "kind": "MANIPULATION",
            })

    signals = pd.DataFrame(rows)
    if not signals.empty:
        signals = signals.sort_values(["time", "asset"]).reset_index(drop=True)
    return signals


def _fmt(asset_name: str, value: float) -> str:
    asset = UNIVERSE.get(asset_name)
    if asset is None or not np.isfinite(value):
        return "-"
    return asset.fmt(value)


def export_html(signals: pd.DataFrame, path: str = SAVE_HTML, days: int = SCAN_DAYS):
    now = datetime.now(NY)
    count = len(signals)
    rows = []
    if signals.empty:
        table = '<div class="empty">No manipulation candles passed the filter.</div>'
    else:
        for _, r in signals.sort_values("time", ascending=False).iterrows():
            rows.append(
                "<tr>"
                f"<td>{r['asset']}</td><td>{pd.Timestamp(r['time']).strftime('%Y-%m-%d %H:%M')}</td>"
                f"<td>{r['direction']}</td><td>{_fmt(r['asset'], r['delta'])}</td>"
                f"<td>{_fmt(r['asset'], r['daily_atr14'])}</td><td>{_fmt(r['asset'], r['atr_25pct'])}</td>"
                f"<td>{r['range_multiple']:.2f}x</td><td>{r['side']}</td>"
                f"<td>{_fmt(r['asset'], r['limit_price'])}</td>"
                "</tr>"
            )
        table = '<table><thead><tr><th>Asset</th><th>09:45 Candle</th><th>Direction</th><th>Delta</th><th>Daily ATR(14)</th><th>25% ATR</th><th>Range / Threshold</th><th>Setup</th><th>Limit</th></tr></thead><tbody>' + ''.join(rows) + '</tbody></table>'

    html = f'''<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Touch & Turn Scanner</title>
<style>body{{font-family:system-ui,-apple-system,sans-serif;margin:0;padding:24px;background:#f6f7f9;color:#17202a}}.wrap{{max-width:1200px;margin:auto}}.card{{background:white;border-radius:14px;padding:20px;box-shadow:0 2px 12px #00000012;margin-bottom:18px}}h1{{margin:0 0 6px}}.sub{{color:#667085}}.stats{{display:flex;gap:14px;flex-wrap:wrap}}.stat{{background:#f1f3f5;border-radius:10px;padding:12px 16px}}table{{width:100%;border-collapse:collapse;font-size:14px}}th,td{{padding:10px;border-bottom:1px solid #e6e8eb;text-align:left}}th{{background:#f8f9fa}}.empty{{padding:30px;text-align:center;color:#667085}}code{{background:#f1f3f5;padding:2px 5px;border-radius:5px}}</style></head>
<body><div class="wrap"><div class="card"><h1>Touch & Turn Scanner</h1><div class="sub">15-minute candle closing at 09:45 New York time • Daily ATR(14) • Manipulation threshold = 25% of Daily ATR</div></div>
<div class="card"><div class="stats"><div class="stat"><b>Qualified setups</b><br>{count}</div><div class="stat"><b>Lookback</b><br>{days} days</div><div class="stat"><b>Last run</b><br>{now:%Y-%m-%d %H:%M %Z}</div></div></div>
<div class="card">{table}</div><div class="card"><small>Rule: <code>09:45 candle High - Low &gt; 0.25 × Daily ATR(14)</code>. The dashboard labels bullish candles as LONG limit at the candle low and bearish candles as SHORT limit at the candle high.</small></div></div></body></html>'''
    Path(path).write_text(html, encoding="utf-8")


if __name__ == "__main__":
    sig = scan_history(verbose=True)
    export_html(sig)
    print(f"Qualified manipulation candles: {len(sig)}")
    if not sig.empty:
        print(sig[["asset", "time", "direction", "delta", "daily_atr14", "atr_25pct", "side", "limit_price"]].to_string(index=False))
