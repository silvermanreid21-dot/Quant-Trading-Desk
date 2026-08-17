"""
Unattended systematic runner for Strategy v2 (strategy.V2_PARAMS) against the
Alpaca PAPER account. Intended to be scheduled once per weekday, after market
close, so it acts on a fully-formed daily bar and submits market-on-open entry
orders for the next session — matching the backtest's fill assumption.

Safety rails (mirrors the backtest exactly — same params, same position sizing,
same limits):
- Reads credentials from environment variables only (ALPACA_API_KEY_ID /
  ALPACA_API_SECRET_KEY) — never hardcoded, never prompted interactively.
- File-based kill switch: if RUNNER_DISABLED exists in this directory, the run
  exits immediately without evaluating anything.
- Market-hours guard: refuses to run while the market is open (bar would be
  partial/incomplete), unless --force is passed for manual testing.
- Max 5 concurrent positions, 2% of equity risked per new trade, capped at 20%
  of equity notional per position (backstops the risk-sizing formula against a
  near-zero ATR/stop distance blowing the share count up unreasonably).
- Daily circuit breaker: skips all new entries if today's equity is down 5%+
  from yesterday's close.
- Options flow gate: every equity signal is also checked against current options
  positioning (modules/options_flow.py) — heavy put buying or elevated put skew
  skips the entry. This never trades option contracts, only filters the same
  share trades using signals already shown on the dashboard's Options Flow & IV
  tab. Fails open (doesn't block) if no options chain is available. Note: this
  gate only exists live — strategy_backtest.py has no historical options data,
  so backtested results don't reflect it.
- Trailing-stop exits: new entries are a plain market order (no fixed take-profit)
  paired with an Alpaca-native trailing-stop SELL order, submitted once shares are
  actually held — validated in backtest to meaningfully outperform the old fixed
  2:1 bracket target by letting winners run instead of capping them (see
  strategy.TRAILING_STOP_ATR_MULT). Positions opened before 2026-08-17 keep their
  original fixed OCO stop/target; only new entries use trailing.
- Self-healing protection check: Alpaca's bracket/OCO exit legs have repeatedly
  been observed to vanish (expired/canceled) after entry fill — sometimes both
  legs, sometimes just the stop while the target survives. Every run now audits
  every held position for a live STOP or TRAILING_STOP order (not just "any open
  order", which misses the asymmetric case) and resubmits the appropriate
  protective order — trailing-stop or legacy OCO pair, per protection_state.json
  — if none is found. qty always comes from the live position, never blindly from
  remembered state, so a stale/wrong quantity can't submit an oversized sell.
- Desktop notifications (win11toast) fire on new entries, protection repairs
  (and repair failures), circuit breaker trips, and connection/credential
  halts — so these don't require a manual status check to notice.
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

from alpaca.trading.requests import GetOrdersRequest
from alpaca.trading.enums import OrderStatus, OrderType, QueryOrderStatus

# QueryOrderStatus.OPEN (a server-side filter) silently excludes HELD orders —
# exactly the status an OCO's second leg sits in while waiting behind its filled
# sibling. So protection checks query ALL orders and filter client-side against
# this set of genuinely non-terminal statuses instead.
LIVE_ORDER_STATUSES = {
    OrderStatus.NEW, OrderStatus.PARTIALLY_FILLED, OrderStatus.ACCEPTED,
    OrderStatus.PENDING_NEW, OrderStatus.ACCEPTED_FOR_BIDDING, OrderStatus.CALCULATED,
    OrderStatus.HELD, OrderStatus.PENDING_REVIEW, OrderStatus.PENDING_REPLACE,
    OrderStatus.PENDING_CANCEL, OrderStatus.SUSPENDED,
}

import broker_alpaca
import strategy
from data import get_history
from modules import options_flow
from universe import SP100

try:
    from win11toast import toast
except ImportError:
    toast = None

HERE = Path(__file__).parent
KILL_SWITCH_FILE = HERE / "RUNNER_DISABLED"
LOG_FILE = HERE / "runner_log.jsonl"
PROTECTION_STATE_FILE = HERE / "protection_state.json"

MAX_CONCURRENT_POSITIONS = 5
RISK_PER_TRADE_PCT = 0.02
DAILY_LOSS_BREAKER_PCT = 0.05
MAX_POSITION_PCT_OF_EQUITY = 0.20  # single-name concentration cap; also backstops risk-sizing against a near-zero ATR
STRATEGY_PARAMS = strategy.V2_PARAMS


def log_event(event: str, **fields):
    entry = {"timestamp": datetime.now().isoformat(timespec="seconds"), "event": event, **fields}
    with open(LOG_FILE, "a") as f:
        f.write(json.dumps(entry, default=str) + "\n")
    print(entry)


def notify(title: str, message: str):
    """Desktop toast for events worth interrupting for (fills, repairs, halts,
    errors) — not every scan/skip. Silently does nothing if win11toast isn't
    installed or the OS call fails; a missing notification should never break
    a run that otherwise succeeded."""
    if toast is None:
        return
    try:
        toast(title, message, app_id="Quant Trading Desk")
    except Exception:
        pass


def load_protection_state() -> dict:
    if not PROTECTION_STATE_FILE.exists():
        return {}
    with open(PROTECTION_STATE_FILE) as f:
        return json.load(f)


def save_protection_state(state: dict):
    with open(PROTECTION_STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def verify_and_repair_protection(client, positions_df, state: dict, dry_run: bool):
    """Alpaca's bracket/OCO exit legs have repeatedly vanished (expired/canceled)
    shortly after entry fill — sometimes both legs, sometimes just the stop while
    the target survives (CVX, 2026-08-17), which a "does this symbol have any open
    order" check misses entirely. So this checks specifically for a live STOP or
    TRAILING_STOP order per held symbol; anything else (no orders, or only a lone
    target) counts as unprotected and gets a fresh protective order resubmitted —
    canceling any orphaned lone leg first so it doesn't collide with the new one.

    Repairs for trailing positions (state[symbol]["trailing"] == True) submit a
    trailing-stop SELL order instead of the legacy fixed OCO pair. qty is always
    taken from the live position, never blindly from remembered state — submitting
    a sell for more shares than are actually held risks opening an unintended
    short, which is exactly what a stale/wrong remembered qty could do."""
    live_qty = {row.symbol: int(float(row.position)) for row in positions_df.itertuples()} if not positions_df.empty else {}

    for symbol in sorted(live_qty.keys()):
        qty = live_qty[symbol]
        if qty <= 0:
            continue

        all_orders = list(client.get_orders(filter=GetOrdersRequest(status=QueryOrderStatus.ALL, symbols=[symbol], limit=20, direction="desc")))
        live_orders = [o for o in all_orders if o.status in LIVE_ORDER_STATUSES]
        has_live_stop = any(o.order_type in (OrderType.STOP, OrderType.TRAILING_STOP) or o.stop_price is not None for o in live_orders)
        if has_live_stop:
            continue

        remembered = state.get(symbol)
        if not remembered:
            log_event("protection_missing_no_state", symbol=symbol,
                       reason="position held with no live stop and no remembered levels to repair with")
            notify("Quant Desk: UNPROTECTED position", f"{symbol} has no stop-loss and no remembered levels to repair with. Needs manual attention.")
            continue

        if dry_run:
            log_event("dry_run_protection_repair", symbol=symbol, qty=qty, **remembered)
            continue

        for orphan in live_orders:
            try:
                client.cancel_order_by_id(orphan.id)
            except Exception:
                pass
        try:
            if remembered.get("trailing"):
                order = broker_alpaca.place_trailing_stop_exit_order(client, symbol, qty, remembered["trail_price"])
                log_event("protection_repaired", symbol=symbol, order_id=str(order.id), qty=qty, trail_price=remembered["trail_price"])
                notify("Quant Desk: protection repaired", f"{symbol} had no live stop — resubmitted trailing stop (${remembered['trail_price']} trail).")
            else:
                order = broker_alpaca.place_oco_exit_order(client, symbol, qty, remembered["stop_price"], remembered["target_price"])
                log_event("protection_repaired", symbol=symbol, order_id=str(order.id), qty=qty,
                          stop_price=remembered["stop_price"], target_price=remembered["target_price"])
                notify("Quant Desk: protection repaired", f"{symbol} had no live stop-loss — resubmitted stop ${remembered['stop_price']} / target ${remembered['target_price']}.")
        except Exception as e:
            log_event("protection_repair_error", symbol=symbol, error=str(e))
            notify("Quant Desk: repair FAILED", f"{symbol} is unprotected and the auto-repair attempt errored: {e}")


def market_is_open_now() -> bool:
    now_et = datetime.now(ZoneInfo("America/New_York"))
    if now_et.weekday() >= 5:
        return False
    return dtime(9, 30) <= now_et.time() <= dtime(16, 0)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="Log intended actions without submitting orders.")
    parser.add_argument("--force", action="store_true", help="Bypass the market-hours guard (testing only).")
    parser.add_argument(
        "--protection-only", action="store_true",
        help="Only audit held positions for missing stop/target protection and repair; skip the signal scan "
             "and the market-must-be-closed guard. Meant to run frequently during market hours as a companion "
             "to the once-daily full run.",
    )
    args = parser.parse_args()

    if KILL_SWITCH_FILE.exists():
        log_event("halted", reason=f"kill switch present ({KILL_SWITCH_FILE.name})")
        return

    if market_is_open_now() and not args.force and not args.protection_only:
        log_event("halted", reason="market is open; this runner expects to run after close on a completed daily bar")
        return

    api_key = broker_alpaca.get_credential_from_env("ALPACA_API_KEY_ID")
    secret_key = broker_alpaca.get_credential_from_env("ALPACA_API_SECRET_KEY")
    if not api_key or not secret_key:
        log_event("halted", reason="ALPACA_API_KEY_ID / ALPACA_API_SECRET_KEY not set in environment")
        notify("Quant Desk: run HALTED", "No Alpaca credentials found in environment/registry. Positions were not audited this run.")
        return

    try:
        client = broker_alpaca.connect(api_key, secret_key)
    except Exception as e:
        log_event("halted", reason=f"could not connect to Alpaca: {e}")
        notify("Quant Desk: run HALTED", f"Could not connect to Alpaca: {e}. Positions were not audited this run.")
        return

    account = client.get_account()
    equity = float(account.equity)
    last_equity = float(account.last_equity) if float(account.last_equity) else equity
    daily_pnl_pct = (equity - last_equity) / last_equity if last_equity else 0.0

    log_event("run_start", dry_run=args.dry_run, protection_only=args.protection_only,
               equity=equity, daily_pnl_pct=round(daily_pnl_pct * 100, 2))

    positions_df = broker_alpaca.get_positions(client)
    held_symbols = set(positions_df["symbol"]) if not positions_df.empty else set()
    orders_df = broker_alpaca.get_open_orders(client)
    pending_symbols = set(orders_df["symbol"]) if not orders_df.empty else set()
    committed_symbols = held_symbols | pending_symbols

    available_slots = MAX_CONCURRENT_POSITIONS - len(committed_symbols)
    log_event("portfolio_state", held=sorted(held_symbols), pending=sorted(pending_symbols), available_slots=available_slots)

    # Runs unconditionally, even if the circuit breaker below trips or this is a
    # protection-only pass — protecting an existing position is never gated on
    # whether new entries are currently allowed.
    protection_state = load_protection_state()
    verify_and_repair_protection(client, positions_df, protection_state, args.dry_run)
    protection_state = {sym: v for sym, v in protection_state.items() if sym in held_symbols}
    save_protection_state(protection_state)

    if args.protection_only:
        log_event("run_end", reason="protection-only run")
        return

    if daily_pnl_pct <= -DAILY_LOSS_BREAKER_PCT:
        log_event("circuit_breaker_tripped", daily_pnl_pct=round(daily_pnl_pct * 100, 2), threshold_pct=-DAILY_LOSS_BREAKER_PCT * 100)
        notify("Quant Desk: circuit breaker tripped", f"Equity down {round(daily_pnl_pct * 100, 2)}% today — new entries skipped for the rest of this run.")
        return

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
        stop_price = float(last["stop_price"])  # initial risk-sizing reference only; the live exit is a trailing stop, not this fixed level
        trail_price = round(strategy.TRAILING_STOP_ATR_MULT * atr, 2)
        risk_per_share = strategy.STOP_ATR_MULT * atr
        if risk_per_share <= 0:
            continue

        risk_dollars = RISK_PER_TRADE_PCT * equity
        qty = int(risk_dollars // risk_per_share)
        max_qty_by_notional = int((MAX_POSITION_PCT_OF_EQUITY * equity) // float(last["close"]))
        qty = min(qty, max_qty_by_notional)
        if qty <= 0:
            log_event("signal_skipped", symbol=symbol, reason="computed quantity was 0 at current equity/risk sizing")
            continue

        if args.dry_run:
            log_event("dry_run_signal", symbol=symbol, qty=qty, stop_price=round(stop_price, 2), trail_price=trail_price)
        else:
            try:
                # Plain market entry, no bracket: a trailing-stop SELL order can't be
                # submitted until shares are actually held. The protection-only check
                # (runs every 30 min during market hours) picks this up once the entry
                # fills and submits the real trailing stop from this remembered state.
                order = broker_alpaca.place_market_entry_order(client, symbol, "BUY", qty)
                log_event("order_submitted", symbol=symbol, qty=qty, trail_price=trail_price, order_id=str(order.id))
                protection_state[symbol] = {"qty": qty, "trailing": True, "trail_price": trail_price}
                save_protection_state(protection_state)
                notify("Quant Desk: new entry", f"{symbol} x{qty} submitted for next open. Trailing stop ${trail_price} once filled.")
            except Exception as e:
                log_event("order_error", symbol=symbol, error=str(e))
                notify("Quant Desk: order FAILED", f"{symbol} entry could not be submitted: {e}")
                continue
        submitted += 1

    log_event("run_end", submitted=submitted)


if __name__ == "__main__":
    main()
