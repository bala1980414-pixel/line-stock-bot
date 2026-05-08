from flask import Flask, request
import os
import requests
import yfinance as yf
import pandas as pd

app = Flask(__name__)

# =========================
# LINE 環境變數
# =========================
CHANNEL_ACCESS_TOKEN = os.getenv("CHANNEL_ACCESS_TOKEN")
CHANNEL_SECRET = os.getenv("CHANNEL_SECRET")

LINE_REPLY_URL = "https://api.line.me/v2/bot/message/reply"


# =========================
# RSI 計算
# =========================
def calculate_rsi(close_series, period=14):
    delta = close_series.diff()

    gain = delta.where(delta > 0, 0)
    loss = -delta.where(delta < 0, 0)

    avg_gain = gain.rolling(window=period).mean()
    avg_loss = loss.rolling(window=period).mean()

    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))

    return rsi


# =========================
# 股票代碼處理
# =========================
def normalize_symbol(stock_code):
    stock_code = stock_code.strip().upper()

    if stock_code.endswith(".TW") or stock_code.endswith(".TWO"):
        return stock_code

    # 預設用上市 .TW
    return stock_code + ".TW"


# =========================
# 主要分析邏輯：V2 實戰版
# =========================
def analyze_stock(stock_code, buy_price):
    symbol = normalize_symbol(stock_code)

    try:
        data = yf.download(
            symbol,
            period="6mo",
            interval="1d",
            auto_adjust=False,
            progress=False
        )
    except Exception:
        return f"查詢 {stock_code} 時發生錯誤，請稍後再試。"

    if data is None or data.empty or len(data) < 30:
        return (
            f"查不到 {stock_code} 的有效資料。\n\n"
            "可能原因：\n"
            "1. 股票代碼輸入錯誤\n"
            "2. 該股票不是上市股票\n"
            "3. Yahoo Finance 暫時無資料\n\n"
            "若是上櫃股票，請輸入完整代碼，例如：\n"
            "6488.TWO 100"
        )

    try:
        close = data["Close"].squeeze()
        high = data["High"].squeeze()
        low = data["Low"].squeeze()
        volume = data["Volume"].squeeze()

        current_price = float(close.iloc[-1])

        ma5 = float(close.rolling(5).mean().iloc[-1])
        ma10 = float(close.rolling(10).mean().iloc[-1])
        ma20 = float(close.rolling(20).mean().iloc[-1])

        rsi_series = calculate_rsi(close)
        rsi = float(rsi_series.iloc[-1])

        recent_high_20 = float(high.iloc[-20:].max())
        recent_low_20 = float(low.iloc[-20:].min())

        avg_volume_20 = float(volume.rolling(20).mean().iloc[-1])
        today_volume = float(volume.iloc[-1])
        volume_ratio = today_volume / avg_volume_20 if avg_volume_20 > 0 else 0

    except Exception:
        return f"{stock_code} 技術資料計算失敗，請稍後再試。"

    # =========================
    # 基本損益
    # =========================
    profit_pct = ((current_price - buy_price) / buy_price) * 100

    # =========================
    # 基本停損 / 停利
    # =========================
    basic_stop_loss = buy_price * 0.93
    take_profit_1 = buy_price * 1.08
    take_profit_2 = buy_price * 1.15

    # =========================
    # 移動停利
    # 用近20日高點做移動停利基準
    # =========================
    trailing_profit_1 = recent_high_20 * 0.95
    trailing_profit_2 = recent_high_20 * 0.90

    # =========================
    # 均線停損
    # =========================
    ma_stop_loss = ma20

    # =========================
    # RSI 過熱判斷
    # =========================
    if rsi >= 80:
        rsi_status = "過熱，短線容易震盪，不建議追高"
    elif rsi >= 70:
        rsi_status = "偏熱，仍屬強勢，但要留意拉回"
    elif rsi >= 50:
        rsi_status = "強勢區，股價仍有動能"
    elif rsi >= 40:
        rsi_status = "中性偏弱，需觀察是否轉強"
    else:
        rsi_status = "偏弱，暫不適合積極操作"

    # =========================
    # 均線結構
    # =========================
    if current_price > ma5 > ma10 > ma20:
        ma_status = "多頭排列，趨勢偏強"
    elif current_price < ma20:
        ma_status = "跌破20日線，趨勢轉弱"
    elif current_price < ma10:
        ma_status = "跌破10日線，短線轉弱"
    elif current_price < ma5:
        ma_status = "跌破5日線，短線降溫"
    else:
        ma_status = "均線結構普通，需觀察"

    # =========================
    # 量能判斷
    # =========================
    if volume_ratio >= 2:
        volume_status = "爆量，市場關注度高"
    elif volume_ratio >= 1.3:
        volume_status = "放量，動能增加"
    elif volume_ratio >= 0.8:
        volume_status = "量能正常"
    else:
        volume_status = "量縮，追價力道不足"

    # =========================
    # 操作建議
    # =========================
    exit_reasons = []
    hold_reasons = []

    if current_price < basic_stop_loss:
        exit_reasons.append("跌破基本停損")
    if current_price < ma_stop_loss:
        exit_reasons.append("跌破20日均線停損")
    if current_price < trailing_profit_2 and current_price > buy_price:
        exit_reasons.append("跌破移動停利2")
    elif current_price < trailing_profit_1 and current_price > buy_price:
        exit_reasons.append("跌破移動停利1，可考慮部分停利")

    if current_price > ma5 > ma10 > ma20:
        hold_reasons.append("均線多頭排列")
    if rsi >= 50 and rsi < 80:
        hold_reasons.append("RSI仍在強勢區")
    if current_price >= take_profit_1:
        hold_reasons.append("已達停利目標1，啟動移動停利")
    if current_price >= take_profit_2:
        hold_reasons.append("已達停利目標2，建議嚴格執行移動停利")

    if len(exit_reasons) > 0:
        suggestion = "出場 / 減碼"
        suggestion_detail = "、".join(exit_reasons)
    elif rsi >= 80 and current_price < ma5:
        suggestion = "部分停利"
        suggestion_detail = "RSI過熱後跌破5日線，短線可能拉回"
    elif len(hold_reasons) > 0:
        suggestion = "續抱"
        suggestion_detail = "、".join(hold_reasons)
    else:
        suggestion = "觀察"
        suggestion_detail = "尚未出現明確續抱或出場訊號"

    # =========================
    # 回覆文字
    # =========================
    reply = f"""LINE股票機器人 V2 實戰版

股票：{stock_code}
買入價：{buy_price:.2f}
目前價：{current_price:.2f}
目前損益：約 {profit_pct:.2f}%

【RSI過熱判斷】
RSI：{rsi:.2f}
狀態：{rsi_status}

【均線狀態】
MA5：{ma5:.2f}
MA10：{ma10:.2f}
MA20：{ma20:.2f}
判斷：{ma_status}

【量能狀態】
量比：約 {volume_ratio:.2f}
判斷：{volume_status}

【停損】
基本停損：{basic_stop_loss:.2f}
均線停損：{ma_stop_loss:.2f}

【停利】
停利目標1：{take_profit_1:.2f}
停利目標2：{take_profit_2:.2f}

【移動停利】
近20日高點：{recent_high_20:.2f}
移動停利1：{trailing_profit_1:.2f}
移動停利2：{trailing_profit_2:.2f}

【建議】
{suggestion}
原因：{suggestion_detail}

提醒：此為技術分析輔助，不是保證獲利訊號。
"""
    return reply


# =========================
# LINE 回覆
# =========================
def reply_message(reply_token, text):
    if not CHANNEL_ACCESS_TOKEN:
        print("缺少 CHANNEL_ACCESS_TOKEN")
        return

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {CHANNEL_ACCESS_TOKEN}"
    }

    body = {
        "replyToken": reply_token,
        "messages": [
            {
                "type": "text",
                "text": text
            }
        ]
    }

    try:
        response = requests.post(LINE_REPLY_URL, headers=headers, json=body)
        print("LINE reply status:", response.status_code)
        print(response.text)
    except Exception as e:
        print("LINE reply error:", e)


# =========================
# 首頁測試
# =========================
@app.route("/", methods=["GET"])
def home():
    return "LINE股票機器人 V2 實戰版 正常運行中"


# =========================
# LINE Webhook
# =========================
@app.route("/callback", methods=["POST"])
def callback():
    body = request.get_json()

    try:
        events = body.get("events", [])

        for event in events:
            if event.get("type") != "message":
                continue

            if event.get("message", {}).get("type") != "text":
                continue

            reply_token = event["replyToken"]
            user_text = event["message"]["text"].strip()

            if user_text.lower() in ["help", "說明", "使用說明"]:
                help_text = """LINE股票機器人 V2 實戰版

使用方式：
輸入 股票代碼 買入價

範例：
2330 800
2317 180

若是上櫃股票，請輸入：
6488.TWO 100

回傳內容：
1. RSI過熱判斷
2. 移動停利1 / 移動停利2
3. 均線停損
4. 建議：續抱 / 出場 / 觀察
"""
                reply_message(reply_token, help_text)
                continue

            parts = user_text.replace("，", " ").replace(",", " ").split()

            if len(parts) != 2:
                reply_message(
                    reply_token,
                    "請輸入格式：股票代碼 買入價\n例如：2330 800\n若是上櫃股票：6488.TWO 100"
                )
                continue

            stock_code = parts[0]

            try:
                buy_price = float(parts[1])
            except Exception:
                reply_message(reply_token, "買入價請輸入數字，例如：2330 800")
                continue

            result = analyze_stock(stock_code, buy_price)
            reply_message(reply_token, result)

        return "OK"

    except Exception as e:
        print("callback error:", e)
        return "OK"


# =========================
# 本機測試用
# =========================
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
