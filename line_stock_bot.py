from flask import Flask, request
import os
import requests
import yfinance as yf
import pandas as pd
import math

app = Flask(__name__)

# ============================================================
# LINE股票機器人 V2.1 穩定修正版
# 功能：
# 1. RSI過熱判斷
# 2. 移動停利
# 3. 均線停損
# 4. 建議：續抱 / 出場 / 觀察 / 部分停利
# 5. 股價異常保護
# 6. 上市 .TW / 上櫃 .TWO 自動嘗試
# 7. AI續抱分數
# 8. 多頭 / 轉弱燈號
# ============================================================

CHANNEL_ACCESS_TOKEN = os.getenv("CHANNEL_ACCESS_TOKEN")
CHANNEL_SECRET = os.getenv("CHANNEL_SECRET")

LINE_REPLY_URL = "https://api.line.me/v2/bot/message/reply"


# ============================================================
# RSI 計算
# ============================================================
def calculate_rsi(close_series, period=14):
    delta = close_series.diff()

    gain = delta.where(delta > 0, 0)
    loss = -delta.where(delta < 0, 0)

    avg_gain = gain.rolling(window=period).mean()
    avg_loss = loss.rolling(window=period).mean()

    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))

    return rsi


# ============================================================
# 安全轉數字
# ============================================================
def safe_float(value, default=0.0):
    try:
        if value is None:
            return default
        v = float(value)
        if math.isnan(v) or math.isinf(v):
            return default
        return v
    except Exception:
        return default


# ============================================================
# 嘗試抓股票資料
# ============================================================
def download_stock_data(stock_code):
    stock_code = stock_code.strip().upper()

    candidates = []

    if stock_code.endswith(".TW") or stock_code.endswith(".TWO"):
        candidates.append(stock_code)
    else:
        candidates.append(stock_code + ".TW")
        candidates.append(stock_code + ".TWO")

    for symbol in candidates:
        try:
            data = yf.download(
                symbol,
                period="6mo",
                interval="1d",
                auto_adjust=False,
                progress=False,
                threads=False
            )

            if data is not None and not data.empty and len(data) >= 30:
                return symbol, data

        except Exception as e:
            print(f"下載 {symbol} 失敗：", e)

    return None, None


# ============================================================
# 股價異常保護
# ============================================================
def is_price_abnormal(current_price, buy_price):
    if current_price <= 0:
        return True, "目前價小於等於0，資料異常"

    if buy_price <= 0:
        return True, "買入價小於等於0"

    ratio = current_price / buy_price

    # 防止 Yahoo Finance 偶發錯抓價格，例如突然放大數倍
    if ratio >= 3:
        return True, "目前價與買入價差距過大，可能是資料源異常"

    if ratio <= 0.2:
        return True, "目前價與買入價差距過大，可能是資料源異常"

    return False, ""


# ============================================================
# 技術分析主程式
# ============================================================
def analyze_stock(stock_code, buy_price):
    symbol, data = download_stock_data(stock_code)

    if data is None or data.empty:
        return (
            f"查不到 {stock_code} 的有效資料。\n\n"
            "可能原因：\n"
            "1. 股票代碼輸入錯誤\n"
            "2. Yahoo Finance 暫時無資料\n"
            "3. 該股票資料尚未更新\n\n"
            "請確認格式：\n"
            "上市股票：2330 800\n"
            "上櫃股票：6488.TWO 100"
        )

    try:
        close = data["Close"].squeeze()
        high = data["High"].squeeze()
        low = data["Low"].squeeze()
        volume = data["Volume"].squeeze()

        current_price = safe_float(close.iloc[-1])
        previous_close = safe_float(close.iloc[-2])

        ma5 = safe_float(close.rolling(5).mean().iloc[-1])
        ma10 = safe_float(close.rolling(10).mean().iloc[-1])
        ma20 = safe_float(close.rolling(20).mean().iloc[-1])
        ma60 = safe_float(close.rolling(60).mean().iloc[-1])

        rsi_series = calculate_rsi(close)
        rsi = safe_float(rsi_series.iloc[-1])

        recent_high_20 = safe_float(high.iloc[-20:].max())
        recent_low_20 = safe_float(low.iloc[-20:].min())

        avg_volume_20 = safe_float(volume.rolling(20).mean().iloc[-1])
        today_volume = safe_float(volume.iloc[-1])
        volume_ratio = today_volume / avg_volume_20 if avg_volume_20 > 0 else 0

    except Exception as e:
        print("技術資料計算失敗：", e)
        return f"{stock_code} 技術資料計算失敗，請稍後再試。"

    abnormal, abnormal_reason = is_price_abnormal(current_price, buy_price)

    if abnormal:
        return (
            f"LINE股票機器人 V2.1 穩定修正版\n\n"
            f"股票：{stock_code}\n"
            f"資料代碼：{symbol}\n"
            f"買入價：{buy_price:.2f}\n"
            f"目前價：{current_price:.2f}\n\n"
            "【資料異常保護】\n"
            f"{abnormal_reason}\n\n"
            "系統已停止產生停損停利建議，避免用錯誤股價誤判。\n"
            "請稍後再查一次，或確認 Yahoo Finance 資料是否正常。"
        )

    # ============================================================
    # 基本損益
    # ============================================================
    profit_pct = ((current_price - buy_price) / buy_price) * 100
    daily_change_pct = ((current_price - previous_close) / previous_close) * 100 if previous_close > 0 else 0

    # ============================================================
    # 停損 / 停利
    # ============================================================
    basic_stop_loss = buy_price * 0.93
    take_profit_1 = buy_price * 1.08
    take_profit_2 = buy_price * 1.15

    # 移動停利：用近20日高點計算
    trailing_profit_1 = recent_high_20 * 0.95
    trailing_profit_2 = recent_high_20 * 0.90

    # 均線停損：保守用 MA20
    ma_stop_loss = ma20

    # ============================================================
    # RSI 狀態
    # ============================================================
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

    # ============================================================
    # 均線狀態
    # ============================================================
    if current_price > ma5 > ma10 > ma20:
        ma_status = "多頭排列，趨勢偏強"
        trend_light = "綠燈：趨勢偏多"
    elif current_price < ma20:
        ma_status = "跌破20日線，趨勢轉弱"
        trend_light = "紅燈：轉弱警戒"
    elif current_price < ma10:
        ma_status = "跌破10日線，短線轉弱"
        trend_light = "黃燈：短線降溫"
    elif current_price < ma5:
        ma_status = "跌破5日線，短線降溫"
        trend_light = "黃燈：短線降溫"
    else:
        ma_status = "均線結構普通，需觀察"
        trend_light = "黃燈：觀察"

    # ============================================================
    # 量能狀態
    # ============================================================
    if volume_ratio >= 2:
        volume_status = "爆量，市場關注度高"
    elif volume_ratio >= 1.3:
        volume_status = "放量，動能增加"
    elif volume_ratio >= 0.8:
        volume_status = "量能正常"
    else:
        volume_status = "量縮，追價力道不足"

    # ============================================================
    # 假突破風險
    # ============================================================
    false_break_risk = "低"

    if current_price >= recent_high_20 * 0.98 and volume_ratio < 1:
        false_break_risk = "高：接近20日高點但量能不足"
    elif current_price >= recent_high_20 * 0.95 and volume_ratio < 1.3:
        false_break_risk = "中：接近高點但量能未明顯放大"
    elif rsi >= 80 and volume_ratio < 1:
        false_break_risk = "中：RSI過熱但量能不足"

    # ============================================================
    # AI續抱分數 0~7分
    # ============================================================
    ai_score = 0
    score_reasons = []

    if current_price > ma5:
        ai_score += 1
        score_reasons.append("站上5日線")
    if ma5 > ma10:
        ai_score += 1
        score_reasons.append("5日線大於10日線")
    if ma10 > ma20:
        ai_score += 1
        score_reasons.append("10日線大於20日線")
    if rsi >= 50:
        ai_score += 1
        score_reasons.append("RSI強勢")
    if volume_ratio >= 1:
        ai_score += 1
        score_reasons.append("量能正常以上")
    if current_price > buy_price:
        ai_score += 1
        score_reasons.append("仍有獲利")
    if current_price >= take_profit_1:
        ai_score += 1
        score_reasons.append("達停利目標1")

    if ai_score >= 6:
        ai_level = "A：強勢續抱"
    elif ai_score >= 4:
        ai_level = "B：偏多續抱"
    elif ai_score >= 2:
        ai_level = "C：觀察"
    else:
        ai_level = "D：偏弱"

    # ============================================================
    # 操作建議
    # ============================================================
    exit_reasons = []
    hold_reasons = []

    if current_price < basic_stop_loss:
        exit_reasons.append("跌破基本停損")
    if current_price < ma_stop_loss:
        exit_reasons.append("跌破20日均線停損")
    if current_price > buy_price and current_price < trailing_profit_2:
        exit_reasons.append("跌破移動停利2")
    elif current_price > buy_price and current_price < trailing_profit_1:
        exit_reasons.append("跌破移動停利1，可考慮部分停利")

    if current_price > ma5 > ma10 > ma20:
        hold_reasons.append("均線多頭排列")
    if 50 <= rsi < 80:
        hold_reasons.append("RSI仍在強勢區")
    if current_price >= take_profit_1:
        hold_reasons.append("已達停利目標1，啟動移動停利")
    if current_price >= take_profit_2:
        hold_reasons.append("已達停利目標2，建議嚴格執行移動停利")
    if ai_score >= 5:
        hold_reasons.append("AI續抱分數偏高")

    if len(exit_reasons) > 0:
        suggestion = "出場 / 減碼"
        suggestion_detail = "、".join(exit_reasons)
    elif rsi >= 80 and current_price < ma5:
        suggestion = "部分停利"
        suggestion_detail = "RSI過熱後跌破5日線，短線可能拉回"
    elif ai_score >= 5 and len(hold_reasons) > 0:
        suggestion = "續抱"
        suggestion_detail = "、".join(hold_reasons)
    elif ai_score >= 3:
        suggestion = "觀察 / 小心續抱"
        suggestion_detail = "趨勢尚未完全轉弱，但續抱力道普通"
    else:
        suggestion = "出場觀察"
        suggestion_detail = "AI續抱分數偏低，技術面轉弱"

    # ============================================================
    # 回覆內容
    # ============================================================
    reply = f"""LINE股票機器人 V2.1 穩定修正版

股票：{stock_code}
資料代碼：{symbol}
買入價：{buy_price:.2f}
目前價：{current_price:.2f}
今日漲跌：約 {daily_change_pct:.2f}%
目前損益：約 {profit_pct:.2f}%

【趨勢燈號】
{trend_light}

【AI續抱分數】
分數：{ai_score}/7
等級：{ai_level}
原因：{ "、".join(score_reasons) if score_reasons else "暫無明顯優勢" }

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

【假突破風險】
{false_break_risk}

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


# ============================================================
# LINE 回覆
# ============================================================
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


# ============================================================
# 首頁測試
# ============================================================
@app.route("/", methods=["GET"])
def home():
    return "LINE股票機器人 V2.1 穩定修正版 正常運行中"


# ============================================================
# LINE Webhook
# ============================================================
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
                help_text = """LINE股票機器人 V2.1 穩定修正版

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
4. 股價異常保護
5. 假突破風險
6. AI續抱分數
7. 建議：續抱 / 出場 / 觀察
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

            if buy_price <= 0:
                reply_message(reply_token, "買入價必須大於0，例如：2330 800")
                continue

            result = analyze_stock(stock_code, buy_price)
            reply_message(reply_token, result)

        return "OK"

    except Exception as e:
        print("callback error:", e)
        return "OK"


# ============================================================
# 本機測試用
# ============================================================
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
