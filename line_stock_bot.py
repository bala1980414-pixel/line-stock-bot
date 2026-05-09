# -*- coding: utf-8 -*-
"""
LINE 股票機器人 V4-Lite AI選股雲端版
功能：
1. LINE 輸入：2330 800
   → 回傳停損點、停利點1、停利點2

2. LINE 輸入：選股
   → 回傳 AI選股 V4-Lite 簡化名單
   → 不產生 Excel
   → 適合 Render 雲端部署

Render Start Command：
gunicorn line_stock_bot:app

需要環境變數：
CHANNEL_ACCESS_TOKEN
CHANNEL_SECRET
"""

import os
import traceback
from datetime import datetime

import requests
import pandas as pd
import yfinance as yf
from flask import Flask, request, abort


# ==============================
# LINE 設定
# ==============================

CHANNEL_ACCESS_TOKEN = os.environ.get("CHANNEL_ACCESS_TOKEN", "")
CHANNEL_SECRET = os.environ.get("CHANNEL_SECRET", "")

LINE_REPLY_URL = "https://api.line.me/v2/bot/message/reply"

app = Flask(__name__)


# ==============================
# 股票池：V4-Lite 雲端精簡版
# 可之後再增加
# ==============================

STOCK_POOL = {
    # 半導體 / IC 設計
    "2330.TW": "台積電",
    "2303.TW": "聯電",
    "2454.TW": "聯發科",
    "3034.TW": "聯詠",
    "2379.TW": "瑞昱",
    "3443.TW": "創意",
    "3661.TW": "世芯-KY",
    "6415.TW": "矽力*-KY",
    "4966.TW": "譜瑞-KY",

    # AI / 伺服器 / 電子代工
    "2317.TW": "鴻海",
    "2382.TW": "廣達",
    "3231.TW": "緯創",
    "6669.TW": "緯穎",
    "3017.TW": "奇鋐",
    "3324.TWO": "雙鴻",
    "3653.TW": "健策",
    "6230.TWO": "尼得科超眾",
    "2356.TW": "英業達",
    "2357.TW": "華碩",
    "2376.TW": "技嘉",
    "2385.TW": "群光",

    # PCB / 載板
    "3037.TW": "欣興",
    "3189.TWO": "景碩",
    "8046.TWO": "南電",
    "2368.TW": "金像電",
    "6213.TW": "聯茂",

    # 光學 / 連接器 / 其他電子
    "3008.TW": "大立光",
    "3406.TW": "玉晶光",
    "2395.TW": "研華",
    "2474.TW": "可成",
    "4938.TW": "和碩",
    "2324.TW": "仁寶",

    # 重電 / 電力
    "1513.TW": "中興電",
    "1504.TW": "東元",
    "1519.TW": "華城",
    "1605.TW": "華新",
    "1609.TW": "大亞",
    "1618.TW": "合機",
}


# ==============================
# LINE 回覆
# ==============================

def reply_text(reply_token: str, text: str) -> None:
    """回覆 LINE 文字訊息"""
    if not CHANNEL_ACCESS_TOKEN:
        print("缺少 CHANNEL_ACCESS_TOKEN")
        return

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {CHANNEL_ACCESS_TOKEN}",
    }

    payload = {
        "replyToken": reply_token,
        "messages": [
            {
                "type": "text",
                "text": text[:4900],
            }
        ],
    }

    response = requests.post(LINE_REPLY_URL, headers=headers, json=payload, timeout=15)
    print("LINE reply status:", response.status_code, response.text)


# ==============================
# 技術指標
# ==============================

def calc_rsi(close: pd.Series, period: int = 14) -> float:
    """計算 RSI"""
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    avg_gain = gain.rolling(period).mean()
    avg_loss = loss.rolling(period).mean()

    rs = avg_gain / avg_loss.replace(0, pd.NA)
    rsi = 100 - (100 / (1 + rs))

    value = rsi.iloc[-1]
    if pd.isna(value):
        return 50.0
    return float(value)


def calc_macd(close: pd.Series):
    """計算 MACD"""
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    macd = ema12 - ema26
    signal = macd.ewm(span=9, adjust=False).mean()
    hist = macd - signal
    return float(macd.iloc[-1]), float(signal.iloc[-1]), float(hist.iloc[-1])


def safe_float(value, default=0.0) -> float:
    try:
        if pd.isna(value):
            return default
        return float(value)
    except Exception:
        return default


# ==============================
# V4-Lite AI 選股核心
# ==============================

def analyze_one_stock(symbol: str, name: str):
    """分析單一股票，回傳評分結果"""
    try:
        df = yf.download(
            symbol,
            period="3mo",
            interval="1d",
            auto_adjust=True,
            progress=False,
            threads=False,
        )

        if df is None or df.empty or len(df) < 30:
            return None

        # yfinance 有時會回傳多層欄位，這裡統一攤平
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = [c[0] for c in df.columns]

        close = df["Close"].dropna()
        volume = df["Volume"].dropna()

        if len(close) < 30 or len(volume) < 20:
            return None

        latest_close = safe_float(close.iloc[-1])
        prev_close = safe_float(close.iloc[-2])
        latest_volume = safe_float(volume.iloc[-1])
        avg_volume_20 = safe_float(volume.tail(20).mean(), 1)

        ma5 = safe_float(close.rolling(5).mean().iloc[-1])
        ma10 = safe_float(close.rolling(10).mean().iloc[-1])
        ma20 = safe_float(close.rolling(20).mean().iloc[-1])

        rsi = calc_rsi(close)
        macd, signal, hist = calc_macd(close)

        high_20_prev = safe_float(close.iloc[:-1].tail(20).max())
        volume_ratio = latest_volume / avg_volume_20 if avg_volume_20 > 0 else 0

        today_up = latest_close > prev_close
        ma_bull = ma5 > ma10 > ma20
        volume_boom = volume_ratio >= 1.5
        rsi_strong = 55 <= rsi <= 80
        rsi_overheat = rsi > 80
        breakout_20 = latest_close > high_20_prev
        macd_good = macd > signal and hist > 0

        score = 0
        reasons = []

        if ma_bull:
            score += 1
            reasons.append("均線多頭")

        if today_up:
            score += 1
            reasons.append("今日上漲")

        if volume_boom:
            score += 1
            reasons.append("量能放大")

        if rsi_strong:
            score += 1
            reasons.append("RSI強勢")

        if breakout_20:
            score += 1
            reasons.append("突破20日高")

        if macd_good:
            score += 1
            reasons.append("MACD轉強")

        # 題材分：雲端 Lite 版先用股票池分類概念，保守給 1 分
        if symbol in STOCK_POOL:
            score += 1
            reasons.append("核心觀察股")

        # 假突破風險
        if breakout_20 and volume_ratio < 1.2:
            risk = "高"
        elif rsi_overheat or volume_ratio > 3:
            risk = "中"
        else:
            risk = "低"

        if score >= 5 and volume_boom:
            strategy = "突破追強"
        elif score >= 4 and ma_bull and not volume_boom:
            strategy = "提前布局"
        else:
            strategy = "觀察"

        return {
            "symbol": symbol.replace(".TW", "").replace(".TWO", ""),
            "name": name,
            "close": round(latest_close, 2),
            "score": score,
            "rsi": round(rsi, 1),
            "volume_ratio": round(volume_ratio, 2),
            "risk": risk,
            "strategy": strategy,
            "reasons": "、".join(reasons),
        }

    except Exception as e:
        print(f"{symbol} 分析失敗：{e}")
        return None


def run_v4_lite_stock_picker() -> str:
    """執行 V4-Lite AI 選股，回傳 LINE 簡短文字"""
    results = []

    for symbol, name in STOCK_POOL.items():
        item = analyze_one_stock(symbol, name)
        if item:
            results.append(item)

    if not results:
        return (
            "AI選股 V4-Lite\n\n"
            "今日暫時沒有取得足夠資料。\n"
            "可能原因：Yahoo Finance 資料延遲、雲端連線不穩，或目前非交易時段。\n\n"
            "請稍後再輸入：選股"
        )

    strong = [
        x for x in results
        if x["strategy"] == "突破追強"
    ]

    early = [
        x for x in results
        if x["strategy"] == "提前布局"
    ]

    all_ranked = sorted(
        results,
        key=lambda x: (x["score"], x["volume_ratio"], x["rsi"]),
        reverse=True,
    )

    strong_ranked = sorted(
        strong,
        key=lambda x: (x["score"], x["volume_ratio"]),
        reverse=True,
    )

    early_ranked = sorted(
        early,
        key=lambda x: (x["score"], x["rsi"]),
        reverse=True,
    )

    now_text = datetime.now().strftime("%Y-%m-%d %H:%M")

    lines = []
    lines.append("AI選股 V4-Lite 雲端版")
    lines.append(f"資料時間：{now_text}")
    lines.append("")
    lines.append("【今日強勢股 TOP5】")

    for i, x in enumerate(all_ranked[:5], start=1):
        lines.append(
            f"{i}. {x['symbol']} {x['name']}｜分數{x['score']}｜"
            f"量比{x['volume_ratio']}｜RSI{x['rsi']}｜風險{x['risk']}"
        )

    lines.append("")
    lines.append("【突破追強 TOP10】")
    if strong_ranked:
        for i, x in enumerate(strong_ranked[:10], start=1):
            lines.append(
                f"{i}. {x['symbol']} {x['name']}｜分數{x['score']}｜"
                f"量比{x['volume_ratio']}｜風險{x['risk']}"
            )
    else:
        lines.append("今日沒有明確突破追強名單。")

    lines.append("")
    lines.append("【提前布局 TOP5】")
    if early_ranked:
        for i, x in enumerate(early_ranked[:5], start=1):
            lines.append(
                f"{i}. {x['symbol']} {x['name']}｜分數{x['score']}｜"
                f"RSI{x['rsi']}｜尚未爆量"
            )
    else:
        lines.append("今日沒有明確提前布局名單。")

    lines.append("")
    lines.append("提醒：")
    lines.append("V4-Lite 僅回傳簡化名單，不產生 Excel。")
    lines.append("實際進出場請搭配停損、停利與大盤風險。")

    return "\n".join(lines)


# ==============================
# 停損停利功能
# ==============================

def analyze_stop_profit(user_text: str) -> str:
    """
    輸入格式：
    2330 800
    2330.TW 800
    """
    parts = user_text.replace("，", " ").replace(",", " ").split()

    if len(parts) < 2:
        return (
            "請輸入股票代碼與買入價格，例如：\n"
            "2330 800\n\n"
            "或輸入：選股\n"
            "查看 V4-Lite AI選股名單"
        )

    stock_code = parts[0].strip()
    buy_price = float(parts[1])

    if not stock_code.endswith(".TW") and not stock_code.endswith(".TWO"):
        stock_code_yf = stock_code + ".TW"
    else:
        stock_code_yf = stock_code

    df = yf.download(
        stock_code_yf,
        period="2mo",
        interval="1d",
        auto_adjust=True,
        progress=False,
        threads=False,
    )

    if df is None or df.empty or len(df) < 20:
        return f"{stock_code} 目前抓不到足夠資料，請確認股票代碼是否正確。"

    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [c[0] for c in df.columns]

    close = df["Close"].dropna()
    latest_close = safe_float(close.iloc[-1])

    ma5 = safe_float(close.rolling(5).mean().iloc[-1])
    ma10 = safe_float(close.rolling(10).mean().iloc[-1])
    ma20 = safe_float(close.rolling(20).mean().iloc[-1])

    rsi = calc_rsi(close)

    # 停損停利邏輯：簡單實戰版
    stop_loss_1 = buy_price * 0.95
    stop_loss_2 = min(ma20, buy_price * 0.93)

    take_profit_1 = buy_price * 1.08
    take_profit_2 = buy_price * 1.15

    if rsi > 80:
        suggestion = "RSI 過熱，建議採用移動停利，避免追高。"
    elif latest_close > ma5 > ma10 > ma20:
        suggestion = "短線偏強，可續抱，但仍需守停損。"
    elif latest_close < ma20:
        suggestion = "跌破月線，轉弱機率增加，請保守處理。"
    else:
        suggestion = "目前屬於整理觀察，請等待方向明確。"

    return (
        f"股票分析：{stock_code.replace('.TW', '').replace('.TWO', '')}\n"
        f"買入價格：{buy_price:.2f}\n"
        f"最新收盤：{latest_close:.2f}\n\n"
        f"停損點1：{stop_loss_1:.2f}（約 -5%）\n"
        f"停損點2：{stop_loss_2:.2f}（月線/防守停損）\n\n"
        f"停利點1：{take_profit_1:.2f}（約 +8%）\n"
        f"停利點2：{take_profit_2:.2f}（約 +15%）\n\n"
        f"MA5：{ma5:.2f}\n"
        f"MA10：{ma10:.2f}\n"
        f"MA20：{ma20:.2f}\n"
        f"RSI：{rsi:.1f}\n\n"
        f"建議：{suggestion}"
    )


# ==============================
# Flask Routes
# ==============================

@app.route("/", methods=["GET"])
def home():
    return "LINE 股票機器人 V4-Lite AI選股雲端版運行中"


@app.route("/callback", methods=["POST"])
def callback():
    try:
        body = request.get_json()

        if not body or "events" not in body:
            return "OK"

        for event in body["events"]:
            if event.get("type") != "message":
                continue

            message = event.get("message", {})
            if message.get("type") != "text":
                continue

            reply_token = event.get("replyToken")
            user_text = message.get("text", "").strip()

            if not reply_token:
                continue

            if user_text in ["選股", "ai選股", "AI選股", "強勢股", "今日選股"]:
                reply = run_v4_lite_stock_picker()
            elif user_text in ["help", "Help", "說明", "功能"]:
                reply = (
                    "LINE 股票機器人 V4-Lite\n\n"
                    "功能1：停損停利\n"
                    "輸入範例：2330 800\n\n"
                    "功能2：AI選股\n"
                    "輸入：選股\n\n"
                    "雲端版不產生 Excel，只回傳簡化名單。"
                )
            else:
                reply = analyze_stop_profit(user_text)

            reply_text(reply_token, reply)

        return "OK"

    except Exception as e:
        print("callback error:", e)
        traceback.print_exc()
        return "OK"


# ==============================
# 本機測試用
# Render 使用 gunicorn，不會跑這段
# ==============================

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print("LINE 股票機器人 V4-Lite AI選股雲端版啟動")
    print(f"本機網址：http://127.0.0.1:{port}")
    app.run(host="0.0.0.0", port=port)
