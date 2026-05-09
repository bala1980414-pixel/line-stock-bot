# -*- coding: utf-8 -*-
"""
LINE 股票機器人 V4-Lite AI選股雲端版 正式修正版

更新重點：
1. 股票分析會顯示股票代碼 + 股票名稱
2. 停損點1 改為約 -10%
3. 停利點1、停利點2 改為移動式停利建議
4. 修正 3324 雙鴻抓不到：會自動優先抓 3324.TWO
5. 輸入「選股」只顯示股票代碼與股票名稱
"""

import os
import traceback
from datetime import datetime

import requests
import pandas as pd
import yfinance as yf
from flask import Flask, request


CHANNEL_ACCESS_TOKEN = os.environ.get("CHANNEL_ACCESS_TOKEN", "")
CHANNEL_SECRET = os.environ.get("CHANNEL_SECRET", "")
LINE_REPLY_URL = "https://api.line.me/v2/bot/message/reply"

app = Flask(__name__)


STOCK_NAMES = {
    "2330": "台積電",
    "2303": "聯電",
    "2454": "聯發科",
    "3034": "聯詠",
    "2379": "瑞昱",
    "3443": "創意",
    "3661": "世芯-KY",
    "6415": "矽力*-KY",
    "4966": "譜瑞-KY",
    "2317": "鴻海",
    "2382": "廣達",
    "3231": "緯創",
    "6669": "緯穎",
    "3017": "奇鋐",
    "3324": "雙鴻",
    "3653": "健策",
    "6230": "尼得科超眾",
    "2356": "英業達",
    "2357": "華碩",
    "2376": "技嘉",
    "2385": "群光",
    "2395": "研華",
    "3037": "欣興",
    "3189": "景碩",
    "8046": "南電",
    "2368": "金像電",
    "6213": "聯茂",
    "3008": "大立光",
    "3406": "玉晶光",
    "2474": "可成",
    "4938": "和碩",
    "2324": "仁寶",
    "1513": "中興電",
    "1504": "東元",
    "1519": "華城",
    "1605": "華新",
    "1609": "大亞",
    "1618": "合機",
}

# 上櫃股代碼，避免誤抓 .TW
OTC_CODES = {
    "3324",  # 雙鴻
    "3189",  # 景碩
    "8046",  # 南電
    "6230",  # 尼得科超眾
}

STOCK_POOL = {
    "2330.TW": "台積電",
    "2303.TW": "聯電",
    "2454.TW": "聯發科",
    "3034.TW": "聯詠",
    "2379.TW": "瑞昱",
    "3443.TW": "創意",
    "3661.TW": "世芯-KY",
    "6415.TW": "矽力*-KY",
    "4966.TW": "譜瑞-KY",
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
    "2395.TW": "研華",
    "3037.TW": "欣興",
    "3189.TWO": "景碩",
    "8046.TWO": "南電",
    "2368.TW": "金像電",
    "6213.TW": "聯茂",
    "3008.TW": "大立光",
    "3406.TW": "玉晶光",
    "2474.TW": "可成",
    "4938.TW": "和碩",
    "2324.TW": "仁寶",
    "1513.TW": "中興電",
    "1504.TW": "東元",
    "1519.TW": "華城",
    "1605.TW": "華新",
    "1609.TW": "大亞",
    "1618.TW": "合機",
}


def reply_text(reply_token: str, text: str) -> None:
    if not CHANNEL_ACCESS_TOKEN:
        print("缺少 CHANNEL_ACCESS_TOKEN")
        return

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {CHANNEL_ACCESS_TOKEN}",
    }
    payload = {
        "replyToken": reply_token,
        "messages": [{"type": "text", "text": text[:4900]}],
    }
    response = requests.post(LINE_REPLY_URL, headers=headers, json=payload, timeout=15)
    print("LINE reply status:", response.status_code, response.text)


def safe_float(value, default=0.0) -> float:
    try:
        if pd.isna(value):
            return default
        return float(value)
    except Exception:
        return default


def normalize_code(raw_code: str) -> str:
    return raw_code.upper().strip().replace(".TW", "").replace(".TWO", "")


def get_stock_name(code: str) -> str:
    return STOCK_NAMES.get(code, "名稱未建檔")


def build_yahoo_symbols(code: str):
    if code in OTC_CODES:
        return [f"{code}.TWO", f"{code}.TW"]
    return [f"{code}.TW", f"{code}.TWO"]


def download_stock_data(code: str, period="3mo"):
    for symbol in build_yahoo_symbols(code):
        try:
            df = yf.download(
                symbol,
                period=period,
                interval="1d",
                auto_adjust=True,
                progress=False,
                threads=False,
            )

            if df is not None and not df.empty and len(df) >= 20:
                if isinstance(df.columns, pd.MultiIndex):
                    df.columns = [c[0] for c in df.columns]
                return symbol, df

        except Exception as e:
            print(f"{symbol} 抓取失敗：{e}")

    return None, None


def calc_rsi(close: pd.Series, period: int = 14) -> float:
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
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    macd = ema12 - ema26
    signal = macd.ewm(span=9, adjust=False).mean()
    hist = macd - signal
    return float(macd.iloc[-1]), float(signal.iloc[-1]), float(hist.iloc[-1])


def analyze_one_stock(symbol: str, name: str):
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
        breakout_20 = latest_close > high_20_prev
        macd_good = macd > signal and hist > 0

        score = 0
        if ma_bull:
            score += 1
        if today_up:
            score += 1
        if volume_boom:
            score += 1
        if rsi_strong:
            score += 1
        if breakout_20:
            score += 1
        if macd_good:
            score += 1
        if symbol in STOCK_POOL:
            score += 1

        if score >= 5 and volume_boom:
            strategy = "突破追強"
        elif score >= 4 and ma_bull and not volume_boom:
            strategy = "提前布局"
        else:
            strategy = "觀察"

        return {
            "symbol": symbol.replace(".TW", "").replace(".TWO", ""),
            "name": name,
            "score": score,
            "rsi": round(rsi, 1),
            "volume_ratio": round(volume_ratio, 2),
            "strategy": strategy,
        }

    except Exception as e:
        print(f"{symbol} 分析失敗：{e}")
        return None


def run_v4_lite_stock_picker() -> str:
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

    strong = [x for x in results if x["strategy"] == "突破追強"]
    early = [x for x in results if x["strategy"] == "提前布局"]

    all_ranked = sorted(results, key=lambda x: (x["score"], x["volume_ratio"], x["rsi"]), reverse=True)
    strong_ranked = sorted(strong, key=lambda x: (x["score"], x["volume_ratio"]), reverse=True)
    early_ranked = sorted(early, key=lambda x: (x["score"], x["rsi"]), reverse=True)

    now_text = datetime.now().strftime("%Y-%m-%d %H:%M")

    lines = []
    lines.append("AI選股 V4-Lite 雲端版")
    lines.append(f"資料時間：{now_text}")
    lines.append("")
    lines.append("【今日強勢股 TOP5】")
    for i, x in enumerate(all_ranked[:5], start=1):
        lines.append(f"{i}. {x['symbol']} {x['name']}")

    lines.append("")
    lines.append("【突破追強 TOP10】")
    if strong_ranked:
        for i, x in enumerate(strong_ranked[:10], start=1):
            lines.append(f"{i}. {x['symbol']} {x['name']}")
    else:
        lines.append("今日沒有明確突破追強名單。")

    lines.append("")
    lines.append("【提前布局 TOP5】")
    if early_ranked:
        for i, x in enumerate(early_ranked[:5], start=1):
            lines.append(f"{i}. {x['symbol']} {x['name']}")
    else:
        lines.append("今日沒有明確提前布局名單。")

    lines.append("")
    lines.append("提醒：")
    lines.append("V4-Lite 僅回傳簡化名單，不產生 Excel。")
    lines.append("實際進出場請搭配停損、停利與大盤風險。")

    return "\n".join(lines)


def analyze_stop_profit(user_text: str) -> str:
    parts = user_text.replace("，", " ").replace(",", " ").split()

    if len(parts) < 2:
        return (
            "請輸入股票代碼與買入價格，例如：\n"
            "2330 800\n"
            "3324 1191\n\n"
            "或輸入：選股\n"
            "查看 V4-Lite AI選股名單"
        )

    raw_code = parts[0].strip()
    code = normalize_code(raw_code)
    stock_name = get_stock_name(code)

    try:
        buy_price = float(parts[1])
    except Exception:
        return "買入價格格式錯誤，請輸入例如：2330 800"

    used_symbol, df = download_stock_data(code, period="3mo")

    if df is None or df.empty:
        return (
            f"{code} {stock_name} 目前抓不到足夠資料，請確認股票代碼是否正確。\n\n"
            "若這檔是上櫃股，系統已自動嘗試 .TWO。"
        )

    close = df["Close"].dropna()
    if len(close) < 20:
        return f"{code} {stock_name} 資料不足，暫時無法分析。"

    latest_close = safe_float(close.iloc[-1])
    prev_close = safe_float(close.iloc[-2]) if len(close) >= 2 else latest_close
    today_change_pct = ((latest_close - prev_close) / prev_close * 100) if prev_close else 0.0
    profit_loss_pct = ((latest_close - buy_price) / buy_price * 100) if buy_price else 0.0

    ma5 = safe_float(close.rolling(5).mean().iloc[-1])
    ma10 = safe_float(close.rolling(10).mean().iloc[-1])
    ma20 = safe_float(close.rolling(20).mean().iloc[-1])
    rsi = calc_rsi(close)

    stop_loss_1 = buy_price * 0.90
    stop_loss_2 = min(ma20, buy_price * 0.93)

    take_profit_1 = buy_price * 1.08
    take_profit_2 = buy_price * 1.15

    if latest_close >= take_profit_2:
        profit_advice = (
            "已接近或達到停利點2，可採移動停利。\n"
            f"建議停利點1：跌破 MA5（{ma5:.2f}）先減碼\n"
            f"建議停利點2：跌破 MA10（{ma10:.2f}）再出場"
        )
    elif latest_close >= take_profit_1:
        profit_advice = (
            "已接近或達到停利點1，可先保護獲利。\n"
            f"建議停利點1：跌破 MA5（{ma5:.2f}）先減碼\n"
            f"建議停利點2：跌破 MA10（{ma10:.2f}）再出場"
        )
    else:
        profit_advice = (
            "尚未達主要停利區，先以目標價與均線移動停利追蹤。\n"
            f"建議停利點1：{take_profit_1:.2f}，或之後跌破 MA5 先減碼\n"
            f"建議停利點2：{take_profit_2:.2f}，或之後跌破 MA10 再出場"
        )

    if rsi > 80:
        suggestion = "RSI 過熱，建議採用移動停利，避免追高。"
    elif latest_close > ma5 > ma10 > ma20:
        suggestion = "短線偏強，可續抱，但仍需守停損。"
    elif latest_close < ma20:
        suggestion = "跌破月線，轉弱機率增加，請保守處理。"
    else:
        suggestion = "目前屬於整理觀察，請等待方向明確。"

    return (
        f"股票分析：{code} {stock_name}\n"
        f"Yahoo代號：{used_symbol}\n"
        f"買入價格：{buy_price:.2f}\n"
        f"最新收盤：{latest_close:.2f}\n\n"
        f"停損點1：{stop_loss_1:.2f}（約 -10%）\n"
        f"停損點2：{stop_loss_2:.2f}（月線/防守停損）\n\n"
        f"移動式停利建議：\n"
        f"{profit_advice}\n\n"
        f"MA5：{ma5:.2f}\n"
        f"MA10：{ma10:.2f}\n"
        f"MA20：{ma20:.2f}\n"
        f"RSI：{rsi:.1f}\n\n"
        f"建議：{suggestion}"
    )


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
                    "輸入範例：2330 800\n"
                    "輸入範例：3324 1191\n\n"
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


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print("LINE 股票機器人 V4-Lite AI選股雲端版啟動")
    print(f"本機網址：http://127.0.0.1:{port}")
    app.run(host="0.0.0.0", port=port)
