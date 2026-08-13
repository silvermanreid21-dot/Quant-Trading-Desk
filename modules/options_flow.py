"""Options Flow Analysis & Implied Volatility Intelligence Framework."""

import numpy as np
import pandas as pd

from data import get_option_chain, get_option_expirations, get_history


def realized_vol(close: pd.Series, window: int) -> float:
    rets = close.pct_change().tail(window)
    return float(rets.std() * np.sqrt(252) * 100)


def analyze(ticker: str, expiration: str | None = None) -> dict:
    expirations = get_option_expirations(ticker)
    if not expirations:
        return {"error": f"No listed options chain available for {ticker}."}

    exp = expiration or expirations[0]
    calls, puts = get_option_chain(ticker, exp)
    if calls.empty and puts.empty:
        return {"error": f"Empty options chain for {ticker} {exp}."}

    price_df = get_history(ticker, period="1y")
    spot = float(price_df["Close"].iloc[-1])

    pc_ratio_volume = puts["volume"].sum() / calls["volume"].sum() if calls["volume"].sum() else np.nan
    pc_ratio_oi = puts["openInterest"].sum() / calls["openInterest"].sum() if calls["openInterest"].sum() else np.nan

    # ATM contracts (closest strike to spot)
    calls_sorted = calls.assign(dist=(calls["strike"] - spot).abs()).sort_values("dist")
    puts_sorted = puts.assign(dist=(puts["strike"] - spot).abs()).sort_values("dist")
    atm_call = calls_sorted.iloc[0] if not calls_sorted.empty else None
    atm_put = puts_sorted.iloc[0] if not puts_sorted.empty else None

    atm_call_iv = float(atm_call["impliedVolatility"] * 100) if atm_call is not None else None
    atm_put_iv = float(atm_put["impliedVolatility"] * 100) if atm_put is not None else None
    atm_iv = np.nanmean([v for v in [atm_call_iv, atm_put_iv] if v is not None])

    hv30 = realized_vol(price_df["Close"], 30)
    hv60 = realized_vol(price_df["Close"], 60)
    hv90 = realized_vol(price_df["Close"], 90)

    iv_vs_hv = "IV rich vs realized (options pricing in more movement than recently occurred)" if atm_iv > hv30 else \
               "IV cheap vs realized (options pricing in less movement than recently occurred)"

    # skew: compare IV of a downside put ~10% OTM vs upside call ~10% OTM
    otm_put_target = spot * 0.90
    otm_call_target = spot * 1.10
    otm_put = puts.assign(dist=(puts["strike"] - otm_put_target).abs()).sort_values("dist").iloc[0] if not puts.empty else None
    otm_call = calls.assign(dist=(calls["strike"] - otm_call_target).abs()).sort_values("dist").iloc[0] if not calls.empty else None
    skew = None
    skew_desc = "N/A"
    if otm_put is not None and otm_call is not None:
        skew = float(otm_put["impliedVolatility"] - otm_call["impliedVolatility"]) * 100
        skew_desc = "Put skew (downside fear priced in)" if skew > 1 else \
                    "Call skew (upside speculation priced in)" if skew < -1 else "Flat skew"

    # implied move from ATM straddle
    implied_move_pct = None
    if atm_call is not None and atm_put is not None:
        straddle_price = float(atm_call["lastPrice"]) + float(atm_put["lastPrice"])
        implied_move_pct = straddle_price / spot * 100

    # unusual activity: contracts where volume > openInterest (new positioning) and volume is large
    def unusual(df, side):
        d = df.copy()
        d["vol_oi_ratio"] = d["volume"] / d["openInterest"].replace(0, np.nan)
        d = d[(d["volume"] > 100) & (d["vol_oi_ratio"] > 0.5)].sort_values("volume", ascending=False).head(5)
        return [
            {
                "side": side,
                "strike": float(r["strike"]),
                "volume": int(r["volume"]),
                "open_interest": int(r["openInterest"]),
                "vs_oi_ratio": round(float(r["vol_oi_ratio"]), 2) if not np.isnan(r["vol_oi_ratio"]) else None,
                "implied_vol_%": round(float(r["impliedVolatility"] * 100), 1),
            }
            for _, r in d.iterrows()
        ]

    unusual_activity = unusual(calls, "CALL") + unusual(puts, "PUT")

    # gamma pin candidates: strikes with largest open interest
    oi_by_strike = pd.concat([
        calls[["strike", "openInterest"]].assign(side="call"),
        puts[["strike", "openInterest"]].assign(side="put"),
    ])
    top_oi = oi_by_strike.groupby("strike")["openInterest"].sum().sort_values(ascending=False).head(5)
    gamma_pins = [{"strike": float(k), "total_open_interest": int(v)} for k, v in top_oi.items()]

    synthesis_bits = []
    if pc_ratio_volume and pc_ratio_volume > 1.2:
        synthesis_bits.append("Put volume dominant — market hedging/bearish positioning")
    elif pc_ratio_volume and pc_ratio_volume < 0.7:
        synthesis_bits.append("Call volume dominant — market leaning bullish/speculative")
    if skew is not None and skew > 3:
        synthesis_bits.append("Elevated put skew signals downside protection demand exceeding upside speculation")
    if implied_move_pct:
        synthesis_bits.append(f"Options market pricing ~{implied_move_pct:.1f}% move by {exp}")
    synthesis = "; ".join(synthesis_bits) if synthesis_bits else "No strong directional signal from options positioning"

    return {
        "expiration": exp,
        "all_expirations": expirations,
        "spot": spot,
        "pc_ratio_volume": pc_ratio_volume,
        "pc_ratio_oi": pc_ratio_oi,
        "atm_iv_%": atm_iv,
        "hv30_%": hv30,
        "hv60_%": hv60,
        "hv90_%": hv90,
        "iv_vs_hv": iv_vs_hv,
        "skew_%": skew,
        "skew_desc": skew_desc,
        "implied_move_%": implied_move_pct,
        "unusual_activity": unusual_activity,
        "gamma_pins": gamma_pins,
        "synthesis": synthesis,
    }


def bearish_flag(ticker: str) -> tuple[bool, str]:
    """Live-runner gate: True if current options positioning looks bearish enough to
    skip an otherwise-valid equity entry signal. We only ever trade shares — this
    never buys contracts — it just uses the same put/call volume ratio and skew
    shown on the dashboard's Options Flow & IV tab as an extra filter.

    Fails open (returns False) on any lookup error or missing chain, since an
    illiquid or unavailable options market shouldn't block an equity-only decision."""
    try:
        result = analyze(ticker)
    except Exception as e:
        return False, f"options lookup failed ({e}), not blocking"
    if "error" in result:
        return False, f"{result['error']}, not blocking"

    pc_ratio = result["pc_ratio_volume"]
    skew = result["skew_%"]

    if pc_ratio and pc_ratio > 1.5:
        return True, f"put/call volume ratio {pc_ratio:.2f} > 1.5 (heavy put buying)"
    if skew and skew > 5:
        return True, f"put skew {skew:.1f}% > 5% (downside protection demand)"
    return False, "options flow not bearish"
