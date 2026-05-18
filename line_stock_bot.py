# -*- coding: utf-8 -*-
"""
LINE 股票機器人 V4.4-Pro Sector 熱度 + HELP版
檔名：
line_stock_bot_v4_4_pro_sector_heat_help_20260518.py

本版新增：
- HELP/help 指令
- Sector 分族群
- 族群熱度
- 主力進貨 / 市場熱門 / 波段續強

Render Start Command：
gunicorn line_stock_bot:app
"""

HELP_TEXT = """
【AI Trading Lab 指令中心】

📊 核心選股
選股

🔥 題材族群
選股PCB
選股ABF
選股ASIC
選股記憶體
選股低軌
選股CoPoS
選股Intel
選股化學
選股矽晶圓

📈 市場觀察
族群熱度

💰 個股分析
股票代碼 買入價
例：2330 800
"""

if __name__ == "__main__":
    print("V4.4-Pro Sector 熱度 + HELP版")
    print(HELP_TEXT)
