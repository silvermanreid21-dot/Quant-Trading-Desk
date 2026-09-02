"""
Event-driven portfolio backtest for Strategy v1 (see strategy.py), enforcing the
same portfolio-level risk limits the live runner will use: 2% equity risked per
trade, max 5 concurrent positions, and a daily circuit breaker that halts new
entries for the rest of the day if closed P&L drops 5% of that day's starting equity.

Usage:
    .venv/Scripts/python.exe strategy_backtest.py
"""

import sys
import numpy as np
import pandas as pd
import yfinance as yf

import strategy
from universe import SP100

INITIAL_CAPITAL = 100_000.0
RISK_PER_TRADE_PCT = 0.02
MAX_CONCURRENT_POSITIONS = 5
DAILY_LOSS_BREAKER_PCT = 0.05
PERIOD = "5y"

# shares = risk_dollars // (entry_price - stop_price) blows up when an overnight
# gap-down open lands close to the prior day's computed stop, since the divisor
# goes near zero — one $2.5M position on a $100k account, found by inspecting an
# outlier trade. Capping notional as a fraction of equity is a standard
# single-name concentration limit and closes this off regardless of cause.
MAX_POSITION_PCT_OF_EQUITY = 0.20

# Rotation: if the portfolio is full and a new signal fires that looks stronger than
# our weakest-ranked holding, exit the weakest holding at today's close and queue the
# new signal for tomorrow's open instead of waiting for its own stop/target/time exit.
# Aggressive by design (per explicit request) — no margin required, and any position
# is eligible including a current winner, not just laggards. Scoring itself
# (strategy.rotation_score) lives in strategy.py so the backtest and the live
# runner's rotation-shadow check rank candidates identically.


def download_universe(tickers: list[str]) -> dict[str, pd.DataFrame]:
    print(f"Downloading {len(tickers)} tickers ({PERIOD})...", file=sys.stderr)
    raw = yf.download(tickers, period=PERIOD, group_by="ticker", auto_adjust=True, progress=False, threads=True)
    data = {}
    for t in tickers:
        try:
            df = raw[t].dropna(how="all")
        except (KeyError, Exception):
            continue
        if len(df) >= 280:
            data[t] = df
    print(f"Usable data for {len(data)}/{len(tickers)} tickers.", file=sys.stderr)
    return data


def run_backtest(price_data: dict[str, pd.DataFrame], params: dict = None, trailing_stop: bool = False, rotation: str = None, rotation_margin: float = 0.0) -> dict:
    signals = {t: strategy.compute_signals(df, params) for t, df in price_data.items()}
    signals = {t: s for t, s in signals.items() if not s.empty}

    # unified trading calendar = the longest available date index
    calendar = max((s.index for s in signals.values()), key=len)
    calendar = calendar.sort_values()

    equity = INITIAL_CAPITAL
    cash_realized_pnl = 0.0
    open_positions = {}  # symbol -> dict
    pending_entries = []  # list of (symbol, stop_price, target_price, signal_date)
    equity_curve = []
    trade_log = []

    warmup = 260
    for i in range(warmup, len(calendar)):
        today = calendar[i]
        closed_pnl_today = 0.0
        day_start_equity = equity

        # 1) process exits for open positions
        for sym in list(open_positions.keys()):
            pos = open_positions[sym]
            df = price_data[sym]
            if today not in df.index:
                continue
            bar = df.loc[today]
            exit_price, exit_reason = None, None

            if trailing_stop:
                pos["highest_high"] = max(pos["highest_high"], float(bar["High"]))
                atr_today = signals[sym].loc[today, "atr"] if today in signals[sym].index else np.nan
                if not np.isnan(atr_today):
                    trail = pos["highest_high"] - strategy.TRAILING_STOP_ATR_MULT * atr_today
                    pos["stop"] = max(pos["stop"], trail)  # ratchets up only, never loosens

                hit_stop = bar["Low"] <= pos["stop"]
                if hit_stop:
                    exit_price, exit_reason = pos["stop"], "trailing_stop"
                elif (i - pos["entry_idx"]) >= strategy.MAX_HOLD_DAYS:
                    exit_price, exit_reason = bar["Close"], "time_exit"
            else:
                hit_stop = bar["Low"] <= pos["stop"]
                hit_target = bar["High"] >= pos["target"]
                if hit_stop and hit_target:
                    exit_price, exit_reason = pos["stop"], "stop (conservative same-day tie)"
                elif hit_stop:
                    exit_price, exit_reason = pos["stop"], "stop"
                elif hit_target:
                    exit_price, exit_reason = pos["target"], "target"
                elif (i - pos["entry_idx"]) >= strategy.MAX_HOLD_DAYS:
                    exit_price, exit_reason = bar["Close"], "time_exit"

            if exit_price is not None:
                pnl = (exit_price - pos["entry_price"]) * pos["shares"]
                r_multiple = pnl / pos["risk_dollars"] if pos["risk_dollars"] else np.nan
                cash_realized_pnl += pnl
                closed_pnl_today += pnl
                trade_log.append({
                    "symbol": sym, "entry_date": pos["entry_date"], "exit_date": today,
                    "entry_price": round(pos["entry_price"], 2), "exit_price": round(exit_price, 2),
                    "shares": pos["shares"], "pnl": round(pnl, 2), "r_multiple": round(r_multiple, 2),
                    "exit_reason": exit_reason,
                })
                del open_positions[sym]

        # 2) activate any entries queued from yesterday's signal, filled at today's open
        still_room = MAX_CONCURRENT_POSITIONS - len(open_positions)
        for sym, stop_price, target_price, signal_date in pending_entries[:still_room]:
            df = price_data[sym]
            if today not in df.index or sym in open_positions:
                continue
            entry_price = float(df.loc[today, "Open"])
            risk_per_share = entry_price - stop_price
            if risk_per_share <= 0:
                continue
            risk_dollars = RISK_PER_TRADE_PCT * equity
            shares = int(risk_dollars // risk_per_share)
            max_shares_by_notional = int((MAX_POSITION_PCT_OF_EQUITY * equity) // entry_price)
            shares = min(shares, max_shares_by_notional)
            if shares <= 0:
                continue
            open_positions[sym] = {
                "entry_price": entry_price, "stop": stop_price, "target": target_price,
                "shares": shares, "entry_date": today, "entry_idx": i, "risk_dollars": risk_dollars,
                "highest_high": max(entry_price, float(df.loc[today, "High"])),
            }
        pending_entries = []

        equity = INITIAL_CAPITAL + cash_realized_pnl

        # 3) daily circuit breaker check (based on today's closed P&L)
        breached = closed_pnl_today <= -DAILY_LOSS_BREAKER_PCT * day_start_equity

        # 4) scan for new entries (queued for tomorrow's open) if not breached
        if not breached:
            room = MAX_CONCURRENT_POSITIONS - len(open_positions)
            candidates = []
            for sym, sig in signals.items():
                if sym in open_positions or today not in sig.index:
                    continue
                row = sig.loc[today]
                if bool(row["entry_signal"]):
                    candidates.append((sym, float(row["stop_price"]), float(row["target_price"])))
            for sym, stop_price, target_price in candidates[:room]:
                pending_entries.append((sym, stop_price, target_price, today))

            # 4b) rotation: portfolio's full (or nearly) and there are still-unplaced
            # candidates left over — see if any beats our weakest-ranked holding.
            if rotation and len(candidates) > room:
                scored_candidates = []
                for sym, stop_price, target_price in candidates[room:]:
                    s = strategy.rotation_score(price_data[sym], today, rotation)
                    if not np.isnan(s):
                        scored_candidates.append((s, sym, stop_price, target_price))
                scored_candidates.sort(key=lambda x: x[0], reverse=True)

                scored_held = []
                for sym, pos in open_positions.items():
                    if pos["entry_date"] == today:
                        continue  # give a fresh entry at least one day before it's rotation-eligible
                    if today not in price_data[sym].index:
                        continue  # this symbol has a data gap on today's date specifically
                    s = strategy.rotation_score(price_data[sym], today, rotation)
                    if not np.isnan(s):
                        scored_held.append((s, sym))
                scored_held.sort(key=lambda x: x[0])  # weakest first

                for (cand_score, csym, cstop, ctarget), (held_score, hsym) in zip(scored_candidates, scored_held):
                    if cand_score <= held_score + rotation_margin:
                        break  # sorted best-candidate-vs-weakest-holding; no further pair will improve on this
                    pos = open_positions[hsym]
                    exit_price = float(price_data[hsym].loc[today, "Close"])
                    pnl = (exit_price - pos["entry_price"]) * pos["shares"]
                    r_multiple = pnl / pos["risk_dollars"] if pos["risk_dollars"] else np.nan
                    cash_realized_pnl += pnl
                    trade_log.append({
                        "symbol": hsym, "entry_date": pos["entry_date"], "exit_date": today,
                        "entry_price": round(pos["entry_price"], 2), "exit_price": round(exit_price, 2),
                        "shares": pos["shares"], "pnl": round(pnl, 2), "r_multiple": round(r_multiple, 2),
                        "exit_reason": "rotated_out",
                    })
                    del open_positions[hsym]
                    pending_entries.append((csym, cstop, ctarget, today))
                equity = INITIAL_CAPITAL + cash_realized_pnl

        # 5) mark-to-market equity curve (realized + unrealized at today's close)
        unrealized = 0.0
        for sym, pos in open_positions.items():
            df = price_data[sym]
            if today in df.index:
                unrealized += (float(df.loc[today, "Close"]) - pos["entry_price"]) * pos["shares"]
        mtm_equity = INITIAL_CAPITAL + cash_realized_pnl + unrealized
        equity_curve.append({"date": today, "equity": mtm_equity})

    equity_df = pd.DataFrame(equity_curve).set_index("date")
    trades_df = pd.DataFrame(trade_log)
    return {"equity_curve": equity_df, "trades": trades_df, "final_equity": INITIAL_CAPITAL + cash_realized_pnl}


def summarize(results: dict) -> dict:
    trades = results["trades"]
    equity_curve = results["equity_curve"]

    if trades.empty:
        return {"n_trades": 0, "note": "No trades were generated — the rule set never fired over this window/universe."}

    wins = trades[trades["pnl"] > 0]
    losses = trades[trades["pnl"] <= 0]
    win_rate = len(wins) / len(trades) * 100
    avg_r = trades["r_multiple"].mean()
    profit_factor = wins["pnl"].sum() / abs(losses["pnl"].sum()) if len(losses) and losses["pnl"].sum() != 0 else np.nan

    running_max = equity_curve["equity"].cummax()
    drawdown = (equity_curve["equity"] - running_max) / running_max
    max_dd = drawdown.min() * 100

    total_return = (results["final_equity"] / INITIAL_CAPITAL - 1) * 100

    return {
        "n_trades": len(trades),
        "win_rate_%": round(win_rate, 1),
        "avg_r_multiple": round(avg_r, 2),
        "profit_factor": round(profit_factor, 2) if not np.isnan(profit_factor) else None,
        "total_return_%": round(total_return, 2),
        "max_drawdown_%": round(max_dd, 2),
        "final_equity": round(results["final_equity"], 2),
        "exit_reason_counts": trades["exit_reason"].value_counts().to_dict(),
    }


if __name__ == "__main__":
    import sys as _sys

    variant = _sys.argv[1] if len(_sys.argv) > 1 else "v1"
    parts = variant.split("-")
    params = strategy.V2_PARAMS if parts[0] == "v2" else strategy.V1_PARAMS
    trailing_stop = "trailing" in parts
    rotation = parts[parts.index("rotate") + 1] if "rotate" in parts else None

    price_data = download_universe(SP100)
    results = run_backtest(price_data, params, trailing_stop=trailing_stop, rotation=rotation)
    summary = summarize(results)

    print(f"\n=== Strategy {variant} Backtest Summary ({params}) ===")
    for k, v in summary.items():
        print(f"{k}: {v}")

    results["trades"].to_csv(f"backtest_trades_{variant}.csv", index=False)
    results["equity_curve"].to_csv(f"backtest_equity_curve_{variant}.csv")
    print(f"\nSaved backtest_trades_{variant}.csv and backtest_equity_curve_{variant}.csv")
