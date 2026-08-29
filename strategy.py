"""
Trend-continuation-with-volume-confirmation strategy, long-only, parameterized so
different rule-tightness variants can be backtested through the exact same code path.

This module is the single source of truth for entry logic: both the backtest
engine (strategy_backtest.py) and the live unattended runner (strategy_runner.py)
call `compute_signals()` on the same OHLCV data and act on the same boolean
column. That's deliberate — a backtest only tells you something useful about a
strategy if it's testing the exact rules that will actually run live, not an
approximation of them.

Rule shape (tunable via params):
- Trend filter:   Close > MA50 > MA200 (optionally both MAs required to be rising).
- Volume filter:  Close within `breakout_pct` of the prior 20-session high, on volume
                   > `volume_mult`x the 20-session average.
- Overextension filter (mean-reversion guard): RSI(14) < `rsi_max` and the 252-session
                   Z-score < `z_max` — don't chase a move that's already statistically stretched.
- Risk (ATR-based, not support/resistance): stop = entry - 2*ATR(14),
                   target = entry + 4*ATR(14) — a 2:1 reward:risk by construction.
"""

import numpy as np
import pandas as pd

from modules import indicators as ind

MAX_HOLD_DAYS = 20
STOP_ATR_MULT = 2.0
TARGET_ATR_MULT = 4.0

# Chandelier-exit trailing stop, as an alternative to the fixed TARGET_ATR_MULT
# take-profit: trails at TRAILING_STOP_ATR_MULT * ATR below the highest high seen
# since entry, ratcheting up only. Standard trend-following practice (used by CTAs/
# managed-futures funds) is to fix the stop-loss by ATR but let the profit exit
# trail instead of capping it at a fixed reward:risk multiple — asymmetric winners
# pay for the (still fixed-risk) losers, instead of every trade being symmetric 2:1.
TRAILING_STOP_ATR_MULT = 3.0

# v1: strict — confirmed new-high breakout, strong volume, established+rising trend, tight overextension guard.
V1_PARAMS = dict(require_rising_ma=True, volume_mult=1.2, breakout_pct=0.0, rsi_max=70, z_max=2.0)

# v2: loosened — near-breakout (not just new highs), lighter volume bar, trend structure without requiring
# both MAs actively rising, and more room before something counts as "overextended".
V2_PARAMS = dict(require_rising_ma=False, volume_mult=1.0, breakout_pct=0.02, rsi_max=75, z_max=2.5)

# Rotation scoring (see strategy_backtest.py's "rotate" variants and the live
# runner's rotation-shadow check): ranks how strong a name's current setup is, so
# the weakest-ranked holding can be compared against the strongest unheld candidate.
# Backtested composite scoring outperformed plain momentum with similar drawdown
# to the no-rotation baseline — see ROTATION_LOOKBACK_DAYS-day backtest comparison.
ROTATION_LOOKBACK_DAYS = 20


def _momentum_score(df: pd.DataFrame, today, lookback: int = ROTATION_LOOKBACK_DAYS) -> float:
    idx = df.index.get_loc(today)
    if idx < lookback:
        return np.nan
    return (df["Close"].iloc[idx] / df["Close"].iloc[idx - lookback] - 1) * 100


def _composite_score(df: pd.DataFrame, today, lookback: int = ROTATION_LOOKBACK_DAYS) -> float:
    idx = df.index.get_loc(today)
    if idx < 50:
        return np.nan
    close = df["Close"]
    mom = (close.iloc[idx] / close.iloc[idx - lookback] - 1) * 100
    vol_avg20 = df["Volume"].iloc[idx - 20:idx].mean()
    vol_ratio = df["Volume"].iloc[idx] / vol_avg20 if vol_avg20 else np.nan
    ma50 = close.iloc[idx - 50:idx].mean()
    trend_strength = (close.iloc[idx] / ma50 - 1) * 100 if ma50 else np.nan
    return mom + trend_strength + (vol_ratio - 1) * 20


def rotation_score(df: pd.DataFrame, today, mode: str) -> float:
    return _momentum_score(df, today) if mode == "momentum" else _composite_score(df, today)


def compute_signals(df: pd.DataFrame, params: dict = None) -> pd.DataFrame:
    """Returns df's index aligned frame with signal columns. Every column is causal
    (uses only data up to and including that row) so taking .iloc[-1] for live use
    and iterating rows for backtesting are equivalent."""
    p = {**V1_PARAMS, **(params or {})}

    if df.empty or len(df) < 260:
        return pd.DataFrame(index=df.index)

    close, volume = df["Close"], df["Volume"]

    ma50 = ind.sma(close, 50)
    ma200 = ind.sma(close, 200)
    rsi14 = ind.rsi(close, 14)
    atr14 = ind.atr(df, 14)
    vol_avg20 = volume.rolling(20).mean()
    rolling_high20_prior = close.rolling(20).max().shift(1)

    mean_252 = close.rolling(252).mean()
    std_252 = close.rolling(252).std()
    z_score = (close - mean_252) / std_252.replace(0, np.nan)

    trend_ok = (close > ma50) & (ma50 > ma200)
    if p["require_rising_ma"]:
        trend_ok = trend_ok & (ma50 > ma50.shift(5)) & (ma200 > ma200.shift(5))

    volume_ok = (close >= rolling_high20_prior * (1 - p["breakout_pct"])) & (volume > p["volume_mult"] * vol_avg20)
    meanrev_ok = (rsi14 < p["rsi_max"]) & (z_score < p["z_max"])

    entry_signal = (trend_ok & volume_ok & meanrev_ok).fillna(False)

    out = pd.DataFrame(index=df.index)
    out["close"] = close
    out["entry_signal"] = entry_signal
    out["atr"] = atr14
    out["stop_price"] = close - STOP_ATR_MULT * atr14
    out["target_price"] = close + TARGET_ATR_MULT * atr14
    return out
