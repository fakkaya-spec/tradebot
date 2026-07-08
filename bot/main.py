"""Canli bot dongusu: her 4 saatlik bar kapanisinda calisir.

Akis her sembol ve her kol (trend / breakout) icin aynidir ve backtest
motoruyla birebir ayni strateji fonksiyonlarini kullanir:

1. Kapanan barin indikatorlerini hesapla.
2. Acik pozisyon varsa: stop'u guncelle (trailing), kural bazli cikis var mi bak.
3. Pozisyon yoksa: giris sinyali + funding filtresi + risk sinirlari -> emir.
4. Aylik kill-switch: limit asildiysa her seyi kapat, ay sonuna kadar bekle.

DRY_RUN=true iken hicbir emir gonderilmez, her sey loglanir.
"""
import json
import logging
import os
import time
from datetime import datetime, timezone

from .config import Config
from .exchange import assert_key_safety, current_funding_rate, fetch_ohlcv_df, make_exchange
from .indicators import add_indicators
from .notifier import Notifier
from .risk import MonthlyKillSwitch, RiskParams, position_size
from .strategy import (LONG, SHORT, SLEEVES, StrategyParams, entry_signal,
                       exit_signal, funding_blocks_entry, initial_stop,
                       update_trailing_stop)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("bot")

TIMEFRAME_SECONDS = 4 * 3600
PAPER_START_EQUITY = 10_000.0


class State:
    """Pozisyonlar kol (sleeve) bilgisiyle burada tutulur; borsa tarafinda
    sadece net pozisyon gorunur. Dosyaya yazilir ki restart guvenli olsun."""

    def __init__(self, path: str):
        self.path = path
        self.positions = {}  # "SYMBOL|sleeve" -> dict
        self.paper_equity = PAPER_START_EQUITY
        self.load()

    def load(self):
        if os.path.exists(self.path):
            with open(self.path) as f:
                data = json.load(f)
            self.positions = data.get("positions", {})
            self.paper_equity = data.get("paper_equity", PAPER_START_EQUITY)

    def save(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w") as f:
            json.dump({"positions": self.positions, "paper_equity": self.paper_equity}, f, indent=2)
        os.replace(tmp, self.path)


def sleep_until_next_close():
    now = time.time()
    next_close = (int(now) // TIMEFRAME_SECONDS + 1) * TIMEFRAME_SECONDS
    wait = next_close - now + 30  # borsanin bari kapatmasi icin pay
    log.info("Sonraki bar kapanisina %.0f dk uyunuyor", wait / 60)
    time.sleep(wait)


def get_equity(ex, cfg, state: State) -> float:
    if cfg.dry_run:
        return state.paper_equity
    bal = ex.fetch_balance()
    return float(bal["info"].get("totalMarginBalance") or bal["total"].get("USDT", 0.0))


def open_notional(state: State, marks: dict) -> float:
    total = 0.0
    for key, pos in state.positions.items():
        symbol = key.split("|")[0]
        total += pos["qty"] * marks.get(symbol, pos["entry"])
    return total


def place_market(ex, cfg, symbol, side, qty, reduce_only=False):
    if cfg.dry_run:
        log.info("[DRY_RUN] %s %s %s qty=%.6f reduceOnly=%s", symbol, side, "MARKET", qty, reduce_only)
        return
    params = {"reduceOnly": True} if reduce_only else {}
    ex.create_order(symbol, "market", side, qty, None, params)


def sync_stop_order(ex, cfg, symbol, pos_side, qty, stop_price):
    """Pozisyonun borsa tarafindaki STOP_MARKET emrini gunceller - bot
    coksede stop borsada durur."""
    if cfg.dry_run:
        log.info("[DRY_RUN] %s STOP_MARKET @ %.4f", symbol, stop_price)
        return
    for order in ex.fetch_open_orders(symbol):
        if order.get("type", "").lower().replace("_", "") == "stopmarket":
            ex.cancel_order(order["id"], symbol)
    close_side = "sell" if pos_side == LONG else "buy"
    ex.create_order(symbol, "STOP_MARKET", close_side, qty, None,
                    {"stopPrice": stop_price, "reduceOnly": True})


def close_position(ex, cfg, notifier, state, key, pos, price, reason):
    symbol = key.split("|")[0]
    side = "sell" if pos["side"] == LONG else "buy"
    place_market(ex, cfg, symbol, side, pos["qty"], reduce_only=True)
    pnl = pos["qty"] * (price - pos["entry"]) * (1 if pos["side"] == LONG else -1)
    if cfg.dry_run:
        state.paper_equity += pnl
    del state.positions[key]
    notifier.send(f"KAPANDI {symbol} [{key.split('|')[1]}] {pos['side']} @ {price:.4f} | {reason} | PnL~{pnl:+.2f} USDT")


def process_symbol(ex, cfg, notifier, state, ks_tripped, symbol, params, risk, marks):
    df = add_indicators(
        fetch_ohlcv_df(ex, symbol, cfg.timeframe),
        cfg.ema_fast, cfg.ema_slow, cfg.adx_period, cfg.atr_period,
        cfg.donchian_entry, cfg.donchian_exit,
    )
    row = df.iloc[-1]
    marks[symbol] = float(row.close)
    funding = current_funding_rate(ex, symbol)

    for sleeve in SLEEVES:
        key = f"{symbol}|{sleeve}"
        pos = state.positions.get(key)

        if pos:
            # iz suren stop ve en iyi fiyat guncellemesi
            pos["best"] = max(pos["best"], float(row.high)) if pos["side"] == LONG else min(pos["best"], float(row.low))
            new_stop = update_trailing_stop(
                sleeve, pos["side"], pos["entry"], pos["entry_atr"],
                pos["best"], float(row.atr), pos["stop"], params,
            )
            stop_hit = (pos["side"] == LONG and row.low <= pos["stop"]) or (
                pos["side"] == SHORT and row.high >= pos["stop"])
            if stop_hit:
                close_position(ex, cfg, notifier, state, key, pos, pos["stop"], "stop")
                continue
            if exit_signal(sleeve, row, pos["side"]):
                close_position(ex, cfg, notifier, state, key, pos, float(row.close), "sinyal cikisi")
                continue
            if new_stop != pos["stop"]:
                pos["stop"] = new_stop
                sync_stop_order(ex, cfg, symbol, pos["side"], pos["qty"], new_stop)
            continue

        if ks_tripped:
            continue
        side = entry_signal(sleeve, row, params)
        if not side or funding_blocks_entry(side, funding, cfg.funding_limit):
            continue
        equity = get_equity(ex, cfg, state)
        stop_dist = cfg.stop_atr * float(row.atr)
        qty = position_size(equity, float(row.close), stop_dist, open_notional(state, marks), risk)
        if qty <= 0:
            continue
        place_market(ex, cfg, symbol, "buy" if side == LONG else "sell", qty)
        stop = initial_stop(side, float(row.close), float(row.atr), params)
        state.positions[key] = {
            "side": side, "qty": qty, "entry": float(row.close), "stop": stop,
            "best": float(row.close), "entry_atr": float(row.atr),
            "entry_time": str(row.name),
        }
        sync_stop_order(ex, cfg, symbol, side, qty, stop)
        notifier.send(f"ACILDI {symbol} [{sleeve}] {side} qty={qty:.6f} @ {row.close:.4f} stop={stop:.4f}")


def main():
    cfg = Config()
    notifier = Notifier(cfg.telegram_token, cfg.telegram_chat_id)
    ex = make_exchange(cfg)
    if not cfg.dry_run:
        assert_key_safety(ex, cfg.testnet)
    state = State(os.path.join(cfg.state_dir, "positions.json"))
    params = StrategyParams(cfg.adx_threshold, cfg.stop_atr, cfg.breakeven_atr, cfg.trail_atr)
    risk = RiskParams(cfg.risk_per_trade, cfg.max_leverage,
                      cfg.max_position_notional_pct, cfg.monthly_kill_switch)
    kill_switch = MonthlyKillSwitch(cfg.monthly_kill_switch)

    mode = "DRY_RUN" if cfg.dry_run else ("TESTNET" if cfg.testnet else "CANLI")
    notifier.send(f"Bot basladi [{mode}] semboller: {', '.join(cfg.symbols)}")

    while True:
        try:
            marks = {}
            now = datetime.now(timezone.utc)
            equity = get_equity(ex, cfg, state)
            if kill_switch.update(now, equity):
                notifier.send(f"KILL-SWITCH: aylik zarar limiti asildi (ozsermaye {equity:.2f}). "
                              "Tum pozisyonlar kapatiliyor, ay sonuna kadar islem yok.")
                for key in list(state.positions):
                    pos = state.positions[key]
                    symbol = key.split("|")[0]
                    price = marks.get(symbol) or float(ex.fetch_ticker(symbol)["last"])
                    close_position(ex, cfg, notifier, state, key, pos, price, "kill-switch")
            for symbol in cfg.symbols:
                try:
                    process_symbol(ex, cfg, notifier, state, kill_switch.tripped,
                                   symbol, params, risk, marks)
                except Exception as exc:
                    log.exception("%s islenirken hata", symbol)
                    notifier.send(f"HATA {symbol}: {exc}")
            state.save()
        except Exception as exc:
            log.exception("Dongu hatasi")
            notifier.send(f"DONGU HATASI: {exc}")
        sleep_until_next_close()


if __name__ == "__main__":
    main()
