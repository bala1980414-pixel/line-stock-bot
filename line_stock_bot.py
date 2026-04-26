from flask import Flask, request, abort
import requests
import yfinance as yf
import os

app = Flask(__name__)

# 👉 改成用環境變數（Render用）
CHANNEL_ACCESS_TOKEN = os.environ.get("CHANNEL_ACCESS_TOKEN")
CHANNEL_SECRET = os.environ.get("CHANNEL_SECRET")

# =========================
# 股票分析
# =========================
def analyze_stock(stock_code, buy_price):
    try:
        stock_code = stock_code.strip()

        if stock_code.endswith(".TW") or stock_code.endswith(".TWO"):
            stock_id = stock_code
        else:
            stock_id = stock_code + ".TW"

        df = yf.download(stock_id, period="30d", progress=False)

        if df.empty and not stock_code.endswith(".TWO"):
            stock_id = stock_code + ".TWO"
            df = yf.download(stock_id, period="30d", progress=False)

        if df.empty:
            return "❌ 找不到股票資料"

        price = float(df["Close"].iloc[-1])

        # 停損停利
        stop_loss = round(buy_price * 0.93, 2)
        take_profit1 = round(buy_price * 1.1, 2)
        take_profit2 = round(buy_price * 1.2, 2)

        profit = round((price - buy_price) / buy_price * 100, 2)

        # RSI
        delta = df["Close"].diff()
        gain = delta.clip(lower=0).rolling(14).mean()
        loss = -delta.clip(upper=0).rolling(14).mean()
        rs = gain / loss
        rsi = round(100 - (100 / (1 + rs.iloc[-1])), 1)

        # 量比（簡化）
        vol_ratio = round(df["Volume"].iloc[-1] / df["Volume"].rolling(5).mean().iloc[-1], 2)

        # MACD（簡化）
        macd = "多方" if price > df["Close"].rolling(20).mean().iloc[-1] else "空方"

        # 均線
        ma5 = df["Close"].rolling(5).mean().iloc[-1]
        ma10 = df["Close"].rolling(10).mean().iloc[-1]
        ma20 = df["Close"].rolling(20).mean().iloc[-1]

        if ma5 > ma10 > ma20:
            trend = "多頭排列"
        else:
            trend = "整理"

        # 風險
        risk = "低"
        reason = "正常區間"

        if rsi > 80:
            risk = "中"
            reason = "RSI過高"

        if price >= take_profit2:
            risk = "高"
            reason = "已達停利2"

        # 建議
        advice = "持有觀察"
        if price >= take_profit2:
            advice = "已達停利2，可考慮分批獲利了結"
        elif price >= take_profit1:
            advice = "接近停利，可注意壓力"
        elif price <= stop_loss:
            advice = "跌破停損，建議停損"

        return f"""📊 股票分析：{stock_code}

買入價：{buy_price}
目前價：{price}
目前損益：{profit}%

🛑 停損點：{stop_loss}
🎯 停利1：{take_profit1}
🚀 停利2：{take_profit2}

📈 技術狀態
RSI：{rsi}
量比：{vol_ratio}
MACD：{macd}
均線：{trend}

⚠️ 風險：{risk}
原因：{reason}

📌 建議：{advice}
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
        if event["type"] == "message":
            text = event["message"]["text"]
            reply_token = event["replyToken"]

            try:
                parts = text.split()
                stock_code = parts[0]
                buy_price = float(parts[1])

                result = analyze_stock(stock_code, buy_price)

            except:
                result = "請輸入格式：2330 600"

            reply_message(reply_token, result)

    return "OK"


# =========================
# 回覆LINE
# =========================
def reply_message(reply_token, text):
    url = "https://api.line.me/v2/bot/message/reply"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {CHANNEL_ACCESS_TOKEN}"
    }
    data = {
        "replyToken": reply_token,
        "messages": [{"type": "text", "text": text}]
    }
    requests.post(url, headers=headers, json=data)


# =========================
# Render 啟動（關鍵）
# =========================
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)