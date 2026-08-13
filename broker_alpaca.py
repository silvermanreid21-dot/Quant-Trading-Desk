"""
Alpaca execution layer (paper trading), via the official alpaca-py SDK.

Safety model:
- The trading client is always constructed with paper=True. There is no code path in
  this module that can point at Alpaca's live-trading endpoint — going live would mean
  deliberately changing this file, not flipping a UI toggle.
"""

import os
import sys

import pandas as pd
from alpaca.trading.client import TradingClient
from alpaca.trading.enums import OrderClass, OrderSide, QueryOrderStatus, TimeInForce
from alpaca.trading.requests import (
    GetOrdersRequest,
    LimitOrderRequest,
    MarketOrderRequest,
    StopLossRequest,
    TakeProfitRequest,
)


def get_credential_from_env(name: str) -> str | None:
    """Reads a credential, preferring the live Windows registry value over the
    process's own os.environ. `setx` writes ALPACA_API_KEY_ID / _SECRET_KEY to
    HKCU\\Environment immediately, but that never propagates into already-running
    processes — and this dashboard, plus the shells that manage it, are long-lived.
    Reading straight from the registry means a rotated key works immediately,
    everywhere, without hunting down every stale process and restarting it.

    Checks the per-user location first (HKCU, where `setx NAME value` writes), then
    the machine-wide location (HKLM, where `setx NAME value /M` writes) — the latter
    is what the SYSTEM account sees, since SYSTEM has no HKCU of its own that maps
    to a real user's env vars. Needed because the scheduled runner now runs as
    SYSTEM to avoid the "Interactive only" / stored-password problems."""
    if sys.platform == "win32":
        import winreg

        for hive, subkey in (
            (winreg.HKEY_CURRENT_USER, "Environment"),
            (winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"),
        ):
            try:
                with winreg.OpenKey(hive, subkey) as key:
                    value, _ = winreg.QueryValueEx(key, name)
                    if value:
                        return value
            except OSError:
                continue
    return os.environ.get(name)


def connect(api_key: str, secret_key: str) -> TradingClient:
    if not api_key or not secret_key:
        raise ValueError("API key and secret key are both required.")
    client = TradingClient(api_key, secret_key, paper=True)
    client.get_account()  # raises if credentials are invalid
    return client


def get_account_summary(client: TradingClient) -> dict:
    a = client.get_account()
    return {
        "NetLiquidation": f"${float(a.equity):,.2f}",
        "BuyingPower": f"${float(a.buying_power):,.2f}",
        "Cash": f"${float(a.cash):,.2f}",
        "PatternDayTrader": str(a.pattern_day_trader),
    }


def get_positions(client: TradingClient) -> pd.DataFrame:
    positions = client.get_all_positions()
    rows = [
        {
            "symbol": p.symbol,
            "position": float(p.qty),
            "avg_cost": round(float(p.avg_entry_price), 2),
            "unrealized_pl": round(float(p.unrealized_pl), 2),
        }
        for p in positions
    ]
    return pd.DataFrame(rows)


def get_open_orders(client: TradingClient) -> pd.DataFrame:
    orders = client.get_orders(filter=GetOrdersRequest(status=QueryOrderStatus.OPEN))
    rows = [
        {
            "symbol": o.symbol,
            "side": o.side.value,
            "order_type": o.order_type.value,
            "quantity": o.qty,
            "limit_price": o.limit_price,
            "status": o.status.value,
        }
        for o in orders
    ]
    return pd.DataFrame(rows)


def place_bracket_order(
    client: TradingClient,
    symbol: str,
    action: str,
    quantity: int,
    entry_price: float,
    stop_loss_price: float,
    take_profit_price: float,
):
    """Places a limit entry with attached take-profit and stop-loss legs (Alpaca bracket order)."""
    if action not in ("BUY", "SELL"):
        raise ValueError("action must be 'BUY' or 'SELL'")
    if quantity <= 0:
        raise ValueError("quantity must be positive")

    order_req = LimitOrderRequest(
        symbol=symbol.upper(),
        qty=quantity,
        side=OrderSide.BUY if action == "BUY" else OrderSide.SELL,
        time_in_force=TimeInForce.GTC,
        limit_price=round(entry_price, 2),
        order_class=OrderClass.BRACKET,
        take_profit=TakeProfitRequest(limit_price=round(take_profit_price, 2)),
        stop_loss=StopLossRequest(stop_price=round(stop_loss_price, 2)),
    )
    return client.submit_order(order_req)


def place_bracket_market_order(
    client: TradingClient,
    symbol: str,
    action: str,
    quantity: int,
    stop_loss_price: float,
    take_profit_price: float,
):
    """Market entry + attached stop-loss/take-profit legs. Used by the systematic
    runner, whose backtest assumes fills at the next session's open — a market
    order at/near open approximates that, where a limit order might miss the fill
    entirely on a gap."""
    if action not in ("BUY", "SELL"):
        raise ValueError("action must be 'BUY' or 'SELL'")
    if quantity <= 0:
        raise ValueError("quantity must be positive")

    order_req = MarketOrderRequest(
        symbol=symbol.upper(),
        qty=quantity,
        side=OrderSide.BUY if action == "BUY" else OrderSide.SELL,
        time_in_force=TimeInForce.DAY,
        order_class=OrderClass.BRACKET,
        take_profit=TakeProfitRequest(limit_price=round(take_profit_price, 2)),
        stop_loss=StopLossRequest(stop_price=round(stop_loss_price, 2)),
    )
    return client.submit_order(order_req)


def place_oco_exit_order(
    client: TradingClient,
    symbol: str,
    quantity: int,
    stop_loss_price: float,
    take_profit_price: float,
):
    """Attaches a stop-loss + take-profit pair (OCO — one leg filling cancels the
    other) to a position that's already held, with no new entry leg. Used to
    re-protect a position whose original bracket legs were lost (expired/canceled)
    after the entry itself already filled."""
    if quantity <= 0:
        raise ValueError("quantity must be positive")

    order_req = LimitOrderRequest(
        symbol=symbol.upper(),
        qty=quantity,
        side=OrderSide.SELL,
        time_in_force=TimeInForce.GTC,
        order_class=OrderClass.OCO,
        take_profit=TakeProfitRequest(limit_price=round(take_profit_price, 2)),
        stop_loss=StopLossRequest(stop_price=round(stop_loss_price, 2)),
    )
    return client.submit_order(order_req)


def get_recent_orders(client: TradingClient, limit: int = 50) -> pd.DataFrame:
    """All orders (any status), most recent first — the trade log. Covers orders
    submitted from the dashboard and from the unattended runner alike, since both
    hit the same paper account."""
    orders = client.get_orders(
        filter=GetOrdersRequest(status=QueryOrderStatus.ALL, limit=limit, direction="desc")
    )
    rows = [
        {
            "submitted_at": o.submitted_at,
            "symbol": o.symbol,
            "side": o.side.value,
            "order_class": o.order_class.value,
            "qty": o.qty,
            "order_type": o.order_type.value,
            "limit_price": o.limit_price,
            "stop_price": o.stop_price,
            "status": o.status.value,
            "filled_avg_price": o.filled_avg_price,
            "filled_at": o.filled_at,
        }
        for o in orders
    ]
    df = pd.DataFrame(rows)
    if not df.empty:
        for col in ["qty", "limit_price", "stop_price", "filled_avg_price"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        for col in ["submitted_at", "filled_at"]:
            df[col] = pd.to_datetime(df[col])
    return df


def cancel_all_open_orders(client: TradingClient):
    client.cancel_orders()
