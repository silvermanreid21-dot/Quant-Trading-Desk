"""Reusable technical indicator calculations, computed directly from OHLCV data."""

import numpy as np
import pandas as pd


def sma(series: pd.Series, window: int) -> pd.Series:
    return series.rolling(window).mean()


def atr(df: pd.DataFrame, window: int = 14) -> pd.Series:
    high, low, close = df["High"], df["Low"], df["Close"]
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / window, min_periods=window, adjust=False).mean()


def rsi(series: pd.Series, window: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / window, min_periods=window, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / window, min_periods=window, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    out = 100 - (100 / (1 + rs))
    return out.fillna(50)


def macd(series: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9):
    ema_fast = series.ewm(span=fast, adjust=False).mean()
    ema_slow = series.ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    hist = macd_line - signal_line
    return macd_line, signal_line, hist


def bollinger(series: pd.Series, window: int = 20, num_std: float = 2.0):
    mid = series.rolling(window).mean()
    std = series.rolling(window).std()
    upper = mid + num_std * std
    lower = mid - num_std * std
    return upper, mid, lower


def obv(close: pd.Series, volume: pd.Series) -> pd.Series:
    direction = np.sign(close.diff().fillna(0))
    return (direction * volume).cumsum()


def vpt(close: pd.Series, volume: pd.Series) -> pd.Series:
    pct_change = close.pct_change().fillna(0)
    return (pct_change * volume).cumsum()


def find_support_resistance(df: pd.DataFrame, window: int = 5, tolerance: float = 0.015, lookback: int = 250):
    """Cluster local swing highs/lows into support & resistance levels with a test count."""
    sub = df.tail(lookback)
    highs = sub["High"]
    lows = sub["Low"]

    swing_highs = []
    swing_lows = []
    h = highs.values
    l = lows.values
    dates = sub.index
    for i in range(window, len(sub) - window):
        if h[i] == max(h[i - window:i + window + 1]):
            swing_highs.append((dates[i], h[i]))
        if l[i] == min(l[i - window:i + window + 1]):
            swing_lows.append((dates[i], l[i]))

    def cluster(points):
        levels = []
        for date, price in points:
            placed = False
            for lvl in levels:
                if abs(price - lvl["price"]) / lvl["price"] <= tolerance:
                    lvl["tests"] += 1
                    lvl["price"] = (lvl["price"] * (lvl["tests"] - 1) + price) / lvl["tests"]
                    lvl["last_date"] = max(lvl["last_date"], date)
                    placed = True
                    break
            if not placed:
                levels.append({"price": price, "tests": 1, "last_date": date})
        levels.sort(key=lambda x: x["tests"], reverse=True)
        return levels

    resistance = cluster(swing_highs)
    support = cluster(swing_lows)
    return support, resistance


def trend_from_ma(price: float, ma_series: pd.Series):
    """Return ('UP'/'DOWN'/'FLAT', slope) based on current price vs MA and MA's recent slope."""
    if ma_series.dropna().empty:
        return "N/A", 0.0
    ma_now = ma_series.iloc[-1]
    ma_prev = ma_series.iloc[-6] if len(ma_series) > 6 else ma_series.iloc[0]
    slope = (ma_now - ma_prev) / ma_prev * 100 if ma_prev else 0.0
    if price > ma_now and slope > 0.1:
        return "UP", slope
    if price < ma_now and slope < -0.1:
        return "DOWN", slope
    return "NEUTRAL", slope


def hh_hl_pattern(df: pd.DataFrame, lookback: int = 60) -> str:
    """Detect higher-highs/higher-lows vs lower-highs/lower-lows over recent swing points."""
    sub = df.tail(lookback)
    if len(sub) < 10:
        return "insufficient data"
    mid = len(sub) // 2
    first_half, second_half = sub.iloc[:mid], sub.iloc[mid:]
    hh = second_half["High"].max() > first_half["High"].max()
    hl = second_half["Low"].min() > first_half["Low"].min()
    lh = second_half["High"].max() < first_half["High"].max()
    ll = second_half["Low"].min() < first_half["Low"].min()
    if hh and hl:
        return "Higher Highs / Higher Lows (uptrend structure)"
    if lh and ll:
        return "Lower Highs / Lower Lows (downtrend structure)"
    return "Mixed structure (no clean HH/HL or LH/LL)"
