import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

from data import get_history, get_info, validate_ticker, SECTOR_ETFS
from modules import trend as trend_mod
from modules import momentum as momentum_mod
from modules import mean_reversion as mr_mod
from modules import volume_flow as vol_mod
from modules import seasonality as season_mod
from modules import cross_asset as cross_mod
from modules import options_flow as opt_mod
from modules import trade_plan as plan_mod
import os

import broker_alpaca

st.set_page_config(page_title="Quant Trading Desk", layout="wide", page_icon="📈")

CUSTOM_CSS = """
<style>
:root, .stApp, body { background-color: #000000 !important; }
* { font-family: 'Consolas', 'Courier New', monospace !important; }
[data-testid="stIconMaterial"], [data-testid="stIconMaterial"] * { font-family: 'Material Symbols Rounded' !important; }

.desk-header { display:flex; justify-content:space-between; align-items:baseline;
    border-bottom: 1px solid #FF9900; padding-bottom: 10px; margin-bottom: 6px; }
.ticker-price { font-size: 2.1rem; font-weight: 700; color: #FFB000; text-shadow: 0 0 6px rgba(255,153,0,0.35); }
.ticker-name { color: #FF9900; font-size: 0.9rem; letter-spacing: 0.5px; }
.metric-up { color: #00FF41; font-weight: 700; }
.metric-down { color: #FF3333; font-weight: 700; }
.conviction-STRONG { color: #00FF41; font-weight:700; }
.conviction-MODERATE { color: #FFB000; font-weight:700; }
.conviction-WEAK { color: #FF3333; font-weight:700; }

/* headers */
h1, h2, h3, h4 { color: #FF9900 !important; text-transform: uppercase; letter-spacing: 0.5px; }

/* metrics */
div[data-testid="stMetricValue"] { font-size: 1.3rem; color: #FFB000 !important; }
div[data-testid="stMetricLabel"] { color: #FF9900 !important; text-transform: uppercase; font-size: 0.75rem; letter-spacing: 0.5px; }
div[data-testid="stMetric"] { background-color: #0a0a0a; border: 1px solid #331f00; border-radius: 2px; padding: 8px; }

/* tabs */
button[data-baseweb="tab"] { color: #FF9900 !important; font-weight: 600; }
button[data-baseweb="tab"][aria-selected="true"] { color: #FFB000 !important; border-bottom: 2px solid #FF9900 !important; }

/* sidebar */
section[data-testid="stSidebar"] { background-color: #050505; border-right: 1px solid #331f00; }

/* dataframes / tables */
div[data-testid="stDataFrame"] { border: 1px solid #331f00; }

/* buttons */
.stButton > button { background-color: #0a0a0a; color: #FFB000; border: 1px solid #FF9900; }
.stButton > button:hover { background-color: #FF9900; color: #000000; }

/* inputs */
div[data-baseweb="input"], div[data-baseweb="select"] { background-color: #0a0a0a !important; border: 1px solid #331f00 !important; }

/* dividers */
hr { border-color: #331f00 !important; }
</style>
"""
st.markdown(CUSTOM_CSS, unsafe_allow_html=True)

with st.sidebar:
    st.markdown("## 🏦 Quant Trading Desk")
    st.caption("Institutional multi-framework analysis — real market data via yfinance.")
    ticker = st.text_input("Primary ticker", value="AAPL").strip().upper()
    sector_hint = st.selectbox("Sector (for cross-asset comparisons)", ["Auto / None"] + list(SECTOR_ETFS.keys()))
    st.markdown("---")
    universe_text = st.text_area(
        "Momentum ranking universe (comma-separated)",
        value="AAPL, MSFT, NVDA, AMZN, GOOGL, META, TSLA, AVGO, JPM, XOM",
        height=90,
    )
    st.markdown("---")
    st.caption("Data: Yahoo Finance via yfinance. For research/education only — not investment advice.")

if not ticker:
    st.stop()

if not validate_ticker(ticker):
    st.error(f"Couldn't find market data for '{ticker}'. Check the symbol and try again.")
    st.stop()

info = get_info(ticker)
daily5y = get_history(ticker, period="5y")
prev_close = float(daily5y["Close"].iloc[-2])
last_close = float(daily5y["Close"].iloc[-1])
change = last_close - prev_close
change_pct = change / prev_close * 100
direction_class = "metric-up" if change >= 0 else "metric-down"
arrow = "▲" if change >= 0 else "▼"

st.markdown(
    f"""
    <div class="desk-header">
        <div>
            <span class="ticker-price">{ticker}</span>
            <span class="ticker-name"> {info.get('shortName', '')} · {info.get('sector', 'N/A')}</span>
        </div>
        <div>
            <span class="ticker-price">${last_close:,.2f}</span>
            <span class="{direction_class}"> {arrow} {change:+.2f} ({change_pct:+.2f}%)</span>
        </div>
    </div>
    """,
    unsafe_allow_html=True,
)

tabs = st.tabs([
    "📐 Trend & Signal",
    "🚀 Momentum Ranking",
    "🔄 Mean Reversion",
    "📊 Volume & Order Flow",
    "🔍 Seasonality / Edge",
    "🌐 Cross-Asset",
    "🎯 Options Flow & IV",
    "📋 Trade Plan",
    "🤖 Paper Execution (Alpaca)",
])

# ---------- TAB 1: TREND & SIGNAL ----------
with tabs[0]:
    result = trend_mod.analyze(daily5y)
    if "error" in result:
        st.warning(result["error"])
    else:
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Daily Trend", result["trend_daily"])
        c2.metric("Weekly Trend", result["trend_weekly"])
        c3.metric("Monthly Trend", result["trend_monthly"])
        c4.metric("Alignment", result["alignment"])

        st.caption(f"Structure: {result['structure']}  |  MA Cross: {result['cross_signal']}")

        fig = make_subplots(
            rows=3, cols=1, shared_xaxes=True, vertical_spacing=0.03,
            row_heights=[0.55, 0.2, 0.25],
            specs=[[{"secondary_y": False}], [{"secondary_y": False}], [{"secondary_y": False}]],
        )
        d = result["daily"].tail(260)
        fig.add_trace(go.Candlestick(x=d.index, open=d["Open"], high=d["High"], low=d["Low"], close=d["Close"], name="Price"), row=1, col=1)
        fig.add_trace(go.Scatter(x=d.index, y=result["ma50_series"].tail(260), name="MA50", line=dict(width=1, color="orange")), row=1, col=1)
        if not result["ma200_series"].empty:
            fig.add_trace(go.Scatter(x=d.index, y=result["ma200_series"].tail(260), name="MA200", line=dict(width=1, color="purple")), row=1, col=1)
        fig.add_trace(go.Scatter(x=d.index, y=result["bb_upper_series"].tail(260), name="BB Upper", line=dict(width=1, dash="dot", color="grey")), row=1, col=1)
        fig.add_trace(go.Scatter(x=d.index, y=result["bb_lower_series"].tail(260), name="BB Lower", line=dict(width=1, dash="dot", color="grey")), row=1, col=1)

        fig.add_trace(go.Scatter(x=d.index, y=result["rsi_series"].tail(260), name="RSI", line=dict(color="cyan")), row=2, col=1)
        fig.add_hline(y=70, line_dash="dash", line_color="red", row=2, col=1)
        fig.add_hline(y=30, line_dash="dash", line_color="green", row=2, col=1)

        fig.add_trace(go.Bar(x=d.index, y=result["macd_hist"].tail(260), name="MACD Hist"), row=3, col=1)
        fig.add_trace(go.Scatter(x=d.index, y=result["macd_line"].tail(260), name="MACD", line=dict(color="yellow")), row=3, col=1)
        fig.add_trace(go.Scatter(x=d.index, y=result["signal_line"].tail(260), name="Signal", line=dict(color="magenta")), row=3, col=1)

        fig.update_layout(height=650, template="plotly_dark", xaxis_rangeslider_visible=False, margin=dict(t=10, b=10))
        st.plotly_chart(fig, use_container_width=True)

        colA, colB = st.columns(2)
        with colA:
            st.markdown("**Moving Averages**")
            st.table(pd.DataFrame({
                "MA": ["MA50", "MA100", "MA200"],
                "Level": [result["ma50"], result["ma100"], result["ma200"]],
            }).round(2))
            st.markdown("**Indicator Readings**")
            st.table(pd.DataFrame({
                "Indicator": ["RSI(14)", "MACD signal", "Bollinger position"],
                "Reading": [f"{result['rsi']:.1f} ({result['rsi_read']})", result["macd_signal"], f"{result['bb_position_pct']:.0f}% of band"],
            }))
        with colB:
            st.markdown("**Support Levels**")
            st.table(pd.DataFrame([
                {"Price": round(s["price"], 2), "Tests": s["tests"], "Last tested": s["last_date"].strftime("%Y-%m-%d")}
                for s in result["support"]
            ]) if result["support"] else pd.DataFrame({"info": ["none detected"]}))
            st.markdown("**Resistance Levels**")
            st.table(pd.DataFrame([
                {"Price": round(r["price"], 2), "Tests": r["tests"], "Last tested": r["last_date"].strftime("%Y-%m-%d")}
                for r in result["resistance"]
            ]) if result["resistance"] else pd.DataFrame({"info": ["none detected"]}))

        st.markdown("### Trade Plan Summary")
        rr_flag = " ⚠️ BELOW 2:1 THRESHOLD" if result["rr"] < 2 else ""
        st.markdown(
            f"Entry: **\\${result['entry_zone'][0]:.2f}&ndash;\\${result['entry_zone'][1]:.2f}**  |  "
            f"Stop: **\\${result['stop']:.2f}**  |  T1: **\\${result['target1']:.2f}**  |  T2: **\\${result['target2']:.2f}**  |  "
            f"R:R **1:{result['rr']:.2f}**{rr_flag}  |  "
            f"Conviction: <span class='conviction-{result['conviction']}'>{result['conviction']}</span>",
            unsafe_allow_html=True,
        )

# ---------- TAB 2: MOMENTUM RANKING ----------
with tabs[1]:
    st.markdown("### Systematic Momentum Ranking")
    st.caption("12-1 month return (Jegadeesh & Titman), volatility-adjusted, ranked vs benchmark (SPY).")
    universe = [t for t in universe_text.split(",") if t.strip()]
    if st.button("Run momentum ranking", type="primary"):
        with st.spinner("Pulling universe data and computing momentum scores..."):
            rank_df = momentum_mod.rank_universe(universe)
        st.dataframe(rank_df, use_container_width=True, hide_index=True)
        if "crash_risk" in rank_df.columns:
            high_risk = rank_df[rank_df["crash_risk"] == "HIGH"]
            if not high_risk.empty:
                st.warning(f"Momentum crash risk flagged for: {', '.join(high_risk['ticker'])}")
    else:
        st.info("Click 'Run momentum ranking' to score the universe above.")

# ---------- TAB 3: MEAN REVERSION ----------
with tabs[2]:
    mr = mr_mod.analyze(daily5y)
    if "error" in mr:
        st.warning(mr["error"])
    else:
        c1, c2, c3 = st.columns(3)
        c1.metric("Z-score vs 5yr mean", f"{mr['z_score']:.2f}σ")
        c2.metric("RSI(14)", f"{mr['rsi']:.1f}")
        c3.metric("Signal", mr["signal"])

        fig = go.Figure()
        fig.add_trace(go.Scatter(x=mr["z_series"].index, y=mr["z_series"], name="Z-score (252d)"))
        fig.add_hline(y=2, line_dash="dash", line_color="red")
        fig.add_hline(y=-2, line_dash="dash", line_color="green")
        fig.update_layout(height=300, template="plotly_dark", margin=dict(t=10, b=10), title="Rolling Z-score vs 1yr mean")
        st.plotly_chart(fig, use_container_width=True)

        colA, colB = st.columns(2)
        with colA:
            st.markdown("**Historical forward return after RSI extremes (10 trading days)**")
            st.table(pd.DataFrame({
                "Condition": ["RSI < 30 (oversold)", "RSI > 70 (overbought)"],
                "Avg fwd 10d return": [
                    f"{mr['oversold_fwd_ret_mean_%']:.2f}%" if mr['oversold_fwd_ret_mean_%'] is not None else "n/a",
                    f"{mr['overbought_fwd_ret_mean_%']:.2f}%" if mr['overbought_fwd_ret_mean_%'] is not None else "n/a",
                ],
                "Sample n": [mr["oversold_fwd_ret_n"], mr["overbought_fwd_ret_n"]],
            }))
        with colB:
            st.markdown("**Bollinger Band extremes**")
            st.table(pd.DataFrame({
                "Metric": ["% time below lower band (5yr)", "% time above upper band (5yr)", "Avg days to revert from |Z|>2 to <0.5", "Extreme episodes (1yr Z-score)"],
                "Value": [
                    f"{mr['pct_time_below_lower_band_%']:.2f}%",
                    f"{mr['pct_time_above_upper_band_%']:.2f}%",
                    f"{mr['avg_reversion_days']:.1f}" if mr["avg_reversion_days"] else "n/a",
                    str(mr["n_extreme_episodes"]),
                ],
            }))
        st.caption("Risk: mean reversion assumes no fundamental deterioration. A sustained trend break (fundamentals-driven) can invalidate reversion signals — cross-check against the Trend & Signal tab.")

# ---------- TAB 4: VOLUME & ORDER FLOW ----------
with tabs[3]:
    vf = vol_mod.analyze(daily5y)
    if "error" in vf:
        st.warning(vf["error"])
    else:
        c1, c2, c3 = st.columns(3)
        c1.metric("Rel. Volume vs 20d avg", f"{vf['rel_vol_20d_pct']:.0f}%")
        c2.metric("Rel. Volume vs 50d avg", f"{vf['rel_vol_50d_pct']:.0f}%")
        c3.metric("Rel. Volume vs 200d avg", f"{vf['rel_vol_200d_pct']:.0f}%")

        st.markdown(f"**Price-volume relationship (20d):** {vf['pv_relationship']}")
        st.markdown(f"**OBV divergence check:** {vf['divergence']}")
        st.markdown(f"**Institutional signature:** {vf['institutional_signature']}")
        badges = []
        if vf["breakout_today"]:
            badges.append("Breakout confirmed by volume ✅" if vf["breakout_confirmed"] else "Breakout NOT confirmed by volume ⚠️")
        if vf["volume_dry_up"]:
            badges.append("Volume dry-up during consolidation (potential spring) 🌀")
        if badges:
            st.info(" | ".join(badges))

        fig = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.05, row_heights=[0.5, 0.5])
        c = vf["close"].tail(260)
        fig.add_trace(go.Scatter(x=c.index, y=c, name="Close"), row=1, col=1)
        fig.add_trace(go.Scatter(x=vf["obv_series"].tail(260).index, y=vf["obv_series"].tail(260), name="OBV"), row=2, col=1)
        fig.update_layout(height=450, template="plotly_dark", margin=dict(t=10, b=10))
        st.plotly_chart(fig, use_container_width=True)

        st.markdown("**Top unusual volume days (last 6 months)**")
        st.dataframe(pd.DataFrame(vf["unusual_days"]), use_container_width=True, hide_index=True)

# ---------- TAB 5: SEASONALITY / EDGE DETECTION ----------
with tabs[4]:
    st.markdown("### Statistical Pattern Detection")
    st.caption("Every pattern is tested with a one-sample t-test against zero return; p<0.05 flagged significant. Statistical significance alone is NOT sufficient — check the economic rationale column.")
    monthly_tbl = season_mod.monthly_seasonality(daily5y["Close"])
    dow_tbl = season_mod.day_of_week_seasonality(daily5y["Close"])
    earnings = season_mod.earnings_drift(daily5y, info)

    st.markdown("**Monthly seasonality**")
    st.dataframe(monthly_tbl, use_container_width=True, hide_index=True)
    st.markdown("**Day-of-week effect**")
    st.dataframe(dow_tbl, use_container_width=True, hide_index=True)
    st.markdown("**Earnings-window drift (volume/price-shock proxy)**")
    st.json(earnings)

# ---------- TAB 6: CROSS-ASSET ----------
with tabs[5]:
    st.markdown("### Cross-Asset & Inter-Market Signals")
    hint = None if sector_hint == "Auto / None" else sector_hint
    if hint is None and info.get("sector") in SECTOR_ETFS:
        hint = info.get("sector")
    with st.spinner("Pulling macro proxy data..."):
        ca = cross_mod.analyze(ticker, sector_hint=hint)
    if "error" in ca:
        st.warning(ca["error"])
    else:
        st.dataframe(ca["table"], use_container_width=True, hide_index=True)
        diverging = ca["table"][ca["table"]["current_signal"].str.contains("Diverging", na=False)]
        if not diverging.empty:
            st.warning(f"Divergence flagged vs: {', '.join(diverging['relationship'])}")

# ---------- TAB 7: OPTIONS FLOW & IV ----------
with tabs[6]:
    st.markdown("### Options Flow & Implied Volatility")
    ofr_expirations = None
    try:
        from data import get_option_expirations
        ofr_expirations = get_option_expirations(ticker)
    except Exception:
        pass

    if not ofr_expirations:
        st.warning(f"No listed options chain found for {ticker}.")
    else:
        exp_choice = st.selectbox("Expiration", ofr_expirations)
        with st.spinner("Pulling options chain..."):
            opt = opt_mod.analyze(ticker, exp_choice)
        if "error" in opt:
            st.warning(opt["error"])
        else:
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Put/Call Vol Ratio", f"{opt['pc_ratio_volume']:.2f}" if opt['pc_ratio_volume'] == opt['pc_ratio_volume'] else "n/a")
            c2.metric("Put/Call OI Ratio", f"{opt['pc_ratio_oi']:.2f}" if opt['pc_ratio_oi'] == opt['pc_ratio_oi'] else "n/a")
            c3.metric("ATM IV", f"{opt['atm_iv_%']:.1f}%")
            c4.metric("Implied move to exp.", f"±{opt['implied_move_%']:.1f}%" if opt['implied_move_%'] else "n/a")

            st.markdown("**IV vs realized (historical) volatility**")
            st.table(pd.DataFrame({
                "Window": ["ATM Implied Vol", "30d Realized", "60d Realized", "90d Realized"],
                "Value": [f"{opt['atm_iv_%']:.1f}%", f"{opt['hv30_%']:.1f}%", f"{opt['hv60_%']:.1f}%", f"{opt['hv90_%']:.1f}%"],
            }))
            st.caption(opt["iv_vs_hv"])

            st.markdown(f"**Skew:** {opt['skew_desc']}" + (f" ({opt['skew_%']:.1f} vol pts, 10%-OTM put minus 10%-OTM call)" if opt['skew_%'] is not None else ""))

            st.markdown("**Unusual activity (volume > 100 and volume/OI > 0.5)**")
            st.dataframe(pd.DataFrame(opt["unusual_activity"]), use_container_width=True, hide_index=True)

            st.markdown("**Potential gamma-pin strikes (highest total open interest)**")
            st.dataframe(pd.DataFrame(opt["gamma_pins"]), use_container_width=True, hide_index=True)

            st.info(f"**Signal synthesis:** {opt['synthesis']}")

# ---------- TAB 8: TRADE PLAN ----------
with tabs[7]:
    trend_result = trend_mod.analyze(daily5y)
    mr_result = mr_mod.analyze(daily5y)
    vol_result = vol_mod.analyze(daily5y)
    plan = plan_mod.build(trend_result, mr_result, vol_result, ticker)
    if "error" in plan:
        st.warning(plan["error"])
    else:
        st.markdown(f"### Trade Plan | {ticker}")
        st.markdown(f"**Thesis:** {plan['thesis']}")

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Entry Zone", f"\\${plan['entry_zone'][0]:.2f}\u2013\\${plan['entry_zone'][1]:.2f}")
        c2.metric("Stop-Loss", f"\\${plan['stop']:.2f}")
        c3.metric("Target 1", f"\\${plan['target1']:.2f}")
        c4.metric("Target 2", f"\\${plan['target2']:.2f}")

        rr_text = f"1:{plan['rr']:.2f}"
        if plan["rr_flag"]:
            st.error(f"R:R = {rr_text} — BELOW the 2:1 minimum threshold. Flagged per institutional risk policy.")
        else:
            st.success(f"R:R = {rr_text} — meets the 2:1 minimum threshold.")

        st.markdown(f"**Position sizing:** {plan['position_size']}  |  **Conviction:** "
                    f"<span class='conviction-{plan['conviction']}'>{plan['conviction']}</span>", unsafe_allow_html=True)

        st.markdown("**Monitoring KPIs**")
        st.dataframe(pd.DataFrame(plan["monitoring_kpis"]), use_container_width=True, hide_index=True)

        st.markdown("**Pre-committed scenario responses**")
        st.dataframe(pd.DataFrame(plan["scenarios"]), use_container_width=True, hide_index=True)

        st.caption("This is a systematic, data-derived plan for research/education purposes — not investment advice. Verify independently before risking capital.")

# ---------- TAB 9: PAPER EXECUTION (ALPACA) ----------
with tabs[8]:
    st.markdown("### 🤖 Paper Trading Execution — Alpaca")
    st.warning(
        "PAPER TRADING ONLY. This tab hardcodes paper=True when connecting to Alpaca — there is no "
        "code path here that reaches the live-trading endpoint. No real money moves through this tab."
    )

    if "alpaca_client" not in st.session_state:
        st.session_state.alpaca_client = None
        st.session_state.alpaca_auto_connect_tried = False

    if not st.session_state.alpaca_client and not st.session_state.alpaca_auto_connect_tried:
        st.session_state.alpaca_auto_connect_tried = True
        env_key = broker_alpaca.get_credential_from_env("ALPACA_API_KEY_ID")
        env_secret = broker_alpaca.get_credential_from_env("ALPACA_API_SECRET_KEY")
        if env_key and env_secret:
            try:
                st.session_state.alpaca_client = broker_alpaca.connect(env_key, env_secret)
            except Exception as e:
                st.session_state.alpaca_auto_connect_error = str(e)

    connected = st.session_state.alpaca_client is not None

    if not connected and st.session_state.get("alpaca_auto_connect_error"):
        st.warning(
            f"Found ALPACA_API_KEY_ID / ALPACA_API_SECRET_KEY in the environment, but Alpaca "
            f"rejected them: {st.session_state.alpaca_auto_connect_error}. The key may have been "
            f"regenerated in the Alpaca dashboard — generate a fresh pair and reset the env vars, "
            f"or paste a valid pair below."
        )

    with st.expander("First time here? Alpaca paper trading setup", expanded=not connected):
        st.markdown(
            """
1. Sign up / log in at [alpaca.markets](https://alpaca.markets) — a paper account is created
   automatically with every account, no funding required.
2. Open the **Paper Trading** dashboard and go to **API Keys**.
3. Generate a paper **API Key ID** and **Secret Key**.
4. Paste both below and click **Connect**. Keys are kept only in this session's memory — never
   written to disk.
            """
        )

    colc1, colc2, colc3 = st.columns([2, 2, 1])
    api_key_input = colc1.text_input("API Key ID", type="password")
    secret_key_input = colc2.text_input("Secret Key", type="password")

    with colc3:
        st.write("")
        st.write("")
        if not connected:
            if st.button("Connect", type="primary"):
                try:
                    client = broker_alpaca.connect(api_key_input, secret_key_input)
                    st.session_state.alpaca_client = client
                    st.rerun()
                except Exception as e:
                    st.error(f"Could not connect: {e}. Double-check the API key ID and secret from your Alpaca paper dashboard.")
        else:
            if st.button("Disconnect"):
                st.session_state.alpaca_client = None
                st.rerun()

    if connected:
        client = st.session_state.alpaca_client
        st.success("Connected to Alpaca paper account.")

        summary = broker_alpaca.get_account_summary(client)
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Net Liquidation (Equity)", summary.get("NetLiquidation", "n/a"))
        m2.metric("Buying Power", summary.get("BuyingPower", "n/a"))
        m3.metric("Cash", summary.get("Cash", "n/a"))
        m4.metric("Pattern Day Trader", summary.get("PatternDayTrader", "n/a"))

        colp, colo = st.columns(2)
        with colp:
            st.markdown("**Positions (paper account)**")
            st.dataframe(broker_alpaca.get_positions(client), use_container_width=True, hide_index=True)
        with colo:
            st.markdown("**Open orders (paper account)**")
            st.dataframe(broker_alpaca.get_open_orders(client), use_container_width=True, hide_index=True)

        if st.button("Cancel all open orders"):
            broker_alpaca.cancel_all_open_orders(client)
            st.rerun()

        st.markdown("---")
        st.markdown("### Trade Log")
        st.caption(
            "Every order on this paper account, most recent first — submitted manually from this "
            "dashboard or automatically by the unattended runner (strategy_runner.py). Refresh the "
            "page to pull the latest."
        )
        trade_log = broker_alpaca.get_recent_orders(client, limit=100)
        if trade_log.empty:
            st.info("No orders yet.")
        else:
            n_filled = (trade_log["status"] == "filled").sum()
            n_open = trade_log["status"].isin(["new", "accepted", "pending_new", "held"]).sum()
            l1, l2, l3 = st.columns(3)
            l1.metric("Total orders", len(trade_log))
            l2.metric("Filled", int(n_filled))
            l3.metric("Open / pending", int(n_open))
            st.dataframe(trade_log, use_container_width=True, hide_index=True)

        st.markdown("---")
        st.markdown("### Send Trade Plan to Paper Account")

        have_plan = "plan" in dir() and isinstance(plan, dict) and "error" not in plan
        if not have_plan:
            st.info("Open the 📋 Trade Plan tab first so there's a plan to send.")
        else:
            st.caption(f"Pre-filled from the Trade Plan tab for {ticker}. Review and adjust before sending — nothing is submitted automatically.")
            t1, t2, t3 = st.columns(3)
            order_action = t1.selectbox("Action", ["BUY", "SELL"], index=0)
            order_qty = t2.number_input("Quantity (shares)", min_value=1, value=10, step=1)
            t3.metric("Conviction", plan["conviction"])

            e1, e2, e3 = st.columns(3)
            entry_input = e1.number_input("Entry (limit price)", value=round(float(plan["entry_zone"][1]), 2), step=0.01, format="%.2f")
            stop_input = e2.number_input("Stop-loss price", value=round(float(plan["stop"]), 2), step=0.01, format="%.2f")
            target_input = e3.number_input("Take-profit price", value=round(float(plan["target1"]), 2), step=0.01, format="%.2f")

            est_risk = abs(entry_input - stop_input) * order_qty
            est_reward = abs(target_input - entry_input) * order_qty
            st.caption(f"Estimated risk: \\${est_risk:,.2f}  |  Estimated reward to T1: \\${est_reward:,.2f}  |  R:R 1:{(est_reward / est_risk):.2f}" if est_risk else "")

            confirm = st.checkbox(f"I confirm this is a PAPER order for {ticker} — {order_action} {order_qty} shares, entry \\${entry_input:.2f}, stop \\${stop_input:.2f}, target \\${target_input:.2f}.")
            if st.button("Submit paper bracket order", type="primary", disabled=not confirm):
                try:
                    order = broker_alpaca.place_bracket_order(
                        client, ticker, order_action, int(order_qty), entry_input, stop_input, target_input
                    )
                    st.success(f"Submitted bracket order (entry + stop + target) to the paper account. Order ID: {order.id}")
                    st.rerun()
                except Exception as e:
                    st.error(f"Order submission failed: {e}")
    else:
        st.info("Not connected. Paste your Alpaca paper API keys above, then click Connect.")
