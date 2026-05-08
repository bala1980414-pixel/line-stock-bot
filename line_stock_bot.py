from flask import Flask, request
import os
import requests
import yfinance as yf
import pandas as pd
import math

app = Flask(__name__)

# ============================================================
# LINE股票機器人 V2.2 專業穩定版
# 功能：
# 1. RSI過熱判斷
# 2. 移動停利
# 3. 均線停損
# 4. 建議：續抱 / 出場 / 觀察 / 部分停利
# 5. 股價異常保護
# 6. Yahoo Finance 台股錯價倍率自動修正
# 7. 上市 .TW / 上櫃 .TWO 自動嘗試
# 8. AI續抱分數
# 9. AI進出場分數
# 10. 假突破風險
# 11. 支撐壓力位
# 12. 漲停 / 跌停接近判斷
# 13. 短線 / 波段模式
# 14. 輸入：選股 → 回覆功能開發提示
# ============================================================

CHANNEL_ACCESS_TOKEN = os.getenv("CHANNEL_ACCESS_TOKEN")
CHANNEL_SECRET = os.getenv("CHANNEL_SECRET")

LINE_REPLY_URL = "https://api.line.me/v2/bot/message/reply"


# ============================================================
# 工具：安全轉數字
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
# 股票資料下載：自動嘗試 .TW / .TWO
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

            if data is not None and not data.empty and len(data) >= 60:
                return symbol, data

        except Exception as e:
            print(f"下載 {symbol} 失敗：", e)

    return None, None


# ============================================================
# Yahoo Finance 台股錯價倍率自動修正
# 說明：
# 有時台股資料會出現 10倍 / 100倍錯價。
# 此處用「買入價」做合理性基準，只在明顯錯價時修正。
# ============================================================
def auto_fix_price_scale(data, buy_price):
    try:
        close = data["Close"].squeeze()
        last_close = safe_float(close.iloc[-1])

        if last_close <= 0 or buy_price <= 0:
            return data, 1, "未修正"

        ratio = last_close / buy_price

        fix_factor = 1
        fix_note = "未修正"

        # 價格明顯為 10倍
        if 3 <= ratio < 30:
            fix_factor = 10
            fix_note = "偵測到疑似10倍錯價，已自動除以10修正"

        # 價格明顯為 100倍
        elif ratio >= 30:
            fix_factor = 100
            fix_note = "偵測到疑似100倍錯價，已自動除以100修正"

        # 價格明顯為 1/10
        elif 0.03 < ratio <= 0.2:
            fix_factor = 0.1
            fix_note = "偵測到疑似1/10錯價，已自動乘以10修正"

        # 價格明顯為 1/100
        elif ratio <= 0.03:
            fix_factor = 0.01
            fix_note = "偵測到疑似1/100錯價，已自動乘以100修正"

        if fix_factor == 1:
            return data, 1, fix_note

        fixed_data = data.copy()

        price_cols = ["Open", "High", "Low", "Close", "Adj Close"]

        for col in price_cols:
            if col in fixed_data.columns:
                if fix_factor in [10, 100]:
                    fixed_data[col] = fixed_data[col] / fix_factor
                elif fix_factor == 0.1:
                    fixed_data[col] = fixed_data[col] * 10
                elif fix_factor == 0.01:
                    fixed_data[col] = fixed_data[col] * 100

        return fixed_data, fix_factor, fix_note

    except Exception as e:
        print("價格倍率修正失敗：", e)
        return data, 1, "倍率修正失敗，使用原始資料"


# ============================================================
# 最終價格合理性檢查
# ============================================================
def final_price_check(current_price, buy_price):
    if current_price <= 0:
        return False, "目前價小於等於0，資料異常"

    if buy_price <= 0:
        return False, "買入價小於等於0"

    ratio = current_price / buy_price

    # 修正後仍差距過大，停止分析
    if ratio >= 3:
        return False, "修正後目前價仍與買入價差距過大，可能資料異常"
    if ratio <= 0.2:
        return False, "修正後目前價仍與買入價差距過大，可能資料異常"

    return True, "資料合理"


# ============================================================
# 主分析程式
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

    data, scale_factor, scale_note = auto_fix_price_scale(data, buy_price)

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
        recent_high_60 = safe_float(high.iloc[-60:].max())
        recent_low_60 = safe_float(low.iloc[-60:].min())

        avg_volume_20 = safe_float(volume.rolling(20).mean().iloc[-1])
        today_volume = safe_float(volume.iloc[-1])
        volume_ratio = today_volume / avg_volume_20 if avg_volume_20 > 0 else 0

    except Exception as e:
        print("技術資料計算失敗：", e)
        return f"{stock_code} 技術資料計算失敗，請稍後再試。"

    ok, check_message = final_price_check(current_price, buy_price)

    if not ok:
        return (
            f"LINE股票機器人 V2.2 專業穩定版\n\n"
            f"股票：{stock_code}\n"
            f"資料代碼：{symbol}\n"
            f"買入價：{buy_price:.2f}\n"
            f"目前價：{current_price:.2f}\n\n"
            "【資料異常保護】\n"
            f"{check_message}\n\n"
            f"資料修正狀態：{scale_note}\n\n"
            "系統已停止產生停損停利建議，避免用錯誤股價誤判。"
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

    trailing_profit_1 = recent_high_20 * 0.95
    trailing_profit_2 = recent_high_20 * 0.90

    ma_stop_loss = ma20

    # ============================================================
    # 支撐壓力位
    # ============================================================
    support_1 = max(ma20, recent_low_20)
    support_2 = recent_low_60
    pressure_1 = recent_high_20
    pressure_2 = recent_high_60

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
    # 均線與趨勢燈號
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
    # 漲停 / 跌停接近判斷
    # 台股一般漲跌停約10%，此處以近似值輔助判斷
    # ============================================================
    limit_status = "一般區間"

    if daily_change_pct >= 9:
        limit_status = "接近漲停，短線不建議追高"
    elif daily_change_pct <= -9:
        limit_status = "接近跌停，需嚴格控風險"

    # ============================================================
    # AI續抱分數 0~7分
    # ============================================================
    hold_score = 0
    hold_reasons = []

    if current_price > ma5:
        hold_score += 1
        hold_reasons.append("站上5日線")
    if ma5 > ma10:
        hold_score += 1
        hold_reasons.append("5日線大於10日線")
    if ma10 > ma20:
        hold_score += 1
        hold_reasons.append("10日線大於20日線")
    if rsi >= 50:
        hold_score += 1
        hold_reasons.append("RSI強勢")
    if volume_ratio >= 1:
        hold_score += 1
        hold_reasons.append("量能正常以上")
    if current_price > buy_price:
        hold_score += 1
        hold_reasons.append("仍有獲利")
    if current_price >= take_profit_1:
        hold_score += 1
        hold_reasons.append("達停利目標1")

    if hold_score >= 6:
        hold_level = "A：強勢續抱"
    elif hold_score >= 4:
        hold_level = "B：偏多續抱"
    elif hold_score >= 2:
        hold_level = "C：觀察"
    else:
        hold_level = "D：偏弱"

    # ============================================================
    # AI進出場分數 0~10分
    # ============================================================
    entry_exit_score = 0
    entry_exit_reasons = []

    if current_price > ma20:
        entry_exit_score += 1
        entry_exit_reasons.append("站上20日線")
    if ma5 > ma10 > ma20:
        entry_exit_score += 2
        entry_exit_reasons.append("均線多頭")
    if 50 <= rsi <= 75:
        entry_exit_score += 2
        entry_exit_reasons.append("RSI健康強勢")
    elif rsi > 80:
        entry_exit_score -= 1
        entry_exit_reasons.append("RSI過熱扣分")
    if volume_ratio >= 1.3:
        entry_exit_score += 2
        entry_exit_reasons.append("量能放大")
    elif volume_ratio < 0.8:
        entry_exit_score -= 1
        entry_exit_reasons.append("量縮扣分")
    if current_price >= recent_high_20 * 0.98 and volume_ratio >= 1.3:
        entry_exit_score += 2
        entry_exit_reasons.append("接近突破且有量")
    if current_price < ma20:
        entry_exit_score -= 3
        entry_exit_reasons.append("跌破20日線扣分")
    if daily_change_pct <= -5:
        entry_exit_score -= 1
        entry_exit_reasons.append("單日跌幅偏大扣分")

    if entry_exit_score >= 8:
        entry_exit_level = "強勢偏多，可續抱或小量加碼"
    elif entry_exit_score >= 5:
        entry_exit_level = "偏多，續抱觀察"
    elif entry_exit_score >= 2:
        entry_exit_level = "中性，先觀察"
    else:
        entry_exit_level = "偏弱，避免加碼並留意出場"

    # ============================================================
    # 短線 / 波段模式
    # ============================================================
    short_term_mode = "觀察"
    swing_mode = "觀察"

    if current_price > ma5 and rsi >= 50 and volume_ratio >= 1:
        short_term_mode = "短線偏多"
    elif current_price < ma5 or rsi < 50:
        short_term_mode = "短線轉弱"

    if current_price > ma20 and ma10 > ma20:
        swing_mode = "波段偏多"
    elif current_price < ma20:
        swing_mode = "波段轉弱"

    # ============================================================
    # 建議
    # ============================================================
    exit_reasons = []
    keep_reasons = []

    if current_price < basic_stop_loss:
        exit_reasons.append("跌破基本停損")
    if current_price < ma_stop_loss:
        exit_reasons.append("跌破20日均線停損")
    if current_price > buy_price and current_price < trailing_profit_2:
        exit_reasons.append("跌破移動停利2")
    elif current_price > buy_price and current_price < trailing_profit_1:
        exit_reasons.append("跌破移動停利1，可考慮部分停利")

    if current_price > ma5 > ma10 > ma20:
        keep_reasons.append("均線多頭排列")
    if 50 <= rsi < 80:
        keep_reasons.append("RSI仍在強勢區")
    if current_price >= take_profit_1:
        keep_reasons.append("已達停利目標1，啟動移動停利")
    if current_price >= take_profit_2:
        keep_reasons.append("已達停利目標2，建議嚴格執行移動停利")
    if hold_score >= 5:
        keep_reasons.append("AI續抱分數偏高")

    if len(exit_reasons) > 0:
        suggestion = "出場 / 減碼"
        suggestion_detail = "、".join(exit_reasons)
    elif rsi >= 80 and current_price < ma5:
        suggestion = "部分停利"
        suggestion_detail = "RSI過熱後跌破5日線，短線可能拉回"
    elif hold_score >= 5 and entry_exit_score >= 5:
        suggestion = "續抱"
        suggestion_detail = "、".join(keep_reasons) if keep_reasons else "趨勢與分數仍偏多"
    elif hold_score >= 3:
        suggestion = "觀察 / 小心續抱"
        suggestion_detail = "趨勢尚未完全轉弱，但續抱力道普通"
    else:
        suggestion = "出場觀察"
        suggestion_detail = "AI分數偏低，技術面轉弱"

    # ============================================================
    # 回覆內容
    # ============================================================
    reply = f"""LINE股票機器人 V2.2 專業穩定版

股票：{stock_code}
資料代碼：{symbol}
買入價：{buy_price:.2f}
目前價：{current_price:.2f}
今日漲跌：約 {daily_change_pct:.2f}%
目前損益：約 {profit_pct:.2f}%

【資料穩定檢查】
{scale_note}

【趨勢燈號】
{trend_light}
漲跌停狀態：{limit_status}

【AI續抱分數】
分數：{hold_score}/7
等級：{hold_level}
原因：{ "、".join(hold_reasons) if hold_reasons else "暫無明顯優勢" }

【AI進出場分數】
分數：{entry_exit_score}/10
判斷：{entry_exit_level}
原因：{ "、".join(entry_exit_reasons) if entry_exit_reasons else "暫無明顯訊號" }

【模式判斷】
短線：{short_term_mode}
波段：{swing_mode}

【RSI過熱判斷】
RSI：{rsi:.2f}
狀態：{rsi_status}

【均線狀態】
MA5：{ma5:.2f}
MA10：{ma10:.2f}
MA20：{ma20:.2f}
MA60：{ma60:.2f}
判斷：{ma_status}

【量能狀態】
量比：約 {volume_ratio:.2f}
判斷：{volume_status}

【假突破風險】
{false_break_risk}

【支撐壓力】
支撐1：約 {support_1:.2f}
支撐2：約 {support_2:.2f}
壓力1：約 {pressure_1:.2f}
壓力2：約 {pressure_2:.2f}

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
# 選股功能暫時提示
# ============================================================
def stock_pick_message():
    return """LINE股票機器人 V2.2 專業穩定版

你輸入了：選股

目前 V2.2 已先完成單檔股票專業分析：
1. RSI過熱判斷
2. 移動停利
3. 均線停損
4. 股價錯價修正
5. 假突破風險
6. AI續抱分數
7. AI進出場分數
8. 支撐壓力位

下一階段可串接你的 AI選股主程式 V7.5：
輸入「選股」→ 回傳今日強勢股 / 提前布局股。

目前請先用：
2330 800
2317 180
6488.TWO 100
"""


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
    return "LINE股票機器人 V2.2 專業穩定版 正常運行中"


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
                help_text = """LINE股票機器人 V2.2 專業穩定版

使用方式：
輸入 股票代碼 買入價

範例：
2330 800
2317 180

若是上櫃股票，請輸入：
6488.TWO 100

也可輸入：
選股

回傳內容：
1. RSI過熱判斷
2. 移動停利1 / 移動停利2
3. 均線停損
4. 股價錯價修正
5. 假突破風險
6. AI續抱分數
7. AI進出場分數
8. 支撐壓力位
9. 建議：續抱 / 出場 / 觀察
"""
                reply_message(reply_token, help_text)
                continue

            if user_text in ["選股", "今日選股", "強勢股"]:
                reply_message(reply_token, stock_pick_message())
                continue

            parts = user_text.replace("，", " ").replace(",", " ").split()

            if len(parts) != 2:
                reply_message(
                    reply_token,
                    "請輸入格式：股票代碼 買入價\n例如：2330 800\n若是上櫃股票：6488.TWO 100\n或輸入：選股"
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
