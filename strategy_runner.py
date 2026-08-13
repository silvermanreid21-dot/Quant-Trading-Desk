"""
Unattended systematic runner for Strategy v2 (strategy.V2_PARAMS) against the
Alpaca PAPER account. Intended to be scheduled once per weekday, after market
close, so it acts on a fully-formed daily bar and submits market-on-open
bracket orders for the next session — matching the backtest's fill assumption.

Safety rails (mirrors the backtest exactly — same params, same position sizing,
same limits):
- Reads credentials from environment variables only (ALPACA_API_KEY_ID /
  ALPACA_API_SECRET_KEY) — never hardcoded, never prompted interactively.
- File-based kill switch: if RUNNER_DISABLED exists in this directory, the run
  exits immediately without evaluating anything.
- Market-hours guard: refuses to run while the market is open (bar would be
  partial/incomplete), unless --force is passed for manual testing.
- Max 5 concurrent positions, 2% of equity risked per new trade.
- Daily circuit breaker: skips all new entries if today's equity is down 5%+
  from yesterday's close.
- Options flow gate: every equity signal is also checked against current options
  positioning (modules/options_flow.py) — heavy put buying or elevated put skew
  skips the entry. This never trades option contracts, only filters the same
  share trades using signals already shown on the dashboard's Options Flow & IV
  tab. Fails open (doesn't block) if no options chain is available. Note: this
  gate only exists live — strategy_backtest.py has no historical options data,
  so backtested results don't reflect it.
- Every decision — scanned, skipped, signaled, submitted, or errored — is
  appended to runner_log.jsonl for after-the-fact auditing.

Usage:
    .venv/Scripts/python.exe strategy_runner.py --dry-run   # logs intended actions, submits nothing
    .venv/Scripts/python.exe strategy_runner.py             # live paper submission
"""

import argparse
import json
import os
import sys
from datetime import datetime, time as dtime
from pathlib import Path
from zoneinfo import ZoneInfo

import broker_alpaca
import strategy
from data import get_history
from modules import options_flow
from universe import SP100

HERE = Path(__file__).parent
KILL_SWITCH_FILE = HERE / "RUNNER_DISABLED"
LOG_FILE = HERE / "runner_log.jsonl"

MAX_CONCURRENT_POSITIONS = 5
RISK_PER_TRADE_PCT = 0.02
DAILY_LOSS_BREAKER_PCT = 0.05
STRATEGY_PARAMS = strategy.V2_PARAMS


def log_event(event: str, **fields):
    entry = {"timestamp": datetime.now().isoformat(timespec="seconds"), "event": event, **fields}
    with open(LOG_FILE, "a") as f:
        f.write(json.dumps(entry, default=str) + "\n")
    print(entry)


def market_is_open_now() -> bool:
    now_et = datetime.now(ZoneInfo("America/New_York"))
    if now_et.weekday() >= 5:
        return False
    return dtime(9, 30) <= now_et.time() <= dtime(16, 0)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="Log intended actions without submitting orders.")
    parser.add_argument("--force", action="store_true", help="Bypass the market-hours guard (testing only).")
    args = parser.parse_args()

    if KILL_SWITCH_FILE.exists():
        log_event("halted", reason=f"kill switch present ({KILL_SWITCH_FILE.name})")
        return

    if market_is_open_now() and not args.force:
        log_event("halted", reason="market is open; this runner expects to run after close on a completed daily bar")
        return

    api_key = broker_alpaca.get_credential_from_env("ALPACA_API_KEY_ID")
    secret_key = broker_alpaca.get_credential_from_env("ALPACA_API_SECRET_KEY")
    if not api_key or not secret_key:
        log_event("halted", reason="ALPACA_API_KEY_ID / ALPACA_API_SECRET_KEY not set in environment")
        return

    try:
        client = broker_alpaca.connect(api_key, secret_key)
    except Exception as e:
        log_event("halted", reason=f"could not connect to Alpaca: {e}")
        return

    account = client.get_account()
    equity = float(account.equity)
    last_equity = float(account.last_equity) if float(account.last_equity) else equity
    daily_pnl_pct = (equity - last_equity) / last_equity if last_equity else 0.0

    log_event("run_start", dry_run=args.dry_run, equity=equity, daily_pnl_pct=round(daily_pnl_pct * 100, 2))

    if daily_pnl_pct <= -DAILY_LOSS_BREAKER_PCT:
        log_event("circuit_breaker_tripped", daily_pnl_pct=round(daily_pnl_pct * 100, 2), threshold_pct=-DAILY_LOSS_BREAKER_PCT * 100)
        return

    positions_df = broker_alpaca.get_positions(client)
    held_symbols = set(positions_df["symbol"]) if not positions_df.empty else set()
    orders_df = broker_alpaca.get_open_orders(client)
    pending_symbols = set(orders_df["symbol"]) if not orders_df.empty else set()
    committed_symbols = held_symbols | pending_symbols

    available_slots = MAX_CONCURRENT_POSITIONS - len(committed_symbols)
    log_event("portfolio_state", held=sorted(held_symbols), pending=sorted(pending_symbols), available_slots=available_slots)

    if available_slots <= 0:
        log_event("run_end", reason="no available position slots")
        return

    submitted = 0
    for symbol in SP100:
        if submitted >= available_slots:
            break
        if symbol in committed_symbols:
            continue
        try:
            df = get_history(symbol, period="2y")
            sig = strategy.compute_signals(df, STRATEGY_PARAMS)
            if sig.empty:
                continue
            last = sig.iloc[-1]
        except Exception as e:
            log_event("scan_error", symbol=symbol, error=str(e))
            continue

        if not bool(last["entry_signal"]):
            continue

        flagged, options_reason = options_flow.bearish_flag(symbol)
        if flagged:
            log_event("signal_skipped", symbol=symbol, reason=f"options flow gate: {options_reason}")
            continue

        atr = float(last["atr"])
        stop_price = float(last["stop_price"])
        target_price = float(last["target_price"])
        risk_per_share = strategy.STOP_ATR_MULT * atr
        if risk_per_share <= 0:
            continue

        risk_dollars = RISK_PER_TRADE_PCT * equity
        qty = int(risk_dollars // risk_per_share)
        if qty <= 0:
            log_event("signal_skipped", symbol=symbol, reason="computed quantity was 0 at current equity/risk sizing")
            continue

        if args.dry_run:
            log_event("dry_run_signal", symbol=symbol, qty=qty, stop_price=round(stop_price, 2), target_price=round(target_price, 2))
        else:
            try:
                order = broker_alpaca.place_bracket_market_order(client, symbol, "BUY", qty, stop_price, target_price)
                log_event("order_submitted", symbol=symbol, qty=qty, stop_price=round(stop_price, 2), target_price=round(target_price, 2), order_id=str(order.id))
            except Exception as e:
                log_event("order_error", symbol=symbol, error=str(e))
                continue
        submitted += 1

    log_event("run_end", submitted=submitted)


if __name__ == "__main__":
    main()
