from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from contextlib import asynccontextmanager
import asyncio
import threading
import logging
from datetime import datetime
import pyupbit
import pandas as pd
import pandas_ta as ta
import os
from dotenv import load_dotenv
from pathlib import Path

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("bot")


@asynccontextmanager
async def lifespan(app: FastAPI):
    if os.getenv("BOT_AUTO_START", "false").lower() == "true":
        log.info("BOT_AUTO_START=true — 봇 자동 시작")
        threading.Thread(target=bot_loop, daemon=True).start()
    yield
    stop_event.set()
    log.info("서버 종료 — 봇 중지")


app = FastAPI(lifespan=lifespan)

# ── 공유 상태 ──────────────────────────────────────────────────────────────────
state = {
    "running": False,
    "is_holding": False,
    "current_price": 0,
    "entry_price": None,
    "stop_loss": None,
    "take_profit": None,
    "rr_ratio": None,
    "risk_pct": None,
    "reward_pct": None,
    "ema_7": None,
    "bb_upper": None,
    "bb_mid": None,
    "bb_lower": None,
    "psar_bull": None,
    "psar_bear": None,
    "signal": None,
    "trade_log": [],
    "last_update": None,
    "error": None,
}

stop_event = threading.Event()

TICKER   = "KRW-BTC"
INTERVAL = "minute5"
MIN_RR   = 1.5

access = os.getenv("UPBIT_ACCESS")
secret = os.getenv("UPBIT_SECRET")
upbit_client = pyupbit.Upbit(access, secret)


# ── 지표 계산 ──────────────────────────────────────────────────────────────────
def get_df():
    df = pyupbit.get_ohlcv(TICKER, interval=INTERVAL, count=100)
    if df is None or len(df) < 21:
        return None

    df["ema_7"]    = ta.ema(df["close"], length=7)
    bb             = ta.bbands(df["close"], length=20, std=2)
    df["bb_lower"] = bb["BBL_20_2.0"]
    df["bb_mid"]   = bb["BBM_20_2.0"]
    df["bb_upper"] = bb["BBU_20_2.0"]
    psar           = ta.psar(df["high"], df["low"], df["close"], af0=0.02, af=0.02, max_af=0.2)
    df["psar_bull"] = psar["PSARl_0.02_0.2"]
    df["psar_bear"] = psar["PSARs_0.02_0.2"]
    return df


def calc_rr(entry, stop, target):
    r, rw = entry - stop, target - entry
    if r <= 0 or rw <= 0:
        return None, None, None
    return r / entry * 100, rw / entry * 100, rw / r


def add_log(action, price, reason="", rr=None, risk=None, reward=None):
    state["trade_log"].insert(0, {
        "time":   datetime.now().strftime("%H:%M:%S"),
        "action": action,
        "price":  f"{int(price):,}",
        "reason": reason,
        "rr":     f"{rr:.2f}"     if rr     else "-",
        "risk":   f"{risk:.2f}%"  if risk   else "-",
        "reward": f"{reward:.2f}%" if reward else "-",
    })
    state["trade_log"] = state["trade_log"][:30]


# ── 봇 루프 ────────────────────────────────────────────────────────────────────
def bot_loop():
    stop_event.clear()
    state.update({"running": True, "trade_log": [], "error": None})
    log.info("봇 루프 시작")

    bal = upbit_client.get_balance(TICKER.split("-")[1])
    state["is_holding"] = bool(bal and bal > 0.00001)
    log.info("초기 포지션: %s", "보유" if state["is_holding"] else "미보유")

    while not stop_event.is_set():
        try:
            df = get_df()
            if df is None:
                state["error"] = "OHLCV 데이터 없음"
                stop_event.wait(10)
                continue

            price     = pyupbit.get_current_price(TICKER)
            last, prev = df.iloc[-2], df.iloc[-3]

            def sv(v):
                return float(v) if pd.notna(v) else None

            state.update({
                "current_price": price,
                "ema_7":    sv(last["ema_7"]),
                "bb_upper": sv(last["bb_upper"]),
                "bb_mid":   sv(last["bb_mid"]),
                "bb_lower": sv(last["bb_lower"]),
                "psar_bull": sv(last["psar_bull"]),
                "psar_bear": sv(last["psar_bear"]),
                "last_update": datetime.now().strftime("%H:%M:%S"),
                "signal": None,
                "error":  None,
            })

            if not state["is_holding"]:
                bull_flip = pd.isna(prev["psar_bull"]) and pd.notna(last["psar_bull"])
                above_ema = price > last["ema_7"]
                near_bot  = price < last["bb_mid"]

                if bull_flip and above_ema and near_bot:
                    stop   = float(last["ema_7"]) * 0.995
                    target = float(last["bb_upper"])
                    rp, rwp, rr = calc_rr(price, stop, target)

                    if rr and rr >= MIN_RR:
                        krw = upbit_client.get_balance("KRW")
                        if krw and krw > 5000:
                            upbit_client.buy_market_order(TICKER, krw * 0.9995)
                            state.update({
                                "is_holding": True,
                                "entry_price": price,
                                "stop_loss":   stop,
                                "take_profit": target,
                                "rr_ratio":    round(rr, 2),
                                "risk_pct":    round(rp, 2),
                                "reward_pct":  round(rwp, 2),
                                "signal": "BUY",
                            })
                            add_log("BUY", price, f"R:R {rr:.2f}", rr, rp, rwp)
                            log.info("BUY  price=%s  R:R=%.2f  risk=%.2f%%  reward=%.2f%%", price, rr, rp, rwp)
                    elif rr:
                        state["signal"] = "SKIP"
                        add_log("SKIP", price, f"R:R {rr:.2f} < {MIN_RR}")
                        log.info("SKIP price=%s  R:R=%.2f < %.1f", price, rr, MIN_RR)

            else:
                bear_flip  = pd.isna(prev["psar_bear"]) and pd.notna(last["psar_bear"])
                hit_stop   = state["stop_loss"]   and price <= state["stop_loss"]
                hit_target = state["take_profit"] and price >= state["take_profit"]

                if hit_stop or hit_target or bear_flip:
                    reason = "손절" if hit_stop else ("익절" if hit_target else "SAR전환")
                    coin = upbit_client.get_balance(TICKER.split("-")[1])
                    if coin and coin > 0:
                        upbit_client.sell_market_order(TICKER, coin)
                        state.update({
                            "is_holding": False,
                            "entry_price": None,
                            "stop_loss":   None,
                            "take_profit": None,
                            "signal": "SELL",
                        })
                        add_log("SELL", price, reason)
                        log.info("SELL price=%s  reason=%s", price, reason)

            stop_event.wait(300)

        except Exception as e:
            state["error"] = str(e)
            log.error("봇 오류: %s", e)
            stop_event.wait(10)

    state["running"] = False
    log.info("봇 루프 종료")


# ── API ────────────────────────────────────────────────────────────────────────
@app.get("/")
async def root():
    return HTMLResponse(Path("templates/index.html").read_text(encoding="utf-8"))


@app.post("/bot/start")
async def start():
    if not state["running"]:
        threading.Thread(target=bot_loop, daemon=True).start()
    return {"ok": True}


@app.post("/bot/stop")
async def stop():
    stop_event.set()
    return {"ok": True}


@app.get("/api/chart")
async def chart():
    df = get_df()
    if df is None:
        return {"candles": []}

    tail = df.tail(60).copy()
    if tail.index.tz is None:
        tail.index = tail.index.tz_localize("Asia/Seoul")

    def sv(v):
        return float(v) if pd.notna(v) else None

    return {
        "candles": [
            {
                "time":     int(row.name.timestamp()),
                "open":     row["open"],
                "high":     row["high"],
                "low":      row["low"],
                "close":    row["close"],
                "ema_7":    sv(row["ema_7"]),
                "bb_upper": sv(row["bb_upper"]),
                "bb_mid":   sv(row["bb_mid"]),
                "bb_lower": sv(row["bb_lower"]),
                "psar_bull": sv(row["psar_bull"]),
                "psar_bear": sv(row["psar_bear"]),
            }
            for _, row in tail.iterrows()
        ]
    }


@app.websocket("/ws")
async def ws_endpoint(websocket: WebSocket):
    await websocket.accept()
    try:
        while True:
            await websocket.send_json(state)
            await asyncio.sleep(2)
    except (WebSocketDisconnect, Exception):
        pass
