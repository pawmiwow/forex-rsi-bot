import pandas as pd
import numpy as np
import requests
import time
from datetime import datetime, timezone
import json
import os
import gc
from threading import Thread
from flask import Flask

# ================== 配置 ==================
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
CHECK_INTERVAL = 300
RSI_PERIOD = 14
OVERBOUGHT = 73
OVERSOLD = 27

PAIRS = [
    "USDCAD", "GBPUSD", "USDCHF", "USDJPY", "AUDUSD",
    "EURUSD", "NZDUSD", "GBPJPY", "GBPAUD", "GBPCAD",
    "GBPCHF", "EURGBP", "EURCHF", "EURCAD", "EURAUD",
    "EURJPY", "AUDJPY", "AUDCHF", "AUDCAD", "CADCHF",
    "CADJPY", "NZDJPY", "NZDCHF", "CHFJPY", "NZDCAD"
]

DISPLAY_NAME = {
    "USDCAD": "USD/CAD", "GBPUSD": "GBP/USD", "USDCHF": "USD/CHF",
    "USDJPY": "USD/JPY", "AUDUSD": "AUD/USD", "EURUSD": "EUR/USD",
    "NZDUSD": "NZD/USD", "GBPJPY": "GBP/JPY", "GBPAUD": "GBP/AUD",
    "GBPCAD": "GBP/CAD", "GBPCHF": "GBP/CHF", "EURGBP": "EUR/GBP",
    "EURCHF": "EUR/CHF", "EURCAD": "EUR/CAD", "EURAUD": "EUR/AUD",
    "EURJPY": "EUR/JPY", "AUDJPY": "AUD/JPY", "AUDCHF": "AUD/CHF",
    "AUDCAD": "AUD/CAD", "CADCHF": "CAD/CHF", "CADJPY": "CAD/JPY",
    "NZDJPY": "NZD/JPY", "NZDCHF": "NZD/CHF", "CHFJPY": "CHF/JPY",
    "NZDCAD": "NZD/CAD"
}

STATE_FILE = "rsi_alert_state.json"
# ==========================================

app = Flask(__name__)

@app.route("/")
def home():
    return "外汇 RSI 监控机器人运行中（biquote）✅", 200

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

def get_latest_rsi(symbol):
    """使用 biquote 获取数据，增加超时和异常处理"""
    try:
        url = f"https://biquote.io/api/{symbol}/ohlc"
        params = {"interval": "15m", "limit": 40}
        
        resp = requests.get(url, params=params, timeout=8)  # 严格超时
        if resp.status_code != 200:
            print(f"  [{symbol}] HTTP {resp.status_code}")
            return None

        data = resp.json()
        bars = data.get("bars", [])
        if len(bars) < RSI_PERIOD + 5:
            print(f"  [{symbol}] 数据不足")
            return None

        bars = list(reversed(bars))
        closes = [float(bar["close"]) for bar in bars]
        
        series = pd.Series(closes)
        rsi = calculate_rsi(series, RSI_PERIOD)
        
        latest_rsi = float(rsi.iloc[-1])
        latest_price = float(closes[-1])
        latest_time = bars[-1].get("openTime", "")

        del bars, closes, series, rsi
        gc.collect()

        if pd.isna(latest_rsi):
            return None
        return latest_rsi, latest_price, latest_time

    except requests.exceptions.Timeout:
        print(f"  [{symbol}] 超时")
        return None
    except Exception as e:
        print(f"  [{symbol}] 错误: {str(e)[:50]}")
        return None

def send_discord_alert(pair_name, rsi_value, price, signal_type, time_str):
    color = 0xFF0000 if signal_type == "超买" else 0x00FF00
    title_text = f"【{pair_name}】 RSI(14) {rsi_value:.2f}"

    embed = {
        "title": title_text,
        "description": f"**{signal_type}信号**",
        "color": color,
        "fields": [
            {"name": "当前价格", "value": f"{price:.5f}", "inline": True},
            {"name": "时间周期", "value": "M15", "inline": True},
            {"name": "触发条件", "value": f"RSI {'≥ 73' if signal_type=='超买' else '≤ 27'}", "inline": True},
            {"name": "K线时间", "value": str(time_str)[:19], "inline": True},
        ],
        "footer": {"text": "外汇RSI监控 · biquote · 每5分钟 · 不重复提醒"},
        "timestamp": datetime.now(timezone.utc).isoformat()
    }

    payload = {"username": "外汇RSI监控", "embeds": [embed]}

    try:
        r = requests.post(WEBHOOK_URL, json=payload, timeout=8)
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
    print("外汇 RSI 监控机器人启动成功（biquote 修复版）")
    print(f"监控 {len(PAIRS)} 个货币对 | M15 | RSI(14)")
    print(f"超买≥{OVERBOUGHT} | 超卖≤{OVERSOLD}")
    print("=" * 50)

    state = load_state()

    while True:
        start = time.time()
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print(f"\n[{now}] 开始检测...")

        success_count = 0
        for i, symbol in enumerate(PAIRS):
            result = get_latest_rsi(symbol)
            if not result:
                continue

            success_count += 1
            rsi, price, t = result
            pair_name = DISPLAY_NAME.get(symbol, symbol)
            current = state.get(symbol, "normal")

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
                send_discord_alert(pair_name, rsi, price, signal, t)

            state[symbol] = new_status
            print(f"  {pair_name:10} RSI={rsi:6.2f} → {new_status}")

            # 每处理几个就休息一下，避免请求过快
            if (i + 1) % 5 == 0:
                time.sleep(0.5)
                gc.collect()

        save_state(state)
        elapsed = time.time() - start
        print(f"本轮完成，成功 {success_count}/{len(PAIRS)} 个，耗时 {elapsed:.1f}s")
        
        sleep_time = max(30, CHECK_INTERVAL - elapsed)
        print(f"等待 {sleep_time:.0f} 秒后继续...")
        time.sleep(sleep_time)

if __name__ == "__main__":
    t = Thread(target=monitor_loop, daemon=True)
    t.start()

    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
