# -*- coding: utf-8 -*-
"""
LINE 股票機器人 V4.2-Lite 低追高倍量版
用途：部署在 Render，LINE 輸入「選股」回傳低追高優先股、提前布局、今日強勢股。
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
from typing import Dict, List, Optional, Tuple

import requests
import pandas as pd
import yfinance as yf
from flask import Flask, request, abort

app = Flask(__name__)

CHANNEL_ACCESS_TOKEN = os.getenv("CHANNEL_ACCESS_TOKEN", "").strip()
CHANNEL_SECRET = os.getenv("CHANNEL_SECRET", "").strip()
LINE_REPLY_URL = "https://api.line.me/v2/bot/message/reply"

# =========================
# V4.1-Lite 股票池：台股電子 + 重電核心觀察名單
# 可自行增減，不影響主程式
# =========================
STOCK_POOL: Dict[str, str] = {
    # 半導體 / IC / AI / 電子權值
    "2330.TW": "台積電", "2303.TW": "聯電", "2454.TW": "聯發科", "3034.TW": "聯詠",
    "2379.TW": "瑞昱", "3443.TW": "創意", "3661.TW": "世芯-KY", "3529.TWO": "力旺",
    "4966.TW": "譜瑞-KY", "5269.TW": "祥碩", "6488.TWO": "環球晶", "3105.TWO": "穩懋",

    # AI伺服器 / 散熱 / 機殼 / PCB / 連接器
    "2317.TW": "鴻海", "2382.TW": "廣達", "3231.TW": "緯創", "6669.TW": "緯穎",
    "2356.TW": "英業達", "3017.TW": "奇鋐", "3324.TWO": "雙鴻", "3653.TW": "健策",
    "3533.TW": "嘉澤", "2383.TW": "台光電", "3037.TW": "欣興", "8046.TW": "南電",
    "2368.TW": "金像電", "6213.TW": "聯茂", "6274.TWO": "台燿", "2308.TW": "台達電",

    # 光學 / 電子零組件 / 消費電子
    "3008.TW": "大立光", "3406.TW": "玉晶光", "2395.TW": "研華", "2376.TW": "技嘉",
    "2357.TW": "華碩", "2353.TW": "宏碁", "4938.TW": "和碩", "2474.TW": "可成",
    "2324.TW": "仁寶", "2498.TW": "宏達電", "2409.TW": "友達", "3481.TW": "群創",

    # 重電 / 電力 / 線纜
    "1513.TW": "中興電", "1504.TW": "東元", "1605.TW": "華新", "1609.TW": "大亞",
    "1618.TW": "合機", "1514.TW": "亞力", "1519.TW": "華城", "8996.TW": "高力",
}

# =========================
# 參數區：低追高版核心設定
# =========================
MIN_AVG_VOLUME = 800_000          # 過濾低流動性，20日均量低於此值不列入
MAX_TODAY_GAIN_LOW_CHASE = 4.0    # 低追高：今日漲幅不可太大
MAX_MA5_BIAS_LOW_CHASE = 4.5      # 低追高：收盤價離5MA不可太遠
GOOD_RSI_LOW = 52
GOOD_RSI_HIGH = 68
HOT_RSI = 75
HEALTHY_VOL_RATIO_LOW = 1.05
HEALTHY_VOL_RATIO_HIGH = 2.20
DATA_PERIOD = "4mo"

CACHE_SECONDS = 60 * 10           # 選股快取10分鐘，避免LINE連續查詢太慢
_last_pick_cache = {"ts": 0.0, "message": ""}


# =========================
# LINE 基本功能
# =========================
def verify_signature(body: bytes, signature: str) -> bool:
    if not CHANNEL_SECRET:
        # 本機測試或未設定時不擋，但 Render 正式建議一定要設定
        return True
    mac = hmac.new(CHANNEL_SECRET.encode("utf-8"), body, hashlib.sha256).digest()
    expected = base64.b64encode(mac).decode("utf-8")
    return hmac.compare_digest(expected, signature or "")


def reply_text(reply_token: str, text: str) -> None:
    if not CHANNEL_ACCESS_TOKEN:
        print("缺少 CHANNEL_ACCESS_TOKEN，無法回覆 LINE")
        return
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {CHANNEL_ACCESS_TOKEN}",
    }
    payload = {
        "replyToken": reply_token,
        "messages": [{"type": "text", "text": text[:4900]}],
    }
    r = requests.post(LINE_REPLY_URL, headers=headers, json=payload, timeout=10)
    if r.status_code >= 300:
        print("LINE reply error:", r.status_code, r.text)


@app.route("/", methods=["GET"])
def home():
    return "LINE 股票機器人 V4.1-Lite 低追高版 running."


@app.route("/callback", methods=["POST"])
def callback():
    body = request.get_data()
    signature = request.headers.get("X-Line-Signature", "")
    if not verify_signature(body, signature):
        abort(400)

    data = request.get_json(silent=True) or {}
    for event in data.get("events", []):
        try:
            if event.get("type") != "message":
                continue
            message = event.get("message", {})
            if message.get("type") != "text":
                continue
            user_text = (message.get("text") or "").strip()
            reply_token = event.get("replyToken")
            if not reply_token:
                continue

            response = handle_user_text(user_text)
            reply_text(reply_token, response)
        except Exception:
            traceback.print_exc()
    return "OK"


# =========================
# 使用者文字處理
# =========================
def handle_user_text(text: str) -> str:
    normalized = text.strip().replace("，", ",").replace("：", ":")

    if normalized in ["選股", "今日選股", "低追高", "強勢股"]:
        return get_stock_picks_message()

    if normalized in ["說明", "help", "Help", "HELP", "使用說明"]:
        return help_message()

    trade = parse_trade_input(normalized)
    if trade:
        code, buy_price = trade
        return analyze_trade_price(code, buy_price)

    return (
        "我看不懂這個指令。\n\n"
        "可輸入：\n"
        "1）選股\n"
        "2）2330 800\n"
        "3）2330.TW 800\n"
        "4）說明"
    )


def help_message() -> str:
    return (
        "LINE 股票機器人 V4.1-Lite 低追高版\n\n"
        "可用指令：\n"
        "【選股】\n"
        "回傳：低追高優先股、提前布局、今日強勢股。\n\n"
        "【股票代碼 買進價】\n"
        "例如：2330 800\n"
        "回傳：停損、停利1、停利2、風險提醒。\n\n"
        "本版重點：避免追高，優先找剛轉強、未過熱、低乖離標的。"
    )


def parse_trade_input(text: str) -> Optional[Tuple[str, float]]:
    # 支援：2330 800、2330.TW 800、買2330 800
    m = re.search(r"(\d{4}(?:\.(?:TW|TWO))?)\D+([0-9]+(?:\.[0-9]+)?)", text.upper())
    if not m:
        return None
    code = m.group(1)
    price = float(m.group(2))
    if price <= 0:
        return None
    if "." not in code:
        code = f"{code}.TW"
    return code, price


# =========================
# 技術指標
# =========================
def safe_float(x, default=0.0) -> float:
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


def download_stock(symbol: str) -> Optional[pd.DataFrame]:
    try:
        df = yf.download(symbol, period=DATA_PERIOD, interval="1d", auto_adjust=True, progress=False, threads=False)
        if df is None or df.empty or len(df) < 35:
            return None
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = [c[0] for c in df.columns]
        return df.dropna()
    except Exception as e:
        print(f"下載失敗 {symbol}: {e}")
        return None


def analyze_symbol(symbol: str, name: str) -> Optional[Dict]:
    df = download_stock(symbol)
    if df is None or len(df) < 35:
        return None

    close = df["Close"]
    volume = df["Volume"]
    df["MA5"] = close.rolling(5).mean()
    df["MA10"] = close.rolling(10).mean()
    df["MA20"] = close.rolling(20).mean()
    df["RSI"] = calc_rsi(close)
    df["VOL20"] = volume.rolling(20).mean()

    latest = df.iloc[-1]
    prev = df.iloc[-2]
    prev2 = df.iloc[-3]

    close_now = safe_float(latest["Close"])
    close_prev = safe_float(prev["Close"])
    if close_now <= 0 or close_prev <= 0:
        return None

    ma5 = safe_float(latest["MA5"])
    ma10 = safe_float(latest["MA10"])
    ma20 = safe_float(latest["MA20"])
    rsi = safe_float(latest["RSI"], 50)
    vol = safe_float(latest["Volume"])
    vol20 = safe_float(latest["VOL20"])
    if vol20 < MIN_AVG_VOLUME:
        return None

    change_pct = (close_now - close_prev) / close_prev * 100
    ma5_bias = (close_now - ma5) / ma5 * 100 if ma5 else 0
    vol_ratio = vol / vol20 if vol20 else 0
    ma_bull = ma5 > ma10 > ma20
    above_ma5 = close_now > ma5
    just_cross_ma5 = close_now > ma5 and safe_float(prev["Close"]) <= safe_float(prev["MA5"])
    ma5_up = ma5 > safe_float(prev["MA5"])
    ma10_up = ma10 > safe_float(prev["MA10"])
    high20_prev = safe_float(df["Close"].iloc[-21:-1].max())
    breakout20 = close_now > high20_prev if high20_prev > 0 else False
    two_day_up = close_now > safe_float(prev["Close"]) > safe_float(prev2["Close"])

    # 低追高分數：重點不是最強，而是剛轉強且還沒過熱
    low_chase_score = 0
    reasons = []

    if GOOD_RSI_LOW <= rsi <= GOOD_RSI_HIGH:
        low_chase_score += 1
        reasons.append("RSI剛轉強")
    if 0 <= change_pct <= MAX_TODAY_GAIN_LOW_CHASE:
        low_chase_score += 1
        reasons.append("漲幅未過大")
    if 0 <= ma5_bias <= MAX_MA5_BIAS_LOW_CHASE:
        low_chase_score += 1
        reasons.append("低乖離")
    if HEALTHY_VOL_RATIO_LOW <= vol_ratio <= HEALTHY_VOL_RATIO_HIGH:
        low_chase_score += 1
        reasons.append("量能健康")
    if just_cross_ma5 or (above_ma5 and ma5_up and ma10_up):
        low_chase_score += 1
        reasons.append("剛轉強")

    # 強勢分數：保留強勢股，但會標風險，不讓它壓過低追高股
    strength_score = 0
    if ma_bull:
        strength_score += 1
    if change_pct > 0:
        strength_score += 1
    if vol_ratio >= 1.5:
        strength_score += 1
    if rsi >= 60:
        strength_score += 1
    if breakout20:
        strength_score += 1

    chase_risk = classify_chase_risk(rsi, change_pct, ma5_bias, vol_ratio, two_day_up)

    # 分類
    if low_chase_score >= 4 and chase_risk in ["低", "中"]:
        category = "低追高優先"
    elif low_chase_score >= 3 and change_pct < 3.5 and rsi < 70:
        category = "提前布局"
    elif strength_score >= 4:
        category = "今日強勢"
    else:
        category = "觀察"

    return {
        "symbol": symbol,
        "code": symbol.replace(".TW", "").replace(".TWO", ""),
        "name": name,
        "close": close_now,
        "change_pct": change_pct,
        "rsi": rsi,
        "vol_ratio": vol_ratio,
        "ma5_bias": ma5_bias,
        "low_chase_score": low_chase_score,
        "strength_score": strength_score,
        "chase_risk": chase_risk,
        "category": category,
        "reasons": "、".join(reasons[:3]) if reasons else "-",
        "two_day_up": two_day_up,
        "breakout20": breakout20,
    }


def classify_chase_risk(rsi: float, change_pct: float, ma5_bias: float, vol_ratio: float, two_day_up: bool) -> str:
    risk_points = 0
    if rsi >= HOT_RSI:
        risk_points += 2
    elif rsi >= 70:
        risk_points += 1

    if change_pct >= 6:
        risk_points += 2
    elif change_pct >= 4:
        risk_points += 1

    if ma5_bias >= 7:
        risk_points += 2
    elif ma5_bias >= 4.5:
        risk_points += 1

    if vol_ratio >= 3:
        risk_points += 1
    if two_day_up:
        risk_points += 1

    if risk_points >= 4:
        return "高"
    if risk_points >= 2:
        return "中"
    return "低"


# =========================
# 選股訊息
# =========================
def get_stock_picks_message() -> str:
    now = time.time()
    if _last_pick_cache["message"] and now - _last_pick_cache["ts"] < CACHE_SECONDS:
        return _last_pick_cache["message"]

    results: List[Dict] = []
    for symbol, name in STOCK_POOL.items():
        item = analyze_symbol(symbol, name)
        if item:
            results.append(item)

    if not results:
        return "目前資料不足或 Yahoo Finance 暫時無法取得資料，請稍後再試。"

    low_chase = [x for x in results if x["category"] == "低追高優先"]
    early = [x for x in results if x["category"] == "提前布局"]
    strong = [x for x in results if x["category"] == "今日強勢"]

    low_chase = sorted(low_chase, key=lambda x: (x["low_chase_score"], -x["change_pct"], -x["ma5_bias"]), reverse=True)[:5]
    early = sorted(early, key=lambda x: (x["low_chase_score"], x["strength_score"], -abs(x["change_pct"])), reverse=True)[:5]
    strong = sorted(strong, key=lambda x: (x["strength_score"], x["change_pct"]), reverse=True)[:5]

    dt = datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = [
        "V4.1-Lite 低追高版",
        f"資料時間：{dt}",
        "策略：優先找剛轉強、未過熱、低乖離標的",
        "",
    ]

    lines += format_section("低追高優先股 TOP5", low_chase, main=True)
    lines += format_section("提前布局觀察 TOP5", early, main=False)
    lines += format_section("今日強勢股 TOP5（注意追高）", strong, main=False)

    lines += [
        "",
        "提醒：",
        "追高風險=高，代表可能已漲多、RSI過熱或離5MA太遠，不建議直接追價。",
        "本工具為技術面輔助，不保證獲利，請搭配停損與部位控管。",
    ]

    msg = "\n".join(lines)
    _last_pick_cache["ts"] = now
    _last_pick_cache["message"] = msg
    return msg


def format_section(title: str, rows: List[Dict], main: bool = False) -> List[str]:
    lines = [f"【{title}】"]
    if not rows:
        lines.append("目前沒有符合條件的標的")
        lines.append("")
        return lines

    for i, x in enumerate(rows, 1):
        risk_icon = "✅" if x["chase_risk"] == "低" else "⚠️" if x["chase_risk"] == "中" else "⛔"
        title_line = f"{i}. {x['code']} {x['name']} {risk_icon}"
        detail = (
            f"收盤:{x['close']:.2f}｜漲幅:{x['change_pct']:+.2f}%｜"
            f"RSI:{x['rsi']:.1f}｜量比:{x['vol_ratio']:.2f}｜"
            f"5MA乖離:{x['ma5_bias']:+.2f}%"
        )
        score = f"低追高分:{x['low_chase_score']}/5｜強勢分:{x['strength_score']}/5｜追高風險:{x['chase_risk']}"
        reason = f"原因:{x['reasons']}"
        warn = ""
        if x["two_day_up"]:
            warn = "｜提醒:已連漲2日"
        lines.extend([title_line, detail, score, reason + warn, ""])
    return lines


# =========================
# 停損停利分析
# =========================
def analyze_trade_price(symbol: str, buy_price: float) -> str:
    if "." not in symbol:
        symbol = f"{symbol}.TW"
    name = STOCK_POOL.get(symbol, symbol.replace(".TW", "").replace(".TWO", ""))
    df = download_stock(symbol)

    stop_loss = buy_price * 0.95
    tp1 = buy_price * 1.06
    tp2 = buy_price * 1.10
    trail1 = buy_price * 1.03
    trail2 = buy_price * 1.06

    extra = ""
    if df is not None and len(df) >= 20:
        close = df["Close"]
        ma5 = safe_float(close.rolling(5).mean().iloc[-1])
        ma10 = safe_float(close.rolling(10).mean().iloc[-1])
        rsi = safe_float(calc_rsi(close).iloc[-1], 50)
        latest_close = safe_float(close.iloc[-1])
        change_from_buy = (latest_close - buy_price) / buy_price * 100

        # 用均線支撐修正停損：不讓停損抓太離譜
        support_stop = max(stop_loss, ma10 * 0.985 if ma10 else stop_loss)
        stop_loss = min(support_stop, buy_price * 0.98) if latest_close >= buy_price else stop_loss

        if rsi >= 80:
            suggestion = "RSI過熱，建議偏保守，接近停利可分批出場。"
        elif latest_close > buy_price and rsi >= 65:
            suggestion = "目前偏強，可續抱，但跌破5MA要提高警覺。"
        elif latest_close < buy_price:
            suggestion = "目前低於買進價，請嚴守停損，不建議攤平。"
        else:
            suggestion = "目前尚可觀察，等待量能與均線確認。"

        extra = (
            f"\n現價:{latest_close:.2f}\n"
            f"相對買進:{change_from_buy:+.2f}%\n"
            f"MA5:{ma5:.2f}｜MA10:{ma10:.2f}｜RSI:{rsi:.1f}\n"
            f"建議:{suggestion}\n"
        )

    return (
        f"{name} {symbol}\n"
        f"買進價:{buy_price:.2f}\n"
        f"\n停損價:{stop_loss:.2f}\n"
        f"停利價1:{tp1:.2f}\n"
        f"停利價2:{tp2:.2f}\n"
        f"移動停利1:{trail1:.2f}\n"
        f"移動停利2:{trail2:.2f}"
        f"{extra}\n"
        "提醒：停損停利為輔助參考，請依個人資金控管執行。"
    )


if __name__ == "__main__":
    port = int(os.getenv("PORT", "5000"))
    app.run(host="0.0.0.0", port=port)
