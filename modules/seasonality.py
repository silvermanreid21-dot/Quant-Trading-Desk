"""Quantitative Market Anomaly & Edge Detection Framework (seasonality with real significance tests)."""

import numpy as np
import pandas as pd
from scipy import stats

MONTH_NAMES = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri"]

ECONOMIC_RATIONALE = {
    "Jan": "January effect / tax-loss-selling rebound, small-cap reallocation flows",
    "Apr": "Tax-related flows in some markets; Q1 earnings season momentum",
    "May": "\"Sell in May\" seasonal liquidity thinning into summer",
    "Sep": "Historically weakest month — post-summer institutional repositioning, redemptions",
    "Oct": "Volatility clustering from historical crash memory; tax-loss harvesting begins",
    "Nov": "Start of seasonally strong Nov-Apr window; holiday consumer spending flows",
    "Dec": "Santa Claus rally — light volume, window dressing, year-end fund flows",
}
DAY_RATIONALE = {
    "Mon": "Weekend information accumulation / negative sentiment carryover",
    "Fri": "Pre-weekend position squaring, risk reduction into the close",
}


def monthly_seasonality(close: pd.Series) -> pd.DataFrame:
    monthly = close.resample("ME").last()
    monthly_ret = monthly.pct_change().dropna() * 100
    monthly_ret_df = monthly_ret.to_frame("ret")
    monthly_ret_df["month"] = monthly_ret_df.index.month

    rows = []
    overall_mean = monthly_ret_df["ret"].mean()
    for m in range(1, 13):
        sample = monthly_ret_df.loc[monthly_ret_df["month"] == m, "ret"]
        n = len(sample)
        if n < 3:
            continue
        t_stat, p_val = stats.ttest_1samp(sample, 0)
        name = MONTH_NAMES[m - 1]
        rows.append({
            "month": name,
            "avg_return_%": round(sample.mean(), 2),
            "p_value": round(p_val, 3),
            "n": n,
            "economic_reason": ECONOMIC_RATIONALE.get(name, "No well-established economic rationale — treat as statistical noise unless p<0.05 and n>=10"),
            "significant": bool(p_val < 0.05 and n >= 8),
        })
    out = pd.DataFrame(rows).sort_values("avg_return_%", ascending=False).reset_index(drop=True)
    return out


def day_of_week_seasonality(close: pd.Series) -> pd.DataFrame:
    daily_ret = close.pct_change().dropna() * 100
    dow = daily_ret.index.dayofweek  # 0=Mon
    df = daily_ret.to_frame("ret")
    df["dow"] = dow

    rows = []
    for d in range(5):
        sample = df.loc[df["dow"] == d, "ret"]
        n = len(sample)
        if n < 10:
            continue
        t_stat, p_val = stats.ttest_1samp(sample, 0)
        name = DAY_NAMES[d]
        rows.append({
            "day": name,
            "avg_return_%": round(sample.mean(), 3),
            "p_value": round(p_val, 3),
            "n": n,
            "economic_reason": DAY_RATIONALE.get(name, "No strong documented rationale beyond microstructure noise"),
            "significant": bool(p_val < 0.05),
        })
    return pd.DataFrame(rows).sort_values("avg_return_%", ascending=False).reset_index(drop=True)


def earnings_drift(df: pd.DataFrame, info: dict) -> dict:
    """Approximate post-earnings-announcement drift using large single-day volume+price moves as earnings proxies."""
    close = df["Close"]
    volume = df["Volume"]
    ret = close.pct_change() * 100
    vol_avg = volume.rolling(20).mean()

    # proxy for earnings days: >2x average volume AND |return| > 3%
    candidates = df.copy()
    candidates["ret"] = ret
    candidates["vol_avg"] = vol_avg
    likely_earnings = candidates[(candidates["Volume"] > 2 * candidates["vol_avg"]) & (candidates["ret"].abs() > 3)]

    drift_5d = []
    for idx in likely_earnings.index:
        loc = close.index.get_loc(idx)
        if loc + 5 < len(close):
            d = (close.iloc[loc + 5] / close.iloc[loc] - 1) * 100
            drift_5d.append(d)

    return {
        "n_likely_earnings_events_detected": len(likely_earnings),
        "avg_post_event_5d_drift_%": round(float(np.mean(drift_5d)), 2) if drift_5d else None,
        "drift_sample_n": len(drift_5d),
        "note": "Earnings dates approximated from volume/price shock days (>2x avg volume, >3% move) since no earnings-calendar feed is wired in.",
    }
