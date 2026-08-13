"""Shared market-data access layer, backed by yfinance (free, no API key)."""

import numpy as np
import pandas as pd
import streamlit as st
import yfinance as yf

# Proxies used for cross-asset / benchmark comparisons
BENCHMARK = "SPY"
MACRO_PROXIES = {
    "10Y Treasury Yield": "^TNX",
    "13-Week T-Bill Yield": "^IRX",
    "Crude Oil (WTI)": "CL=F",
    "US Dollar Index": "DX-Y.NYB",
    "Gold": "GC=F",
    "VIX": "^VIX",
}

SECTOR_ETFS = {
    "Technology": "XLK",
    "Financials": "XLF",
    "Energy": "XLE",
    "Healthcare": "XLV",
    "Industrials": "XLI",
    "Materials": "XLB",
    "Utilities": "XLU",
    "Consumer Discretionary": "XLY",
    "Consumer Staples": "XLP",
    "Real Estate": "XLRE",
    "Communication Services": "XLC",
}


def _flatten(df: pd.DataFrame) -> pd.DataFrame:
    """yfinance returns MultiIndex columns for single tickers in recent versions."""
    if isinstance(df.columns, pd.MultiIndex):
        df = df.copy()
        df.columns = df.columns.get_level_values(0)
    return df


@st.cache_data(ttl=900, show_spinner=False)
def get_history(ticker: str, period: str = "5y", interval: str = "1d") -> pd.DataFrame:
    df = yf.download(ticker, period=period, interval=interval, progress=False, auto_adjust=True)
    df = _flatten(df)
    df = df.dropna(how="all")
    return df


@st.cache_data(ttl=900, show_spinner=False)
def get_info(ticker: str) -> dict:
    try:
        return yf.Ticker(ticker).get_info()
    except Exception:
        return {}


@st.cache_data(ttl=900, show_spinner=False)
def get_option_expirations(ticker: str) -> tuple:
    try:
        return tuple(yf.Ticker(ticker).options)
    except Exception:
        return tuple()


@st.cache_data(ttl=900, show_spinner=False)
def get_option_chain(ticker: str, expiration: str):
    tk = yf.Ticker(ticker)
    chain = tk.option_chain(expiration)
    return chain.calls, chain.puts


def validate_ticker(ticker: str) -> bool:
    if not ticker:
        return False
    df = get_history(ticker, period="5d")
    return df is not None and not df.empty
