# -*- coding: utf-8 -*-
"""
LINE 股票機器人 V4.5-Pro Smart Money 資金流向版
用途：部署在 Render，LINE 輸入「選股」或族群指令回傳 V4.5 資金流向版 TOP5。
也支援：輸入「股票代碼 買入價」或「2330 800」回傳固定格式股票分析。

Render Start Command：gunicorn line_stock_bot:app
Environment Variables：
- CHANNEL_ACCESS_TOKEN
- CHANNEL_SECRET

重要固定規格：
1. LINE 回傳資料時間一律使用台灣時間 Asia/Taipei。
2. 「股票代碼 買入價」回傳之【股票分析】格式維持使用者習慣版本，不調整區塊順序與欄位名稱。
3. V4.5 選股重點：主力分數、左倍量品質、假突破2.0、風險分級、KD/RSI方向。
"""

import os
import re
import time
import hmac
import base64
import hashlib
import traceback
from datetime import datetime
from typing import Dict, List, Tuple, Optional

import numpy as np
import pandas as pd
import yfinance as yf
from flask import Flask, request, abort
import requests

try:
    import pytz
except Exception:
    pytz = None

app = Flask(__name__)

CHANNEL_ACCESS_TOKEN = os.getenv("CHANNEL_ACCESS_TOKEN", "")
CHANNEL_SECRET = os.getenv("CHANNEL_SECRET", "")
LINE_REPLY_URL = "https://api.line.me/v2/bot/message/reply"

# =========================================================
# V4.5 股票池 / 族群設定
# 可自行擴充，重複代碼會自動略過
# =========================================================
STOCK_GROUPS: Dict[str, List[str]] = {
    "全部電子+重電": [
        "2330", "2317", "2454", "2308", "2382", "3231", "2356", "2357", "2376", "2377",
        "3017", "3324", "3443", "3661", "3711", "4938", "6669", "2383", "3037", "2313",
        "2368", "3035", "8046", "3189", "2367", "2375", "4958", "3533", "4966", "6531",
        "5269", "6223", "6415", "6438", "6515", "6789", "3450", "3105", "3163", "3529",
        "1513", "1514", "1504", "1503", "1605", "1609", "1611", "1612", "1618", "9958"
    ],
    "PCB": ["2313", "2368", "2383", "3037", "3044", "3189", "3324", "4958", "5469", "8046"],
    "ABF": ["3037", "3189", "8046", "4958"],
    "ASIC": ["2330", "2454", "3034", "3443", "3661", "4919", "5274", "6531", "6643", "6789"],
    "記憶體": ["2344", "2408", "3006", "3260", "4967", "5351", "6239", "8299"],
    "低軌": ["2317", "2412", "2498", "3045", "4904", "6285", "3491", "6213"],
    "CoPoS": ["2330", "3017", "3231", "3450", "3661", "3711", "4977", "6669"],
    "Intel": ["2356", "2382", "3231", "4938", "6669"],
    "化學": ["1717", "1722", "1723", "1726", "1735", "4763", "4766", "4770"],
    "矽晶圓": ["2330", "2303", "6488", "3532", "6182", "5483", "3016", "3707", "6770", "8096"],
}

COMMAND_GROUP_MAP = {
    "1": "全部電子+重電", "選股": "全部電子+重電",
    "2": "PCB", "選股PCB": "PCB", "選股pcb": "PCB",
    "3": "ABF", "選股ABF": "ABF", "選股abf": "ABF",
    "4": "ASIC", "選股ASIC": "ASIC", "選股asic": "ASIC",
    "5": "記憶體", "選股記憶體": "記憶體",
    "6": "低軌", "選股低軌": "低軌",
    "7": "CoPoS", "選股CoPoS": "CoPoS", "選股copos": "CoPoS", "選股COPS": "CoPoS",
    "8": "Intel", "選股Intel": "Intel", "選股intel": "Intel",
    "9": "化學", "選股化學": "化學",
    "10": "矽晶圓", "選股矽晶圓": "矽晶圓",
}

STOCK_NAMES = {
    "2330": "台積電", "2317": "鴻海", "2454": "聯發科", "2308": "台達電", "2382": "廣達",
    "3231": "緯創", "2356": "英業達", "2357": "華碩", "2376": "技嘉", "2377": "微星",
    "3017": "奇鋐", "3324": "雙鴻", "3443": "創意", "3661": "世芯-KY", "3711": "日月光投控",
    "4938": "和碩", "6669": "緯穎", "2383": "台光電", "3037": "欣興", "2313": "華通",
    "2368": "金像電", "3035": "智原", "8046": "南電", "3189": "景碩", "4958": "臻鼎-KY",
    "1513": "中興電", "1514": "亞力", "1504": "東元", "1605": "華新", "1609": "大亞",
    "2344": "華邦電", "2408": "南亞科", "6488": "環球晶", "3532": "台勝科", "6182": "合晶",
}

# =========================================================
# 基礎工具
# =========================================================
def taiwan_now_str() -> str:
    if pytz:
        tz = pytz.timezone("Asia/Taipei")
        return datetime.now(tz).strftime("%Y-%m-%d %H:%M:%S")
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S") + "（UTC，請安裝 pytz 修正）"


def unique_codes(codes: List[str]) -> List[str]:
    seen = set()
    out = []
    for code in codes:
        c = str(code).strip().replace(".TW", "").replace(".TWO", "")
        if c and c not in seen:
            seen.add(c)
            out.append(c)
    return out


def yf_symbol(code: str) -> str:
    # 先嘗試上市 .TW，失敗時再由抓取函數試 .TWO
    return f"{code}.TW"


def get_stock_name(code: str) -> str:
    return STOCK_NAMES.get(code, code)


def safe_float(x, default=np.nan):
    try:
        if pd.isna(x):
            return default
        return float(x)
    except Exception:
        return default


def calc_rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.rolling(period).mean()
    avg_loss = loss.rolling(period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def calc_kd(df: pd.DataFrame, n: int = 9) -> Tuple[pd.Series, pd.Series]:
    low_min = df["Low"].rolling(n).min()
    high_max = df["High"].rolling(n).max()
    rsv = (df["Close"] - low_min) / (high_max - low_min).replace(0, np.nan) * 100
    k = rsv.ewm(alpha=1/3, adjust=False).mean()
    d = k.ewm(alpha=1/3, adjust=False).mean()
    return k, d


def fetch_history(code: str, period: str = "90d") -> Optional[pd.DataFrame]:
    for suffix in [".TW", ".TWO"]:
        symbol = f"{code}{suffix}"
        try:
            df = yf.download(symbol, period=period, interval="1d", progress=False, auto_adjust=False, threads=False)
            if df is not None and not df.empty and len(df) >= 25:
                if isinstance(df.columns, pd.MultiIndex):
                    df.columns = [c[0] for c in df.columns]
                return df.dropna(subset=["Close"])
        except Exception:
            continue
    return None


def enrich(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["MA5"] = df["Close"].rolling(5).mean()
    df["MA10"] = df["Close"].rolling(10).mean()
    df["MA20"] = df["Close"].rolling(20).mean()
    df["RSI"] = calc_rsi(df["Close"])
    k, d = calc_kd(df)
    df["K"] = k
    df["D"] = d
    df["VOL5"] = df["Volume"].rolling(5).mean()
    df["VOL20"] = df["Volume"].rolling(20).mean()
    df["20HighPrev"] = df["High"].shift(1).rolling(20).max()
    return df

# =========================================================
# 股票分析：格式固定不變
# =========================================================
def analyze_single_stock(code: str, buy_price: float) -> str:
    df = fetch_history(code, "120d")
    name = get_stock_name(code)
    if df is None or df.empty:
        return f"查無 {code} {name} 資料，可能是代碼錯誤或 Yahoo 暫時無資料。"

    df = enrich(df)
    last = df.iloc[-1]
    prev = df.iloc[-2]

    close = safe_float(last["Close"])
    prev_close = safe_float(prev["Close"])
    ma5 = safe_float(last["MA5"])
    ma10 = safe_float(last["MA10"])
    ma20 = safe_float(last["MA20"])
    rsi = safe_float(last["RSI"])
    vol = safe_float(last["Volume"])
    vol5 = safe_float(last["VOL5"])

    change_pct = ((close - prev_close) / prev_close * 100) if prev_close else 0
    pnl_pct = ((close - buy_price) / buy_price * 100) if buy_price else 0
    volume_ratio = (vol / vol5) if vol5 and not np.isnan(vol5) else np.nan

    if close > ma5 > ma10 > ma20:
        trend = "綠燈｜多頭續強"
    elif close > ma20:
        trend = "黃燈｜多頭整理"
    else:
        trend = "紅燈｜轉弱觀察"

    if rsi >= 80:
        rsi_text = f"RSI：{rsi:.1f}｜過熱"
    elif rsi >= 70:
        rsi_text = f"RSI：{rsi:.1f}｜偏熱"
    elif rsi >= 50:
        rsi_text = f"RSI：{rsi:.1f}｜中性偏強"
    else:
        rsi_text = f"RSI：{rsi:.1f}｜偏弱"

    if pnl_pct > 20 or rsi >= 75:
        advice = "觀察整理，勿急追高"
    elif close > ma20 and rsi >= 50:
        advice = "可續抱觀察，跌破支撐需留意"
    else:
        advice = "轉弱觀察，嚴守停損"

    stop1 = buy_price * 0.90
    support_stop = ma20 if not np.isnan(ma20) else stop1
    tp1 = buy_price * 1.10
    tp2 = buy_price * 1.18

    vol_text = f"量比：約 {volume_ratio:.2f}" if not np.isnan(volume_ratio) else "量比：約 N/A"

    return f"""【股票分析】{code} {name}

【防錯價引擎】
買入價：{buy_price:.2f}
最新收盤：{close:.2f}
今日漲跌：約 {change_pct:.2f}%
目前損益：約 {pnl_pct:.2f}%

【趨勢燈號】
{trend}

【支撐壓力】
MA5：{ma5:.2f}
MA10：{ma10:.2f}
MA20：{ma20:.2f}

【停損】
停損點1：約 {stop1:.2f}（買入價 -10%）
均線支撐停損：約 {support_stop:.2f}

【停利】
停利點1：約 {tp1:.2f}
停利點2：約 {tp2:.2f}

【移動停利】
移動停利1：約 {ma5:.2f}
移動停利2：約 {ma10:.2f}

【建議】
{advice}

【RSI過熱判斷】
{rsi_text}

【均線狀態】
MA5 {ma5:.2f} / MA10 {ma10:.2f} / MA20 {ma20:.2f}

【量能狀態】
{vol_text}


提醒：以上為技術分析輔助，不代表保證獲利。"""

# =========================================================
# V4.5 選股邏輯
# =========================================================
def stock_signal(code: str) -> Optional[Dict]:
    df = fetch_history(code, "120d")
    if df is None or len(df) < 35:
        return None
    df = enrich(df)
    last = df.iloc[-1]
    prev = df.iloc[-2]

    close = safe_float(last["Close"])
    open_ = safe_float(last["Open"])
    high = safe_float(last["High"])
    low = safe_float(last["Low"])
    prev_close = safe_float(prev["Close"])
    volume = safe_float(last["Volume"])
    prev_volume = safe_float(prev["Volume"])
    vol5 = safe_float(last["VOL5"])
    vol20 = safe_float(last["VOL20"])
    ma5 = safe_float(last["MA5"])
    ma10 = safe_float(last["MA10"])
    ma20 = safe_float(last["MA20"])
    rsi = safe_float(last["RSI"])
    prev_rsi = safe_float(prev["RSI"])
    k = safe_float(last["K"])
    d = safe_float(last["D"])
    prev_k = safe_float(prev["K"])
    high20prev = safe_float(last["20HighPrev"])

    if any(np.isnan(x) for x in [close, open_, volume, vol5, vol20, ma5, ma10, ma20, rsi, k, d]):
        return None

    left_volume_ratio = volume / vol20 if vol20 else 0
    right_volume_ratio = volume / vol5 if vol5 else 0
    price_change = (close - prev_close) / prev_close * 100 if prev_close else 0
    body_pct = (close - open_) / open_ * 100 if open_ else 0
    bias20 = (close - ma20) / ma20 * 100 if ma20 else 0

    kd_up = k > d and k > prev_k
    rsi_up = rsi > prev_rsi
    ma_bull = ma5 > ma10 > ma20 and close > ma20
    price_volume_good = close > prev_close and volume > prev_volume
    left_volume_ok = left_volume_ratio >= 1.2 and volume > prev_volume
    right_volume_hot = right_volume_ratio >= 1.5
    breakout_ok = (not np.isnan(high20prev)) and close > high20prev * 1.01
    false_break_risk = (not np.isnan(high20prev)) and high > high20prev and close <= high20prev * 1.01
    long_black = close < open_ and abs(body_pct) >= 3 and left_volume_ratio >= 1.5

    smart_score = 0
    if left_volume_ok:
        smart_score += 3
    if price_volume_good:
        smart_score += 2
    if kd_up:
        smart_score += 1
    if rsi_up:
        smart_score += 1
    if close > ma20:
        smart_score += 1

    if long_black or rsi >= 85 or false_break_risk:
        risk = "🔴高風險"
    elif rsi >= 75 or bias20 >= 12 or body_pct < -1.5:
        risk = "🟡中風險"
    else:
        risk = "🟢低風險"

    if smart_score >= 7 and risk != "🔴高風險":
        entry = "可進場觀察"
    elif smart_score >= 5:
        entry = "等拉回確認"
    else:
        entry = "暫不追價"

    if price_volume_good:
        pv_text = "價漲量增"
    elif volume > prev_volume and close <= prev_close:
        pv_text = "量增價弱"
    else:
        pv_text = "量價觀察"

    return {
        "code": code,
        "name": get_stock_name(code),
        "close": close,
        "smart_score": smart_score,
        "left_ratio": left_volume_ratio,
        "right_ratio": right_volume_ratio,
        "kd_up": kd_up,
        "rsi_up": rsi_up,
        "ma_bull": ma_bull,
        "breakout_ok": breakout_ok,
        "false_break_risk": false_break_risk,
        "risk": risk,
        "entry": entry,
        "pv_text": pv_text,
        "price_change": price_change,
        "rsi": rsi,
        "bias20": bias20,
    }


def fmt_line(item: Dict, mode: str) -> str:
    kd = "KD↑" if item["kd_up"] else "KD→"
    rsi = "RSI↑" if item["rsi_up"] else "RSI→"
    if mode == "smart":
        return (
            f"{item['code']} {item['name']}\n"
            f"主力分數：{item['smart_score']}/8｜左倍量：{item['left_ratio']:.2f}倍\n"
            f"{item['pv_text']}｜{kd} {rsi}｜{item['risk']}\n"
            f"建議：{item['entry']}"
        )
    if mode == "hot":
        return (
            f"{item['code']} {item['name']}\n"
            f"右倍量：{item['right_ratio']:.2f}倍｜漲跌：{item['price_change']:.2f}%\n"
            f"{item['pv_text']}｜{item['risk']}\n"
            f"建議：{item['entry']}"
        )
    return (
        f"{item['code']} {item['name']}\n"
        f"主力分數：{item['smart_score']}/8｜{kd} {rsi}\n"
        f"RSI：{item['rsi']:.1f}｜{item['risk']}\n"
        f"建議：{item['entry']}"
    )


def select_stocks(group_name: str, raw_command: str) -> str:
    codes = unique_codes(STOCK_GROUPS.get(group_name, []))
    results = []
    for code in codes:
        try:
            sig = stock_signal(code)
            if sig:
                results.append(sig)
            time.sleep(0.03)
        except Exception:
            continue

    if not results:
        return f"指令：{raw_command}｜族群：{group_name}\n掃描檔數：0 檔\n資料時間：{taiwan_now_str()}（台灣時間）\n\n目前沒有符合條件股票。"

    smart = [x for x in results if x["left_ratio"] >= 1.2 and x["smart_score"] >= 4 and x["risk"] != "🔴高風險"]
    smart = sorted(smart, key=lambda x: (x["smart_score"], x["left_ratio"]), reverse=True)[:5]

    hot = [x for x in results if x["right_ratio"] >= 1.4 and x["risk"] != "🔴高風險"]
    hot = sorted(hot, key=lambda x: (x["right_ratio"], x["price_change"]), reverse=True)[:5]

    swing = [x for x in results if x["ma_bull"] and x["kd_up"] and x["rsi_up"] and x["risk"] != "🔴高風險"]
    swing = sorted(swing, key=lambda x: (x["smart_score"], -abs(x["bias20"])), reverse=True)[:5]

    lines = [
        f"指令：{raw_command}｜族群：{group_name}",
        f"掃描檔數：{len(codes)} 檔",
        f"資料時間：{taiwan_now_str()}（台灣時間）",
        "",
        "【主力資金流 TOP5（左倍量／提前布局）】"
    ]

    if smart:
        for i, item in enumerate(smart, 1):
            lines.append(f"{i}. {fmt_line(item, 'smart')}")
    else:
        lines.append("目前沒有符合條件股票。")

    lines += ["", "【市場熱門 TOP5（右倍量／人氣股）】"]
    if hot:
        for i, item in enumerate(hot, 1):
            lines.append(f"{i}. {fmt_line(item, 'hot')}")
    else:
        lines.append("目前沒有符合條件股票。")

    lines += ["", "【波段續強 TOP5（健康續強）】"]
    if swing:
        for i, item in enumerate(swing, 1):
            lines.append(f"{i}. {fmt_line(item, 'swing')}")
    else:
        lines.append("目前沒有符合條件股票。")

    lines += [
        "",
        "V4.5說明：主力分數=左倍量+價漲量增+KD/RSI方向+站上MA20。",
        "提醒：以上為技術分析輔助，不代表保證獲利。"
    ]
    return "\n".join(lines)


def group_heat() -> str:
    rows = []
    for group, codes in STOCK_GROUPS.items():
        scores = []
        for code in unique_codes(codes[:15]):
            try:
                sig = stock_signal(code)
                if sig:
                    scores.append(sig["smart_score"])
            except Exception:
                pass
        avg = sum(scores) / len(scores) if scores else 0
        rows.append((group, avg, len(scores)))
    rows.sort(key=lambda x: x[1], reverse=True)
    lines = ["【族群熱度】", f"資料時間：{taiwan_now_str()}（台灣時間）", ""]
    for i, (group, avg, n) in enumerate(rows[:10], 1):
        lines.append(f"{i}. {group}｜熱度：{avg:.1f}/8｜樣本：{n}檔")
    return "\n".join(lines)


def help_text() -> str:
    return """【V4.5 指令說明】
0 = 族群熱度
1 = 選股（全電子 + 重電）
2 = 選股PCB
3 = 選股ABF
4 = 選股ASIC
5 = 選股記憶體
6 = 選股低軌
7 = 選股CoPoS
8 = 選股Intel
9 = 選股化學
10 = 選股矽晶圓

股票分析：
輸入：2330 800
格式：股票代碼 空格 買入價

資料時間皆為台灣時間。"""

# =========================================================
# LINE Webhook
# =========================================================
def verify_signature(body: bytes, signature: str) -> bool:
    if not CHANNEL_SECRET:
        return True
    hash_digest = hmac.new(CHANNEL_SECRET.encode("utf-8"), body, hashlib.sha256).digest()
    expected = base64.b64encode(hash_digest).decode("utf-8")
    return hmac.compare_digest(expected, signature)


def reply_message(reply_token: str, text: str):
    if not CHANNEL_ACCESS_TOKEN:
        print("CHANNEL_ACCESS_TOKEN 未設定，回傳內容：", text)
        return
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {CHANNEL_ACCESS_TOKEN}",
    }
    # LINE 單則文字上限約 5000 字，保守截斷
    text = text[:4900]
    payload = {"replyToken": reply_token, "messages": [{"type": "text", "text": text}]}
    requests.post(LINE_REPLY_URL, headers=headers, json=payload, timeout=10)


@app.route("/", methods=["GET"])
def home():
    return "LINE Stock Bot V4.5-Pro Smart Money is running."


@app.route("/callback", methods=["POST"])
def callback():
    body = request.get_data()
    signature = request.headers.get("X-Line-Signature", "")
    if not verify_signature(body, signature):
        abort(400)

    data = request.get_json(silent=True) or {}
    events = data.get("events", [])
    for event in events:
        try:
            if event.get("type") != "message":
                continue
            msg = event.get("message", {})
            if msg.get("type") != "text":
                continue
            text = msg.get("text", "").strip()
            reply_token = event.get("replyToken")
            response = handle_text(text)
            reply_message(reply_token, response)
        except Exception as e:
            traceback.print_exc()
            try:
                reply_message(event.get("replyToken"), f"系統暫時錯誤：{e}")
            except Exception:
                pass
    return "OK"


def handle_text(text: str) -> str:
    clean = text.strip()
    upper = clean.upper()

    if upper in ["HELP", "說明", "指令"]:
        return help_text()

    if clean == "0" or clean == "族群熱度":
        return group_heat()

    if clean in COMMAND_GROUP_MAP:
        return select_stocks(COMMAND_GROUP_MAP[clean], clean)

    # 股票代碼 + 買入價，例如：2330 800、2330,800
    m = re.match(r"^(\d{4})[\s,，]+(\d+(?:\.\d+)?)$", clean)
    if m:
        code = m.group(1)
        price = float(m.group(2))
        return analyze_single_stock(code, price)

    # 單一股票代碼：預設買入價用最新收盤價，方便快速看分析
    m2 = re.match(r"^(\d{4})$", clean)
    if m2:
        code = m2.group(1)
        df = fetch_history(code, "60d")
        if df is None or df.empty:
            return f"查無 {code} 資料。"
        close = safe_float(df.iloc[-1]["Close"])
        return analyze_single_stock(code, close)

    return "無法辨識指令。請輸入 HELP 查看指令，或輸入例如：2330 800。"


if __name__ == "__main__":
    port = int(os.getenv("PORT", "5000"))
    app.run(host="0.0.0.0", port=port)
