# -*- coding: utf-8 -*-
"""
LINE 股票機器人 V4.4 主力進貨版
用途：部署在 Render，LINE 輸入「選股」回傳：主力進貨TOP5、市場熱門TOP5、波段續強TOP5。
也支援：輸入「股票代碼 買進價」或「2330 800」回傳停損/停利。

Render Start Command：gunicorn line_stock_bot:app
Environment Variables：
- CHANNEL_ACCESS_TOKEN
- CHANNEL_SECRET
"""

import os
import re
import time
import hmac
import base64
import hashlib
import traceback
from datetime import datetime

import requests
import pandas as pd
import yfinance as yf
from flask import Flask, request, abort

app = Flask(__name__)

CHANNEL_ACCESS_TOKEN = os.getenv("CHANNEL_ACCESS_TOKEN", "")
CHANNEL_SECRET = os.getenv("CHANNEL_SECRET", "")
LINE_REPLY_URL = "https://api.line.me/v2/bot/message/reply"

# ============================================================
# 股票池：台股電子 + 重電核心名單
# 之後若要加股票，直接在這裡增加即可
# ============================================================
STOCK_POOL = {
    # 半導體 / IC / AI
    "2330": "台積電", "2303": "聯電", "2454": "聯發科", "3034": "聯詠",
    "2379": "瑞昱", "3443": "創意", "3661": "世芯-KY", "3529": "力旺",
    "5274": "信驊", "6488": "環球晶", "4966": "譜瑞-KY", "2408": "南亞科",
    "2344": "華邦電", "2388": "威盛", "3260": "威剛", "8299": "群聯",

    # AI 伺服器 / 電腦週邊
    "2317": "鴻海", "2382": "廣達", "3231": "緯創", "6669": "緯穎",
    "2356": "英業達", "2357": "華碩", "2376": "技嘉", "2324": "仁寶",
    "4938": "和碩", "3017": "奇鋐", "3324": "雙鴻", "6230": "尼得科超眾",
    "3653": "健策", "3533": "嘉澤", "3413": "京鼎", "6187": "萬潤",

    # 光學 / PCB / 零組件
    "3008": "大立光", "3406": "玉晶光", "3481": "群創", "2409": "友達",
    "8046": "南電", "3037": "欣興", "3189": "景碩", "2368": "金像電",
    "4958": "臻鼎-KY", "6274": "台燿", "6213": "聯茂", "2383": "台光電",

    # 電子通路 / 其他電子
    "2347": "聯強", "3702": "大聯大", "2353": "宏碁", "2301": "光寶科",
    "2395": "研華", "6415": "矽力-KY", "6409": "旭隼", "2474": "可成",

    # 重電 / 電力設備
    "1513": "中興電", "1504": "東元", "1609": "大亞", "1519": "華城",
    "1611": "中電", "1618": "合機", "6806": "森崴能源", "6873": "泓德能源",
    "4588": "玖鼎電力", "6282": "康舒", "1514": "亞力", "1605": "華新",
}

# ============================================================
# LINE 基本功能
# ============================================================
def verify_signature(body: bytes, signature: str) -> bool:
    if not CHANNEL_SECRET:
        return False
    digest = hmac.new(CHANNEL_SECRET.encode("utf-8"), body, hashlib.sha256).digest()
    calculated = base64.b64encode(digest).decode("utf-8")
    return hmac.compare_digest(calculated, signature)


def reply_text(reply_token: str, text: str):
    if not CHANNEL_ACCESS_TOKEN:
        print("CHANNEL_ACCESS_TOKEN 未設定")
        return
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {CHANNEL_ACCESS_TOKEN}",
    }
    payload = {
        "replyToken": reply_token,
        "messages": [{"type": "text", "text": text[:4900]}],
    }
    try:
        requests.post(LINE_REPLY_URL, headers=headers, json=payload, timeout=10)
    except Exception:
        print(traceback.format_exc())


@app.route("/", methods=["GET"])
def home():
    return "LINE 股票機器人 V4.4 主力進貨版 is running."


@app.route("/callback", methods=["POST"])
def callback():
    body = request.get_data()
    signature = request.headers.get("X-Line-Signature", "")

    if CHANNEL_SECRET and not verify_signature(body, signature):
        abort(400)

    data = request.get_json(silent=True) or {}
    events = data.get("events", [])

    for event in events:
        if event.get("type") != "message":
            continue
        message = event.get("message", {})
        if message.get("type") != "text":
            continue

        user_text = (message.get("text") or "").strip()
        reply_token = event.get("replyToken")

        try:
            if user_text == "選股":
                result = build_selection_reply()
            else:
                result = handle_price_query(user_text)
            reply_text(reply_token, result)
        except Exception:
            print(traceback.format_exc())
            reply_text(reply_token, "系統暫時忙碌或資料源異常，請稍後再試。")

    return "OK"

# ============================================================
# 技術指標
# ============================================================
def tw_symbol(code: str) -> str:
    return f"{code}.TW"


def safe_float(x, default=0.0):
    try:
        if pd.isna(x):
            return default
        return float(x)
    except Exception:
        return default


def calc_rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.rolling(period).mean()
    avg_loss = loss.rolling(period).mean()
    rs = avg_gain / avg_loss.replace(0, pd.NA)
    rsi = 100 - (100 / (1 + rs))
    return rsi.fillna(50)


def download_stock(code: str, period="4mo"):
    symbol = tw_symbol(code)
    df = yf.download(symbol, period=period, interval="1d", progress=False, auto_adjust=False, threads=False)
    if df is None or df.empty:
        return None
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [c[0] for c in df.columns]
    df = df.dropna()
    if len(df) < 30:
        return None
    return df


def analyze_stock(code: str, name: str):
    df = download_stock(code)
    if df is None:
        return None

    close = df["Close"]
    volume = df["Volume"]
    high = df["High"]
    low = df["Low"]
    open_ = df["Open"]

    last = df.iloc[-1]
    prev = df.iloc[-2]

    ma5 = close.rolling(5).mean().iloc[-1]
    ma10 = close.rolling(10).mean().iloc[-1]
    ma20 = close.rolling(20).mean().iloc[-1]
    ma60 = close.rolling(60).mean().iloc[-1] if len(df) >= 60 else ma20
    vol5 = volume.rolling(5).mean().iloc[-1]
    vol20 = volume.rolling(20).mean().iloc[-1]
    rsi = calc_rsi(close).iloc[-1]

    c = safe_float(last["Close"])
    o = safe_float(last["Open"])
    h = safe_float(last["High"])
    l = safe_float(last["Low"])
    v = safe_float(last["Volume"])
    pc = safe_float(prev["Close"])
    pv = safe_float(prev["Volume"])

    change_pct = ((c - pc) / pc * 100) if pc else 0
    vol_ratio = (v / vol20) if vol20 else 0
    prev_vol_ratio = (pv / volume.rolling(20).mean().iloc[-2]) if volume.rolling(20).mean().iloc[-2] else 0

    is_red = c > o
    prev_is_red = safe_float(prev["Close"]) > safe_float(prev["Open"])
    same_color_2 = (is_red == prev_is_red)

    # 左倍量：昨日或前一根先出現倍量，今日仍維持同色K，代表可能不是今天才追右側爆量
    left_volume = prev_vol_ratio >= 1.5 and same_color_2 and c >= ma10 and ma5 >= ma10
    # 右倍量：今日爆量上漲，市場熱門，但較容易已在右側
    right_volume = vol_ratio >= 1.5 and change_pct > 0 and c > ma5
    # 波段續強：多頭排列 + 未過熱失控 + 趨勢延續
    trend_continue = ma5 > ma10 > ma20 and c > ma20 and change_pct > -1.5

    high20_prev = high.shift(1).rolling(20).max().iloc[-1]
    breakout20 = c > high20_prev

    upper_shadow = ((h - max(c, o)) / c * 100) if c else 0
    lower_shadow = ((min(c, o) - l) / c * 100) if c else 0
    deviation20 = ((c - ma20) / ma20 * 100) if ma20 else 0

    fake_risk = "低"
    if vol_ratio >= 2.0 and upper_shadow >= 3 and change_pct < 1:
        fake_risk = "高"
    elif rsi >= 78 or deviation20 >= 12 or upper_shadow >= 2.2:
        fake_risk = "中"

    main_force_score = 0
    if left_volume: main_force_score += 3
    if ma5 >= ma10 >= ma20: main_force_score += 2
    if 45 <= rsi <= 72: main_force_score += 2
    if c >= ma20: main_force_score += 1
    if change_pct > 0: main_force_score += 1
    if fake_risk == "高": main_force_score -= 2

    hot_score = 0
    if right_volume: hot_score += 3
    if change_pct > 0: hot_score += 2
    if breakout20: hot_score += 2
    if ma5 > ma10 > ma20: hot_score += 1
    if rsi >= 80: hot_score -= 1
    if fake_risk == "高": hot_score -= 2

    trend_score = 0
    if trend_continue: trend_score += 3
    if ma5 > ma10 > ma20: trend_score += 2
    if c > ma60: trend_score += 1
    if 50 <= rsi <= 78: trend_score += 2
    if vol_ratio >= 1.0: trend_score += 1
    if deviation20 >= 15: trend_score -= 2
    if fake_risk == "高": trend_score -= 2

    return {
        "code": code,
        "name": name,
        "close": c,
        "change_pct": change_pct,
        "rsi": safe_float(rsi),
        "vol_ratio": safe_float(vol_ratio),
        "prev_vol_ratio": safe_float(prev_vol_ratio),
        "left_volume": left_volume,
        "right_volume": right_volume,
        "trend_continue": trend_continue,
        "fake_risk": fake_risk,
        "main_force_score": main_force_score,
        "hot_score": hot_score,
        "trend_score": trend_score,
        "deviation20": safe_float(deviation20),
    }

# ============================================================
# 選股回覆：V4.4 主力進貨版
# ============================================================
def build_selection_reply():
    rows = []
    for code, name in STOCK_POOL.items():
        try:
            item = analyze_stock(code, name)
            if item:
                rows.append(item)
            time.sleep(0.02)
        except Exception:
            continue

    if not rows:
        return "目前抓不到 Yahoo 股價資料，請稍後再試。"

    # 主力進貨：以左倍量為主，排除高假突破風險
    main_force = [r for r in rows if r["left_volume"] and r["fake_risk"] != "高"]
    main_force = sorted(main_force, key=lambda x: (x["main_force_score"], x["prev_vol_ratio"], -abs(x["change_pct"])), reverse=True)[:5]

    # 市場熱門：今日右倍量人氣股
    hot = [r for r in rows if r["right_volume"]]
    hot = sorted(hot, key=lambda x: (x["hot_score"], x["vol_ratio"], x["change_pct"]), reverse=True)[:5]

    # 波段續強：趨勢延續，不一定爆量
    trend = [r for r in rows if r["trend_continue"] and r["fake_risk"] != "高"]
    trend = sorted(trend, key=lambda x: (x["trend_score"], x["change_pct"], -x["deviation20"]), reverse=True)[:5]

    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    def fmt_item(i, r, mode):
        if mode == "main":
            extra = f"左倍{r['prev_vol_ratio']:.2f}｜RSI {r['rsi']:.0f}｜風險{r['fake_risk']}"
        elif mode == "hot":
            extra = f"右倍{r['vol_ratio']:.2f}｜漲跌{r['change_pct']:.1f}%｜風險{r['fake_risk']}"
        else:
            extra = f"量比{r['vol_ratio']:.2f}｜RSI {r['rsi']:.0f}｜乖離{r['deviation20']:.1f}%"
        return f"{i}. {r['code']} {r['name']}｜收 {r['close']:.1f}\n   {extra}"

    def section(title, data, mode, empty_text):
        lines = [title]
        if not data:
            lines.append(empty_text)
        else:
            for i, r in enumerate(data, 1):
                lines.append(fmt_item(i, r, mode))
        return "\n".join(lines)

    text = f"""【AI選股 V4.4 主力進貨版】
資料時間：{now}

━━━━━━━━━━━━━━
🔷 主力進貨 TOP5（左倍量）
━━━━━━━━━━━━━━
{section('', main_force, 'main', '目前沒有符合左倍量條件的股票。')}

說明：左倍量＝前一根已先放量，今日仍維持多頭，偏提前布局。

━━━━━━━━━━━━━━
🔥 市場熱門 TOP5（右倍量）
━━━━━━━━━━━━━━
{section('', hot, 'hot', '目前沒有明顯右倍量人氣股。')}

說明：右倍量＝今日人氣強，但較容易已在右側，勿盲追。

━━━━━━━━━━━━━━
🚀 波段續強 TOP5
━━━━━━━━━━━━━━
{section('', trend, 'trend', '目前沒有明顯波段續強股。')}

說明：多頭排列延續，適合觀察拉回或續強機會。

提醒：這是量價篩選，不是保證獲利。進場仍需搭配停損。"""

    # 清掉 section 空標題造成的開頭空行
    text = text.replace("\n\n\n", "\n\n").strip()
    return text

# ============================================================
# 股票代碼 + 買入價：保留停損停利回覆架構
# ============================================================
def handle_price_query(user_text: str):
    m = re.match(r"^\s*(\d{4})\s+([0-9]+(?:\.[0-9]+)?)\s*$", user_text)
    if not m:
        return "請輸入：\n1）選股\n或\n2）股票代碼 買入價格\n例如：2330 800"

    code = m.group(1)
    buy_price = float(m.group(2))
    name = STOCK_POOL.get(code, "")

    df = download_stock(code, period="6mo")
    if df is None:
        return f"{code} 資料取得失敗，請稍後再試。"

    close = df["Close"]
    high = df["High"]
    low = df["Low"]
    volume = df["Volume"]
    latest = safe_float(close.iloc[-1])
    prev_close = safe_float(close.iloc[-2])
    rsi = safe_float(calc_rsi(close).iloc[-1])
    ma5 = safe_float(close.rolling(5).mean().iloc[-1])
    ma10 = safe_float(close.rolling(10).mean().iloc[-1])
    ma20 = safe_float(close.rolling(20).mean().iloc[-1])
    vol_ratio = safe_float(volume.iloc[-1] / volume.rolling(20).mean().iloc[-1])

    today_pct = ((latest - prev_close) / prev_close * 100) if prev_close else 0
    profit_pct = ((latest - buy_price) / buy_price * 100) if buy_price else 0

    stop_loss_1 = buy_price * 0.90
    stop_loss_ma = min(ma10, ma20) if ma10 and ma20 else stop_loss_1
    take_profit_1 = buy_price * 1.10
    take_profit_2 = buy_price * 1.18
    moving_take_1 = max(ma5, latest * 0.95)
    moving_take_2 = max(ma10, latest * 0.90)

    if latest > ma5 > ma10 > ma20:
        trend_light = "綠燈｜多頭續強"
    elif latest > ma20:
        trend_light = "黃燈｜多頭整理"
    else:
        trend_light = "紅燈｜跌破月線"

    if rsi >= 80:
        rsi_text = "過熱，停利要提高警覺"
    elif rsi >= 60:
        rsi_text = "偏強，仍有續抱條件"
    elif rsi >= 45:
        rsi_text = "中性整理"
    else:
        rsi_text = "偏弱，避免加碼"

    if profit_pct >= 10 and rsi >= 75:
        advice = "分批停利或提高移動停利"
    elif latest > ma10 and profit_pct >= 0:
        advice = "可續抱，但跌破 MA10 要小心"
    elif latest < ma20:
        advice = "偏弱，應嚴守停損"
    else:
        advice = "觀察整理，勿急追高"

    name_part = f" {name}" if name else ""
    return f"""【股票分析】{code}{name_part}

【防錯價引擎】
買入價：{buy_price:.2f}
最新收盤：{latest:.2f}
今日漲跌：約 {today_pct:.2f}%
目前損益：約 {profit_pct:.2f}%

【趨勢燈號】
{trend_light}

【支撐壓力】
MA5：{ma5:.2f}
MA10：{ma10:.2f}
MA20：{ma20:.2f}

【停損】
停損點1：約 {stop_loss_1:.2f}（買入價 -10%）
均線支撐停損：約 {stop_loss_ma:.2f}

【停利】
停利點1：約 {take_profit_1:.2f}
停利點2：約 {take_profit_2:.2f}

【移動停利】
移動停利1：約 {moving_take_1:.2f}
移動停利2：約 {moving_take_2:.2f}

【建議】
{advice}

【RSI過熱判斷】
RSI：{rsi:.1f}｜{rsi_text}

【均線狀態】
MA5 {ma5:.2f} / MA10 {ma10:.2f} / MA20 {ma20:.2f}

【量能狀態】
量比：約 {vol_ratio:.2f}

提醒：以上為技術分析輔助，不代表保證獲利。"""


if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    print("LINE 股票機器人 V4.4 主力進貨版啟動中...")
    app.run(host="0.0.0.0", port=port)
