"""
S&P 100 constituent list (source: Wikipedia, as of 2025-09-22), adjusted for
Yahoo Finance ticker formatting (e.g. BRK.B -> BRK-B).

Index composition drifts over time. This list is a practical, "close enough"
research universe, not a live feed of index membership — a handful of tickers
may be delisted, renamed, or added/removed since it was captured. The backtest
and runner both drop any ticker that fails to return usable price data rather
than assuming this list is authoritative.
"""

SP100 = [
    "AAPL", "ABBV", "ABT", "ACN", "ADBE", "AMAT", "AMD", "AMGN", "AMT", "AMZN",
    "AVGO", "AXP", "BA", "BAC", "BKNG", "BLK", "BMY", "BNY", "BRK-B", "C",
    "CAT", "CL", "CMCSA", "COF", "COP", "COST", "CRM", "CSCO", "CVS", "CVX",
    "DE", "DHR", "DIS", "DUK", "EMR", "FDX", "GD", "GE", "GEV", "GILD",
    "GM", "GOOG", "GOOGL", "GS", "HD", "HON", "IBM", "INTC", "INTU", "ISRG",
    "JNJ", "JPM", "KO", "LIN", "LLY", "LMT", "LOW", "LRCX", "MA", "MCD",
    "MDLZ", "MDT", "META", "MMM", "MO", "MRK", "MS", "MSFT", "MU", "NEE",
    "NFLX", "NKE", "NOW", "NVDA", "ORCL", "PEP", "PFE", "PG", "PLTR", "PM",
    "QCOM", "RTX", "SBUX", "SCHW", "SO", "SPG", "T", "TMO", "TMUS", "TSLA",
    "TXN", "UBER", "UNH", "UNP", "UPS", "USB", "V", "VZ", "WFC", "WMT", "XOM",
]

# Tickers in the list above that are just different share classes of the same
# underlying company (e.g. GOOG/GOOGL) -- left as separate SP100 entries since
# each has its own price/volume history, but they move together and shouldn't
# both be entered as if they were independent, diversified positions.
SAME_COMPANY = {
    "GOOG": "ALPHABET",
    "GOOGL": "ALPHABET",
}


def company_key(symbol: str) -> str:
    """Canonical grouping key for entry-time dedup: same key means same underlying
    company. Symbols with no listed share-class sibling just key off themselves."""
    return SAME_COMPANY.get(symbol, symbol)
