# Quant Trading Desk

A Streamlit dashboard for systematic equity analysis and paper trading — real market data, a multi-framework signal engine, a validated backtest, and an unattended scheduled runner that trades the same rules live.

**Paper trading only.** Every code path that touches a broker is hardcoded to Alpaca's paper endpoint (`paper=True`) — there is no way to route an order to a live account without deliberately changing the code.

## What's in here

- **Dashboard** (`app.py`) — nine tabs covering trend/signal, momentum ranking, mean reversion, volume & order flow, seasonality, cross-asset correlation, options flow/IV, a synthesized trade plan, and paper execution against Alpaca. Styled as a dark, amber-on-black terminal.
- **Strategy** (`strategy.py`) — the single source of truth for entry/exit logic, shared by both the backtest and the live runner so a backtest result reflects what actually runs live. Trend-continuation-with-volume-confirmation: price above a rising 50/200-day MA structure, a near-breakout on above-average volume, and RSI/Z-score guards against chasing an already-overextended move. Risk is ATR-based — a 2×ATR stop and 4×ATR target (2:1 reward:risk).
- **Backtest** (`strategy_backtest.py`) — runs the strategy over historical data for validation before anything trades live.
- **Live runner** (`strategy_runner.py`) — unattended script intended to run once per weekday after market close, scheduled via Windows Task Scheduler. Scans the S&P 100, sizes positions at 2% risk of equity per trade, caps at 5 concurrent positions, and trips a circuit breaker if the account is down 5%+ on the day. Every scan/signal/submission/skip is logged to `runner_log.jsonl`. A file-based kill switch (`RUNNER_DISABLED`, create an empty file with that name in this directory) halts it immediately.
- **Options flow gate** (`modules/options_flow.py`) — the runner never trades option contracts, only shares. Before submitting an equity signal it checks put/call volume ratio and skew for that ticker and skips the entry if positioning looks bearish. Live-only; the backtest has no historical options data to replay this against.
- **Broker layer** (`broker_alpaca.py`) — all Alpaca interaction (connect, positions, orders, bracket/OCO order placement, trade log). Reads `ALPACA_API_KEY_ID` / `ALPACA_API_SECRET_KEY` straight from the Windows registry rather than trusting a process's own (possibly stale) copy of its environment — a rotated key takes effect immediately everywhere, no restarts needed.

## Setup

```powershell
cd quant_trading_desk
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
```

Set your Alpaca **paper** API credentials as environment variables (get these from your Alpaca paper dashboard, not the live one):

```powershell
setx ALPACA_API_KEY_ID "your_key_id"
setx ALPACA_API_SECRET_KEY "your_secret_key"
```

Open a fresh terminal afterward — `setx` doesn't propagate into already-running processes.

## Running the dashboard

```powershell
.venv\Scripts\streamlit run app.py
```

## Running the backtest

```powershell
.venv\Scripts\python strategy_backtest.py
```

## Running the live runner

```powershell
.venv\Scripts\python strategy_runner.py --dry-run   # logs intended actions, submits nothing
.venv\Scripts\python strategy_runner.py              # live paper submission
.venv\Scripts\python strategy_runner.py --protection-only  # audit/repair held-position stops only
```

### Scheduling: GitHub Actions (recommended)

`.github/workflows/main-run.yml` and `protection-check.yml` replicate the schedule below independent of any local machine's power state — this exists because a laptop that's off, asleep, or (on Modern Standby hardware) just has its lid closed will silently miss or stall a scheduled run. Each workflow gates on the actual US/Eastern clock to handle DST correctly without double-firing.

Setup: add `ALPACA_API_KEY_ID` and `ALPACA_API_SECRET_KEY` as **repository secrets** (Settings → Secrets and variables → Actions → New repository secret) — do this yourself in the GitHub UI, never by pasting keys into a chat or committing them to a file. That's the only setup step; both workflows install `requirements-ci.txt` (excludes `win11toast`/`winrt`, which are Windows-only and won't install on Ubuntu runners — so **desktop toast notifications don't fire for cloud-triggered runs**, only for ones run locally by hand).

`runner_log.jsonl` and `protection_state.json` are committed to the repo (not gitignored) so state persists across runs — Actions runners are ephemeral, nothing survives on local disk between invocations. Each workflow run commits its own updates back automatically.

The kill switch (`RUNNER_DISABLED`) only stops a *local* run — it has no effect on the cloud schedule. To halt cloud runs, disable the workflow itself (Actions tab → select workflow → "..." → Disable workflow).

### Scheduling: Windows Task Scheduler (local, legacy)

Point Windows Task Scheduler at `.venv\Scripts\python.exe strategy_runner.py`, timed for after market close on weekdays, with a second task running `--protection-only` every 30 minutes during market hours (9:35am-4:00pm ET). If the task is set to run whether logged on or not, make sure `DisallowStartIfOnBatteries` is off if this runs on a laptop that isn't always plugged in. Note: on hardware that only supports Modern Standby, there's no lid-close-action setting to configure at all, and closing the lid will throttle/stall a running scan for hours — the GitHub Actions path above avoids this entirely.

Alpaca's bracket/OCO exit legs have been observed to silently expire/cancel shortly after entry fill, leaving a held position with no live stop-loss or take-profit. `strategy_runner.py` self-heals this on every run using `protection_state.json`.

New entries use a trailing stop instead of a fixed take-profit: a plain market entry, then an Alpaca-native `TRAILING_STOP` sell order (trail distance = 3x ATR, set once at entry) submitted once shares are actually held — backtested to meaningfully outperform the old fixed 2:1 target by letting winners run instead of capping them (`strategy_backtest.py v2-trailing` vs `v2`). Positions opened before 2026-08-17 keep their original fixed OCO stop/target; the protection-check logic handles both kinds side by side.

**Rotation (shadow mode only, not yet live):** when the portfolio is full, the runner compares the strongest unheld signal against the weakest-ranked current holding using a composite strength score (`strategy.rotation_score`) — momentum + trend strength + volume surge. Backtested (`strategy_backtest.py v2-trailing-rotate-composite`) at 406% return vs. 147% without rotation over the same 5y window, but with ~86% of trades being rotation-driven and no out-of-sample validation, so it's log-only for now: `rotation_shadow_signal` events show what it *would* swap, and a desktop notification fires, but nothing is ever sold or bought by this check. Meant to run for real days before deciding whether to wire it to execution.

## Disclaimer

For research and education only — not investment advice. Paper trading only; no real money moves through any code path in this repository.
