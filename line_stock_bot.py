from flask import Flask, request
import os
import requests
import yfinance as yf
import pandas as pd
import math
import time
from datetime import datetime

app = Flask(__name__)

# ============================================================
# LINE股票機器人 V3.3 專業視覺強化版
# 新增重點：
# 1. 加入「今日操作摘要」
# 2. 支撐壓力、停損、停利、移動停利加入圖示
# 3. 趨勢燈號使用 🟢🟡🔴
# 4. 單檔分析順序維持交易決策優先
# 5. 保留股票名稱確認、台股即時價優先、Yahoo備援、防錯價引擎、AI分數、選股
# ============================================================

CHANNEL_ACCESS_TOKEN = os.getenv("CHANNEL_ACCESS_TOKEN")
CHANNEL_SECRET = os.getenv("CHANNEL_SECRET")
LINE_REPLY_URL = "https://api.line.me/v2/bot/message/reply"


# ============================================================
# 台股名稱表：常用電子 / AI / 重電 / 權值股
# ============================================================
STOCK_NAME_MAP = {
    "1101": "台泥", "1102": "亞泥", "1216": "統一",
    "1301": "台塑", "1303": "南亞", "1326": "台化",
    "1402": "遠東新", "1504": "東元", "1513": "中興電",
    "1514": "亞力", "1605": "華新", "1609": "大亞",
    "2002": "中鋼", "2207": "和泰車",
    "2301": "光寶科", "2303": "聯電", "2308": "台達電",
    "2317": "鴻海", "2324": "仁寶", "2327": "國巨",
    "2330": "台積電", "2344": "華邦電", "2345": "智邦",
    "2353": "宏碁", "2354": "鴻準", "2356": "英業達",
    "2357": "華碩", "2368": "金像電", "2379": "瑞昱",
    "2382": "廣達", "2383": "台光電", "2395": "研華",
    "2408": "南亞科", "2409": "友達", "2412": "中華電",
    "2449": "京元電子", "2454": "聯發科", "2474": "可成",
    "2603": "長榮", "2609": "陽明", "2615": "萬海",
    "2880": "華南金", "2881": "富邦金", "2882": "國泰金",
    "2884": "玉山金", "2885": "元大金", "2886": "兆豐金",
    "2891": "中信金", "2892": "第一金",
    "3008": "大立光", "3017": "奇鋐", "3034": "聯詠",
    "3037": "欣興", "3045": "台灣大", "3231": "緯創",
    "3324": "雙鴻", "3443": "創意", "3481": "群創",
    "3653": "健策", "3661": "世芯-KY", "3711": "日月光投控",
    "4938": "和碩", "5269": "祥碩", "5347": "世界",
    "5483": "中美晶", "5871": "中租-KY", "5880": "合庫金",
    "6409": "旭隼", "6415": "矽力*-KY", "6446": "藥華藥",
    "6488": "環球晶", "6669": "緯穎", "8046": "南電",
    "8069": "元太", "8299": "群聯",
}

WATCHLIST = {
    "2330": "台積電", "2317": "鴻海", "2454": "聯發科",
    "2303": "聯電", "2308": "台達電", "2382": "廣達",
    "3231": "緯創", "2356": "英業達", "6669": "緯穎",
    "3711": "日月光投控", "3034": "聯詠", "2379": "瑞昱",
    "2345": "智邦", "2383": "台光電", "2368": "金像電",
    "3324": "雙鴻", "3017": "奇鋐", "3653": "健策",
    "2449": "京元電子", "1513": "中興電", "1514": "亞力",
    "1504": "東元", "1605": "華新", "1609": "大亞",
}


def safe_float(value, default=0.0):
    try:
        if value is None:
            return default
        if isinstance(value, str):
            value = value.replace(",", "").strip()
            if value in ["", "-", "--", "NaN", "nan"]:
                return default
        v = float(value)
        if math.isnan(v) or math.isinf(v):
            return default
        return v
    except Exception:
        return default


def clean_stock_code(stock_code):
    code = stock_code.strip().upper()
    code = code.replace(".TW", "").replace(".TWO", "")
    return code


def get_stock_name(code, realtime_name=""):
    code = clean_stock_code(code)
    if realtime_name and realtime_name.strip():
        return realtime_name.strip()
    return STOCK_NAME_MAP.get(code, "名稱未收錄")


def calculate_rsi(close_series, period=14):
    delta = close_series.diff()
    gain = delta.where(delta > 0, 0)
    loss = -delta.where(delta < 0, 0)
    avg_gain = gain.rolling(window=period).mean()
    avg_loss = loss.rolling(window=period).mean()
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def normalize_yfinance_columns(data):
    if data is None or data.empty:
        return data
    if isinstance(data.columns, pd.MultiIndex):
        data = data.copy()
        data.columns = data.columns.get_level_values(0)
    return data


# ============================================================
# TWSE / OTC 即時價格
# ============================================================
def get_twse_otc_realtime_price(stock_code):
    code = clean_stock_code(stock_code)
    sources = [
        ("上市TWSE", f"tse_{code}.tw"),
        ("上櫃OTC", f"otc_{code}.tw"),
    ]
    headers = {"User-Agent": "Mozilla/5.0"}

    for market_name, ex_ch in sources:
        try:
            url = "https://mis.twse.com.tw/stock/api/getStockInfo.jsp"
            params = {
                "ex_ch": ex_ch,
                "json": "1",
                "delay": "0",
                "_": str(int(time.time() * 1000))
            }
            r = requests.get(url, params=params, headers=headers, timeout=5)
            data = r.json()
            msg_array = data.get("msgArray", [])
            if not msg_array:
                continue

            item = msg_array[0]
            current_price = safe_float(item.get("z"), 0)
            previous_close = safe_float(item.get("y"), 0)
            open_price = safe_float(item.get("o"), 0)
            high_price = safe_float(item.get("h"), 0)
            low_price = safe_float(item.get("l"), 0)
            name = item.get("n", "")
            time_str = item.get("t", "")

            if current_price <= 0 and previous_close > 0:
                current_price = previous_close

            if current_price > 0:
                return {
                    "success": True,
                    "source": market_name,
                    "symbol": ex_ch,
                    "name": name,
                    "current_price": current_price,
                    "previous_close": previous_close,
                    "open": open_price,
                    "high": high_price,
                    "low": low_price,
                    "time": time_str,
                }

        except Exception as e:
            print(f"{market_name} 即時價格讀取失敗：", e)

    return {
        "success": False,
        "source": "無",
        "symbol": "",
        "name": "",
        "current_price": 0,
        "previous_close": 0,
        "open": 0,
        "high": 0,
        "low": 0,
        "time": "",
    }


# ============================================================
# Yahoo Finance 歷史資料
# ============================================================
def download_yahoo_history(stock_code):
    code = stock_code.strip().upper()
    candidates = []

    if code.endswith(".TW") or code.endswith(".TWO"):
        candidates.append(code)
    else:
        candidates.append(code + ".TW")
        candidates.append(code + ".TWO")

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
            data = normalize_yfinance_columns(data)
            if data is not None and not data.empty and len(data) >= 60:
                return symbol, data

        except Exception as e:
            print(f"Yahoo 下載 {symbol} 失敗：", e)

    return None, None


def patch_history_with_realtime(data, realtime):
    if data is None or data.empty:
        return data
    if not realtime.get("success"):
        return data

    current_price = safe_float(realtime.get("current_price"), 0)
    previous_close = safe_float(realtime.get("previous_close"), 0)
    high_price = safe_float(realtime.get("high"), 0)
    low_price = safe_float(realtime.get("low"), 0)
    open_price = safe_float(realtime.get("open"), 0)

    if current_price <= 0:
        return data

    fixed = data.copy()
    last_idx = fixed.index[-1]

    fixed.loc[last_idx, "Close"] = current_price
    if "Adj Close" in fixed.columns:
        fixed.loc[last_idx, "Adj Close"] = current_price

    if open_price > 0:
        fixed.loc[last_idx, "Open"] = open_price

    if high_price > 0:
        fixed.loc[last_idx, "High"] = max(high_price, current_price)
    else:
        fixed.loc[last_idx, "High"] = max(safe_float(fixed.loc[last_idx, "High"]), current_price)

    if low_price > 0:
        fixed.loc[last_idx, "Low"] = min(low_price, current_price)
    else:
        fixed.loc[last_idx, "Low"] = min(safe_float(fixed.loc[last_idx, "Low"]), current_price)

    if previous_close > 0 and len(fixed) >= 2:
        fixed.loc[fixed.index[-2], "Close"] = previous_close
        if "Adj Close" in fixed.columns:
            fixed.loc[fixed.index[-2], "Adj Close"] = previous_close

    return fixed


def price_guard(current_price, buy_price, realtime, yahoo_price):
    messages = []

    if current_price <= 0:
        return False, "目前價小於等於0，資料異常"

    if buy_price <= 0:
        return False, "買入價小於等於0"

    ratio = current_price / buy_price

    if ratio >= 5:
        return False, "目前價與買入價差距超過5倍，可能資料異常或買入價輸入錯誤"

    if ratio <= 0.2:
        return False, "目前價與買入價差距過大，可能資料異常或買入價輸入錯誤"

    if realtime.get("success"):
        rt_price = safe_float(realtime.get("current_price"), 0)
        if yahoo_price > 0 and rt_price > 0:
            diff_pct = abs(rt_price - yahoo_price) / rt_price * 100
            if diff_pct >= 10:
                messages.append(f"Yahoo價與台股即時價差異約{diff_pct:.1f}%，已優先採用台股即時價")

    if not messages:
        if realtime.get("success"):
            messages.append("通過，優先採用台股即時價")
        else:
            messages.append("通過，目前使用 Yahoo 備援資料")

    return True, "；".join(messages)


def build_operation_summary(trend_light, suggestion, false_break_risk, current_price, pressure_1, support_1, rsi, volume_ratio):
    if "🟢" in trend_light:
        trend_line = "🟢 趨勢偏多"
    elif "🔴" in trend_light:
        trend_line = "🔴 趨勢轉弱"
    else:
        trend_line = "🟡 趨勢觀察"

    if "續抱" in suggestion:
        action_line = "🟢 建議續抱"
    elif "部分停利" in suggestion or "觀察" in suggestion:
        action_line = "🟡 建議觀察 / 控制部位"
    else:
        action_line = "🔴 建議減碼 / 出場觀察"

    if pressure_1 > 0 and current_price >= pressure_1 * 0.98:
        zone_line = "🟡 接近壓力區"
    elif support_1 > 0 and current_price <= support_1 * 1.02:
        zone_line = "🔴 接近支撐防守區"
    else:
        zone_line = "🟢 價格位置正常"

    if rsi >= 80:
        chase_line = "🔴 RSI過熱，不建議追價"
    elif volume_ratio < 0.8:
        chase_line = "🟡 量縮，追價力道不足"
    else:
        chase_line = "🟢 動能尚可"

    risk_line = false_break_risk

    return f"""{trend_line}
{action_line}
{zone_line}
{chase_line}
假突破：{risk_line}"""


def analyze_stock(stock_code, buy_price):
    code = clean_stock_code(stock_code)

    realtime = get_twse_otc_realtime_price(code)
    stock_name = get_stock_name(code, realtime.get("name", ""))
    yahoo_symbol, data = download_yahoo_history(code)

    if data is None or data.empty:
        if realtime.get("success"):
            current_price = safe_float(realtime.get("current_price"))
            previous_close = safe_float(realtime.get("previous_close"))
            daily_change_pct = ((current_price - previous_close) / previous_close) * 100 if previous_close > 0 else 0
            profit_pct = ((current_price - buy_price) / buy_price) * 100

            return f"""LINE股票機器人 V3.3 專業視覺強化版

股票：{code} {stock_name}
即時來源：{realtime.get("source")}
買入價：{buy_price:.2f}
目前價：{current_price:.2f}
今日漲跌：約 {daily_change_pct:.2f}%
目前損益：約 {profit_pct:.2f}%

【提醒】
目前只取得台股即時價格，Yahoo 歷史K線暫時無法取得。
因此本次不產生 RSI / 均線 / 移動停利分析。

請稍後再試一次。
"""

        return (
            f"查不到 {stock_code} 的有效資料。\n\n"
            "可能原因：\n"
            "1. 股票代碼輸入錯誤\n"
            "2. 台股即時資料與 Yahoo 歷史資料都暫時無法取得\n"
            "3. 該股票資料尚未更新\n\n"
            "請確認格式：\n"
            "上市股票：2330 800\n"
            "上櫃股票：6488.TWO 100"
        )

    yahoo_price = safe_float(data["Close"].squeeze().iloc[-1])
    data = patch_history_with_realtime(data, realtime)

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

        rsi = safe_float(calculate_rsi(close).iloc[-1])

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

    ok, guard_message = price_guard(current_price, buy_price, realtime, yahoo_price)

    if not ok:
        return (
            f"LINE股票機器人 V3.3 專業視覺強化版\n\n"
            f"股票：{code} {stock_name}\n"
            f"買入價：{buy_price:.2f}\n"
            f"目前價：{current_price:.2f}\n\n"
            "【防錯價引擎】\n"
            f"{guard_message}\n\n"
            "系統已停止產生停損停利建議，避免用錯誤股價誤判。"
        )

    profit_pct = ((current_price - buy_price) / buy_price) * 100
    daily_change_pct = ((current_price - previous_close) / previous_close) * 100 if previous_close > 0 else 0

    basic_stop_loss = buy_price * 0.93
    take_profit_1 = buy_price * 1.08
    take_profit_2 = buy_price * 1.15
    trailing_profit_1 = recent_high_20 * 0.95
    trailing_profit_2 = recent_high_20 * 0.90
    ma_stop_loss = ma20

    support_1 = max(ma20, recent_low_20)
    support_2 = recent_low_60
    pressure_1 = recent_high_20
    pressure_2 = recent_high_60

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

    if current_price > ma5 > ma10 > ma20:
        ma_status = "多頭排列，趨勢偏強"
        trend_light = "🟢 趨勢偏多"
    elif current_price < ma20:
        ma_status = "跌破20日線，趨勢轉弱"
        trend_light = "🔴 轉弱警戒"
    elif current_price < ma10:
        ma_status = "跌破10日線，短線轉弱"
        trend_light = "🟡 短線降溫"
    elif current_price < ma5:
        ma_status = "跌破5日線，短線降溫"
        trend_light = "🟡 短線降溫"
    else:
        ma_status = "均線結構普通，需觀察"
        trend_light = "🟡 觀察"

    if volume_ratio >= 2:
        volume_status = "爆量，市場關注度高"
    elif volume_ratio >= 1.3:
        volume_status = "放量，動能增加"
    elif volume_ratio >= 0.8:
        volume_status = "量能正常"
    else:
        volume_status = "量縮，追價力道不足"

    false_break_risk = "🟢 低"
    if current_price >= recent_high_20 * 0.98 and volume_ratio < 1:
        false_break_risk = "🔴 高：接近20日高點但量能不足"
    elif current_price >= recent_high_20 * 0.95 and volume_ratio < 1.3:
        false_break_risk = "🟡 中：接近高點但量能未明顯放大"
    elif rsi >= 80 and volume_ratio < 1:
        false_break_risk = "🟡 中：RSI過熱但量能不足"

    limit_status = "一般區間"
    if daily_change_pct >= 9:
        limit_status = "接近漲停，短線不建議追高"
    elif daily_change_pct <= -9:
        limit_status = "接近跌停，需嚴格控風險"

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
        hold_level = "🟢 A：強勢續抱"
    elif hold_score >= 4:
        hold_level = "🟡 B：偏多續抱"
    elif hold_score >= 2:
        hold_level = "🟡 C：觀察"
    else:
        hold_level = "🔴 D：偏弱"

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
        entry_exit_level = "🟢 強勢偏多，可續抱或小量加碼"
    elif entry_exit_score >= 5:
        entry_exit_level = "🟡 偏多，續抱觀察"
    elif entry_exit_score >= 2:
        entry_exit_level = "🟡 中性，先觀察"
    else:
        entry_exit_level = "🔴 偏弱，避免加碼並留意出場"

    if current_price > ma5 and rsi >= 50 and volume_ratio >= 1:
        short_term_mode = "🟢 短線偏多"
    elif current_price < ma5 or rsi < 50:
        short_term_mode = "🔴 短線轉弱"
    else:
        short_term_mode = "🟡 短線觀察"

    if current_price > ma20 and ma10 > ma20:
        swing_mode = "🟢 波段偏多"
    elif current_price < ma20:
        swing_mode = "🔴 波段轉弱"
    else:
        swing_mode = "🟡 波段觀察"

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
        suggestion = "🔴 出場 / 減碼"
        suggestion_detail = "、".join(exit_reasons)
    elif rsi >= 80 and current_price < ma5:
        suggestion = "🟡 部分停利"
        suggestion_detail = "RSI過熱後跌破5日線，短線可能拉回"
    elif hold_score >= 5 and entry_exit_score >= 5:
        suggestion = "🟢 續抱"
        suggestion_detail = "、".join(keep_reasons) if keep_reasons else "趨勢與分數仍偏多"
    elif hold_score >= 3:
        suggestion = "🟡 觀察 / 小心續抱"
        suggestion_detail = "趨勢尚未完全轉弱，但續抱力道普通"
    else:
        suggestion = "🔴 出場觀察"
        suggestion_detail = "AI分數偏低，技術面轉弱"

    operation_summary = build_operation_summary(
        trend_light,
        suggestion,
        false_break_risk,
        current_price,
        pressure_1,
        support_1,
        rsi,
        volume_ratio
    )

    realtime_source = realtime.get("source") if realtime.get("success") else "Yahoo備援"
    realtime_time = realtime.get("time", "")

    reply = f"""LINE股票機器人 V3.3 專業視覺強化版

股票：{code} {stock_name}
資料來源：{realtime_source}
歷史代碼：{yahoo_symbol}
資料時間：{realtime_time if realtime_time else "依資料源更新"}
買入價：{buy_price:.2f}
目前價：{current_price:.2f}
今日漲跌：約 {daily_change_pct:.2f}%
目前損益：約 {profit_pct:.2f}%

【今日操作摘要】
{operation_summary}

【防錯價引擎】
{guard_message}

【趨勢燈號】
{trend_light}
漲跌停狀態：{limit_status}

【支撐壓力】
🟢 支撐1：約 {support_1:.2f}
🟢 支撐2：約 {support_2:.2f}
🔴 壓力1：約 {pressure_1:.2f}
🔴 壓力2：約 {pressure_2:.2f}

【停損】
🔴 基本停損：{basic_stop_loss:.2f}
🟡 均線停損：{ma_stop_loss:.2f}

【停利】
🟢 停利目標1：{take_profit_1:.2f}
🟢 停利目標2：{take_profit_2:.2f}

【移動停利】
📈 近20日高點：{recent_high_20:.2f}
🟡 移動停利1：{trailing_profit_1:.2f}
🔴 移動停利2：{trailing_profit_2:.2f}

【建議】
{suggestion}
原因：{suggestion_detail}

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

提醒：此為技術分析輔助，不是保證獲利訊號。
"""
    return reply


def score_for_pick(code, name):
    realtime = get_twse_otc_realtime_price(code)
    stock_name = get_stock_name(code, name)
    yahoo_symbol, data = download_yahoo_history(code)

    if data is None or data.empty:
        return None

    data = patch_history_with_realtime(data, realtime)

    try:
        close = data["Close"].squeeze()
        high = data["High"].squeeze()
        volume = data["Volume"].squeeze()

        current_price = safe_float(close.iloc[-1])
        previous_close = safe_float(close.iloc[-2])

        ma5 = safe_float(close.rolling(5).mean().iloc[-1])
        ma10 = safe_float(close.rolling(10).mean().iloc[-1])
        ma20 = safe_float(close.rolling(20).mean().iloc[-1])

        rsi = safe_float(calculate_rsi(close).iloc[-1])
        recent_high_20 = safe_float(high.iloc[-20:].max())

        avg_volume_20 = safe_float(volume.rolling(20).mean().iloc[-1])
        today_volume = safe_float(volume.iloc[-1])
        volume_ratio = today_volume / avg_volume_20 if avg_volume_20 > 0 else 0

        daily_change_pct = ((current_price - previous_close) / previous_close) * 100 if previous_close > 0 else 0

        score = 0
        reasons = []

        if ma5 > ma10 > ma20:
            score += 2
            reasons.append("均線多頭")
        if current_price > ma5:
            score += 1
            reasons.append("站上5日線")
        if daily_change_pct > 0:
            score += 1
            reasons.append("今日上漲")
        if volume_ratio >= 1.3:
            score += 1
            reasons.append("放量")
        if 50 <= rsi <= 75:
            score += 1
            reasons.append("RSI強勢")
        if current_price >= recent_high_20 * 0.98:
            score += 1
            reasons.append("接近20日高點")

        if rsi >= 82:
            score -= 1
            reasons.append("RSI過熱扣分")
        if volume_ratio < 0.7:
            score -= 1
            reasons.append("量縮扣分")

        risk = "🟢 低"
        if current_price >= recent_high_20 * 0.98 and volume_ratio < 1:
            risk = "🔴 高"
        elif current_price >= recent_high_20 * 0.95 and volume_ratio < 1.3:
            risk = "🟡 中"

        return {
            "code": code,
            "name": stock_name,
            "price": current_price,
            "change": daily_change_pct,
            "score": score,
            "rsi": rsi,
            "volume_ratio": volume_ratio,
            "risk": risk,
            "reasons": "、".join(reasons) if reasons else "無明顯訊號"
        }

    except Exception as e:
        print(f"{code} 選股評分失敗：", e)
        return None


def stock_pick_message():
    results = []

    for code, name in WATCHLIST.items():
        item = score_for_pick(code, name)
        if item is not None:
            results.append(item)

    if not results:
        return """LINE股票機器人 V3.3 專業視覺強化版

目前選股資料暫時抓取失敗。
請稍後再輸入：選股
"""

    results = sorted(results, key=lambda x: (x["score"], x["change"]), reverse=True)
    top_results = results[:8]
    today = datetime.now().strftime("%Y-%m-%d %H:%M")

    lines = []
    lines.append("LINE股票機器人 V3.3 專業視覺強化版")
    lines.append("")
    lines.append("【今日AI強勢股觀察】")
    lines.append(f"時間：{today}")
    lines.append("")
    lines.append("說明：分數越高代表技術面越強，但仍需搭配風險控管。")
    lines.append("")

    for idx, item in enumerate(top_results, start=1):
        if item["score"] >= 6:
            rank_light = "🟢"
        elif item["score"] >= 4:
            rank_light = "🟡"
        else:
            rank_light = "🔴"

        lines.append(
            f"{idx}. {rank_light} {item['code']} {item['name']}\n"
            f"價：{item['price']:.2f}｜漲跌：{item['change']:.2f}%\n"
            f"分數：{item['score']}/7｜RSI：{item['rsi']:.1f}｜量比：{item['volume_ratio']:.2f}\n"
            f"假突破風險：{item['risk']}\n"
            f"原因：{item['reasons']}\n"
        )

    lines.append("提醒：此為觀察名單，不是保證獲利訊號。")
    return "\n".join(lines)


def reply_message(reply_token, text):
    if not CHANNEL_ACCESS_TOKEN:
        print("缺少 CHANNEL_ACCESS_TOKEN")
        return

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {CHANNEL_ACCESS_TOKEN}"
    }

    chunks = []
    max_len = 4500

    while len(text) > max_len:
        chunks.append(text[:max_len])
        text = text[max_len:]

    chunks.append(text)

    messages = [{"type": "text", "text": chunk} for chunk in chunks[:5]]

    body = {
        "replyToken": reply_token,
        "messages": messages
    }

    try:
        response = requests.post(LINE_REPLY_URL, headers=headers, json=body)
        print("LINE reply status:", response.status_code)
        print(response.text)
    except Exception as e:
        print("LINE reply error:", e)


@app.route("/", methods=["GET"])
def home():
    return "LINE股票機器人 V3.3 專業視覺強化版 正常運行中"


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
                help_text = """LINE股票機器人 V3.3 專業視覺強化版

使用方式一：
輸入 股票代碼 買入價

範例：
2330 800
2317 180
6488.TWO 100

使用方式二：
輸入：
選股

V3.3重點：
1. 新增今日操作摘要
2. 支撐壓力加入圖示
3. 停損 / 停利 / 移動停利加入圖示
4. 趨勢燈號 🟢🟡🔴
5. 假突破風險視覺化
6. AI強勢股排行加入燈號
"""
                reply_message(reply_token, help_text)
                continue

            if user_text in ["選股", "今日選股", "強勢股", "AI選股"]:
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


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
