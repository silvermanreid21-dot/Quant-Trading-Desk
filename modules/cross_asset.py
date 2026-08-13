"""Cross-Asset Correlation & Inter-Market Signal Detection Framework."""

import numpy as np
import pandas as pd

from data import get_history, MACRO_PROXIES, SECTOR_ETFS


def _lead_lag(a: pd.Series, b: pd.Series, max_lag: int = 10):
    """Find the lag (in days) at which a leads/lags b with the highest absolute correlation."""
    best_lag, best_corr = 0, 0.0
    for lag in range(-max_lag, max_lag + 1):
        if lag < 0:
            x, y = a.shift(-lag), b
        else:
            x, y = a, b.shift(lag)
        aligned = pd.concat([x, y], axis=1).dropna()
        if len(aligned) < 30:
            continue
        c = aligned.iloc[:, 0].corr(aligned.iloc[:, 1])
        if abs(c) > abs(best_corr):
            best_corr, best_lag = c, lag
    return best_lag, best_corr


def analyze(ticker: str, sector_hint: str | None = None) -> dict:
    df = get_history(ticker, period="2y")
    if df.empty:
        return {"error": "No data"}
    ticker_ret = df["Close"].pct_change().dropna()

    relationships = dict(MACRO_PROXIES)
    if sector_hint and sector_hint in SECTOR_ETFS:
        relationships[f"Sector ETF ({sector_hint})"] = SECTOR_ETFS[sector_hint]

    rows = []
    for label, sym in relationships.items():
        proxy_df = get_history(sym, period="2y")
        if proxy_df.empty:
            continue
        proxy_ret = proxy_df["Close"].pct_change().dropna()
        aligned = pd.concat([ticker_ret, proxy_ret], axis=1, keys=["t", "p"]).dropna()
        if len(aligned) < 60:
            continue
        corr_full = aligned["t"].corr(aligned["p"])
        corr_recent = aligned.tail(60)["t"].corr(aligned.tail(60)["p"])
        lag, lag_corr = _lead_lag(aligned["t"], aligned["p"])

        stability = "Stable" if abs(corr_full - corr_recent) < 0.25 else "Unstable / regime-shifting"
        divergence = "Diverging from historical pattern" if abs(corr_full - corr_recent) > 0.3 else "Confirming historical pattern"

        if lag > 0:
            lead_lag_desc = f"{label} leads {ticker} by ~{lag}d"
        elif lag < 0:
            lead_lag_desc = f"{ticker} leads {label} by ~{abs(lag)}d"
        else:
            lead_lag_desc = "Contemporaneous (no clear lead/lag)"

        rows.append({
            "relationship": label,
            "symbol": sym,
            "corr_full_history": round(corr_full, 3),
            "corr_recent_60d": round(corr_recent, 3),
            "stability": stability,
            "current_signal": divergence,
            "lead_lag": lead_lag_desc,
        })

    return {"table": pd.DataFrame(rows)}
