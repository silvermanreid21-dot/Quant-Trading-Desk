"""Institutional Multi-Timeframe Trend & Signal Framework."""

import numpy as np
import pandas as pd

from . import indicators as ind


def resample(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    agg = {"Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"}
    out = df.resample(rule).agg(agg).dropna()
    return out


def analyze(df: pd.DataFrame) -> dict:
    if df.empty or len(df) < 60:
        return {"error": "Not enough price history for a full multi-timeframe read."}

    daily = df.copy()
    weekly = resample(df, "W")
    monthly = resample(df, "ME")

    price = float(daily["Close"].iloc[-1])

    ma50 = ind.sma(daily["Close"], 50)
    ma100 = ind.sma(daily["Close"], 100)
    ma200 = ind.sma(daily["Close"], 200) if len(daily) >= 200 else pd.Series(dtype=float)

    trend_daily, slope_d = ind.trend_from_ma(price, ma50)
    trend_weekly, slope_w = ind.trend_from_ma(float(weekly["Close"].iloc[-1]), ind.sma(weekly["Close"], 10)) if len(weekly) >= 10 else ("N/A", 0)
    trend_monthly, slope_m = ind.trend_from_ma(float(monthly["Close"].iloc[-1]), ind.sma(monthly["Close"], 6)) if len(monthly) >= 6 else ("N/A", 0)

    structure = ind.hh_hl_pattern(daily, lookback=60)

    rsi_series = ind.rsi(daily["Close"])
    rsi_now = float(rsi_series.iloc[-1])

    macd_line, signal_line, hist = ind.macd(daily["Close"])
    macd_signal = "BULLISH (MACD > signal)" if macd_line.iloc[-1] > signal_line.iloc[-1] else "BEARISH (MACD < signal)"
    macd_cross = None
    if len(macd_line) > 2:
        prev_diff = macd_line.iloc[-2] - signal_line.iloc[-2]
        cur_diff = macd_line.iloc[-1] - signal_line.iloc[-1]
        if prev_diff < 0 <= cur_diff:
            macd_cross = "Bullish crossover just occurred"
        elif prev_diff > 0 >= cur_diff:
            macd_cross = "Bearish crossover just occurred"

    bb_up, bb_mid, bb_low = ind.bollinger(daily["Close"])
    bb_position = (price - bb_low.iloc[-1]) / (bb_up.iloc[-1] - bb_low.iloc[-1]) * 100 if bb_up.iloc[-1] != bb_low.iloc[-1] else 50

    vol_avg20 = daily["Volume"].tail(20).mean()
    vol_now = daily["Volume"].iloc[-1]
    vol_ratio = vol_now / vol_avg20 if vol_avg20 else np.nan
    accum_dist = "Accumulation (up-days on above-avg volume)" if (daily["Close"].diff().tail(10) > 0).sum() >= 6 and vol_ratio > 1 else \
                 "Distribution (down-days on above-avg volume)" if (daily["Close"].diff().tail(10) < 0).sum() >= 6 and vol_ratio > 1 else \
                 "Neutral / no clear signature"

    support, resistance = ind.find_support_resistance(daily)
    top_support = [s for s in support if s["price"] < price][:3]
    top_resistance = [r for r in resistance if r["price"] > price][:3]

    # crossover signals
    cross_signal = "N/A"
    if not ma200.empty:
        if ma50.iloc[-1] > ma200.iloc[-1] and ma50.iloc[-6] <= ma200.iloc[-6]:
            cross_signal = "Golden Cross just formed (50MA crossed above 200MA)"
        elif ma50.iloc[-1] < ma200.iloc[-1] and ma50.iloc[-6] >= ma200.iloc[-6]:
            cross_signal = "Death Cross just formed (50MA crossed below 200MA)"
        elif ma50.iloc[-1] > ma200.iloc[-1]:
            cross_signal = "50MA above 200MA (bullish regime)"
        else:
            cross_signal = "50MA below 200MA (bearish regime)"

    # trade plan
    nearest_support = top_support[0]["price"] if top_support else price * 0.95
    nearest_resistance = top_resistance[0]["price"] if top_resistance else price * 1.05
    entry_low, entry_high = min(price, nearest_support * 1.01), price
    stop = nearest_support * 0.985
    t1 = nearest_resistance
    t2 = nearest_resistance + (nearest_resistance - nearest_support) * 0.5
    risk = max(entry_high - stop, 0.01)
    reward = max(t1 - entry_high, 0.01)
    rr = reward / risk

    alignment = "ALL ALIGNED" if len({trend_daily, trend_weekly, trend_monthly} - {"N/A"}) == 1 else "MIXED"

    if rr >= 3 and alignment == "ALL ALIGNED":
        conviction = "STRONG"
    elif rr >= 2:
        conviction = "MODERATE"
    else:
        conviction = "WEAK"

    return {
        "price": price,
        "trend_daily": trend_daily,
        "trend_weekly": trend_weekly,
        "trend_monthly": trend_monthly,
        "alignment": alignment,
        "structure": structure,
        "ma50": float(ma50.iloc[-1]) if not ma50.empty else None,
        "ma100": float(ma100.iloc[-1]) if not ma100.empty else None,
        "ma200": float(ma200.iloc[-1]) if not ma200.empty else None,
        "cross_signal": cross_signal,
        "rsi": rsi_now,
        "rsi_read": "Overbought (>70)" if rsi_now > 70 else "Oversold (<30)" if rsi_now < 30 else "Neutral",
        "macd_signal": macd_signal,
        "macd_cross": macd_cross,
        "bb_upper": float(bb_up.iloc[-1]),
        "bb_mid": float(bb_mid.iloc[-1]),
        "bb_lower": float(bb_low.iloc[-1]),
        "bb_position_pct": bb_position,
        "vol_ratio_20d": vol_ratio,
        "accum_dist": accum_dist,
        "support": top_support,
        "resistance": top_resistance,
        "entry_zone": (entry_low, entry_high),
        "stop": stop,
        "target1": t1,
        "target2": t2,
        "rr": rr,
        "conviction": conviction,
        "daily": daily,
        "weekly": weekly,
        "monthly": monthly,
        "rsi_series": rsi_series,
        "macd_line": macd_line,
        "signal_line": signal_line,
        "macd_hist": hist,
        "bb_upper_series": bb_up,
        "bb_mid_series": bb_mid,
        "bb_lower_series": bb_low,
        "ma50_series": ma50,
        "ma200_series": ma200,
    }
