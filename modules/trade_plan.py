"""Institutional Trade Plan & Risk Management Framework — synthesizes the other frameworks."""


def build(trend_result: dict, mr_result: dict, vol_result: dict, ticker: str) -> dict:
    if "error" in trend_result:
        return {"error": trend_result["error"]}

    entry_low, entry_high = trend_result["entry_zone"]
    stop = trend_result["stop"]
    t1 = trend_result["target1"]
    t2 = trend_result["target2"]
    rr = trend_result["rr"]

    thesis_bits = []
    thesis_bits.append(
        f"{ticker} is in a {trend_result['trend_daily'].lower()} daily trend "
        f"({trend_result['structure']}) with {trend_result['alignment'].lower()} multi-timeframe trend."
    )
    if "signal" in mr_result and mr_result.get("signal") != "NO SIGNAL":
        thesis_bits.append(f"Mean reversion overlay: {mr_result['signal']}.")
    if vol_result.get("breakout_confirmed"):
        thesis_bits.append("Recent breakout is confirmed by above-average volume.")
    elif vol_result.get("volume_dry_up"):
        thesis_bits.append("Volume dry-up during consolidation suggests a potential coiled spring.")
    thesis = " ".join(thesis_bits[:3])

    # position sizing logic
    if rr >= 3 and trend_result["alignment"] == "ALL ALIGNED":
        position_size = "FULL SIZE (core position)"
    elif rr >= 2:
        position_size = "STANDARD / starter position"
    else:
        position_size = "NO ENTRY — R:R below 2:1 minimum threshold"

    monitoring_kpis = [
        {
            "kpi": "50-day MA hold",
            "current": round(trend_result.get("ma50") or 0, 2),
            "confirm_level": f"Price > {round(trend_result.get('ma50') or 0, 2)}",
            "reject_level": f"Daily close < {round((trend_result.get('ma50') or 0) * 0.98, 2)}",
        },
        {
            "kpi": "Relative volume on next breakout attempt",
            "current": round(vol_result.get("rel_vol_20d_pct") or 0, 1),
            "confirm_level": ">130% of 20d avg volume",
            "reject_level": "<80% of 20d avg volume (unconfirmed move)",
        },
        {
            "kpi": "RSI regime",
            "current": round(trend_result.get("rsi") or 0, 1),
            "confirm_level": "RSI holds 40-70 (healthy trend)",
            "reject_level": "RSI < 30 sustained (trend failure) or > 80 (exhaustion)",
        },
    ]

    scenarios = [
        {"scenario": "Gap down through stop-loss at open", "action": f"Exit full position at open regardless of stop price; do not average down."},
        {"scenario": "Range extension beyond Target 2 without pullback", "action": "Trail stop to breakeven+, scale out 1/3 further, let remainder run with trailing stop below prior swing low."},
        {"scenario": "Sideways chop between entry zone and Target 1 for 15+ sessions", "action": "Reassess thesis; if volume/momentum has faded, reduce to half size."},
    ]

    return {
        "thesis": thesis,
        "entry_zone": (entry_low, entry_high),
        "stop": stop,
        "target1": t1,
        "target2": t2,
        "rr": rr,
        "rr_flag": rr < 2,
        "position_size": position_size,
        "conviction": trend_result.get("conviction"),
        "monitoring_kpis": monitoring_kpis,
        "scenarios": scenarios,
    }
