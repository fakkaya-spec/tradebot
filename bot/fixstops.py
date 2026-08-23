"""Stop temizligi + teshis: her adimda ham emir sayisini raporlar.

Kullanim (Railway Console): source /root/.profile && python -m bot.fixstops
"""
import os

from .config import Config
from .exchange import (cancel_all_symbol_orders, fetch_conditional_orders,
                       make_exchange)
from .main import LONG, State
from .notifier import Notifier


def raw_count(ex, symbol) -> int:
    """Iki deponun toplami: kosullu (stop) + klasik."""
    try:
        klasik = len(ex.fapiPrivateGetOpenOrders({"symbol": ex.market_id(symbol)}))
    except Exception:
        klasik = 0
    return len(fetch_conditional_orders(ex, symbol)) + klasik


def main():
    cfg = Config()
    ex = make_exchange(cfg)
    ex.load_markets()
    notifier = Notifier(cfg.telegram_token, cfg.telegram_chat_id)
    state = State(os.path.join(cfg.state_dir, "positions.json"))

    symbols = sorted({k.split("|")[0] for k in state.positions} | set(cfg.symbols))
    total_placed = 0
    for sym in symbols:
        before = raw_count(ex, sym)
        ok = cancel_all_symbol_orders(ex, sym)
        after = raw_count(ex, sym)
        print(f"{sym}: emir {before} -> cancel-all({'OK' if ok else 'HATA'}) -> {after}")

        placed = 0
        for key, pos in state.positions.items():
            if key.split("|")[0] != sym:
                continue
            close_side = "sell" if pos["side"] == LONG else "buy"
            try:
                ex.create_order(sym, "STOP_MARKET", close_side, pos["qty"], None,
                                {"stopPrice": pos["stop"], "reduceOnly": True,
                                 "workingType": "MARK_PRICE"})
                placed += 1
                print(f"  {key}: stop kuruldu @ {pos['stop']:.4f}")
            except Exception as exc:
                print(f"  {key}: STOP KURULAMADI: {exc}")
        total_placed += placed
        print(f"{sym}: son durum = {raw_count(ex, sym)} emir (beklenen: {placed})")

    msg = f"STOP TEMIZLIGI: tamamlandi, {total_placed} stop kuruldu. Detay console ciktisinda."
    print(msg)
    notifier.send(msg)


if __name__ == "__main__":
    main()
