"""Systematic Momentum Signal Detection & Ranking Framework (Jegadeesh & Titman style)."""

import numpy as np
import pandas as pd

from data import get_history, BENCHMARK


def _twelve_minus_one(close: pd.Series) -> float:
    """12-1 month return: return from 12 months ago to 1 month ago, excluding the most recent month."""
    if len(close) < 260:
        return np.nan
    price_12m_ago = close.iloc[-252] if len(close) >= 252 else close.iloc[0]
    price_1m_ago = close.iloc[-21]
    return (price_1m_ago / price_12m_ago - 1) * 100


def _annualized_vol(close: pd.Series, window: int = 63) -> float:
    rets = close.pct_change().tail(window)
    return rets.std() * np.sqrt(252) * 100


def rank_universe(tickers: list[str]) -> pd.DataFrame:
    bench_df = get_history(BENCHMARK, period="2y")
    bench_close = bench_df["Close"]
    bench_mom = _twelve_minus_one(bench_close)

    rows = []
    for t in tickers:
        t = t.strip().upper()
        if not t:
            continue
        df = get_history(t, period="2y")
        if df.empty or len(df) < 70:
            rows.append({"ticker": t, "error": "insufficient data"})
            continue
        close = df["Close"]
        mom_12_1 = _twelve_minus_one(close)
        vol = _annualized_vol(close)
        vol_adj_mom = mom_12_1 / vol if vol else np.nan

        # acceleration: compare last 3-month momentum vs prior 3-month momentum
        if len(close) >= 126:
            recent_3m = (close.iloc[-1] / close.iloc[-63] - 1) * 100
            prior_3m = (close.iloc[-63] / close.iloc[-126] - 1) * 100
            accelerating = recent_3m > prior_3m
        else:
            recent_3m = prior_3m = np.nan
            accelerating = None

        rel_mom = mom_12_1 - bench_mom if not np.isnan(mom_12_1) and not np.isnan(bench_mom) else np.nan

        # crash risk proxy: high recent vol + big drawdown from 60d high
        rolling_max = close.tail(60).max()
        drawdown = (close.iloc[-1] / rolling_max - 1) * 100
        crash_risk = "HIGH" if vol > 40 and drawdown < -8 else "ELEVATED" if vol > 30 else "LOW"

        rows.append({
            "ticker": t,
            "12-1_month_return_%": round(mom_12_1, 2) if not np.isnan(mom_12_1) else None,
            "ann_volatility_%": round(vol, 2) if not np.isnan(vol) else None,
            "vol_adj_momentum": round(vol_adj_mom, 3) if not np.isnan(vol_adj_mom) else None,
            "vs_benchmark_%": round(rel_mom, 2) if not np.isnan(rel_mom) else None,
            "accelerating": accelerating,
            "crash_risk": crash_risk,
        })

    out = pd.DataFrame(rows)
    if "vol_adj_momentum" in out.columns and out["vol_adj_momentum"].notna().any():
        out = out.sort_values("vol_adj_momentum", ascending=False, na_position="last").reset_index(drop=True)
        out.insert(0, "rank", range(1, len(out) + 1))
    return out
