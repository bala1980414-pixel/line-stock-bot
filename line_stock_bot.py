from flask import Flask, request
import requests
import yfinance as yf
import pandas as pd

app = Flask(__name__)

# ============================================================
# LINE 設定：請貼你自己的資料
# ============================================================
CHANNEL_ACCESS_TOKEN = "pFTv08zf8YAdzE6GqpRc+uq9a+m70TKWF01+TrOKdstzd+pg1oGpZ8rr82pz0TKQQNDEGkI6fZQsIfoIsBfRRCX9Qme1TJXXLeWGGGJTiCo11vH2uBldaLzhULch7fe9smQMYld8GXt2jiS0HlH3TwdB04t89/1O/w1cDnyilFU="
CHANNEL_SECRET = "162af4488351b3e8d42464e5ff3f03cd"


# ============================================================
# 安全取得單一數值，避免 yfinance Series 錯誤
# ============================================================
def get_value(value):
    try:
        if hasattr(value, "item"):
            return float(value.item())
        return float(value)
    except Exception:
        return None


# ============================================================
# RSI 計算
# ============================================================
def calculate_rsi(close, period=14):
    delta = close.diff()
    gain = delta.where(delta > 0, 0)
    loss = -delta.where(delta < 0, 0)

    avg_gain = gain.rolling(period).mean()
    avg_loss = loss.rolling(period).mean()

    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    return rsi


# ============================================================
# 股票分析核心 V1.1
# ============================================================
def analyze_stock(stock_code, buy_price):
    try:
        stock_code = stock_code.strip()

        if stock_code.endswith(".TW") or stock_code.endswith(".TWO"):
            stock_id = stock_code
            display_code = stock_code.replace(".TW", "").replace(".TWO", "")
        else:
            stock_id = stock_code + ".TW"
            display_code = stock_code

        df = yf.download(stock_id, period="90d", progress=False, auto_adjust=False)

        # .TW 查不到，自動改查 .TWO
        if df.empty and not stock_code.endswith(".TWO"):
            stock_id = stock_code + ".TWO"
            df = yf.download(stock_id, period="90d", progress=False, auto_adjust=False)

        if df.empty:
            return f"查無資料：{stock_code}\n請確認股票代碼，例如：2330 600"

        # 避免多層欄位錯誤
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)

        close = df["Close"]
        volume = df["Volume"]

        current_price = get_value(close.iloc[-1])
        if current_price is None:
            return f"分析錯誤：無法取得 {stock_code} 最新價格"

        current_price = round(current_price, 2)

        # ====================================================
        # 停損停利
        # ====================================================
        stop_loss = round(buy_price * 0.93, 2)
        take_profit1 = round(buy_price * 1.10, 2)
        take_profit2 = round(buy_price * 1.20, 2)
        profit_rate = round((current_price - buy_price) / buy_price * 100, 2)

        # ====================================================
        # 均線
        # ====================================================
        ma5 = close.rolling(5).mean()
        ma10 = close.rolling(10).mean()
        ma20 = close.rolling(20).mean()

        ma5_now = get_value(ma5.iloc[-1])
        ma10_now = get_value(ma10.iloc[-1])
        ma20_now = get_value(ma20.iloc[-1])

        if ma5_now and ma10_now and ma20_now:
            if ma5_now > ma10_now > ma20_now:
                ma_status = "多頭排列"
            elif ma5_now < ma10_now < ma20_now:
                ma_status = "空頭排列"
            else:
                ma_status = "盤整"
        else:
            ma_status = "資料不足"

        # ====================================================
        # RSI
        # ====================================================
        rsi_series = calculate_rsi(close)
        rsi_value = get_value(rsi_series.iloc[-1])

        if rsi_value is None:
            rsi_text = "資料不足"
            rsi_status = "無法判斷"
        else:
            rsi_value = round(rsi_value, 1)

            if rsi_value >= 80:
                rsi_status = "過熱"
            elif rsi_value >= 70:
                rsi_status = "偏強但接近過熱"
            elif rsi_value >= 60:
                rsi_status = "偏強"
            elif rsi_value >= 45:
                rsi_status = "中性"
            elif rsi_value >= 30:
                rsi_status = "偏弱"
            else:
                rsi_status = "弱勢"

            rsi_text = f"{rsi_value}（{rsi_status}）"

        # ====================================================
        # 量比
        # ====================================================
        today_volume = get_value(volume.iloc[-1])
        avg_volume_20 = get_value(volume.rolling(20).mean().iloc[-1])

        if today_volume and avg_volume_20 and avg_volume_20 > 0:
            vol_ratio = round(today_volume / avg_volume_20, 2)

            if vol_ratio >= 2:
                vol_status = "爆量"
            elif vol_ratio >= 1.3:
                vol_status = "量能增溫"
            elif vol_ratio >= 0.8:
                vol_status = "量能正常"
            else:
                vol_status = "量縮"

            vol_text = f"{vol_ratio}（{vol_status}）"
        else:
            vol_ratio = 0
            vol_status = "資料不足"
            vol_text = "資料不足"

        # ====================================================
        # MACD
        # ====================================================
        ema12 = close.ewm(span=12, adjust=False).mean()
        ema26 = close.ewm(span=26, adjust=False).mean()
        macd = ema12 - ema26
        signal = macd.ewm(span=9, adjust=False).mean()

        macd_now = get_value(macd.iloc[-1])
        signal_now = get_value(signal.iloc[-1])

        if macd_now is not None and signal_now is not None:
            if macd_now > signal_now:
                macd_status = "多方"
            else:
                macd_status = "空方"
        else:
            macd_status = "資料不足"

        # ====================================================
        # 假突破風險判斷
        # ====================================================
        risk_points = 0
        risk_reasons = []

        if rsi_value is not None and rsi_value >= 75:
            risk_points += 1
            risk_reasons.append("RSI偏高")

        if vol_ratio >= 2:
            risk_points += 1
            risk_reasons.append("爆量後追高風險")

        if current_price >= take_profit2:
            risk_points += 1
            risk_reasons.append("已達停利2")

        if ma_status != "多頭排列":
            risk_points += 1
            risk_reasons.append("均線未完整多頭")

        if risk_points >= 3:
            false_break_risk = "高"
        elif risk_points == 2:
            false_break_risk = "中"
        else:
            false_break_risk = "低"

        if not risk_reasons:
            risk_reason_text = "技術面暫無明顯過熱訊號"
        else:
            risk_reason_text = "、".join(risk_reasons)

        # ====================================================
        # 操作建議
        # ====================================================
        if current_price <= stop_loss:
            risk = "高風險 ⚠️"
            advice = "已跌破停損點，建議嚴格執行停損。"
        elif current_price < buy_price:
            risk = "中風險"
            advice = "目前低於買入價，先觀察，不建議加碼。"
        elif current_price >= take_profit2:
            risk = "獲利達標 🚀"
            advice = "已達停利2，可考慮分批獲利了結。"
        elif current_price >= take_profit1:
            risk = "獲利中 🎯"
            advice = "已達停利1，可考慮先分批停利。"
        else:
            risk = "低風險"
            advice = "目前仍在正常區間，可續抱觀察。"

        result = f"""📊 股票分析：{display_code}

買入價：{buy_price}
目前價：{current_price}
目前損益：{profit_rate}%

🛑 停損點：{stop_loss}
🎯 停利1：{take_profit1}
🚀 停利2：{take_profit2}

📈 技術狀態
RSI：{rsi_text}
量比：{vol_text}
MACD：{macd_status}
均線：{ma_status}

⚠️ 假突破風險：{false_break_risk}
原因：{risk_reason_text}

📌 風險：{risk}
📌 建議：{advice}

📎 提醒：
以上為程式依技術指標自動分析，
僅供參考，非投資建議。
"""
        return result

    except Exception as e:
        return f"分析錯誤：{str(e)}"


# ============================================================
# LINE 回覆
# ============================================================
def send_reply(reply_token, text):
    url = "https://api.line.me/v2/bot/message/reply"

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {CHANNEL_ACCESS_TOKEN}"
    }

    payload = {
        "replyToken": reply_token,
        "messages": [
            {
                "type": "text",
                "text": text
            }
        ]
    }

    response = requests.post(url, headers=headers, json=payload)
    print("LINE回覆狀態：", response.status_code, response.text)


# ============================================================
# 首頁測試
# ============================================================
@app.route("/", methods=["GET"])
def home():
    return "LINE股票停損停利機器人 V1.1 已啟動"


# ============================================================
# Webhook
# ============================================================
@app.route("/callback", methods=["POST"])
def callback():
    try:
        data = request.get_json()
        print("收到LINE資料：", data)

        events = data.get("events", [])

        for event in events:
            if event.get("type") == "message":
                message = event.get("message", {})
                reply_token = event.get("replyToken")

                if message.get("type") != "text":
                    send_reply(reply_token, "請輸入文字，例如：2330 600")
                    continue

                user_msg = message.get("text", "").strip()
                parts = user_msg.split()

                if len(parts) != 2:
                    reply_text = "格式錯誤\n請輸入：股票代號 買入價\n例如：2330 600"
                else:
                    stock_code = parts[0]

                    try:
                        buy_price = float(parts[1])
                        reply_text = analyze_stock(stock_code, buy_price)
                    except ValueError:
                        reply_text = "買入價格式錯誤\n請輸入數字，例如：2330 600"

                send_reply(reply_token, reply_text)

    except Exception as e:
        print("Webhook錯誤：", e)

    return "OK", 200


# ============================================================
# 啟動
# ============================================================
if __name__ == "__main__":
    print("============================================================")
    print("LINE股票停損停利機器人 V1.1 穩定修正版啟動中...")
    print("本機測試網址：http://127.0.0.1:5000")
    print("Webhook網址：https://你的ngrok網址/callback")
    print("============================================================")
    app.run(host="0.0.0.0", port=5000, debug=True)