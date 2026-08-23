"""Stop temizligi: her semboldeki TUM acik emirleri iptal eder, sonra
defterdeki her pozisyon icin TEK dogru stop'u yeniden koyar.

Birikmis/sahipsiz stop emirlerini (orn. iptal filtresi bug'inin biriktirdigi
kopyalari) tek seferde duzeltmek icindir.

Kullanim (Railway Console): source /root/.profile && python -m bot.fixstops
"""
import os

from .config import Config
from .exchange import make_exchange
from .main import LONG, State, sync_stop_order
from .notifier import Notifier


def main():
    cfg = Config()
    ex = make_exchange(cfg)
    ex.load_markets()
    notifier = Notifier(cfg.telegram_token, cfg.telegram_chat_id)
    state = State(os.path.join(cfg.state_dir, "positions.json"))

    symbols = sorted({k.split("|")[0] for k in state.positions} | set(cfg.symbols))
    cancelled = 0
    for sym in symbols:
        try:
            seen = len(ex.fetch_open_orders(sym))
            # Kosulsuz cancel-all: API acik emir listesinde kosullu emirleri
            # gostermese bile borsa tarafinda hepsi iptal edilir.
            ex.cancel_all_orders(sym)
            cancelled += seen
            print(f"{sym}: cancel-all gonderildi (listede gorunen: {seen})")
        except Exception as exc:
            print(f"{sym}: iptal hatasi: {exc}")

    replaced = 0
    for key, pos in state.positions.items():
        sym = key.split("|")[0]
        sync_stop_order(ex, cfg, sym, pos["side"], pos["qty"], pos["stop"])
        replaced += 1
        print(f"{key}: stop yeniden kondu @ {pos['stop']:.4f}")

    msg = f"STOP TEMIZLIGI: {cancelled} emir iptal edildi, {replaced} dogru stop yeniden kondu."
    print(msg)
    notifier.send(msg)


if __name__ == "__main__":
    main()
