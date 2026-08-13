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
```

For unattended scheduling, point Windows Task Scheduler at `.venv\Scripts\python.exe strategy_runner.py`, timed for after market close on weekdays. If the task is set to run whether logged on or not, make sure `DisallowStartIfOnBatteries` is off if this runs on a laptop that isn't always plugged in — Task Scheduler silently skips the run otherwise.

## Disclaimer

For research and education only — not investment advice. Paper trading only; no real money moves through any code path in this repository.
