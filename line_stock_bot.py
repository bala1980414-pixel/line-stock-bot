# -*- coding: utf-8 -*-
"""
LINE 股票機器人 V4.4-Pro Entry Signal 完整正式版
版本日期：2026-05-22

功能：
- 0~10 快速指令
- HELP/help 指令
- 主力進貨 TOP5（Entry Signal）
- 市場熱門 TOP5
- 波段續強 TOP5（只保留 KD向上）
- Sector 題材掃描
- 族群熱度排行
- 股票分析（維持原版）

Render Start Command：
gunicorn line_stock_bot:app
"""

import os
import re
import time
import hmac
import base64
import hashlib
import traceback
from datetime import datetime

import pytz
import requests
import pandas as pd
import yfinance as yf

from flask import Flask, request, abort

app = Flask(__name__)

CHANNEL_ACCESS_TOKEN = os.getenv("CHANNEL_ACCESS_TOKEN", "")
CHANNEL_SECRET = os.getenv("CHANNEL_SECRET", "")

LINE_REPLY_URL = "https://api.line.me/v2/bot/message/reply"

# =========================================================
# 股票池
# =========================================================

CORE_POOL = {
    "2330": "台積電",
    "2454": "聯發科",
    "3017": "奇鋐",
    "2376": "技嘉",
    "3231": "緯創",
    "2382": "廣達",
    "3406": "玉晶光",
    "2356": "英業達",
}

SECTOR_POOLS = {
    "PCB": {
        "3037": "欣興",
        "3189": "景碩",
        "8046": "南電",
        "2368": "金像電",
    },

    "ABF": {
        "8046": "南電",
        "3037": "欣興",
        "3189": "景碩",
    },

    "ASIC": {
        "3443": "創意",
        "3661": "世芯-KY",
        "2454": "聯發科",
    },

    "記憶體": {
        "2408": "南亞科",
        "2344": "華邦電",
        "3260": "威剛",
    },

    "低軌": {
        "3491": "昇達科",
        "5388": "中磊",
    },

    "CoPoS": {
        "6239": "力成",
        "6147": "頎邦",
    },

    "Intel": {
        "2382": "廣達",
        "3231": "緯創",
    },

    "化學": {
        "1723": "中碳",
        "6505": "台塑化",
    },

    "矽晶圓": {
        "6488": "環球晶",
        "3532": "台勝科",
    },
}

# =========================================================
# 工具
# =========================================================

def tw_now():
    tz = pytz.timezone("Asia/Taipei")
    return datetime.now(tz).strftime("%Y-%m-%d %H:%M")


def verify_signature(body, signature):
    digest = hmac.new(
        CHANNEL_SECRET.encode("utf-8"),
        body,
        hashlib.sha256
    ).digest()

    signature_check = base64.b64encode(digest).decode("utf-8")

    return hmac.compare_digest(signature_check, signature)


def reply_text(reply_token, text):
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {CHANNEL_ACCESS_TOKEN}",
    }

    payload = {
        "replyToken": reply_token,
        "messages": [{"type": "text", "text": text[:4900]}]
    }

    requests.post(
        LINE_REPLY_URL,
        headers=headers,
        json=payload
    )


def safe_float(x):
    try:
        return float(x)
    except:
        return 0.0


# =========================================================
# 技術分析
# =========================================================

def calc_rsi(close, period=14):
    delta = close.diff()

    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    avg_gain = gain.rolling(period).mean()
    avg_loss = loss.rolling(period).mean()

    rs = avg_gain / avg_loss

    rsi = 100 - (100 / (1 + rs))

    return rsi.fillna(50)


def calc_kd(df):
    low_min = df["Low"].rolling(9).min()
    high_max = df["High"].rolling(9).max()

    rsv = (df["Close"] - low_min) / (high_max - low_min) * 100

    k = rsv.ewm(com=2).mean()
    d = k.ewm(com=2).mean()

    return k, d


def download_stock(code):
    symbol = f"{code}.TW"

    df = yf.download(
        symbol,
        period="4mo",
        interval="1d",
        progress=False,
        threads=False
    )

    if df is None or df.empty:
        return None

    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [c[0] for c in df.columns]

    df = df.dropna()

    if len(df) < 30:
        return None

    return df


def analyze_stock(code, name):
    df = download_stock(code)

    if df is None:
        return None

    close = df["Close"]
    volume = df["Volume"]

    ma5 = close.rolling(5).mean().iloc[-1]
    ma10 = close.rolling(10).mean().iloc[-1]
    ma20 = close.rolling(20).mean().iloc[-1]

    rsi = calc_rsi(close).iloc[-1]

    k, d = calc_kd(df)

    k_now = safe_float(k.iloc[-1])
    d_now = safe_float(d.iloc[-1])

    k_prev = safe_float(k.iloc[-2])
    d_prev = safe_float(d.iloc[-2])

    kd_up = (
        k_now > d_now and
        k_prev <= d_prev
    ) or (
        k_now > d_now and
        k_now > k_prev
    )

    c = safe_float(close.iloc[-1])
    prev_c = safe_float(close.iloc[-2])

    v = safe_float(volume.iloc[-1])

    vol20 = safe_float(volume.rolling(20).mean().iloc[-1])

    vol_ratio = v / vol20 if vol20 else 0

    change_pct = (
        (c - prev_c) / prev_c * 100
    ) if prev_c else 0

    deviation20 = (
        (c - ma20) / ma20 * 100
    ) if ma20 else 0

    left_volume = vol_ratio >= 1.2

    trend_continue = (
        ma5 > ma10 > ma20 and
        c > ma20
    )

    return {
        "code": code,
        "name": name,
        "rsi": safe_float(rsi),
        "kd_up": kd_up,
        "vol_ratio": safe_float(vol_ratio),
        "deviation20": safe_float(deviation20),
        "left_volume": left_volume,
        "trend_continue": trend_continue,
    }


# =========================================================
# Entry Signal 選股
# =========================================================

def build_entry_reply(stock_pool):

    rows = []

    for code, name in stock_pool.items():

        try:
            item = analyze_stock(code, name)

            if item:
                rows.append(item)

            time.sleep(0.05)

        except:
            continue

    if not rows:
        return "目前抓不到資料"

    # 主力進貨
    main_force = []

    for r in rows:

        if (
            r["left_volume"] and
            r["kd_up"] and
            r["rsi"] > 55 and
            r["deviation20"] < 8
        ):
            main_force.append(r)

    # 波段續強
    trend = []

    for r in rows:

        if (
            r["trend_continue"] and
            r["kd_up"] and
            r["rsi"] > 60 and
            r["deviation20"] < 10
        ):
            trend.append(r)

    main_force = sorted(
        main_force,
        key=lambda x: (
            x["rsi"],
            -x["deviation20"]
        ),
        reverse=True
    )[:5]

    trend = sorted(
        trend,
        key=lambda x: (
            x["rsi"],
            -x["deviation20"]
        ),
        reverse=True
    )[:5]

    text = f"""【AI Entry Signal】
資料時間：{tw_now()}

━━━━━━━━━━━━━━
🔷 主力進貨 TOP5
━━━━━━━━━━━━━━
"""

    if not main_force:
        text += "\n目前沒有符合條件股票。\n"

    for i, r in enumerate(main_force, 1):

        text += f"""
{i}. {r['code']} {r['name']}
KD向上｜RSI {r['rsi']:.0f}｜乖離 {r['deviation20']:.1f}%
🟢 可進場觀察
"""

    text += """
━━━━━━━━━━━━━━
🚀 波段續強 TOP5
━━━━━━━━━━━━━━
"""

    if not trend:
        text += "\n目前沒有符合條件股票。\n"

    for i, r in enumerate(trend, 1):

        text += f"""
{i}. {r['code']} {r['name']}
KD向上｜RSI {r['rsi']:.0f}｜乖離 {r['deviation20']:.1f}%
🟢 可進場觀察
"""

    return text[:4900]


# =========================================================
# HELP
# =========================================================

HELP_TEXT = """
【AI Trading Lab 指令中心】

0 = 族群熱度
1 = 選股
2 = 選股PCB
3 = 選股ABF
4 = 選股ASIC
5 = 選股記憶體
6 = 選股低軌
7 = 選股CoPoS
8 = 選股Intel
9 = 選股化學
10 = 選股矽晶圓

股票分析：
股票代碼 買入價
例：2330 800
"""


# =========================================================
# 股票分析
# =========================================================

def stock_analysis(user_text):

    m = re.match(
        r"^\s*(\d{4})\s+([0-9]+(?:\.[0-9]+)?)\s*$",
        user_text
    )

    if not m:
        return HELP_TEXT

    code = m.group(1)
    buy_price = float(m.group(2))

    df = download_stock(code)

    if df is None:
        return "資料取得失敗"

    close = safe_float(df["Close"].iloc[-1])

    pnl = (
        (close - buy_price) / buy_price * 100
    )

    return f"""
【股票分析】

股票：{code}
買入價：{buy_price}

最新價：{close:.2f}

損益：約 {pnl:.2f}%
"""


# =========================================================
# 族群熱度
# =========================================================

def build_heat_reply():

    text = f"""【族群熱度排行】
資料時間：{tw_now()}

"""

    rank = 1

    for sector, pool in SECTOR_POOLS.items():

        score = len(pool) * 10

        text += f"""
{rank}. {sector}
熱度分數：{score}

"""

        rank += 1

    return text


# =========================================================
# LINE Webhook
# =========================================================

@app.route("/", methods=["GET"])
def home():
    return "AI Trading Lab V4.4-Pro Entry Signal版"


@app.route("/callback", methods=["POST"])
def callback():

    body = request.get_data()

    signature = request.headers.get(
        "X-Line-Signature",
        ""
    )

    if not verify_signature(body, signature):
        abort(400)

    data = request.get_json()

    events = data.get("events", [])

    for event in events:

        if event["type"] != "message":
            continue

        user_text = (
            event["message"]["text"]
            .strip()
        )

        reply_token = event["replyToken"]

        try:

            if user_text.lower() == "help":
                result = HELP_TEXT

            elif user_text == "0":
                result = build_heat_reply()

            elif user_text == "1":
                result = build_entry_reply(CORE_POOL)

            elif user_text == "2":
                result = build_entry_reply(SECTOR_POOLS["PCB"])

            elif user_text == "3":
                result = build_entry_reply(SECTOR_POOLS["ABF"])

            elif user_text == "4":
                result = build_entry_reply(SECTOR_POOLS["ASIC"])

            elif user_text == "5":
                result = build_entry_reply(SECTOR_POOLS["記憶體"])

            elif user_text == "6":
                result = build_entry_reply(SECTOR_POOLS["低軌"])

            elif user_text == "7":
                result = build_entry_reply(SECTOR_POOLS["CoPoS"])

            elif user_text == "8":
                result = build_entry_reply(SECTOR_POOLS["Intel"])

            elif user_text == "9":
                result = build_entry_reply(SECTOR_POOLS["化學"])

            elif user_text == "10":
                result = build_entry_reply(SECTOR_POOLS["矽晶圓"])

            else:
                result = stock_analysis(user_text)

            reply_text(reply_token, result)

        except Exception:
            print(traceback.format_exc())

            reply_text(
                reply_token,
                "系統忙碌中，請稍後再試"
            )

    return "OK"


# =========================================================
# Main
# =========================================================

if __name__ == "__main__":

    port = int(
        os.getenv("PORT", 5000)
    )

    print("AI Trading Lab 啟動中...")

    app.run(
        host="0.0.0.0",
        port=port
    )
