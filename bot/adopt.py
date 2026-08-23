"""Yetim pozisyon kurtarma araci.

Borsada acik oldugu halde bot defterinde olmayan pozisyonlari yeniden
yonetime alir: deftere yazar, ATR bazli stop hesaplayip borsaya koyar.
(Ornegin hatali mutabakat pozisyonu dusurup stop'u iptal ettiyse.)

Kullanim (Railway Console): source /root/.profile && python -m bot.adopt
"""
import os

from .config import Config
from .exchange import fetch_ohlcv_df, make_exchange
from .indicators import add_indicators
from .main import State, sync_stop_order
from .notifier import Notifier
from .strategy import (LONG, SHORT, TREND, StrategyParams, initial_stop,
                       update_trailing_stop)


def main():
    cfg = Config()
    if cfg.dry_run:
        print("DRY_RUN modunda yetim pozisyon olamaz; cikiliyor.")
        return
    ex = make_exchange(cfg)
    ex.load_markets()
    notifier = Notifier(cfg.telegram_token, cfg.telegram_chat_id)
    state = State(os.path.join(cfg.state_dir, "positions.json"))
    params = StrategyParams(cfg.adx_threshold, cfg.stop_atr, cfg.breakeven_atr,
                            cfg.trail_atr, enable_regime=cfg.enable_regime)

    ids = {ex.market_id(s): s for s in cfg.symbols}
    adopted = 0
    for p in ex.fetch_positions():
        raw = str((p.get("info") or {}).get("symbol") or "")
        symbol = ids.get(raw)
        qty = float(p.get("contracts") or 0.0)
        if not symbol or not qty:
            continue
        side = LONG if p.get("side") == "long" else SHORT
        key = f"{symbol}|{TREND}"  # yetimler trend koluna yazilir
        if key in state.positions:
            print(f"{symbol}: zaten defterde, atlandi.")
            continue
        entry = float(p.get("entryPrice") or (p.get("info") or {}).get("entryPrice") or 0.0)
        df = add_indicators(fetch_ohlcv_df(ex, symbol, cfg.timeframe, limit=500), cfg)
        row = df.iloc[-1]
        atr, close = float(row.atr), float(row.close)
        entry = entry or close
        # stop: girise gore ilk stop + mevcut fiyati 'en iyi' sayarak trailing
        stop = initial_stop(side, entry, atr, params)
        stop = update_trailing_stop(TREND, side, entry, atr, close, atr, stop, params)
        state.positions[key] = {
            "side": side, "qty": abs(qty), "entry": entry, "stop": stop,
            "best": close, "entry_atr": atr, "entry_time": "adopt",
        }
        state.cooldowns.pop(key, None)
        sync_stop_order(ex, cfg, state, symbol)
        state.save()
        adopted += 1
        msg = (f"KURTARILDI {symbol} [{TREND}] {side.upper()}\n"
               f"miktar : {abs(qty):.6f}\ngiris  : {entry:.4f}\n"
               f"stop   : {stop:.4f} (borsaya yeniden kondu)\n"
               f"guncel : {close:.4f}")
        print(msg)
        notifier.send(msg)
    if not adopted:
        print("Yetim pozisyon bulunamadi - defter ve borsa uyumlu.")


if __name__ == "__main__":
    main()
