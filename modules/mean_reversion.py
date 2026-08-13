"""Statistical Mean Reversion & Contrarian Signal Framework."""

import numpy as np
import pandas as pd

from . import indicators as ind


def analyze(df: pd.DataFrame) -> dict:
    if df.empty or len(df) < 100:
        return {"error": "Not enough price history for mean reversion analysis."}

    close = df["Close"]
    price = float(close.iloc[-1])

    lookback = min(len(close), 252 * 5)
    hist = close.tail(lookback)
    mean_price = hist.mean()
    std_price = hist.std()
    z_score = (price - mean_price) / std_price if std_price else 0.0

    rsi_series = ind.rsi(close)
    rsi_now = float(rsi_series.iloc[-1])

    # historical forward returns when RSI was extreme
    fwd_days = 10
    rsi_hist = rsi_series.copy()
    fwd_ret = close.shift(-fwd_days) / close - 1

    oversold_mask = rsi_hist < 30
    overbought_mask = rsi_hist > 70

    oversold_fwd = fwd_ret[oversold_mask].dropna()
    overbought_fwd = fwd_ret[overbought_mask].dropna()

    bb_up, bb_mid, bb_low = ind.bollinger(close)
    below_lower_pct = (close < bb_low).tail(lookback).mean() * 100
    above_upper_pct = (close > bb_up).tail(lookback).mean() * 100

    # mean reversion speed: avg days for z-score to return within +-0.5 after breaching +-2
    z_series = (close - close.rolling(252).mean()) / close.rolling(252).std()
    z_series = z_series.dropna()
    revert_durations = []
    in_extreme = False
    start_idx = None
    for i in range(len(z_series)):
        val = z_series.iloc[i]
        if not in_extreme and abs(val) > 2:
            in_extreme = True
            start_idx = i
        elif in_extreme and abs(val) < 0.5:
            revert_durations.append(i - start_idx)
            in_extreme = False
    avg_revert_days = float(np.mean(revert_durations)) if revert_durations else None

    signal = "NO SIGNAL"
    if z_score > 2 or rsi_now > 70 or above_upper_pct > 0 and price > bb_up.iloc[-1]:
        signal = "OVERBOUGHT — contrarian short / reduce watch"
    elif z_score < -2 or rsi_now < 30 or price < bb_low.iloc[-1]:
        signal = "OVERSOLD — contrarian long watch"

    return {
        "price": price,
        "mean_5yr": mean_price,
        "std_5yr": std_price,
        "z_score": z_score,
        "rsi": rsi_now,
        "oversold_fwd_ret_mean_%": float(oversold_fwd.mean() * 100) if len(oversold_fwd) else None,
        "oversold_fwd_ret_n": int(len(oversold_fwd)),
        "overbought_fwd_ret_mean_%": float(overbought_fwd.mean() * 100) if len(overbought_fwd) else None,
        "overbought_fwd_ret_n": int(len(overbought_fwd)),
        "pct_time_below_lower_band_%": float(below_lower_pct),
        "pct_time_above_upper_band_%": float(above_upper_pct),
        "avg_reversion_days": avg_revert_days,
        "n_extreme_episodes": len(revert_durations),
        "signal": signal,
        "bb_lower": float(bb_low.iloc[-1]),
        "bb_upper": float(bb_up.iloc[-1]),
        "z_series": z_series,
        "close": close,
    }
