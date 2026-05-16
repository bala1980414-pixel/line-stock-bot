# -*- coding: utf-8 -*-
"""
LINE 股票機器人 V4.2-Lite 低追高倍量版
用途：部署在 Render，LINE 輸入「選股」回傳低追高優先股、提前布局、今日強勢股。
也支援：輸入「股票代碼 買入價」或「2330 800」回傳停損/停利。

Render Start Command：gunicorn line_stock_bot:app
Environment Variables：
- CHANNEL_ACCESS_TOKEN
- CHANNEL_SECRET
"""

import os
import re
import hmac
import base64
import hashlib
import traceback
from datetime import datetime
from zoneinfo import ZoneInfo

import requests
import pandas as pd
import yfinance as yf
from flask import Flask, request, abort


# ============================================================
# LINE 股票機器人 V4.2-Lite 低追高倍量版
# ============================================================

VERSION_NAME = "LINE 股票機器人 V4.2-Lite 低追高倍量版"

CHANNEL_ACCESS_TOKEN = os.getenv("CHANNEL_ACCESS_TOKEN", "")
CHANNEL_SECRET = os.getenv("CHANNEL_SECRET", "")

app = Flask(__name__)


# ============================================================
# 股票清單：全電子 + 重電核心觀察
# 可自行增減
# ============================================================

STOCK_POOL = {
    # 半導體 / IC 設計 / AI
    "2330": "台積電", "2303": "聯電", "2454": "聯發科", "3034": "聯詠",
    "2379": "瑞昱", "3443": "創意", "3661": "世芯-KY", "3529": "力旺",
    "4966": "譜瑞-KY", "6488": "環球晶", "6415": "矽力*-KY",

    # AI伺服器 / 散熱 / 零組件
    "2382": "廣達", "3231": "緯創", "6669": "緯穎", "2356": "英業達",
    "2324": "仁寶", "3017": "奇鋐", "3324": "雙鴻", "6230": "尼得科超眾",
    "3653": "健策", "2383": "台光電", "6213": "聯茂", "8046": "南電",
    "3037": "欣興", "3189": "景碩",

    # 光學 / 消費電子
    "3008": "大立光", "3406": "玉晶光", "4938": "和碩", "2357": "華碩",
    "2376": "技嘉", "2317": "鴻海",

    # 網通 / 電子通路 / 其他電子
    "2345": "智邦", "2412": "中華電", "3702": "大聯大", "2347": "聯強",
    "2308": "台達電", "2395": "研華",

    # 重電 / 電線電纜 / 電力設備
    "1513": "中興電", "1504": "東元", "1605": "華新", "1609": "大亞",
    "1618": "合機", "1519": "華城", "4583": "台灣精銳",
}


# ============================================================
# 基礎工具
# ============================================================

def tw_now() -> datetime:
    """台灣時間"""
    return datetime.now(ZoneInfo("Asia/Taipei"))


def fmt_time() -> str:
    return tw_now().strftime("%Y-%m-%d %H:%M:%S")


def normalize_code(text: str) -> str:
    text = text.strip()
    m = re.search(r"(\d{4})", text)
    return m.group(1) if m else ""


def to_yf_symbol(code: str) -> str:
    return f"{code}.TW"


def safe_float(value, default=0.0) -> float:
    try:
        if pd.isna(value):
            return default
        return float(value)
    except Exception:
        return default


def calc_rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.rolling(period).mean()
    avg_loss = loss.rolling(period).mean()
    rs = avg_gain / avg_loss.replace(0, pd.NA)
    return 100 - (100 / (1 + rs))


def fetch_stock_data(code: str, period: str = "90d") -> pd.DataFrame:
    df = yf.download(
        to_yf_symbol(code),
        period=period,
        interval="1d",
        progress=False,
        auto_adjust=False,
        threads=False,
    )

    if df is None or df.empty:
        return pd.DataFrame()

    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [c[0] for c in df.columns]

    df = df.dropna(subset=["Close", "Volume"])
    return df


def calc_volume_power(df: pd.DataFrame) -> dict:
    """
    左倍量：今日量 / 昨日量
    右倍量：今日量 / 近5日均量（不含今日）
    """
    if df is None or len(df) < 8:
        return {
            "left_volume_ratio": 0.0,
            "right_volume_ratio": 0.0,
            "volume_judgement": "資料不足",
        }

    today_volume = safe_float(df["Volume"].iloc[-1])
    yesterday_volume = safe_float(df["Volume"].iloc[-2])
    avg5_volume = safe_float(df["Volume"].iloc[-6:-1].mean())

    left_ratio = today_volume / yesterday_volume if yesterday_volume > 0 else 0.0
    right_ratio = today_volume / avg5_volume if avg5_volume > 0 else 0.0

    if left_ratio >= 1.5 and right_ratio >= 1.5:
        judgement = "左右倍量同步放大，量能強"
    elif left_ratio >= 1.5:
        judgement = "左倍量放大，短線轉強"
    elif right_ratio >= 1.5:
        judgement = "右倍量放大，量能優於均量"
    elif left_ratio < 0.8 and right_ratio < 0.8:
        judgement = "量能偏弱，追價保守"
    else:
        judgement = "量能普通，觀察續航"

    return {
        "left_volume_ratio": round(left_ratio, 2),
        "right_volume_ratio": round(right_ratio, 2),
        "volume_judgement": judgement,
    }


def calc_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["MA5"] = df["Close"].rolling(5).mean()
    df["MA10"] = df["Close"].rolling(10).mean()
    df["MA20"] = df["Close"].rolling(20).mean()
    df["RSI"] = calc_rsi(df["Close"], 14)

    ema12 = df["Close"].ewm(span=12, adjust=False).mean()
    ema26 = df["Close"].ewm(span=26, adjust=False).mean()
    df["MACD"] = ema12 - ema26
    df["MACD_SIGNAL"] = df["MACD"].ewm(span=9, adjust=False).mean()

    df["HIGH20"] = df["High"].rolling(20).max()
    return df


# ============================================================
# V4.2 選股核心：低追高 + 左右倍量 + 量價判斷
# ============================================================

def analyze_candidate(code: str, name: str) -> dict | None:
    df = fetch_stock_data(code)
    if df.empty or len(df) < 30:
        return None

    df = calc_indicators(df)
    last = df.iloc[-1]
    prev = df.iloc[-2]

    close = safe_float(last["Close"])
    prev_close = safe_float(prev["Close"])
    high = safe_float(last["High"])
    ma5 = safe_float(last["MA5"])
    ma10 = safe_float(last["MA10"])
    ma20 = safe_float(last["MA20"])
    rsi = safe_float(last["RSI"])
    macd = safe_float(last["MACD"])
    macd_signal = safe_float(last["MACD_SIGNAL"])
    high20_prev = safe_float(df["High"].iloc[-21:-1].max())

    if close <= 0 or prev_close <= 0:
        return None

    change_pct = round((close - prev_close) / prev_close * 100, 2)
    ma20_bias = round((close - ma20) / ma20 * 100, 2) if ma20 > 0 else 0.0
    high20_break = close > high20_prev if high20_prev > 0 else False

    vol = calc_volume_power(df)
    left_ratio = vol["left_volume_ratio"]
    right_ratio = vol["right_volume_ratio"]

    score = 0
    tags = []

    if ma5 > ma10 > ma20:
        score += 1
        tags.append("均線多頭")
    if close > prev_close:
        score += 1
        tags.append("今日上漲")
    if 50 <= rsi <= 75:
        score += 1
        tags.append("RSI健康強勢")
    elif rsi > 80:
        score -= 1
        tags.append("RSI過熱")
    if macd > macd_signal:
        score += 1
        tags.append("MACD偏多")
    if high20_break:
        score += 1
        tags.append("突破20日高")
    if left_ratio >= 1.3:
        score += 1
        tags.append("左倍量")
    if right_ratio >= 1.3:
        score += 1
        tags.append("右倍量")

    # 低追高控制：漲太多、乖離太高、RSI太熱，降低排序
    chase_risk = "低"
    if change_pct >= 6 or ma20_bias >= 12 or rsi >= 80:
        chase_risk = "高"
        score -= 2
    elif change_pct >= 3.5 or ma20_bias >= 8 or rsi >= 75:
        chase_risk = "中"
        score -= 1

    if left_ratio >= 1.5 and right_ratio >= 1.5 and change_pct <= 5:
        price_volume = "價漲量增，強勢但仍需控追高"
    elif close > prev_close and right_ratio < 1:
        price_volume = "價漲量縮，續航需觀察"
    elif close < prev_close and right_ratio >= 1.3:
        price_volume = "量增價弱，可能有賣壓"
    elif abs(change_pct) <= 1.5 and right_ratio >= 1.2:
        price_volume = "量先出、價未大漲，偏低追高觀察"
    else:
        price_volume = vol["volume_judgement"]

    if score >= 5 and chase_risk != "高":
        strategy = "突破追強"
    elif score >= 3 and change_pct <= 3.5 and ma5 >= ma10 and right_ratio >= 1.0:
        strategy = "提前布局"
    elif score >= 3 and change_pct <= 5:
        strategy = "今日強勢"
    else:
        strategy = "觀察"

    return {
        "code": code,
        "name": name,
        "close": round(close, 2),
        "change_pct": change_pct,
        "score": score,
        "strategy": strategy,
        "rsi": round(rsi, 1),
        "left_volume_ratio": left_ratio,
        "right_volume_ratio": right_ratio,
        "price_volume": price_volume,
        "chase_risk": chase_risk,
        "tags": "、".join(tags[:5]) if tags else "無明顯訊號",
    }


def pick_stocks() -> str:
    results = []

    for code, name in STOCK_POOL.items():
        try:
            item = analyze_candidate(code, name)
            if item:
                results.append(item)
        except Exception:
            continue

    if not results:
        return (
            f"{VERSION_NAME}\n"
            f"資料時間：{fmt_time()}（台灣時間）\n\n"
            "目前抓不到有效選股資料，可能是 Yahoo Finance 暫時無資料或非交易時段。"
        )

    results = sorted(
        results,
        key=lambda x: (x["score"], -{"低": 0, "中": 1, "高": 2}.get(x["chase_risk"], 1), x["right_volume_ratio"]),
        reverse=True,
    )

    strong = [x for x in results if x["strategy"] in ("突破追強", "今日強勢") and x["chase_risk"] != "高"][:5]
    early = [x for x in results if x["strategy"] == "提前布局" and x["chase_risk"] != "高"][:5]
    volume_watch = [x for x in results if x["right_volume_ratio"] >= 1.3 and x["chase_risk"] != "高"][:5]

    def block(title: str, rows: list[dict]) -> str:
        if not rows:
            return f"{title}\n目前沒有符合條件。"
        lines = [title]
        for i, x in enumerate(rows, 1):
            lines.append(
                f"{i}. {x['code']} {x['name']}｜收盤 {x['close']}｜漲跌 {x['change_pct']}%\n"
                f"   分數 {x['score']}｜RSI {x['rsi']}｜追高風險：{x['chase_risk']}\n"
                f"   左倍量 {x['left_volume_ratio']}｜右倍量 {x['right_volume_ratio']}\n"
                f"   量價判斷：{x['price_volume']}"
            )
        return "\n".join(lines)

    msg = [
        VERSION_NAME,
        f"資料時間：{fmt_time()}（台灣時間）",
        "",
        "【低追高提醒】",
        "優先看：漲幅不過大、右倍量放大、RSI未過熱、均線仍偏多。",
        "",
        block("【今日強勢股 TOP5】", strong),
        "",
        block("【提前布局 TOP5】", early),
        "",
        block("【倍量觀察 TOP5】", volume_watch),
        "",
        "※ 左倍量＝今日量 / 昨日量",
        "※ 右倍量＝今日量 / 近5日均量",
        "※ 僅供紀律觀察，不代表保證獲利。",
    ]

    return "\n".join(msg)


# ============================================================
# 股票代碼 + 買入價：改回 V4-Lite 正式修正版格式
# ============================================================

def analyze_buy_price(code: str, buy_price: float) -> str:
    name = STOCK_POOL.get(code, "")
    df = fetch_stock_data(code)
    if df.empty or len(df) < 30:
        return f"查無 {code} 的有效資料，請確認股票代碼是否正確。"

    df = calc_indicators(df)
    last = df.iloc[-1]
    prev = df.iloc[-2]

    close = safe_float(last["Close"])
    prev_close = safe_float(prev["Close"])
    ma5 = safe_float(last["MA5"])
    ma10 = safe_float(last["MA10"])
    ma20 = safe_float(last["MA20"])
    rsi = safe_float(last["RSI"])

    change_pct = round((close - prev_close) / prev_close * 100, 2) if prev_close > 0 else 0.0
    pnl_pct = round((close - buy_price) / buy_price * 100, 2) if buy_price > 0 else 0.0

    stop_loss_1 = round(buy_price * 0.90, 2)
    stop_loss_ma = round(ma20, 2) if ma20 > 0 else stop_loss_1
    take_profit_1 = round(buy_price * 1.08, 2)
    take_profit_2 = round(buy_price * 1.15, 2)
    trailing_1 = round(close * 0.95, 2)
    trailing_2 = round(close * 0.90, 2)

    vol = calc_volume_power(df)

    if ma5 > ma10 > ma20:
        trend = "多頭排列"
    elif close >= ma20:
        trend = "站上月線"
    else:
        trend = "弱勢整理"

    if rsi >= 80:
        rsi_text = "過熱，避免追高"
    elif rsi >= 65:
        rsi_text = "偏強，續抱但控風險"
    elif rsi >= 50:
        rsi_text = "中性偏多"
    else:
        rsi_text = "偏弱"

    if pnl_pct >= 15:
        suggestion = "已有明顯獲利，可採移動停利保護。"
    elif pnl_pct >= 5:
        suggestion = "小幅獲利，可續抱並觀察量能。"
    elif pnl_pct <= -8:
        suggestion = "接近停損區，需嚴格控風險。"
    else:
        suggestion = "區間觀察，等待方向確認。"

    return (
        f"{VERSION_NAME}\n"
        f"資料時間：{fmt_time()}（台灣時間）\n\n"
        f"【股票分析】\n"
        f"{code} {name}\n"
        f"買入價：{buy_price}\n"
        f"目前價：{round(close, 2)}\n"
        f"今日漲跌：約 {change_pct}%\n"
        f"目前損益：約 {pnl_pct}%\n\n"
        f"【防錯價引擎】\n"
        f"若資料時間非交易時段，價格可能為最近一筆日線收盤價。\n\n"
        f"【趨勢燈號】\n"
        f"{trend}\n\n"
        f"【支撐壓力】\n"
        f"MA5：{round(ma5, 2)}｜MA10：{round(ma10, 2)}｜MA20：{round(ma20, 2)}\n\n"
        f"【停損】\n"
        f"停損點1：約 {stop_loss_1}（買入價 -10%）\n"
        f"均線支撐停損：約 {stop_loss_ma}（MA20）\n\n"
        f"【停利】\n"
        f"停利點1：約 {take_profit_1}（+8%）\n"
        f"停利點2：約 {take_profit_2}（+15%）\n\n"
        f"【移動停利】\n"
        f"移動停利1：約 {trailing_1}（目前價 -5%）\n"
        f"移動停利2：約 {trailing_2}（目前價 -10%）\n\n"
        f"【量能狀態】\n"
        f"左倍量：{vol['left_volume_ratio']}｜右倍量：{vol['right_volume_ratio']}\n"
        f"量價判斷：{vol['volume_judgement']}\n\n"
        f"【RSI過熱判斷】\n"
        f"RSI：{round(rsi, 1)}｜{rsi_text}\n\n"
        f"【建議】\n"
        f"{suggestion}\n\n"
        f"※ 僅供紀律觀察，不代表保證獲利。"
    )


# ============================================================
# LINE Webhook
# ============================================================

def verify_signature(body: bytes, signature: str) -> bool:
    if not CHANNEL_SECRET:
        return False
    digest = hmac.new(CHANNEL_SECRET.encode("utf-8"), body, hashlib.sha256).digest()
    valid_signature = base64.b64encode(digest).decode("utf-8")
    return hmac.compare_digest(valid_signature, signature)


def reply_line(reply_token: str, text: str) -> None:
    if not CHANNEL_ACCESS_TOKEN:
        return

    url = "https://api.line.me/v2/bot/message/reply"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {CHANNEL_ACCESS_TOKEN}",
    }

    # LINE 單則文字上限約 5000 字，保守裁切
    text = text[:4800]

    payload = {
        "replyToken": reply_token,
        "messages": [{"type": "text", "text": text}],
    }

    requests.post(url, headers=headers, json=payload, timeout=10)


def handle_text(text: str) -> str:
    text = text.strip()

    if text in ("選股", "今日選股", "AI選股"):
        return pick_stocks()

    m = re.match(r"^\s*(\d{4})\s+([0-9]+(?:\.[0-9]+)?)\s*$", text)
    if m:
        code = m.group(1)
        buy_price = float(m.group(2))
        return analyze_buy_price(code, buy_price)

    code = normalize_code(text)
    if code and text == code:
        return (
            f"{VERSION_NAME}\n"
            f"資料時間：{fmt_time()}（台灣時間）\n\n"
            "請輸入格式：股票代碼 買入價\n"
            "例如：2330 800\n\n"
            "或輸入：選股"
        )

    return (
        f"{VERSION_NAME}\n"
        f"資料時間：{fmt_time()}（台灣時間）\n\n"
        "可用指令：\n"
        "1. 選股\n"
        "2. 股票代碼 買入價，例如：2330 800"
    )


@app.route("/", methods=["GET"])
def home():
    return f"{VERSION_NAME} is running. Taiwan time: {fmt_time()}"


@app.route("/callback", methods=["POST"])
def callback():
    signature = request.headers.get("X-Line-Signature", "")
    body = request.get_data()

    if not verify_signature(body, signature):
        abort(400)

    try:
        payload = request.get_json(force=True)
        events = payload.get("events", [])

        for event in events:
            if event.get("type") != "message":
                continue
            message = event.get("message", {})
            if message.get("type") != "text":
                continue

            reply_token = event.get("replyToken")
            user_text = message.get("text", "")
            reply_text = handle_text(user_text)

            if reply_token:
                reply_line(reply_token, reply_text)

        return "OK"

    except Exception:
        traceback.print_exc()
        return "OK"


if __name__ == "__main__":
    print("=" * 60)
    print(VERSION_NAME)
    print(f"台灣時間：{fmt_time()}")
    print("本機測試網址：http://127.0.0.1:5000")
    print("Webhook網址：https://你的Render網址/callback")
    print("=" * 60)
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)))
