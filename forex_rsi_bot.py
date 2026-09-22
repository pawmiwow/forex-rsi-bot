import yfinance as yf
import pandas as pd
import numpy as np
import requests
import time
from datetime import datetime, timezone
import json
import os
from threading import Thread
from flask import Flask

# ================== 配置 ==================
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
CHECK_INTERVAL = 300          # 5分钟
RSI_PERIOD = 14
OVERBOUGHT = 73
OVERSOLD = 27

PAIRS = [
    "GBPUSD=X", "EURUSD=X", "AUDUSD=X", "NZDUSD=X",
    "USDCAD=X", "USDCHF=X", "USDJPY=X", "GBPJPY=X",
    "EURJPY=X", "AUDCHF=X", "CHFJPY=X", "GBPAUD=X",
    "EURAUD=X", "AUDJPY=X", "NZDJPY=X", "GBPCAD=X",
    "EURCAD=X", "AUDNZD=X", "NZDCHF=X", "GBPCHF=X",
    "EURCHF=X", "CADCHF=X",  
    "NZDCAD=X", "EURGBP=X", "AUDCAD=X", "CADJPY=X"
]

DISPLAY_NAME = {
    "GBPUSD=X": "GBP/USD", "EURUSD=X": "EUR/USD", "AUDUSD=X": "AUD/USD",
    "NZDUSD=X": "NZD/USD", "USDCAD=X": "USD/CAD", "USDCHF=X": "USD/CHF",
    "USDJPY=X": "USD/JPY", "GBPJPY=X": "GBP/JPY", "EURJPY=X": "EUR/JPY",
    "AUDCHF=X": "AUD/CHF", "CHFJPY=X": "CHF/JPY", "GBPAUD=X": "GBP/AUD",
    "EURAUD=X": "EUR/AUD", "AUDJPY=X": "AUD/JPY", "NZDJPY=X": "NZD/JPY",
    "GBPCAD=X": "GBP/CAD", "EURCAD=X": "EUR/CAD", "AUDNZD=X": "AUD/NZD",
    "NZDCHF=X": "NZD/CHF", "GBPCHF=X": "GBP/CHF", "EURCHF=X": "EUR/CHF",
    "CADCHF=X": "CAD/CHF", 
    "NZDCAD=X": "NZD/CAD", "EURGBP=X": "EUR/GBP", "AUDCAD=X": "AUD/CAD",
    "CADJPY=X": "CAD/JPY"
}

STATE_FILE = "rsi_alert_state.json"
# ==========================================

app = Flask(__name__)

@app.route("/")
def home():
    return "外汇 RSI 监控机器人运行中 ✅", 200

def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r") as f:
                return json.load(f)
        except:
            pass
    return {}

def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f)

def calculate_rsi(series, period=14):
    delta = series.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = -delta.where(delta < 0, 0.0)
    avg_gain = gain.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))

def get_latest_rsi(ticker):
    try:
        df = yf.download(ticker, period="5d", interval="15m", progress=False, auto_adjust=True)
        if df.empty or len(df) < RSI_PERIOD + 5:
            return None
        if isinstance(df.columns, pd.MultiIndex):
            close = df["Close"].iloc[:, 0]
        else:
            close = df["Close"]
        rsi = calculate_rsi(close, RSI_PERIOD)
        latest_rsi = rsi.iloc[-1]
        latest_price = close.iloc[-1]
        latest_time = close.index[-1]
        if pd.isna(latest_rsi):
            return None
        return float(latest_rsi), float(latest_price), latest_time
    except Exception as e:
        print(f"[{ticker}] 错误: {e}")
        return None

def send_discord_alert(pair_name, rsi_value, price, signal_type, time_str):
    color = 0xFF0000 if signal_type == "超买" else 0x00FF00

    # 第一行标题样式：【NZD/JPY】 RSI(14) 73
    title_text = f"【{pair_name}】 RSI(14) {rsi_value:.2f}"

    embed = {
        "title": title_text,
        "description": f"**{signal_type}信号**",
        "color": color,
        "fields": [
            {"name": "当前价格", "value": f"{price:.5f}", "inline": True},
            {"name": "时间周期", "value": "M15", "inline": True},
            {"name": "触发条件", "value": f"RSI {'≥ 73' if signal_type=='超买' else '≤ 27'}", "inline": True},
            {"name": "K线时间", "value": time_str, "inline": True},
        ],
        "footer": {"text": "外汇RSI监控 · 每5分钟检测 · 不重复提醒"},
        "timestamp": datetime.now(timezone.utc).isoformat()
    }

    payload = {
        "username": "外汇RSI监控",
        "embeds": [embed]
    }

    try:
        r = requests.post(WEBHOOK_URL, json=payload, timeout=10)
        if r.status_code in (200, 204):
            print(f"✅ 已发送: {pair_name} RSI={rsi_value:.2f} ({signal_type})")
        else:
            print(f"❌ 发送失败: {r.status_code}")
    except Exception as e:
        print(f"❌ 发送异常: {e}")

def monitor_loop():
    if not WEBHOOK_URL:
        print("错误：请设置环境变量 WEBHOOK_URL")
        return

    print("=" * 50)
    print("外汇 RSI 监控机器人启动成功")
    print(f"监控 {len(PAIRS)} 个货币对 | M15 | RSI(14)")
    print(f"超买≥{OVERBOUGHT} | 超卖≤{OVERSOLD}")
    print("=" * 50)

    state = load_state()

    while True:
        start = time.time()
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print(f"\n[{now}] 开始检测...")

        for ticker in PAIRS:
            result = get_latest_rsi(ticker)
            if not result:
                continue
            rsi, price, t = result
            pair_name = DISPLAY_NAME.get(ticker, ticker)
            current = state.get(ticker, "normal")

            if rsi >= OVERBOUGHT:
                new_status = "overbought"
                signal = "超买"
            elif rsi <= OVERSOLD:
                new_status = "oversold"
                signal = "超卖"
            else:
                new_status = "normal"
                signal = None

            if new_status != "normal" and current != new_status:
                time_str = t.strftime("%Y-%m-%d %H:%M") if hasattr(t, "strftime") else str(t)
                send_discord_alert(pair_name, rsi, price, signal, time_str)

            state[ticker] = new_status
            print(f"  {pair_name:10} RSI={rsi:6.2f} → {new_status}")

        save_state(state)
        elapsed = time.time() - start
        sleep_time = max(30, CHECK_INTERVAL - elapsed)
        print(f"本轮完成，等待 {sleep_time:.0f} 秒...")
        time.sleep(sleep_time)

if __name__ == "__main__":
    # 启动监控线程
    t = Thread(target=monitor_loop, daemon=True)
    t.start()

    # 启动网页服务（给 Render 用）
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
