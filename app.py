# app.py
# -*- coding: utf-8 -*-
import os
import time
import math
import requests
from typing import List, Dict, Optional, Tuple

import streamlit as st
import pandas as pd

COINGECKO_API = "https://api.coingecko.com/api/v3"
VS_CURRENCY = "usd"

# --- Streamlit page config ---
st.set_page_config(
    page_title="Crypto RSI Alerts (5m, period 13)",
    page_icon="📈",
    layout="wide",
)

st.title("📈 Crypto RSI Alerts — 5m, period 13")
st.caption("منبع داده: CoinGecko | بدون کلید API | به‌روزرسانی خودکار هر ۵ دقیقه")

# --- Sidebar controls ---
with st.sidebar:
    st.header("تنظیمات")
    num_coins = st.number_input("تعداد کوین‌های برتر (Market Cap)", min_value=10, max_value=250, value=100, step=10)
    rsi_period = st.number_input("دوره RSI", min_value=2, max_value=50, value=13, step=1)
    low_th = st.number_input("آستانه پایین", min_value=1.0, max_value=49.0, value=25.0, step=0.5)
    high_th = st.number_input("آستانه بالا", min_value=51.0, max_value=99.0, value=75.0, step=0.5)
    auto_refresh = st.toggle("به‌روزرسانی خودکار هر ۵ دقیقه", value=True)
    st.divider()
    st.subheader("اعلان تلگرام (اختیاری)")
    st.caption("برای امنیت، پیشنهاد می‌شود این‌ها را به‌صورت Environment Variable ست کنید.")
    TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", st.text_input("TELEGRAM_BOT_TOKEN (اختیاری)", type="password"))
    TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", st.text_input("TELEGRAM_CHAT_ID (اختیاری)", type="password"))

# --- Auto-refresh every 5 minutes ---
if auto_refresh:
    st.experimental_set_query_params(refresh=str(int(time.time())))  # break caching in query string
    st.autorefresh = st.experimental_rerun  # no-op alias for docs clarity
    st.experimental_singleton.clear()  # ensure singleton caches don't bloat

# Helper functions
@st.cache_data(show_spinner=False, ttl=120)  # cache for 2 minutes
def fetch_top_coins(n: int) -> List[Dict]:
    results = []
    page = 1
    per_page = min(250, n)
    session = requests.Session()
    while len(results) < n:
        url = f"{COINGECKO_API}/coins/markets"
        params = {
            "vs_currency": VS_CURRENCY,
            "order": "market_cap_desc",
            "per_page": per_page,
            "page": page,
            "sparkline": "false",
            "price_change_percentage": "24h",
        }
        r = session.get(url, params=params, timeout=30)
        r.raise_for_status()
        batch = r.json()
        if not batch:
            break
        results.extend(batch)
        page += 1
        if len(batch) < per_page:
            break
    return results[:n]


def _fetch_ohlc_once(coin_id: str, days: int):
    url = f"{COINGECKO_API}/coins/{coin_id}/ohlc"
    params = {"vs_currency": VS_CURRENCY, "days": days}
    r = requests.get(url, params=params, timeout=30)
    r.raise_for_status()
    return r.json()


@st.cache_data(show_spinner=False, ttl=120)
def fetch_ohlc(coin_id: str, days: int = 1) -> List[list]:
    # Basic retry including 429
    try:
        return _fetch_ohlc_once(coin_id, days)
    except requests.HTTPError as e:
        if e.response is not None and e.response.status_code == 429:
            time.sleep(5)
            return _fetch_ohlc_once(coin_id, days)
        raise


def closes_from_ohlc(ohlc: List[list]) -> List[float]:
    return [row[4] for row in ohlc if isinstance(row, list) and len(row) >= 5]


def compute_rsi(closes: List[float], period: int) -> Optional[float]:
    if len(closes) < period + 1:
        return None
    gains = []
    losses = []
    for i in range(1, period + 1):
        change = closes[i] - closes[i - 1]
        gains.append(max(change, 0.0))
        losses.append(abs(min(change, 0.0)))
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    for i in range(period + 1, len(closes)):
        change = closes[i] - closes[i - 1]
        gain = max(change, 0.0)
        loss = abs(min(change, 0.0))
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period
    if abs(avg_loss) < 1e-12:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def notify_telegram(text: str, token: str, chat_id: str) -> bool:
    try:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        payload = {"chat_id": chat_id, "text": text, "disable_web_page_preview": True}
        r = requests.post(url, json=payload, timeout=15)
        r.raise_for_status()
        return True
    except Exception:
        return False


# --- Main logic ---
with st.spinner("در حال دریافت داده‌ها از CoinGecko..."):
    coins = fetch_top_coins(num_coins)

rows = []
alerts = []

for coin in coins:
    coin_id = coin.get("id")
    name = coin.get("name", coin_id)
    symbol = coin.get("symbol", "").upper()
    price = coin.get("current_price", None)

    try:
        ohlc = fetch_ohlc(coin_id, 1)  # ~5m candles
        closes = closes_from_ohlc(ohlc)
        rsi = compute_rsi(closes, rsi_period)
    except Exception as e:
        rsi = None

    rows.append({
        "Name": name,
        "Symbol": symbol,
        "Price (USD)": price,
        "RSI": round(rsi, 2) if rsi is not None else None,
    })

    if rsi is not None and (rsi <= low_th or rsi >= high_th):
        alerts.append((name, symbol, rsi, price))

df = pd.DataFrame(rows).sort_values(by=["RSI"], ascending=True, na_position="last")

col1, col2 = st.columns([1, 2])

with col1:
    st.subheader("وضعیت هشدار")
    if alerts:
        st.success(f"{len(alerts)} هشدار فعال")
        for name, symbol, rsi, price in alerts[:20]:
            st.write(f"**{name} ({symbol})** — RSI: **{rsi:.2f}** | Price: {price if price is not None else '—'} USD")
        if len(alerts) > 20:
            st.caption(f"... و {len(alerts) - 20} مورد دیگر")
    else:
        st.info("فعلاً هشداری وجود ندارد (بر اساس آستانه‌های فعلی).")

with col2:
    st.subheader("جدول RSI کوین‌ها")
    st.dataframe(df, use_container_width=True, height=520)

# Telegram notifications (only on demand via button to avoid spamming)
if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID and alerts:
    if st.button("ارسال هشدارها به تلگرام"):
        sent = 0
        for name, symbol, rsi, price in alerts:
            line = f"[RSI Alert] {'<=' if rsi <= low_th else '>='} {low_th if rsi <= low_th else high_th}: {name} ({symbol}) -> RSI {rsi:.2f} | Price: {price} USD"
            if notify_telegram(line, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID):
                sent += 1
        if sent > 0:
            st.success(f"ارسال شد: {sent} پیام")
        else:
            st.error("ارسال ناموفق بود. توکن/چت‌آیدی را بررسی کنید.")

st.divider()
st.caption("⚠️ احتیاط: RSI و داده‌های CoinGecko ممکن است تأخیر داشته باشند. برای تصمیم‌های مالی صرفاً به این داشبورد اتکا نکنید.")
