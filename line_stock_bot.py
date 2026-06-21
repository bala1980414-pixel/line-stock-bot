# -*- coding: utf-8 -*-
"""
LINE 股票機器人 V4.5.3 Lite FundFlow SmartMoney Final 正式版
用途：部署在 Render，LINE 輸入 0~10 指令回傳族群熱度與選股結果。

Render Start Command：gunicorn line_stock_bot:app
Environment Variables：
- CHANNEL_ACCESS_TOKEN
- CHANNEL_SECRET

版本重點：
1. 沿用 LINE 指令：
   0=族群熱度, 1=選股, 2=選股PCB, 3=選股ABF, 4=選股ASIC, 5=選股記憶體,
   6=選股低軌, 7=選股CoPoS, 8=選股Intel, 9=選股化學, 10=選股矽晶圓
2. 新增主力進貨 TOP5：左倍量 / 低追高 / 提前布局優先
3. 保留波段續強 TOP5、觀察用市場熱門 TOP5
4. 回傳顯示：指令、族群、掃描檔數、資料時間（台灣時間）
5. 股票分析修正：避免「股票代碼 股票代碼」，改顯示「股票代碼 股票名稱」
"""

import os
import re
import time
import hmac
import base64
import hashlib
import traceback
import contextlib
import io
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime
from zoneinfo import ZoneInfo

import requests
import pandas as pd
import yfinance as yf
from flask import Flask, request, abort

app = Flask(__name__)

CHANNEL_ACCESS_TOKEN = os.environ.get("CHANNEL_ACCESS_TOKEN", "")
CHANNEL_SECRET = os.environ.get("CHANNEL_SECRET", "")
LINE_REPLY_URL = "https://api.line.me/v2/bot/message/reply"

# ============================================================
# 股票池設定
# ============================================================
# 說明：股票池先用實戰常見核心名單，後續你測試後可再增減。
# 格式：股票代號.TW / 股票名稱

STOCK_GROUPS = {
    "全部": {
        "2330.TW": "台積電", "2317.TW": "鴻海", "2454.TW": "聯發科", "2303.TW": "聯電",
        "3711.TW": "日月光投控", "2382.TW": "廣達", "3231.TW": "緯創", "2356.TW": "英業達",
        "6669.TW": "緯穎", "3017.TW": "奇鋐", "3324.TW": "雙鴻", "6230.TW": "尼得科超眾",
        "2376.TW": "技嘉", "2357.TW": "華碩", "2383.TW": "台光電", "2368.TW": "金像電",
        "3037.TW": "欣興", "8046.TW": "南電", "3189.TW": "景碩", "2408.TW": "南亞科",
        "2344.TW": "華邦電", "3006.TW": "晶豪科", "3443.TW": "創意", "3661.TW": "世芯-KY",
        "3034.TW": "聯詠", "5269.TW": "祥碩", "3529.TWO": "力旺", "6531.TW": "愛普*",
        "4966.TW": "譜瑞-KY", "3665.TW": "貿聯-KY", "3105.TWO": "穩懋", "8086.TWO": "宏捷科",
        "5483.TWO": "中美晶", "6488.TWO": "環球晶", "3532.TW": "台勝科", "6182.TW": "合晶",
        "3708.TW": "上緯投控", "4739.TW": "康普", "4763.TW": "材料-KY", "4755.TW": "三福化",
        "4721.TW": "美琪瑪", "1513.TW": "中興電", "1609.TW": "大亞", "1504.TW": "東元",
        "1519.TW": "華城", "1605.TW": "華新", "1618.TW": "合機", "6285.TW": "啟碁",
        "2412.TW": "中華電", "4906.TW": "正文", "3596.TW": "智易", "2313.TW": "華通",
        "4958.TW": "臻鼎-KY", "6274.TWO": "台燿", "6213.TW": "聯茂", "3035.TW": "智原",
    },
    "PCB": {
        "2383.TW": "台光電", "2368.TW": "金像電", "3037.TW": "欣興", "8046.TW": "南電",
        "3189.TW": "景碩", "2313.TW": "華通", "4958.TW": "臻鼎-KY", "6274.TWO": "台燿",
        "6213.TW": "聯茂", "5469.TWO": "瀚宇博", "6191.TW": "精成科", "6269.TW": "台郡",
    },
    "ABF": {
        "3037.TW": "欣興", "8046.TW": "南電", "3189.TW": "景碩", "2383.TW": "台光電",
        "2368.TW": "金像電", "6274.TWO": "台燿",
    },
    "ASIC": {
        "3443.TW": "創意", "3661.TW": "世芯-KY", "3035.TW": "智原", "3529.TWO": "力旺",
        "6531.TW": "愛普*", "5269.TW": "祥碩", "3034.TW": "聯詠", "2454.TW": "聯發科",
    },
    "記憶體": {
        "2408.TW": "南亞科", "2344.TW": "華邦電", "3006.TW": "晶豪科", "6531.TW": "愛普*",
        "3260.TWO": "威剛", "8299.TWO": "群聯", "4967.TW": "十銓",
    },
    "低軌衛星": {
        "2412.TW": "中華電", "6285.TW": "啟碁", "4906.TW": "正文", "3596.TW": "智易",
        "3491.TWO": "昇達科", "4977.TWO": "眾達-KY", "3665.TW": "貿聯-KY",
    },
    "CoPoS": {
        "2330.TW": "台積電", "3711.TW": "日月光投控", "2383.TW": "台光電", "2368.TW": "金像電",
        "3037.TW": "欣興", "8046.TW": "南電", "3661.TW": "世芯-KY", "3443.TW": "創意",
    },
    "Intel": {
        "2303.TW": "聯電", "2330.TW": "台積電", "2454.TW": "聯發科", "3035.TW": "智原",
        "2357.TW": "華碩", "2376.TW": "技嘉", "2382.TW": "廣達", "3231.TW": "緯創",
    },
    "化學": {
        "4763.TW": "材料-KY", "4755.TW": "三福化", "4721.TW": "美琪瑪", "4739.TW": "康普",
        "3708.TW": "上緯投控", "1717.TW": "長興", "1723.TW": "中碳", "4749.TWO": "新應材",
    },
    "矽晶圓": {
        "5483.TWO": "中美晶", "6488.TWO": "環球晶", "3532.TW": "台勝科", "6182.TW": "合晶",
        "3016.TW": "嘉晶", "3105.TWO": "穩懋", "8086.TWO": "宏捷科",
    },
}

COMMAND_MAP = {
    "0": ("族群熱度", "族群熱度"),
    "1": ("選股", "全部"),
    "2": ("選股PCB", "PCB"),
    "3": ("選股ABF", "ABF"),
    "4": ("選股ASIC", "ASIC"),
    "5": ("選股記憶體", "記憶體"),
    "6": ("選股低軌", "低軌衛星"),
    "7": ("選股CoPoS", "CoPoS"),
    "8": ("選股Intel", "Intel"),
    "9": ("選股化學", "化學"),
    "10": ("選股矽晶圓", "矽晶圓"),
    "選股": ("選股", "全部"),
    "選股PCB": ("選股PCB", "PCB"),
    "選股ABF": ("選股ABF", "ABF"),
    "選股ASIC": ("選股ASIC", "ASIC"),
    "選股記憶體": ("選股記憶體", "記憶體"),
    "選股低軌": ("選股低軌", "低軌衛星"),
    "選股低軌衛星": ("選股低軌衛星", "低軌衛星"),
    "選股CoPoS": ("選股CoPoS", "CoPoS"),
    "選股Intel": ("選股Intel", "Intel"),
    "選股化學": ("選股化學", "化學"),
    "選股矽晶圓": ("選股矽晶圓", "矽晶圓"),
    "族群熱度": ("族群熱度", "族群熱度"),
}


# 常用台股名稱快取：用於「股票代碼 買入價」避免顯示成 1303 1303。
# 選股股票池沒有收錄的個股，會先查這裡；仍找不到才嘗試 yfinance info。
COMMON_STOCK_NAMES = {
    "1101": "台泥", "1102": "亞泥", "1216": "統一", "1301": "台塑", "1303": "南亞",
    "1326": "台化", "1402": "遠東新", "1476": "儒鴻", "1504": "東元", "1513": "中興電",
    "1519": "華城", "1605": "華新", "1609": "大亞", "1618": "合機", "2002": "中鋼",
    "2207": "和泰車", "2301": "光寶科", "2303": "聯電", "2317": "鴻海", "2327": "國巨",
    "2330": "台積電", "2344": "華邦電", "2356": "英業達", "2357": "華碩", "2368": "金像電",
    "2376": "技嘉", "2379": "瑞昱", "2382": "廣達", "2383": "台光電", "2408": "南亞科",
    "2454": "聯發科", "2603": "長榮", "2609": "陽明", "2615": "萬海", "2881": "富邦金",
    "2882": "國泰金", "2883": "開發金", "2884": "玉山金", "2885": "元大金", "2886": "兆豐金",
    "2891": "中信金", "2892": "第一金", "3006": "晶豪科", "3017": "奇鋐", "3034": "聯詠",
    "3035": "智原", "3037": "欣興", "3062": "建漢", "3189": "景碩", "3231": "緯創",
    "3324": "雙鴻", "3443": "創意", "3529": "力旺", "3532": "台勝科", "3596": "智易",
    "3661": "世芯-KY", "3665": "貿聯-KY", "3711": "日月光投控", "4763": "材料-KY",
    "4906": "正文", "4958": "臻鼎-KY", "4966": "譜瑞-KY", "5269": "祥碩", "6230": "尼得科超眾",
    "6274": "台燿", "6285": "啟碁", "6488": "環球晶", "6531": "愛普*", "6669": "緯穎",
    "8046": "南電", "8299": "群聯",
}

_STOCK_NAME_CACHE = {}
_ANALYSIS_CACHE = {}
_CACHE_TTL_SECONDS = 300

_NEWS_CACHE = {}
_NEWS_CACHE_TTL_SECONDS = 900

GROUP_NEWS_KEYWORDS = {
    "全部": "台股 電子 重電 AI 伺服器 資金",
    "PCB": "台股 PCB ABF 載板 銅箔基板",
    "ABF": "台股 ABF 載板 IC載板",
    "ASIC": "台股 ASIC AI 晶片 IC設計",
    "記憶體": "台股 記憶體 DRAM NAND 南亞科 華邦電",
    "低軌衛星": "台股 低軌衛星 通訊 衛星",
    "CoPoS": "台股 CoWoS CoPoS 先進封裝 AI",
    "Intel": "Intel 台股 供應鏈 半導體",
    "化學": "台股 化學 材料 特化 半導體材料",
    "矽晶圓": "台股 矽晶圓 半導體 中美晶 環球晶",
}

NEWS_BAD_WORDS = [
    "利空", "下修", "砍單", "衰退", "禁令", "制裁", "關稅", "調查", "虧損", "跌停",
    "重挫", "暴跌", "賣壓", "法說保守", "庫存", "需求疲弱", "降評", "減碼", "延後",
]
NEWS_GOOD_WORDS = [
    "利多", "漲價", "訂單", "擴產", "成長", "上修", "旺季", "AI", "伺服器", "需求強",
    "轉強", "買超", "受惠", "突破", "新高", "合作", "接單",
]

# ============================================================
# LINE 基礎功能
# ============================================================

def verify_signature(body: bytes, signature: str) -> bool:
    if not CHANNEL_SECRET:
        return False
    hash_digest = hmac.new(CHANNEL_SECRET.encode("utf-8"), body, hashlib.sha256).digest()
    valid_signature = base64.b64encode(hash_digest).decode("utf-8")
    return hmac.compare_digest(valid_signature, signature or "")


def reply_text(reply_token: str, text: str):
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {CHANNEL_ACCESS_TOKEN}",
    }
    payload = {
        "replyToken": reply_token,
        "messages": [{"type": "text", "text": text[:4900]}],
    }
    requests.post(LINE_REPLY_URL, headers=headers, json=payload, timeout=10)


@app.route("/", methods=["GET"])
def home():
    return "LINE 股票機器人 V4.5.3 Lite FundFlow SmartMoney Final is running."


@app.route("/callback", methods=["POST"])
def callback():
    body = request.get_data()
    signature = request.headers.get("X-Line-Signature", "")

    if CHANNEL_SECRET and not verify_signature(body, signature):
        abort(400)

    try:
        data = request.get_json()
        for event in data.get("events", []):
            if event.get("type") != "message":
                continue
            if event.get("message", {}).get("type") != "text":
                continue
            user_text = event["message"].get("text", "").strip()
            reply_token = event.get("replyToken")
            result = handle_message(user_text)
            reply_text(reply_token, result)
    except Exception:
        traceback.print_exc()
    return "OK"

# ============================================================
# 指標計算
# ============================================================

def normalize_command(text: str):
    t = text.strip()
    t_upper = t.upper().replace(" ", "")

    # 保留 CoPoS 大小寫彈性
    aliases = {
        "選股COPOS": "選股CoPoS",
        "7": "7",
        "選股PCB": "選股PCB",
        "選股ABF": "選股ABF",
        "選股ASIC": "選股ASIC",
        "選股INTEL": "選股Intel",
    }
    if t in COMMAND_MAP:
        return COMMAND_MAP[t]
    if t_upper in aliases and aliases[t_upper] in COMMAND_MAP:
        return COMMAND_MAP[aliases[t_upper]]
    if t_upper in COMMAND_MAP:
        return COMMAND_MAP[t_upper]
    return None


def now_text():
    """Render 預設常是 UTC，這裡固定轉成台灣時間。"""
    try:
        return datetime.now(ZoneInfo("Asia/Taipei")).strftime("%Y-%m-%d %H:%M（台灣時間）")
    except Exception:
        return datetime.utcnow().replace(tzinfo=ZoneInfo("UTC")).astimezone(ZoneInfo("Asia/Taipei")).strftime("%Y-%m-%d %H:%M（台灣時間）")


def safe_float(x, default=0.0):
    try:
        if pd.isna(x):
            return default
        return float(x)
    except Exception:
        return default


def fetch_stock_df(ticker: str, retries: int = 1):
    """抓 Yahoo Finance 日線資料。
    V4.5.1 修正：Render Logs 不再大量印出 Yahoo delisted 紅字，並縮短 0 族群熱度等待時間。
    """
    for i in range(retries):
        try:
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
                df = yf.download(
                    ticker,
                    period="4mo",
                    interval="1d",
                    auto_adjust=False,
                    progress=False,
                    threads=False,
                    timeout=8,
                )
            if df is not None and len(df) >= 35:
                if isinstance(df.columns, pd.MultiIndex):
                    df.columns = [c[0] for c in df.columns]
                df = df.dropna()
                if len(df) >= 35:
                    return df
        except Exception:
            time.sleep(0.15 + i * 0.15)
    return None

def calc_rsi(close: pd.Series, period: int = 14):
    delta = close.diff()
    gain = delta.where(delta > 0, 0).rolling(period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(period).mean()
    rs = gain / loss.replace(0, pd.NA)
    rsi = 100 - (100 / (1 + rs))
    return rsi


def analyze_one(ticker: str, name: str):
    cache_key = ticker
    now_ts = time.time()
    cached = _ANALYSIS_CACHE.get(cache_key)
    if cached and now_ts - cached.get('ts', 0) < _CACHE_TTL_SECONDS:
        row = dict(cached['row'])
        row['name'] = name
        return row

    df = fetch_stock_df(ticker)
    if df is None or len(df) < 35:
        return None

    close = df["Close"]
    open_ = df["Open"]
    high = df["High"]
    low = df["Low"]
    vol = df["Volume"]

    ma5 = close.rolling(5).mean()
    ma10 = close.rolling(10).mean()
    ma20 = close.rolling(20).mean()
    ma60 = close.rolling(60).mean()
    rsi = calc_rsi(close)
    vol5 = vol.rolling(5).mean()
    vol20 = vol.rolling(20).mean()
    high20_prev = high.shift(1).rolling(20).max()

    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    macd = ema12 - ema26
    signal = macd.ewm(span=9, adjust=False).mean()

    # KD 指標
    low9 = low.rolling(9).min()
    high9 = high.rolling(9).max()
    rsv = (close - low9) / (high9 - low9).replace(0, pd.NA) * 100
    k = rsv.ewm(com=2, adjust=False).mean()
    d = k.ewm(com=2, adjust=False).mean()

    i = -1
    c = safe_float(close.iloc[i])
    o = safe_float(open_.iloc[i])
    h = safe_float(high.iloc[i])
    l = safe_float(low.iloc[i])
    v = safe_float(vol.iloc[i])
    prev_c = safe_float(close.iloc[i - 1])
    today_pct = ((c - prev_c) / prev_c * 100) if prev_c else 0
    amp_pct = ((h - l) / c * 100) if c else 0
    upper_shadow = ((h - max(c, o)) / c * 100) if c else 0
    body_up = c >= o

    ma5v = safe_float(ma5.iloc[i])
    ma10v = safe_float(ma10.iloc[i])
    ma20v = safe_float(ma20.iloc[i])
    ma60v = safe_float(ma60.iloc[i])
    r = safe_float(rsi.iloc[i])
    prev_r = safe_float(rsi.iloc[i - 1])
    kv = safe_float(k.iloc[i])
    dv = safe_float(d.iloc[i])
    prev_k = safe_float(k.iloc[i - 1])
    prev_d = safe_float(d.iloc[i - 1])
    vr = (v / safe_float(vol20.iloc[i], 1)) if safe_float(vol20.iloc[i], 0) else 0
    vr5 = (v / safe_float(vol5.iloc[i], 1)) if safe_float(vol5.iloc[i], 0) else 0
    bias5 = ((c - ma5v) / ma5v * 100) if ma5v else 0
    bias20 = ((c - ma20v) / ma20v * 100) if ma20v else 0
    high20 = safe_float(high20_prev.iloc[i])
    near_high20 = ((high20 - c) / high20 * 100) if high20 else 999

    yesterday_up = safe_float(close.iloc[i - 1]) >= safe_float(open_.iloc[i - 1])
    two_red = body_up and yesterday_up

    price_volume_good = bool(today_pct > 0 and (vr >= 1.20 or vr5 >= 1.15) and body_up)
    volume_start = bool(vr >= 1.25 or vr5 >= 1.20)
    early_position = bool((bias20 <= 8) and (r <= 72) and (today_pct <= 6.5))
    left_volume = bool(volume_start and early_position and body_up and two_red)

    # 假突破 2.0：必須站上前 20 日高點 1%，避免擦邊突破。
    breakout = bool(c > high20 * 1.01) if high20 else False
    near_breakout = bool(near_high20 <= 6 or breakout)
    ma_bull = bool(ma5v > ma10v > ma20v)
    macd_bull = bool(safe_float(macd.iloc[i]) > safe_float(signal.iloc[i]))
    macd_turn = bool(safe_float(macd.iloc[i]) > safe_float(macd.iloc[i - 1]))
    kd_up = bool(kv > prev_k and kv >= dv)
    rsi_up = bool(r > prev_r and r >= 50)
    above_ma20 = bool(c >= ma20v) if ma20v else False
    not_overheat = bool(r < 78 and bias5 < 9 and today_pct < 7.5)
    black_volume = bool((c < o) and (today_pct < 0) and (vr >= 1.5))

    # Smart Money Score：重點放在左倍量、價漲量增、KD/RSI 方向與站上趨勢。滿分 10 分。
    smart_score = 0
    smart_score += 3 if left_volume else 0
    smart_score += 2 if price_volume_good else 0
    smart_score += 1 if kd_up else 0
    smart_score += 1 if rsi_up else 0
    smart_score += 1 if above_ma20 else 0
    smart_score += 1 if (macd_bull or macd_turn) else 0
    smart_score += 1 if (50 <= r <= 72 and bias5 <= 7) else 0

    score = 0
    score += 2 if left_volume else 0
    score += 1 if price_volume_good else 0
    score += 1 if ma5v > ma10v else 0
    score += 1 if ma_bull else 0
    score += 1 if today_pct > 0 else 0
    score += 1 if 55 <= r <= 72 else 0
    score += 1 if macd_bull or macd_turn else 0
    score += 1 if vr >= 1.3 else 0
    score += 1 if near_breakout else 0
    score -= 2 if r >= 80 else 0
    score -= 1 if bias5 >= 10 else 0
    score -= 1 if upper_shadow >= 3.5 else 0
    score -= 2 if black_volume else 0

    risk_points = 0
    risk_reasons = []
    if r >= 85:
        risk_points += 3
        risk_reasons.append("RSI極度過熱")
    elif r >= 78:
        risk_points += 2
        risk_reasons.append("RSI過熱")
    elif r >= 74:
        risk_points += 1
        risk_reasons.append("RSI偏高")
    if bias5 >= 10:
        risk_points += 2
        risk_reasons.append("離5MA過遠")
    elif bias5 >= 7:
        risk_points += 1
        risk_reasons.append("短線乖離偏大")
    if upper_shadow >= 3.5:
        risk_points += 1
        risk_reasons.append("上影線偏長")
    if today_pct >= 7:
        risk_points += 1
        risk_reasons.append("單日漲幅偏大")
    if black_volume:
        risk_points += 3
        risk_reasons.append("爆量長黑")
    if ma20v and c < ma20v:
        risk_points += 1
        risk_reasons.append("跌破MA20")

    if risk_points >= 3:
        risk = "高"
        risk_label = "🔴高風險"
    elif risk_points >= 1:
        risk = "中"
        risk_label = "🟡中風險"
    else:
        risk = "低"
        risk_label = "🟢低風險"

    if left_volume and smart_score >= 6:
        signal_text = "主力左倍量"
    elif left_volume:
        signal_text = "左倍量觀察"
    elif breakout and not_overheat:
        signal_text = "有效突破"
    elif volume_start and today_pct > 0:
        signal_text = "量能轉強"
    elif ma_bull and kd_up and rsi_up:
        signal_text = "波段續強"
    elif ma_bull and today_pct > 0:
        signal_text = "多頭續強"
    else:
        signal_text = "觀察"

    kd_text = "KD↑" if kd_up else "KD→"
    rsi_text = "RSI↑" if rsi_up else "RSI→"
    entry_suggestion = "可進場觀察" if (smart_score >= 7 and risk == "低") else ("等拉回確認" if risk != "高" and smart_score >= 5 else "暫不追價")

    row = {
        "ticker": ticker.replace(".TW", "").replace(".TWO", ""),
        "raw_ticker": ticker,
        "name": name,
        "close": c,
        "today_pct": today_pct,
        "rsi": r,
        "vol_ratio": vr,
        "left_vol_ratio": max(vr, vr5),
        "ma5": ma5v,
        "ma10": ma10v,
        "ma20": ma20v,
        "bias5": bias5,
        "bias20": bias20,
        "score": score,
        "smart_score": int(smart_score),
        "risk": risk,
        "risk_label": risk_label,
        "risk_reasons": "、".join(risk_reasons) if risk_reasons else "健康",
        "left_volume": left_volume,
        "price_volume_good": price_volume_good,
        "ma_bull": ma_bull,
        "breakout": breakout,
        "macd_bull": macd_bull,
        "macd_turn": macd_turn,
        "kd_up": kd_up,
        "rsi_up": rsi_up,
        "kd_text": kd_text,
        "rsi_text": rsi_text,
        "not_overheat": not_overheat,
        "signal": signal_text,
        "entry_suggestion": entry_suggestion,
        "fund_light": "🟡資金觀察",
    }
    _ANALYSIS_CACHE[cache_key] = {"ts": now_ts, "row": dict(row)}
    return row

def scan_group(group_name: str, max_items=None):
    pool = STOCK_GROUPS.get(group_name, STOCK_GROUPS["全部"])
    items = list(pool.items())
    if max_items is not None:
        items = items[:max_items]
    rows = []
    for ticker, name in items:
        try:
            row = analyze_one(ticker, name)
            if row:
                rows.append(row)
        except Exception:
            traceback.print_exc()
        time.sleep(0.08)
    return rows, len(items)


def fetch_google_news_titles(query: str, max_items: int = 5):
    """輕量新聞檢查：使用 Google News RSS，不增加新套件；失敗時自動降級，不影響 LINE 回傳。"""
    cache_key = query
    now_ts = time.time()
    cached = _NEWS_CACHE.get(cache_key)
    if cached and now_ts - cached.get("ts", 0) < _NEWS_CACHE_TTL_SECONDS:
        return list(cached.get("titles", []))

    titles = []
    try:
        q = urllib.parse.quote(query)
        url = f"https://news.google.com/rss/search?q={q}&hl=zh-TW&gl=TW&ceid=TW:zh-Hant"
        resp = requests.get(url, timeout=4, headers={"User-Agent": "Mozilla/5.0"})
        if resp.status_code == 200 and resp.text:
            root = ET.fromstring(resp.content)
            for item in root.findall(".//item")[:max_items]:
                title_el = item.find("title")
                if title_el is not None and title_el.text:
                    title = title_el.text.strip()
                    # Google RSS title 常帶來源，用空白即可保留辨識，不做過度清洗。
                    titles.append(title[:80])
    except Exception:
        titles = []

    _NEWS_CACHE[cache_key] = {"ts": now_ts, "titles": list(titles)}
    return titles


def make_news_signal(group_name: str):
    query = GROUP_NEWS_KEYWORDS.get(group_name, f"台股 {group_name}")
    titles = fetch_google_news_titles(query, max_items=5)
    if not titles:
        return {
            "label": "🟡新聞觀察",
            "score": 0,
            "summary": "暫時抓不到即時新聞，先以技術面與資金燈號判斷。",
            "titles": [],
        }

    bad_hits = []
    good_hits = []
    joined_titles = " ".join(titles)
    for w in NEWS_BAD_WORDS:
        if w in joined_titles:
            bad_hits.append(w)
    for w in NEWS_GOOD_WORDS:
        if w in joined_titles:
            good_hits.append(w)

    if len(bad_hits) >= 2 and len(bad_hits) > len(good_hits):
        label = "🔴新聞利空"
        score = -2
    elif len(bad_hits) >= 1 and len(bad_hits) >= len(good_hits):
        label = "🟡新聞警戒"
        score = -1
    elif len(good_hits) >= 2 and len(good_hits) > len(bad_hits):
        label = "🟢新聞偏多"
        score = 1
    else:
        label = "🟡新聞觀察"
        score = 0

    key_text = "、".join((bad_hits[:3] if bad_hits else good_hits[:3])) or "無明顯關鍵字"
    summary = f"{label}｜關鍵字：{key_text}"
    return {"label": label, "score": score, "summary": summary, "titles": titles[:2]}


def calc_fund_signal(row: dict, news_score: int = 0):
    """V4.5.3 Lite 資金流：不接外部法人資料，改用量價、Smart Score、KD/RSI、風險與新聞綜合推估。

    回傳：label / score / note
    score 以 0~10 表示，滿分不常出現，方便實戰區分強弱。
    """
    smart = float(row.get("smart_score", 0) or 0)
    risk = row.get("risk", "中")
    vol_ratio = float(row.get("vol_ratio", 0) or 0)
    left_vol_ratio = float(row.get("left_vol_ratio", vol_ratio) or 0)
    today_pct = float(row.get("today_pct", 0) or 0)
    bias5 = float(row.get("bias5", 0) or 0)
    rsi = float(row.get("rsi", 0) or 0)
    kd_up = bool(row.get("kd_up", False))
    rsi_up = bool(row.get("rsi_up", False))
    left_volume = bool(row.get("left_volume", False))
    price_volume_good = bool(row.get("price_volume_good", False))
    ma_bull = bool(row.get("ma_bull", False))
    breakout = bool(row.get("breakout", False))

    score = 0.0
    reasons = []

    # 核心資金跡象：左倍量與價漲量增權重最高。
    if left_volume:
        score += 2.4
        reasons.append("左倍量")
    if price_volume_good:
        score += 1.8
        reasons.append("價漲量增")
    if vol_ratio >= 1.8 and today_pct > 0:
        score += 1.0
        reasons.append("放量轉強")
    elif vol_ratio >= 1.25 and today_pct > 0:
        score += 0.6
        reasons.append("量能轉強")

    # 趨勢與動能確認。
    if smart >= 8:
        score += 1.2
        reasons.append("主力分高")
    elif smart >= 6:
        score += 0.8
    elif smart >= 4:
        score += 0.4

    if kd_up:
        score += 0.7
        reasons.append("KD向上")
    if rsi_up:
        score += 0.7
        reasons.append("RSI向上")
    if ma_bull:
        score += 0.6
        reasons.append("均線多頭")
    if breakout:
        score += 0.5
        reasons.append("有效突破")

    # 新聞只當加減分，不讓單一新聞完全決定。
    if news_score > 0:
        score += 0.6
        reasons.append("新聞偏多")
    elif news_score < 0:
        score -= 0.9
        reasons.append("新聞警戒")

    # 風險扣分：避免追高與爆量轉弱。
    if risk == "高":
        score -= 2.2
        reasons.append("高風險扣分")
    elif risk == "中":
        score -= 0.7
    if today_pct < 0 and vol_ratio >= 1.2:
        score -= 1.4
        reasons.append("量增價跌")
    if rsi >= 78:
        score -= 0.9
        reasons.append("RSI過熱")
    if bias5 >= 9:
        score -= 0.8
        reasons.append("乖離過大")
    if left_vol_ratio >= 3.0 and today_pct <= 0:
        score -= 1.0
        reasons.append("疑似出貨量")

    score = max(0.0, min(10.0, score))

    if score >= 7.5 and risk != "高" and today_pct > 0:
        label = "🟢強力流入"
    elif score >= 5.5 and risk != "高":
        label = "🟢資金流入"
    elif score >= 3.2:
        label = "🟡資金觀察"
    elif score <= 1.8 or (today_pct < 0 and vol_ratio >= 1.2):
        label = "🔴資金轉弱"
    else:
        label = "🟡資金觀察"

    note = "、".join(reasons[:3]) if reasons else "等待量價確認"
    return {"label": label, "score": score, "note": note}


def calc_fund_light(row: dict, news_score: int = 0):
    """相容舊呼叫：只回傳資金燈號文字。"""
    return calc_fund_signal(row, news_score).get("label", "🟡資金觀察")


def make_group_fund_signal(rows, news_score: int = 0):
    """族群層級資金燈號：用成分股資金分數平均與強勢家數判斷。"""
    if not rows:
        return {"label": "🟡族群資金觀察", "avg_score": 0.0, "strong": 0, "weak": 0}
    signals = [calc_fund_signal(r, news_score) for r in rows]
    avg_score = sum(s["score"] for s in signals) / len(signals)
    strong = sum(1 for s in signals if "流入" in s["label"])
    very_strong = sum(1 for s in signals if "強力" in s["label"])
    weak = sum(1 for s in signals if "轉弱" in s["label"])

    if avg_score >= 5.8 and strong >= max(2, len(rows) * 0.35):
        label = "🟢族群資金流入"
    elif avg_score >= 4.2 or strong > weak:
        label = "🟡族群資金觀察"
    elif weak >= max(2, len(rows) * 0.35):
        label = "🔴族群資金轉弱"
    else:
        label = "🟡族群資金觀察"
    if very_strong >= 2 and weak == 0:
        label = "🟢族群資金強勢"
    return {"label": label, "avg_score": avg_score, "strong": strong, "weak": weak}

# ============================================================
# 回傳格式
# ============================================================

def risk_rank_value(r: dict):
    risk = r.get("risk", "中")
    return {"低": 0, "中": 1, "高": 2}.get(risk, 1)


def fmt_stock_line(idx: int, r: dict, news_score: int = 0):
    fund = calc_fund_signal(r, news_score)
    return (
        f"{idx}. {r['ticker']} {r['name']}\n"
        f"   主力分數：{r.get('smart_score', 0)}/10｜左倍量：{r.get('left_vol_ratio', r.get('vol_ratio', 0)):.2f}倍\n"
        f"   {r.get('kd_text', 'KD→')} {r.get('rsi_text', 'RSI→')}｜漲跌：{r['today_pct']:.2f}%｜RSI：{r['rsi']:.0f}\n"
        f"   資金：{fund['label']}｜資金分數：{fund['score']:.1f}/10\n"
        f"   資金依據：{fund['note']}｜風險：{r.get('risk_label', r.get('risk', '中'))}\n"
        f"   訊號：{r['signal']}｜建議：{r.get('entry_suggestion', '觀察')}"
    )


def make_pick_reply(command_name: str, group_name: str):
    rows, scan_count = scan_group(group_name)
    if not rows:
        return (
            f"【AI選股 V4.5.3 Lite FundFlow SmartMoney Final】\n"
            f"指令：{command_name}｜族群：{group_name}\n"
            f"掃描檔數：{scan_count} 檔\n"
            f"資料時間：{now_text()}\n\n"
            f"目前抓不到足夠資料，可能是 Yahoo Finance 暫時無回應或資料尚未更新。"
        )

    news_signal = make_news_signal(group_name)
    news_score = int(news_signal.get("score", 0))
    group_fund = make_group_fund_signal(rows, news_score)

    # 主力進貨：Smart Money Score + 資金分數優先，仍保留左倍量與低追高精神。
    main_force = [
        r for r in rows
        if r.get("today_pct", 0) > 0
        and r.get("risk") != "高"
        and (r.get("left_volume") or r.get("smart_score", 0) >= 5)
    ]
    main_force = sorted(
        main_force,
        key=lambda x: (
            calc_fund_signal(x, news_score).get("score", 0),
            x.get("smart_score", 0) + news_score,
            x.get("left_volume", False),
            x.get("kd_up", False),
            x.get("rsi_up", False),
            -x.get("bias5", 99),
        ),
        reverse=True,
    )[:5]

    if len(main_force) < 3:
        fallback = [
            r for r in rows
            if r.get("today_pct", 0) > 0
            and r.get("rsi", 99) <= 74
            and r.get("bias5", 99) <= 8
            and r.get("vol_ratio", 0) >= 1.0
            and r.get("risk") != "高"
        ]
        fallback = sorted(fallback, key=lambda x: (x.get("smart_score", 0), x.get("score", 0), x.get("vol_ratio", 0)), reverse=True)
        seen = {r["raw_ticker"] for r in main_force}
        for r in fallback:
            if r["raw_ticker"] not in seen:
                main_force.append(r)
                seen.add(r["raw_ticker"])
            if len(main_force) >= 5:
                break

    swing = [
        r for r in rows
        if r.get("ma_bull")
        and r.get("today_pct", 0) > 0
        and r.get("not_overheat")
        and (r.get("kd_up") or r.get("rsi_up"))
    ]
    swing = sorted(swing, key=lambda x: (calc_fund_signal(x, news_score).get("score", 0), x.get("kd_up", False), x.get("rsi_up", False), x.get("smart_score", 0), x.get("score", 0)), reverse=True)[:5]

    hot = [r for r in rows if r.get("today_pct", 0) > 0 or r.get("vol_ratio", 0) >= 1.2]
    hot = sorted(hot, key=lambda x: (x.get("today_pct", 0), x.get("vol_ratio", 0), calc_fund_signal(x, news_score).get("score", 0), x.get("smart_score", 0)), reverse=True)[:5]

    up_count = sum(1 for r in rows if r["today_pct"] > 0)
    avg_pct = sum(r["today_pct"] for r in rows) / len(rows) if rows else 0
    strong_count = sum(1 for r in rows if r["today_pct"] > 0 and r["vol_ratio"] >= 1.2)

    lines = []
    lines.append("【AI選股 V4.5.3 Lite FundFlow SmartMoney Final】")
    lines.append(f"指令：{command_name}｜族群：{group_name}")
    lines.append(f"掃描檔數：{scan_count} 檔｜成功分析：{len(rows)} 檔")
    lines.append(f"資料時間：{now_text()}")
    lines.append(f"今日族群概況：上漲 {up_count}/{len(rows)} 檔｜平均漲跌 {avg_pct:.2f}%｜量能轉強 {strong_count} 檔")
    lines.append(f"族群資金：{group_fund['label']}｜平均資金 {group_fund['avg_score']:.1f}/10｜流入 {group_fund['strong']}｜轉弱 {group_fund['weak']}")
    lines.append(f"新聞燈號：{news_signal.get('summary', '🟡新聞觀察')}")
    if news_signal.get("titles"):
        for title in news_signal.get("titles", [])[:2]:
            lines.append(f"新聞：{title}")

    lines.append("\n━━━━━━━━━━━━━━")
    lines.append("🔥 主力進貨 TOP5")
    lines.append("（Smart Money／左倍量／提前布局／低追高優先）")
    lines.append("━━━━━━━━━━━━━━")
    if main_force:
        for i, r in enumerate(main_force, 1):
            lines.append(fmt_stock_line(i, r, news_score))
    else:
        lines.append("目前沒有明顯 Smart Money 進貨股，建議先觀察，不硬追。")

    lines.append("\n━━━━━━━━━━━━━━")
    lines.append("🚀 波段續強 TOP5")
    lines.append("（KD↑／RSI↑／健康續強／非過熱）")
    lines.append("━━━━━━━━━━━━━━")
    if swing:
        for i, r in enumerate(swing, 1):
            lines.append(fmt_stock_line(i, r, news_score))
    else:
        lines.append("目前沒有明顯健康續強股。")

    lines.append("\n━━━━━━━━━━━━━━")
    lines.append("🌡 市場熱門 TOP5")
    lines.append("（右倍量／人氣觀察區，不等於建議追價）")
    lines.append("━━━━━━━━━━━━━━")
    if hot:
        for i, r in enumerate(hot, 1):
            lines.append(fmt_stock_line(i, r, news_score))
    else:
        lines.append("目前族群熱度不足。")

    lines.append("\n提醒：市場熱門區主要看人氣與資金流，不代表低風險進場。")
    return "\n".join(lines)


def heat_rank_icon(i: int):
    icons = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]
    if 1 <= i <= len(icons):
        return icons[i - 1]
    return f"{i}."


def make_heat_reply():
    group_items = [
        ("全部", "全電子＋重電", "1"),
        ("PCB", "PCB", "2"),
        ("ABF", "ABF", "3"),
        ("ASIC", "ASIC", "4"),
        ("記憶體", "記憶體", "5"),
        ("低軌衛星", "低軌", "6"),
        ("CoPoS", "CoPoS", "7"),
        ("Intel", "Intel", "8"),
        ("化學", "化學", "9"),
        ("矽晶圓", "矽晶圓", "10"),
    ]
    summaries = []
    for group_name, display_name, cmd_code in group_items:
        rows, scan_count = scan_group(group_name, max_items=8)
        if not rows:
            summaries.append({
                "group": group_name, "display": display_name, "cmd": cmd_code,
                "scan": scan_count, "ok": 0, "up": 0, "avg": -999,
                "strong": 0, "left": 0, "heat_score": -999
            })
            continue
        up = sum(1 for r in rows if r["today_pct"] > 0)
        avg = sum(r["today_pct"] for r in rows) / len(rows)
        strong = sum(1 for r in rows if r["today_pct"] > 0 and r["vol_ratio"] >= 1.2)
        left = sum(1 for r in rows if r.get("left_volume"))
        avg_smart = sum(r.get("smart_score", 0) for r in rows) / len(rows)
        fund_group = make_group_fund_signal(rows, 0)
        avg_fund = fund_group.get("avg_score", 0)
        up_ratio = up / max(len(rows), 1)
        heat_score = max(0, min(8, max(avg, 0) * 0.55 + strong * 0.35 + left * 0.55 + up_ratio * 1.6 + avg_smart * 0.12 + avg_fund * 0.22))
        summaries.append({
            "group": group_name,
            "display": display_name,
            "cmd": cmd_code,
            "scan": scan_count,
            "ok": len(rows),
            "up": up,
            "avg": avg,
            "strong": strong,
            "left": left,
            "avg_smart": avg_smart,
            "avg_fund": avg_fund,
            "fund_label": fund_group.get("label", "🟡族群資金觀察"),
            "heat_score": heat_score,
        })

    summaries = sorted(summaries, key=lambda x: x.get("heat_score", -999), reverse=True)
    lines = []
    lines.append("【AI選股 V4.5.3 Lite FundFlow SmartMoney Final】")
    lines.append("指令：族群熱度｜族群：全部主題")
    lines.append(f"資料時間：{now_text()}")
    lines.append("\n🔥 族群熱度排行")
    for i, s in enumerate(summaries, 1):
        icon = heat_rank_icon(i)
        if s["ok"] == 0:
            lines.append(f"{icon} {s['display']}（{s['cmd']}）｜資料不足｜樣本：{s['scan']}檔")
        else:
            lines.append(
                f"{icon} {s['display']}（{s['cmd']}）｜熱度：{s['heat_score']:.1f}/8｜樣本：{s['ok']}檔"
            )
            lines.append(
                f"   上漲 {s['up']}/{s['ok']}｜平均 {s['avg']:.2f}%｜量能轉強 {s['strong']}｜左倍量 {s.get('left', 0)}"
            )
            lines.append(
                f"   {s.get('fund_label', '🟡族群資金觀察')}｜平均資金 {s.get('avg_fund', 0):.1f}/10"
            )
    lines.append("\n說明：括號內數字為 LINE 指令代碼；熱度已納入量價、左倍量、Smart Money 與資金強度。")
    return "\n".join(lines)


def help_text():
    return (
        "【AI Trading Lab 指令中心】\n\n"
        "0 = 族群熱度\n"
        "1 = 選股\n"
        "2 = 選股PCB\n"
        "3 = 選股ABF\n"
        "4 = 選股ASIC\n"
        "5 = 選股記憶體\n"
        "6 = 選股低軌\n"
        "7 = 選股CoPoS\n"
        "8 = 選股Intel\n"
        "9 = 選股化學\n"
        "10 = 選股矽晶圓\n\n"
        "股票分析：\n"
        "股票代碼 買入價\n"
        "例：2330 800\n\n"
        "V4.5.3 Lite：選股結果新增資金強度分數、族群資金燈號與新聞燈號。"
    )

def handle_message(text: str):
    if text.strip().lower() == "help":
        return help_text()

    cmd = normalize_command(text)
    if cmd:
        command_name, group_name = cmd
        if group_name == "族群熱度":
            return make_heat_reply()
        return make_pick_reply(command_name, group_name)

    # 股票停損停利：格式 2330 800
    m = re.match(r"^(\d{4})\s+(\d+(?:\.\d+)?)$", text.strip())
    if m:
        code = m.group(1)
        buy_price = float(m.group(2))
        return make_price_reply(code, buy_price)

    return help_text()

# ============================================================
# 單股停損停利簡版，保留既有輸入習慣
# ============================================================

def trend_light(row: dict):
    """回傳趨勢燈號文字，盡量沿用前版本語氣。"""
    ma5 = row.get("ma5", 0)
    ma10 = row.get("ma10", 0)
    ma20 = row.get("ma20", 0)
    rsi = row.get("rsi", 0)
    pct = row.get("today_pct", 0)

    if row.get("ma_bull") and pct > 0 and rsi < 78:
        return "綠燈｜多頭續強"
    if ma5 >= ma10 >= ma20 and rsi < 78:
        return "黃燈｜多頭整理"
    if ma5 < ma10 and ma10 < ma20:
        return "紅燈｜空頭偏弱"
    return "黃燈｜整理觀察"


def rsi_status(rsi_value: float):
    if rsi_value >= 80:
        return "過熱警戒"
    if rsi_value >= 70:
        return "偏強但留意過熱"
    if rsi_value >= 55:
        return "中性偏強"
    if rsi_value >= 45:
        return "中性整理"
    return "偏弱整理"


def suggestion_text(row: dict, pnl: float):
    rsi = row.get("rsi", 0)
    risk = row.get("risk", "中")
    ma_bull = row.get("ma_bull", False)

    if risk == "高" or rsi >= 80:
        return "短線偏熱，避免追高，留意分批停利或守停損"
    if pnl <= -8:
        return "接近停損區，先守紀律，勿攤平加碼"
    if pnl >= 8 and rsi >= 70:
        return "已有獲利，建議分批停利並用移動停利保護"
    if ma_bull:
        return "觀察整理，勿急追高"
    return "偏整理觀察，未轉強前不建議加碼"


def get_stock_name_for_code(code: str, raw_ticker: str):
    """取得台股名稱：股票池 → 常用快取 → yfinance info；最後才回傳代碼。"""
    for group in STOCK_GROUPS.values():
        if raw_ticker in group:
            return group[raw_ticker]

    if code in COMMON_STOCK_NAMES:
        return COMMON_STOCK_NAMES[code]

    if code in _STOCK_NAME_CACHE:
        return _STOCK_NAME_CACHE[code]

    # 非選股池個股：嘗試從 yfinance 取名稱。失敗時不影響分析。
    try:
        info = yf.Ticker(raw_ticker).get_info()
        name = info.get("shortName") or info.get("longName") or info.get("displayName")
        if name:
            # 常見英文尾巴簡化，避免 LINE 顯示太長。
            name = str(name).replace(" Co., Ltd.", "").replace(" CO., LTD.", "").strip()
            _STOCK_NAME_CACHE[code] = name
            return name
    except Exception:
        pass

    return code


def make_price_reply(code: str, buy_price: float):
    # 先抓上市 .TW；失敗再抓上櫃 .TWO，保留前版本股票分析輸入習慣。
    ticker = f"{code}.TW"
    name = get_stock_name_for_code(code, ticker)
    row = analyze_one(ticker, name)

    if not row:
        ticker2 = f"{code}.TWO"
        name2 = get_stock_name_for_code(code, ticker2)
        row = analyze_one(ticker2, name2)

    if not row:
        return f"{code} 目前抓不到足夠資料，請稍後再試。"

    # 若分析時名稱仍是代碼，補一次股票名稱，避免顯示「1303 1303」。
    if row.get("name") == code:
        row["name"] = get_stock_name_for_code(code, row.get("raw_ticker", f"{code}.TW"))

    close = row["close"]
    pnl = (close - buy_price) / buy_price * 100 if buy_price else 0
    stop1 = buy_price * 0.90
    take1 = buy_price * 1.10
    take2 = buy_price * 1.18

    ma5 = row.get("ma5", 0)
    ma10 = row.get("ma10", 0)
    ma20 = row.get("ma20", 0)
    moving_take1 = ma5 if ma5 else close
    moving_take2 = ma10 if ma10 else close

    return (
        f"【股票分析】{code} {row['name']}\n\n"
        f"【防錯價引擎】\n"
        f"買入價：{buy_price:.2f}\n"
        f"最新收盤：{close:.2f}\n"
        f"今日漲跌：約 {row['today_pct']:.2f}%\n"
        f"目前損益：約 {pnl:.2f}%\n\n"
        f"【趨勢燈號】\n"
        f"{trend_light(row)}\n\n"
        f"【支撐壓力】\n"
        f"MA5：{ma5:.2f}\n"
        f"MA10：{ma10:.2f}\n"
        f"MA20：{ma20:.2f}\n\n"
        f"【停損】\n"
        f"停損點1：約 {stop1:.2f}（買入價 -10%）\n"
        f"均線支撐停損：約 {ma20:.2f}\n\n"
        f"【停利】\n"
        f"停利點1：約 {take1:.2f}\n"
        f"停利點2：約 {take2:.2f}\n\n"
        f"【移動停利】\n"
        f"移動停利1：約 {moving_take1:.2f}\n"
        f"移動停利2：約 {moving_take2:.2f}\n\n"
        f"【建議】\n"
        f"{suggestion_text(row, pnl)}\n\n"
        f"【RSI過熱判斷】\n"
        f"RSI：{row['rsi']:.1f}｜{rsi_status(row['rsi'])}\n\n"
        f"【均線狀態】\n"
        f"MA5 {ma5:.2f} / MA10 {ma10:.2f} / MA20 {ma20:.2f}\n\n"
        f"【量能狀態】\n"
        f"量比：約 {row['vol_ratio']:.2f}\n\n"
        f"提醒：以上為技術分析輔助，不代表保證獲利。"
    )

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
