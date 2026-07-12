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
from datetime import date, datetime, timedelta, timezone

from .config import Config
from .exchange import (adjust_quantity, assert_key_safety, current_funding_rate,
                       fetch_net_positions, fetch_ohlcv_df, make_exchange,
                       prepare_symbol)
from .indicators import add_indicators
from .notifier import Notifier
from .risk import MonthlyKillSwitch, RiskParams, position_size
from .strategy import (LONG, MEANREV, SHORT, StrategyParams, active_sleeves,
                       entry_signal, exit_signal, funding_blocks_entry,
                       initial_stop, update_trailing_stop)

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
        self.cooldowns = {}  # "SYMBOL|sleeve" -> {"side": ..., "until": iso}
        self.core = {}       # "SYMBOL" -> "coin" | "cash" (spot cekirdek durumu)
        self.ks = {}         # kill-switch ay durumu (restart'a dayanikli)
        self.paper_equity = PAPER_START_EQUITY
        self.load()

    def load(self):
        if os.path.exists(self.path):
            with open(self.path) as f:
                data = json.load(f)
            self.positions = data.get("positions", {})
            self.cooldowns = data.get("cooldowns", {})
            self.core = data.get("core", {})
            self.ks = data.get("ks", {})
            self.paper_equity = data.get("paper_equity", PAPER_START_EQUITY)

    def save(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w") as f:
            json.dump({"positions": self.positions, "cooldowns": self.cooldowns,
                       "core": self.core, "ks": self.ks,
                       "paper_equity": self.paper_equity}, f, indent=2)
        os.replace(tmp, self.path)


def reconcile_state(ex, cfg, notifier, state):
    """Acilista bot defterini borsayla mutabakata cevirir.

    Bot cevrimdisiyken stop tetiklenmis olabilir: kayitta acik gorunen ama
    borsada kapanmis pozisyonlar defterden dusulur, artik kalan stop
    emirleri iptal edilir ve cooldown uygulanir."""
    if cfg.dry_run or not state.positions:
        return
    try:
        symbols = sorted({k.split("|")[0] for k in state.positions})
        net = fetch_net_positions(ex, symbols)
        for sym in symbols:
            keys = [k for k in state.positions if k.split("|")[0] == sym]
            state_net = sum(state.positions[k]["qty"] * (1 if state.positions[k]["side"] == LONG else -1)
                            for k in keys)
            exchange_net = net.get(sym, 0.0)
            if abs(exchange_net) < 1e-12:
                for k in keys:
                    pos = state.positions.pop(k)
                    until = datetime.now(timezone.utc) + timedelta(hours=4 * cfg.cooldown_bars)
                    state.cooldowns[k] = {"side": pos["side"], "until": until.isoformat()}
                try:
                    for order in ex.fetch_open_orders(sym):
                        ex.cancel_order(order["id"], sym)
                except Exception as exc:
                    log.warning("%s artik emirler temizlenemedi: %s", sym, exc)
                notifier.send(f"MUTABAKAT {sym}: pozisyon bot cevrimdisiyken borsada kapanmis "
                              "(muhtemelen stop). Kayit temizlendi, cooldown uygulandi.")
            elif abs(exchange_net - state_net) > 1e-9:
                notifier.send(f"UYARI {sym}: borsa pozisyonu ({exchange_net:+.6f}) bot kaydiyla "
                              f"({state_net:+.6f}) uyusmuyor - manuel kontrol gerekli.")
        state.save()
    except Exception as exc:
        log.warning("mutabakat basarisiz: %s", exc)
        notifier.send(f"UYARI: acilis mutabakati yapilamadi: {exc}")


def check_core_alerts(ex, cfg, notifier, state):
    """Spot cekirdek (70/30 yapinin %30'u) icin EMA200 gecis uyarilari.
    Islem YAPMAZ; sadece durum degisince Telegram'dan haber verir."""
    for symbol in cfg.core_symbols:
        try:
            df = fetch_ohlcv_df(ex, symbol, cfg.timeframe, limit=500)
            close = df["close"]
            ema200 = close.ewm(span=cfg.ema_macro, adjust=False).mean()
            now_state = "coin" if float(close.iloc[-1]) > float(ema200.iloc[-1]) else "cash"
            prev = state.core.get(symbol)
            if prev is None:
                state.core[symbol] = now_state
                notifier.send(f"CEKIRDEK baslangic durumu {symbol}: "
                              f"{'COIN tutulmali (EMA200 ustunde)' if now_state == 'coin' else 'USDT beklenmeli (EMA200 altinda)'}")
            elif now_state != prev:
                state.core[symbol] = now_state
                if now_state == "coin":
                    notifier.send(f"CEKIRDEK SINYALI {symbol}: EMA200 USTUNE kapandi -> spot AL")
                else:
                    notifier.send(f"CEKIRDEK SINYALI {symbol}: EMA200 ALTINA kapandi -> spot SAT, USDT'ye gec")
        except Exception as exc:
            log.warning("%s cekirdek kontrolu basarisiz: %s", symbol, exc)


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
    # workingType=MARK_PRICE: tetikleyici son islem fiyati degil adil fiyat
    # (mark price) - tek barlik manipulatif igneler stop'u yalayamaz.
    ex.create_order(symbol, "STOP_MARKET", close_side, qty, None,
                    {"stopPrice": stop_price, "reduceOnly": True,
                     "workingType": "MARK_PRICE"})


def close_position(ex, cfg, notifier, state, key, pos, price, reason):
    symbol = key.split("|")[0]
    side = "sell" if pos["side"] == LONG else "buy"
    place_market(ex, cfg, symbol, side, pos["qty"], reduce_only=True)
    pnl = pos["qty"] * (price - pos["entry"]) * (1 if pos["side"] == LONG else -1)
    if cfg.dry_run:
        state.paper_equity += pnl
    del state.positions[key]
    if reason == "stop":
        until = datetime.now(timezone.utc) + timedelta(hours=4 * cfg.cooldown_bars)
        state.cooldowns[key] = {"side": pos["side"], "until": until.isoformat()}
    notifier.send(f"KAPANDI {symbol} [{key.split('|')[1]}] {pos['side']} @ {price:.4f} | {reason} | PnL~{pnl:+.2f} USDT")


def process_symbol(ex, cfg, notifier, state, ks_tripped, symbol, params, risk, marks):
    df = add_indicators(fetch_ohlcv_df(ex, symbol, cfg.timeframe, limit=500), cfg)
    row = df.iloc[-1]
    marks[symbol] = float(row.close)
    funding = current_funding_rate(ex, symbol)

    for sleeve in active_sleeves(cfg):
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
        cd = state.cooldowns.get(key)
        if cd and cd["side"] == side and datetime.now(timezone.utc) < datetime.fromisoformat(cd["until"]):
            continue
        same_dir = sum(1 for p in state.positions.values() if p["side"] == side)
        if same_dir >= risk.max_same_direction:
            continue
        equity = get_equity(ex, cfg, state)
        stop_dist = cfg.stop_atr * float(row.atr)
        risk_scale = cfg.meanrev_risk_mult if sleeve == MEANREV else 1.0
        if cfg.enable_vol_target:
            risk_scale *= float(row.vol_scale)
        qty = position_size(equity, float(row.close), stop_dist,
                            open_notional(state, marks), risk, risk_scale)
        if qty <= 0:
            continue
        qty = adjust_quantity(ex, symbol, qty, float(row.close), stop_dist,
                              equity * cfg.risk_per_trade * risk_scale)
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
        notional = qty * float(row.close)
        risk_usdt = qty * abs(float(row.close) - stop)
        notifier.send(
            f"ACILDI {symbol} [{sleeve}] {side.upper()}\n"
            f"miktar : {qty:.6f} (~{notional:,.2f} USDT nominal)\n"
            f"giris  : {row.close:.4f}\n"
            f"stop   : {stop:.4f} (risk ~{risk_usdt:,.2f} USDT)"
        )


def main():
    cfg = Config()
    notifier = Notifier(cfg.telegram_token, cfg.telegram_chat_id)
    ex = make_exchange(cfg)
    ex.load_markets()
    if not cfg.dry_run:
        assert_key_safety(ex, cfg.testnet)
        for symbol in cfg.symbols:
            prepare_symbol(ex, symbol, int(cfg.max_leverage))
    state = State(os.path.join(cfg.state_dir, "positions.json"))
    params = StrategyParams(cfg.adx_threshold, cfg.stop_atr, cfg.breakeven_atr,
                            cfg.trail_atr, enable_regime=cfg.enable_regime,
                            rsi_oversold=cfg.rsi_oversold, rsi_overbought=cfg.rsi_overbought)
    risk = RiskParams(cfg.risk_per_trade, cfg.max_leverage,
                      cfg.max_position_notional_pct, cfg.monthly_kill_switch,
                      max_same_direction=cfg.max_same_direction)
    kill_switch = MonthlyKillSwitch(cfg.monthly_kill_switch)
    # kill-switch ay durumu restart'a dayanikli olsun
    now0 = datetime.now(timezone.utc)
    if state.ks and state.ks.get("month") == [now0.year, now0.month]:
        kill_switch.month = (now0.year, now0.month)
        kill_switch.month_start_equity = state.ks["start_equity"]
        kill_switch.tripped = state.ks["tripped"]

    mode = "DRY_RUN" if cfg.dry_run else ("TESTNET" if cfg.testnet else "CANLI")
    notifier.send(f"Bot basladi [{mode}] semboller: {', '.join(cfg.symbols)}")
    reconcile_state(ex, cfg, notifier, state)

    while True:
        try:
            marks = {}
            now = datetime.now(timezone.utc)
            equity = get_equity(ex, cfg, state)

            # Ay kapanisi: rapor + taban tamamlama talimati (nihai para yonetimi)
            prev_month = kill_switch.month
            prev_start = kill_switch.month_start_equity
            if prev_month is not None and (now.year, now.month) != prev_month:
                pnl = equity - prev_start
                pct = pnl / prev_start * 100 if prev_start else 0.0
                lines = [f"AY KAPANDI {prev_month[0]}-{prev_month[1]:02d}: "
                         f"{prev_start:,.2f} -> {equity:,.2f} USDT ({pnl:+,.2f} / {pct:+.1f}%)"]
                if cfg.capital_base > 0:
                    if equity < cfg.capital_base:
                        lines.append(f"TABAN KONTROLU: cepten {cfg.capital_base - equity:,.2f} USDT "
                                     "tamamla (Binance -> Futures cuzdanina transfer). "
                                     "Plan geregi: eksi ay normaldir, bot calismaya devam ediyor.")
                    else:
                        lines.append(f"TABAN KONTROLU: bakiye tabanin ({cfg.capital_base:,.0f}) ustunde "
                                     "- DOKUNMA, bilesik calissin.")
                notifier.send("\n".join(lines))

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
            if cfg.core_alerts:
                check_core_alerts(ex, cfg, notifier, state)
            if kill_switch.month:
                state.ks = {"month": list(kill_switch.month),
                            "start_equity": kill_switch.month_start_equity,
                            "tripped": kill_switch.tripped}
            state.save()
            # Kritik tarih hatirlatmalari: son 7 gun boyunca gunde bir kez
            if now.hour == cfg.heartbeat_hour:
                for date_s, msg in cfg.reminders:
                    try:
                        days_left = (date.fromisoformat(date_s) - now.date()).days
                    except ValueError:
                        continue
                    if 0 <= days_left <= 7:
                        notifier.send(f"!!! ACIL HATIRLATMA ({days_left} gun kaldi): {msg}")

            # Nabiz gunde BIR kez atilir (heartbeat_hour'daki dongude) - islem,
            # cekirdek ve hata bildirimleri her zaman aninda gider.
            if cfg.heartbeat and now.hour == cfg.heartbeat_hour:
                lines = [f"Gunluk nabiz | ozsermaye: {get_equity(ex, cfg, state):.2f} USDT"]
                if state.positions:
                    for k, v in state.positions.items():
                        sym = k.split("|")[0]
                        mark = marks.get(sym, v["entry"])
                        d = 1 if v["side"] == LONG else -1
                        upnl = v["qty"] * (mark - v["entry"]) * d
                        locked = " (stop girisin ustunde: kar kilitli)" if (
                            (v["side"] == LONG and v["stop"] > v["entry"]) or
                            (v["side"] == SHORT and v["stop"] < v["entry"])) else ""
                        lines.append(
                            f"{k.replace('|', ' ')} {v['side'].upper()}: "
                            f"giris {v['entry']:.4f} | guncel {mark:.4f} | "
                            f"stop {v['stop']:.4f}{locked} | pnl {upnl:+,.2f} USDT"
                        )
                else:
                    lines.append("acik pozisyon: yok")
                notifier.send("\n".join(lines))
        except Exception as exc:
            log.exception("Dongu hatasi")
            notifier.send(f"DONGU HATASI: {exc}")
        sleep_until_next_close()


if __name__ == "__main__":
    main()
