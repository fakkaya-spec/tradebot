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
        self.history = []    # kapanan islemler (haftalik rapor icin, son 100)
        self.week = {}       # haftalik rapor cipasi: {"equity": x, "date": iso}
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
            self.history = data.get("history", [])
            self.week = data.get("week", {})
            self.paper_equity = data.get("paper_equity", PAPER_START_EQUITY)

    def save(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w") as f:
            json.dump({"positions": self.positions, "cooldowns": self.cooldowns,
                       "core": self.core, "ks": self.ks,
                       "history": self.history[-100:], "week": self.week,
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
        net, seen = fetch_net_positions(ex, symbols)
        for sym in symbols:
            keys = [k for k in state.positions if k.split("|")[0] == sym]
            state_net = sum(state.positions[k]["qty"] * (1 if state.positions[k]["side"] == LONG else -1)
                            for k in keys)
            exchange_net = net.get(sym, 0.0)
            if sym not in seen:
                # Borsadan bu sembol icin veri gelmedi: SONUCSUZ. Asla silme,
                # asla emir iptal etme - sadece uyar ve kaydi koru.
                notifier.send(f"UYARI {sym}: mutabakatta borsadan dogrulanamadi, "
                              "kayit korunuyor (islem yapilmadi).")
                continue
            if abs(exchange_net) < 1e-12:
                for k in keys:
                    pos = state.positions.pop(k)
                    until = datetime.now(timezone.utc) + timedelta(hours=4 * cfg.cooldown_bars)
                    state.cooldowns[k] = {"side": pos["side"], "until": until.isoformat()}
                try:
                    ex.cancel_all_orders(sym)
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


def send_weekly_report(ex, cfg, notifier, state, marks, equity, now):
    """Pazar gunleri: kapanan islemler, haftalik P&L, acik pozisyonlar, beklenti."""
    lines = [f"=== HAFTALIK RAPOR ({now.strftime('%d.%m.%Y')}) ==="]

    prev_eq = state.week.get("equity")
    if prev_eq:
        diff = equity - prev_eq
        lines.append(f"Ozsermaye: {equity:,.2f} USDT (hafta: {diff:+,.2f} / {diff/prev_eq*100:+.1f}%)")
    else:
        lines.append(f"Ozsermaye: {equity:,.2f} USDT (ilk haftalik rapor)")

    week_ago = (now - timedelta(days=7)).isoformat()
    closed = [t for t in state.history if t.get("exit_time", "") >= week_ago]
    if closed:
        total = sum(t["pnl"] for t in closed)
        lines.append(f"--- Kapanan islemler ({len(closed)} adet, toplam {total:+,.2f} USDT):")
        for t in closed:
            lines.append(f"{t['symbol']} [{t['sleeve']}] {t['side'].upper()}: "
                         f"{t['entry']:.4f} -> {t['exit']:.4f} | {t['pnl']:+,.2f} USDT ({t['reason']})")
    else:
        lines.append("--- Bu hafta islem kapanmadi.")

    if state.positions:
        lines.append("--- Acik pozisyonlar:")
        for k, v in state.positions.items():
            sym = k.split("|")[0]
            mark = marks.get(sym, v["entry"])
            d = 1 if v["side"] == LONG else -1
            upnl = v["qty"] * (mark - v["entry"]) * d
            lines.append(f"{k.replace('|', ' ')} {v['side'].upper()}: giris {v['entry']:.4f} | "
                         f"stop {v['stop']:.4f} | pnl {upnl:+,.2f} USDT")
    else:
        lines.append("--- Acik pozisyon yok.")

    lines.append("--- Beklenti (uc kilit: EMA200 tarafi / momentum / ADX>25):")
    for symbol in cfg.symbols:
        try:
            df = add_indicators(fetch_ohlcv_df(ex, symbol, cfg.timeframe, limit=500), cfg)
            row = df.iloc[-1]
            macro = "ustu" if row.close > row.ema_macro else "alti"
            mom = "yukari" if row.ema_fast > row.ema_slow else "asagi"
            locks = [row.close > row.ema_macro, row.ema_fast > row.ema_slow,
                     row.adx > cfg.adx_threshold]
            if all(locks):
                verdict = "LONG kosullari tamam/yakin"
            elif not locks[0] and not locks[1] and locks[2]:
                verdict = "SHORT kosullari tamam/yakin"
            else:
                verdict = "bekleniyor"
            lines.append(f"{symbol}: EMA200 {macro}, momentum {mom}, "
                         f"ADX {row.adx:.0f} -> {verdict}")
        except Exception as exc:
            log.warning("%s beklenti hesabi basarisiz: %s", symbol, exc)

    notifier.send("\n".join(lines))
    state.week = {"equity": equity, "date": now.isoformat()}


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


def sync_stop_order(ex, cfg, state, symbol):
    """Semboldeki stop emirlerini SIFIRDAN kurar: once cancel-all, sonra
    defterdeki her pozisyon icin tek STOP_MARKET.

    'Listele-bul-iptal et' yontemi kullanilmaz: bazi ccxt surumleri kosullu
    emirleri acik emir listesinde gostermiyor ve bu, stop birikmesine yol
    aciyordu. Cancel-all borsa tarafinda listeden bagimsiz calisir.
    """
    if cfg.dry_run:
        log.info("[DRY_RUN] %s stoplar senkronlanirdi", symbol)
        return
    try:
        ex.cancel_all_orders(symbol)
    except Exception as exc:
        log.warning("%s cancel-all hatasi: %s", symbol, exc)
    for key, pos in state.positions.items():
        if key.split("|")[0] != symbol:
            continue
        close_side = "sell" if pos["side"] == LONG else "buy"
        # workingType=MARK_PRICE: tek barlik manipulatif igneler tetikleyemez.
        ex.create_order(symbol, "STOP_MARKET", close_side, pos["qty"], None,
                        {"stopPrice": pos["stop"], "reduceOnly": True,
                         "workingType": "MARK_PRICE"})


def close_position(ex, cfg, notifier, state, key, pos, price, reason):
    symbol = key.split("|")[0]
    side = "sell" if pos["side"] == LONG else "buy"
    try:
        place_market(ex, cfg, symbol, side, pos["qty"], reduce_only=True)
    except Exception as exc:
        if "-2022" in str(exc) or "ReduceOnly" in str(exc):
            # Pozisyon borsada zaten kapali (stop borsa tarafinda tetiklenmis).
            # Defteri temizlemeye devam et; PnL stop fiyati uzerinden tahminidir.
            reason = reason + " (borsada zaten kapanmisti)"
        else:
            raise
    pnl = pos["qty"] * (price - pos["entry"]) * (1 if pos["side"] == LONG else -1)
    if cfg.dry_run:
        state.paper_equity += pnl
    del state.positions[key]
    if reason == "stop":
        until = datetime.now(timezone.utc) + timedelta(hours=4 * cfg.cooldown_bars)
        state.cooldowns[key] = {"side": pos["side"], "until": until.isoformat()}
    if not cfg.dry_run:
        try:
            sync_stop_order(ex, cfg, state, symbol)
        except Exception as exc:
            log.warning("%s kapanis sonrasi stop senkronu: %s", symbol, exc)
    state.history.append({
        "symbol": symbol, "sleeve": key.split("|")[1], "side": pos["side"],
        "entry": pos["entry"], "exit": price, "qty": pos["qty"], "pnl": pnl,
        "reason": reason, "entry_time": pos.get("entry_time", ""),
        "exit_time": datetime.now(timezone.utc).isoformat(),
    })
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
                sync_stop_order(ex, cfg, state, symbol)
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
        sync_stop_order(ex, cfg, state, symbol)
        notional = qty * float(row.close)
        risk_usdt = qty * abs(float(row.close) - stop)
        entry_price, atr = float(row.close), float(row.atr)
        d = 1 if side == LONG else -1
        yon = "yukari" if side == LONG else "asagi"
        if sleeve == "trend":
            neden = (f"NEDEN: fiyat EMA200'un {'USTUNDE' if side == LONG else 'ALTINDA'} "
                     f"({float(row.ema_macro):.4f}) + EMA20 {'>' if side == LONG else '<'} EMA50 "
                     f"(momentum {yon}) + ADX {float(row.adx):.1f} > {cfg.adx_threshold:.0f} "
                     f"(trend guclu) -> uc kosul tamam, trend {side} sinyali.")
        else:
            level = float(row.don_hi) if side == LONG else float(row.don_lo)
            neden = (f"NEDEN: fiyat 20 gunluk {'zirveyi' if side == LONG else 'dibi'} "
                     f"({level:.4f}) {yon} yonde kirip kapatti + EMA200 filtresi uyumlu "
                     f"-> kirilim {side} sinyali.")
        if sleeve == "trend":
            be_trigger = entry_price + d * cfg.breakeven_atr * atr
            plan = (
                f"PLAN: Fiyat {be_trigger:.4f} seviyesini gorurse stop girise cekilir "
                f"(kayip riski biter). Sonrasinda stop, en iyi fiyatin {cfg.trail_atr:.0f}xATR "
                f"(~{cfg.trail_atr * atr:.4f}) gerisinden {yon} takip eder - sabit hedef yok, "
                f"trend surdukce tasinir. Trend donerse (EMA20/50 kesisimi) pozisyon kapatilir. "
                f"Stop asla aleyhte yonde oynatilmaz."
            )
        else:  # breakout
            plan = (
                f"PLAN: Stop sabittir ({stop:.4f}), trailing yapilmaz. Cikis: fiyat 10 gunluk "
                f"kanalin karsi tarafina kapanirsa kar alinir/kesilir. Kirilim devam ederse "
                f"pozisyon kanal boyunca tasinir."
            )
        notifier.send(
            f"ACILDI {symbol} [{sleeve}] {side.upper()}\n"
            f"miktar : {qty:.6f} (~{notional:,.2f} USDT nominal)\n"
            f"giris  : {entry_price:.4f}\n"
            f"stop   : {stop:.4f} (risk ~{risk_usdt:,.2f} USDT)\n"
            f"{neden}\n"
            f"{plan}"
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
            # Her dongude borsayla mutabakat: bot uyurken borsada kapanan
            # pozisyonlar (stop) defterden dusulur - ReduceOnly hatasi olusmaz.
            reconcile_state(ex, cfg, notifier, state)
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

            # Haftalik rapor: Pazar gunleri nabiz saatinde
            if (cfg.weekly_report and now.weekday() == cfg.weekly_report_day
                    and now.hour == cfg.heartbeat_hour):
                send_weekly_report(ex, cfg, notifier, state, marks,
                                   get_equity(ex, cfg, state), now)
        except Exception as exc:
            log.exception("Dongu hatasi")
            notifier.send(f"DONGU HATASI: {exc}")
        sleep_until_next_close()


if __name__ == "__main__":
    main()
