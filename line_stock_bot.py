# -*- coding: utf-8 -*-
"""
LINE 股票機器人 V4.4-Pro Entry Signal Final 正式版
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
4. 回傳顯示：指令、族群、掃描檔數、資料時間
"""

import os
import re
import time
import hmac
import base64
import hashlib
import traceback
from datetime import datetime

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
        "3034.TW": "聯詠", "5269.TW": "祥碩", "3529.TW": "力旺", "6531.TW": "愛普*",
        "4966.TW": "譜瑞-KY", "3665.TW": "貿聯-KY", "3105.TWO": "穩懋", "8086.TWO": "宏捷科",
        "5483.TWO": "中美晶", "6488.TWO": "環球晶", "3532.TW": "台勝科", "6182.TW": "合晶",
        "3708.TW": "上緯投控", "4739.TW": "康普", "4763.TW": "材料-KY", "4755.TW": "三福化",
        "4721.TW": "美琪瑪", "1513.TW": "中興電", "1609.TW": "大亞", "1504.TW": "東元",
        "1519.TW": "華城", "1605.TW": "華新", "1618.TW": "合機", "6285.TW": "啟碁",
        "2412.TW": "中華電", "4906.TW": "正文", "3596.TW": "智易", "2313.TW": "華通",
        "4958.TW": "臻鼎-KY", "6274.TW": "台燿", "6213.TW": "聯茂", "3035.TW": "智原",
    },
    "PCB": {
        "2383.TW": "台光電", "2368.TW": "金像電", "3037.TW": "欣興", "8046.TW": "南電",
        "3189.TW": "景碩", "2313.TW": "華通", "4958.TW": "臻鼎-KY", "6274.TW": "台燿",
        "6213.TW": "聯茂", "5469.TWO": "瀚宇博", "6191.TW": "精成科", "6269.TW": "台郡",
    },
    "ABF": {
        "3037.TW": "欣興", "8046.TW": "南電", "3189.TW": "景碩", "2383.TW": "台光電",
        "2368.TW": "金像電", "6274.TW": "台燿",
    },
    "ASIC": {
        "3443.TW": "創意", "3661.TW": "世芯-KY", "3035.TW": "智原", "3529.TW": "力旺",
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
    return "LINE 股票機器人 V4.4-Pro Entry Signal Final is running."


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
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def safe_float(x, default=0.0):
    try:
        if pd.isna(x):
            return default
        return float(x)
    except Exception:
        return default


def fetch_stock_df(ticker: str, retries: int = 2):
    for i in range(retries):
        try:
            df = yf.download(ticker, period="4mo", interval="1d", auto_adjust=False, progress=False, threads=False)
            if df is not None and len(df) >= 35:
                if isinstance(df.columns, pd.MultiIndex):
                    df.columns = [c[0] for c in df.columns]
                df = df.dropna()
                if len(df) >= 35:
                    return df
        except Exception:
            time.sleep(0.3 + i * 0.3)
    return None


def calc_rsi(close: pd.Series, period: int = 14):
    delta = close.diff()
    gain = delta.where(delta > 0, 0).rolling(period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(period).mean()
    rs = gain / loss.replace(0, pd.NA)
    rsi = 100 - (100 / (1 + rs))
    return rsi


def analyze_one(ticker: str, name: str):
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
    vr = (v / safe_float(vol20.iloc[i], 1)) if safe_float(vol20.iloc[i], 0) else 0
    vr5 = (v / safe_float(vol5.iloc[i], 1)) if safe_float(vol5.iloc[i], 0) else 0
    bias5 = ((c - ma5v) / ma5v * 100) if ma5v else 0
    bias20 = ((c - ma20v) / ma20v * 100) if ma20v else 0
    high20 = safe_float(high20_prev.iloc[i])
    near_high20 = ((high20 - c) / high20 * 100) if high20 else 999

    # 連續同色量柱：今天與昨天同為紅K，視為主力進貨更健康
    yesterday_up = safe_float(close.iloc[i - 1]) >= safe_float(open_.iloc[i - 1])
    two_red = body_up and yesterday_up

    # 左倍量：這裡採用「低位或未過熱 + 剛放量 + 尚未大幅突破」的實戰近似判斷
    early_position = (bias20 <= 8) and (r <= 72) and (today_pct <= 6.5)
    volume_start = (vr >= 1.25 or vr5 >= 1.20)
    left_volume = bool(volume_start and early_position and body_up and two_red)

    breakout = bool(c > high20) if high20 else False
    ma_bull = bool(ma5v > ma10v > ma20v)
    macd_bull = bool(safe_float(macd.iloc[i]) > safe_float(signal.iloc[i]))
    macd_turn = bool(safe_float(macd.iloc[i]) > safe_float(macd.iloc[i - 1]))
    not_overheat = bool(r < 78 and bias5 < 9 and today_pct < 7.5)

    score = 0
    score += 2 if left_volume else 0
    score += 1 if ma5v > ma10v else 0
    score += 1 if ma_bull else 0
    score += 1 if today_pct > 0 else 0
    score += 1 if 55 <= r <= 72 else 0
    score += 1 if macd_bull or macd_turn else 0
    score += 1 if vr >= 1.3 else 0
    score += 1 if near_high20 <= 6 or breakout else 0
    score -= 2 if r >= 80 else 0
    score -= 1 if bias5 >= 10 else 0
    score -= 1 if upper_shadow >= 3.5 else 0

    # 風險分級
    risk_points = 0
    risk_reasons = []
    if r >= 80:
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

    if risk_points >= 3:
        risk = "高"
    elif risk_points >= 1:
        risk = "中"
    else:
        risk = "低"

    if left_volume:
        signal_text = "主力左倍量"
    elif breakout and not_overheat:
        signal_text = "突破續強"
    elif volume_start and today_pct > 0:
        signal_text = "量能轉強"
    elif ma_bull and today_pct > 0:
        signal_text = "多頭續強"
    else:
        signal_text = "觀察"

    return {
        "ticker": ticker.replace(".TW", "").replace(".TWO", ""),
        "raw_ticker": ticker,
        "name": name,
        "close": c,
        "today_pct": today_pct,
        "rsi": r,
        "vol_ratio": vr,
        "ma5": ma5v,
        "ma10": ma10v,
        "ma20": ma20v,
        "bias5": bias5,
        "bias20": bias20,
        "score": score,
        "risk": risk,
        "risk_reasons": "、".join(risk_reasons) if risk_reasons else "健康",
        "left_volume": left_volume,
        "ma_bull": ma_bull,
        "breakout": breakout,
        "macd_bull": macd_bull,
        "not_overheat": not_overheat,
        "signal": signal_text,
    }


def scan_group(group_name: str):
    pool = STOCK_GROUPS.get(group_name, STOCK_GROUPS["全部"])
    rows = []
    for ticker, name in pool.items():
        try:
            row = analyze_one(ticker, name)
            if row:
                rows.append(row)
        except Exception:
            traceback.print_exc()
        time.sleep(0.08)
    return rows, len(pool)

# ============================================================
# 回傳格式
# ============================================================

def fmt_stock_line(idx: int, r: dict):
    return (
        f"{idx}. {r['ticker']} {r['name']}\n"
        f"   漲跌：{r['today_pct']:.2f}%｜量比：{r['vol_ratio']:.2f}｜RSI：{r['rsi']:.0f}\n"
        f"   訊號：{r['signal']}｜假突破風險：{r['risk']}"
    )


def make_pick_reply(command_name: str, group_name: str):
    rows, scan_count = scan_group(group_name)
    if not rows:
        return (
            f"【AI選股 V4.4-Pro Entry Signal Final】\n"
            f"指令：{command_name}｜族群：{group_name}\n"
            f"掃描檔數：{scan_count} 檔\n"
            f"資料時間：{now_text()}\n\n"
            f"目前抓不到足夠資料，可能是 Yahoo Finance 暫時無回應或資料尚未更新。"
        )

    # 主力進貨：左倍量優先；若當天市場強但左倍量不足，保留低追高候選避免空白
    main_force = [r for r in rows if r["left_volume"] and r["today_pct"] > 0 and r["risk"] != "高"]
    main_force = sorted(main_force, key=lambda x: (x["score"], x["vol_ratio"], -x["bias5"]), reverse=True)[:5]

    if len(main_force) < 3:
        fallback = [
            r for r in rows
            if r["today_pct"] > 0 and r["rsi"] <= 74 and r["bias5"] <= 8 and r["vol_ratio"] >= 1.0 and r["risk"] != "高"
        ]
        fallback = sorted(fallback, key=lambda x: (x["score"], x["vol_ratio"]), reverse=True)
        seen = {r["raw_ticker"] for r in main_force}
        for r in fallback:
            if r["raw_ticker"] not in seen:
                main_force.append(r)
                seen.add(r["raw_ticker"])
            if len(main_force) >= 5:
                break

    swing = [r for r in rows if r["ma_bull"] and r["today_pct"] > 0 and r["not_overheat"]]
    swing = sorted(swing, key=lambda x: (x["score"], x["today_pct"]), reverse=True)[:5]

    hot = [r for r in rows if r["today_pct"] > 0 or r["vol_ratio"] >= 1.2]
    hot = sorted(hot, key=lambda x: (x["today_pct"], x["vol_ratio"]), reverse=True)[:5]

    up_count = sum(1 for r in rows if r["today_pct"] > 0)
    avg_pct = sum(r["today_pct"] for r in rows) / len(rows) if rows else 0
    strong_count = sum(1 for r in rows if r["today_pct"] > 0 and r["vol_ratio"] >= 1.2)

    lines = []
    lines.append("【AI選股 V4.4-Pro Entry Signal Final】")
    lines.append(f"指令：{command_name}｜族群：{group_name}")
    lines.append(f"掃描檔數：{scan_count} 檔｜成功分析：{len(rows)} 檔")
    lines.append(f"資料時間：{now_text()}")
    lines.append(f"今日族群概況：上漲 {up_count}/{len(rows)} 檔｜平均漲跌 {avg_pct:.2f}%｜量能轉強 {strong_count} 檔")

    lines.append("\n━━━━━━━━━━━━━━")
    lines.append("🔥 主力進貨 TOP5")
    lines.append("（左倍量／提前布局／低追高優先）")
    lines.append("━━━━━━━━━━━━━━")
    if main_force:
        for i, r in enumerate(main_force, 1):
            lines.append(fmt_stock_line(i, r))
    else:
        lines.append("目前沒有明顯左倍量進貨股，建議先觀察，不硬追。")

    lines.append("\n━━━━━━━━━━━━━━")
    lines.append("🚀 波段續強 TOP5")
    lines.append("（健康續強／非過熱）")
    lines.append("━━━━━━━━━━━━━━")
    if swing:
        for i, r in enumerate(swing, 1):
            lines.append(fmt_stock_line(i, r))
    else:
        lines.append("目前沒有明顯健康續強股。")

    lines.append("\n━━━━━━━━━━━━━━")
    lines.append("🌡 市場熱門 TOP5")
    lines.append("（右倍量／人氣觀察區，不等於建議追價）")
    lines.append("━━━━━━━━━━━━━━")
    if hot:
        for i, r in enumerate(hot, 1):
            lines.append(fmt_stock_line(i, r))
    else:
        lines.append("目前族群熱度不足。")

    lines.append("\n提醒：市場熱門區主要看人氣與資金流，不代表低風險進場。")
    return "\n".join(lines)


def make_heat_reply():
    summaries = []
    for group_name in ["PCB", "ABF", "ASIC", "記憶體", "低軌衛星", "CoPoS", "Intel", "化學", "矽晶圓"]:
        rows, scan_count = scan_group(group_name)
        if not rows:
            summaries.append({"group": group_name, "scan": scan_count, "ok": 0, "up": 0, "avg": -999, "strong": 0})
            continue
        up = sum(1 for r in rows if r["today_pct"] > 0)
        avg = sum(r["today_pct"] for r in rows) / len(rows)
        strong = sum(1 for r in rows if r["today_pct"] > 0 and r["vol_ratio"] >= 1.2)
        left = sum(1 for r in rows if r["left_volume"])
        heat_score = avg + strong * 0.6 + left * 0.8 + (up / max(len(rows), 1)) * 2
        summaries.append({
            "group": group_name,
            "scan": scan_count,
            "ok": len(rows),
            "up": up,
            "avg": avg,
            "strong": strong,
            "left": left,
            "heat_score": heat_score,
        })

    summaries = sorted(summaries, key=lambda x: x.get("heat_score", -999), reverse=True)
    lines = []
    lines.append("【AI選股 V4.4-Pro Entry Signal Final】")
    lines.append("指令：族群熱度｜族群：全部主題")
    lines.append(f"資料時間：{now_text()}")
    lines.append("\n🔥 族群熱度排行")
    for i, s in enumerate(summaries, 1):
        if s["ok"] == 0:
            lines.append(f"{i}. {s['group']}｜資料不足")
        else:
            lines.append(
                f"{i}. {s['group']}｜上漲 {s['up']}/{s['ok']}｜平均 {s['avg']:.2f}%｜量能轉強 {s['strong']}｜左倍量 {s.get('left', 0)}"
            )
    lines.append("\n說明：熱度排行用上漲家數、平均漲跌、量能轉強、左倍量綜合判斷。")
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
        "例：2330 800"
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
    """先用內建股票池找名稱，找不到就回傳代碼，避免查詢失敗。"""
    for group in STOCK_GROUPS.values():
        if raw_ticker in group:
            return group[raw_ticker]
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
