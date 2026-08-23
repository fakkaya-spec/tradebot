"""Stop temizligi: her sembolde cancel-all + defterden dogru stoplari kurar.

Kullanim (Railway Console): source /root/.profile && python -m bot.fixstops
"""
import os

from .config import Config
from .exchange import make_exchange
from .main import State, sync_stop_order
from .notifier import Notifier


def main():
    cfg = Config()
    ex = make_exchange(cfg)
    ex.load_markets()
    notifier = Notifier(cfg.telegram_token, cfg.telegram_chat_id)
    state = State(os.path.join(cfg.state_dir, "positions.json"))

    symbols = sorted({k.split("|")[0] for k in state.positions} | set(cfg.symbols))
    placed = 0
    for sym in symbols:
        sync_stop_order(ex, cfg, state, sym)  # cancel-all + defterden yeniden kur
        n = sum(1 for k in state.positions if k.split("|")[0] == sym)
        placed += n
        print(f"{sym}: cancel-all + {n} stop kuruldu")

    msg = f"STOP TEMIZLIGI: tum semboller sifirlandi, {placed} dogru stop kuruldu."
    print(msg)
    notifier.send(msg)


if __name__ == "__main__":
    main()
