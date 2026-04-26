from flask import Flask, request
import requests
import yfinance as yf
import os
import pandas as pd

app = Flask(__name__)

# =========================
# LINE 環境變數
# Render → Environment 設定
# =========================
CHANNEL_ACCESS_TOKEN = os.environ.get("CHANNEL_ACCESS_TOKEN")
CHANNEL_SECRET = os.environ.get("CHANNEL_SECRET")


# =========================
# 安全取得單一數值
# 解決 yfinance Series 錯誤
# =========================
def get_value(value):
    try:
        if hasattr(value, "item"):
            return float(value.item())
        return float(value)
    except Exception:
        return None


# =========================
# 股票分析
# =========================
def analyze_stock(stock_code, buy_price):
    try:
        stock_code = stock_code.strip().upper()

        if stock_code.endswith(".TW") or stock_code.endswith(".TWO"):
            stock_id = stock_code
        else:
            stock_id = stock_code + ".TW"

        df = yf.download(
            stock_id,
            period="60d",
            progress=False,
            auto_adjust=False
        )

        if df.empty and not stock_code.endswith(".TWO"):
            stock_id = stock_code + ".TWO"
            df = yf.download(
                stock_id,
                period="60d",
                progress=False,
                auto_adjust=False
            )

        if df.empty:
            return f"❌ 找不到股票資料：{stock_code}"

        # 若 yfinance 回傳 MultiIndex，轉成一般欄位
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)

        if len(df) < 20:
            return f"❌ 資料不足，無法分析：{stock_code}"

        price = get_value(df["Close"].iloc[-1])
        volume = get_value(df["Volume"].iloc[-1])

        if price is None:
            return f"❌ 無法取得目前價格：{stock_code}"

        # 停損停利
        stop_loss = round(buy_price * 0.93, 2)
        take_profit1 = round(buy_price * 1.10, 2)
        take_profit2 = round(buy_price * 1.20, 2)
        profit = round((price - buy_price) / buy_price * 100, 2)

        # RSI
        delta = df["Close"].diff()
        gain = delta.where(delta > 0, 0).rolling(14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(14).mean()

        rs = gain / loss
        rsi_value = get_value(100 - (100 / (1 + rs.iloc[-1])))

        if rsi_value is None:
            rsi_text = "資料不足"
            rsi_status = "無法判斷"
        else:
            rsi_value = round(rsi_value, 1)
            if rsi_value >= 80:
                rsi_status = "過熱"
            elif rsi_value >= 60:
                rsi_status = "強勢"
            elif rsi_value >= 40:
                rsi_status = "中性"
            else:
                rsi_status = "偏弱"
            rsi_text = f"{rsi_value}（{rsi_status}）"

        # 量比
        avg_vol5 = get_value(df["Volume"].rolling(5).mean().iloc[-1])
        if volume is not None and avg_vol5 and avg_vol5 > 0:
            vol_ratio = round(volume / avg_vol5, 2)
        else:
            vol_ratio = 0

        if vol_ratio >= 1.5:
            vol_status = "量能放大"
        elif vol_ratio >= 1.0:
            vol_status = "量能正常"
        else:
            vol_status = "量能偏弱"

        # 均線
        ma5 = get_value(df["Close"].rolling(5).mean().iloc[-1])
        ma10 = get_value(df["Close"].rolling(10).mean().iloc[-1])
        ma20 = get_value(df["Close"].rolling(20).mean().iloc[-1])

        if ma5 and ma10 and ma20 and ma5 > ma10 > ma20:
            trend = "多頭排列"
        elif ma5 and ma10 and ma20 and ma5 < ma10 < ma20:
            trend = "空頭排列"
        else:
            trend = "整理"

        # MACD
        ema12 = df["Close"].ewm(span=12, adjust=False).mean()
        ema26 = df["Close"].ewm(span=26, adjust=False).mean()
        macd_line = ema12 - ema26
        signal_line = macd_line.ewm(span=9, adjust=False).mean()

        macd_last = get_value(macd_line.iloc[-1])
        signal_last = get_value(signal_line.iloc[-1])

        if macd_last is not None and signal_last is not None:
            macd_status = "多方" if macd_last > signal_last else "空方"
        else:
            macd_status = "無法判斷"

        # 假突破風險
        risk = "低"
        reasons = []

        if rsi_value is not None and rsi_value >= 80:
            risk = "中"
            reasons.append("RSI偏高")

        if price >= take_profit2:
            risk = "高"
            reasons.append("已達停利2")
        elif price >= take_profit1:
            if risk != "高":
                risk = "中"
            reasons.append("已達停利1")

        if vol_ratio < 0.8:
            if risk == "低":
                risk = "中"
            reasons.append("量能不足")

        if not reasons:
            reasons.append("目前在正常區間")

        reason_text = "、".join(reasons)

        # 建議
        if price <= stop_loss:
            advice = "跌破停損，建議停損或降低持股。"
        elif price >= take_profit2:
            advice = "已達停利2，可考慮分批獲利了結。"
        elif price >= take_profit1:
            advice = "已達停利1，可觀察是否續強。"
        else:
            advice = "目前仍在正常區間，可續抱觀察。"

        return f"""📊 股票分析：{stock_code}

買入價：{buy_price}
目前價：{round(price, 2)}
目前損益：{profit}%

🛑 停損點：{stop_loss}
🎯 停利1：{take_profit1}
🚀 停利2：{take_profit2}

📈 技術狀態
RSI：{rsi_text}
量比：{vol_ratio}（{vol_status}）
MACD：{macd_status}
均線：{trend}

⚠️ 假突破風險：{risk}
原因：{reason_text}

📌 建議：{advice}

📎 提醒：
以上為程式依技術指標自動分析，
僅供參考，非投資建議。
"""

    except Exception as e:
        return f"❌ 發生錯誤：{str(e)}"


# =========================
# LINE Webhook
# =========================
@app.route("/callback", methods=["POST"])
def callback():
    body = request.get_json()

    events = body.get("events", [])

    for event in events:
        if event.get("type") == "message":
            message = event.get("message", {})
            if message.get("type") != "text":
                continue

            text = message.get("text", "").strip()
            reply_token = event.get("replyToken")

            try:
                parts = text.split()

                if len(parts) != 2:
                    result = "請輸入格式：2330 600"
                else:
                    stock_code = parts[0]
                    buy_price = float(parts[1])
                    result = analyze_stock(stock_code, buy_price)

            except Exception as e:
                result = f"❌ 輸入錯誤：{str(e)}\n請輸入格式：2330 600"

            reply_message(reply_token, result)

    return "OK", 200


# =========================
# LINE 回覆
# =========================
def reply_message(reply_token, text):
    if not CHANNEL_ACCESS_TOKEN:
        print("❌ CHANNEL_ACCESS_TOKEN 未設定")
        return

    url = "https://api.line.me/v2/bot/message/reply"

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {CHANNEL_ACCESS_TOKEN}"
    }

    data = {
        "replyToken": reply_token,
        "messages": [
            {
                "type": "text",
                "text": text
            }
        ]
    }

    response = requests.post(url, headers=headers, json=data)

    print("LINE回覆狀態：", response.status_code, response.text)


# =========================
# Render 啟動
# =========================
@app.route("/", methods=["GET", "HEAD"])
def home():
    return "LINE股票停損停利機器人 V1.2 雲端穩定版運作中", 200


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
