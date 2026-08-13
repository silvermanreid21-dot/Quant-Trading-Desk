"""Institutional Volume & Order Flow Intelligence Framework."""

import numpy as np
import pandas as pd

from . import indicators as ind


def analyze(df: pd.DataFrame) -> dict:
    if df.empty or len(df) < 60:
        return {"error": "Not enough price/volume history."}

    close = df["Close"]
    volume = df["Volume"]

    vol_avg20 = volume.tail(20).mean()
    vol_avg50 = volume.tail(50).mean()
    vol_avg200 = volume.tail(200).mean() if len(volume) >= 200 else volume.mean()
    vol_now = volume.iloc[-1]

    last20 = df.tail(20)
    up_days = last20[last20["Close"].diff() > 0]
    down_days = last20[last20["Close"].diff() < 0]
    up_vol = up_days["Volume"].sum()
    down_vol = down_days["Volume"].sum()
    pv_relationship = "Up-volume dominant (buying pressure)" if up_vol > down_vol else "Down-volume dominant (selling pressure)"

    obv_series = ind.obv(close, volume)
    vpt_series = ind.vpt(close, volume)

    # divergence: price making new local high but OBV isn't (or vice versa)
    price_slope = np.polyfit(range(20), close.tail(20), 1)[0]
    obv_slope = np.polyfit(range(20), obv_series.tail(20), 1)[0]
    if price_slope > 0 and obv_slope < 0:
        divergence = "BEARISH divergence: price rising, OBV falling"
    elif price_slope < 0 and obv_slope > 0:
        divergence = "BULLISH divergence: price falling, OBV rising"
    else:
        divergence = "No divergence — OBV confirms price trend"

    # unusual volume days: top 10 by ratio to trailing 20d avg, over last 6 months (~126 sessions)
    window = df.tail(126).copy()
    window["avg20"] = window["Volume"].rolling(20).mean()
    window["ratio"] = window["Volume"] / window["avg20"]
    window["pct_change"] = window["Close"].pct_change() * 100
    unusual = window.dropna(subset=["ratio"]).sort_values("ratio", ascending=False).head(10)
    unusual_days = [
        {
            "date": idx.strftime("%Y-%m-%d"),
            "volume": int(row["Volume"]),
            "vs_avg_pct": round((row["ratio"] - 1) * 100, 1),
            "price_change_pct": round(row["pct_change"], 2),
        }
        for idx, row in unusual.iterrows()
    ]

    # institutional signature heuristic: repeated above-avg volume up-days with tight ranges (accumulation)
    last10 = df.tail(10)
    tight_range_up_vol = last10[(last10["Close"].diff() > 0) & (last10["Volume"] > vol_avg20)]
    institutional_signature = (
        "Consistent with institutional accumulation (repeated above-avg-volume up days)"
        if len(tight_range_up_vol) >= 4
        else "No strong institutional accumulation signature detected"
    )

    # breakout confirmation: did the most recent close above prior 20d high happen on high volume?
    prior_high_20d = close.rolling(20).max().shift(1)
    breakout_today = close.iloc[-1] > prior_high_20d.iloc[-1] if not pd.isna(prior_high_20d.iloc[-1]) else False
    breakout_confirmed = breakout_today and (vol_now > vol_avg20 * 1.3)

    # volume dry-up: below-average volume during recent consolidation (tight price range)
    recent_range_pct = (last20["High"].max() - last20["Low"].min()) / close.iloc[-1] * 100
    dry_up = vol_now < vol_avg20 * 0.7 and recent_range_pct < 8

    return {
        "vol_now": int(vol_now),
        "vol_avg20": float(vol_avg20),
        "vol_avg50": float(vol_avg50),
        "vol_avg200": float(vol_avg200),
        "rel_vol_20d_pct": float(vol_now / vol_avg20 * 100) if vol_avg20 else None,
        "rel_vol_50d_pct": float(vol_now / vol_avg50 * 100) if vol_avg50 else None,
        "rel_vol_200d_pct": float(vol_now / vol_avg200 * 100) if vol_avg200 else None,
        "pv_relationship": pv_relationship,
        "divergence": divergence,
        "unusual_days": unusual_days,
        "institutional_signature": institutional_signature,
        "breakout_today": bool(breakout_today),
        "breakout_confirmed": bool(breakout_confirmed),
        "volume_dry_up": bool(dry_up),
        "obv_series": obv_series,
        "vpt_series": vpt_series,
        "close": close,
    }
