"""Kapanan islemlerin poz poz dokumu (botun kendi defteri, son 100 islem).

Not: PnL fiyat bazli tahmindir (komisyon/funding haric). Kurus kurus gercek
sonuc icin bot.livereport kullanilir - o Binance'in resmi kayitlarini okur.

Kullanim (Railway Console): source /root/.profile && python -m bot.tradelist
"""
import os
from collections import defaultdict
from datetime import datetime

from .config import Config
from .main import LONG, State


def _fmt_ts(s: str) -> str:
    try:
        return datetime.fromisoformat(s).strftime("%d.%m.%Y %H:%M")
    except (ValueError, TypeError):
        return (s or "?")[:16]


def main():
    cfg = Config()
    state = State(os.path.join(cfg.state_dir, "positions.json"))
    hist = state.history
    line = "=" * 78
    print(line)
    print(f"  ISLEM DEFTERI  (kapanan {len(hist)} islem; defter son 100'u saklar)")
    print(line)
    if not hist:
        print("  Kayitli kapanan islem yok.")
        return

    total, wins, losses = 0.0, 0, 0
    by = {"kol": defaultdict(lambda: [0, 0.0]),
          "sembol": defaultdict(lambda: [0, 0.0]),
          "sebep": defaultdict(lambda: [0, 0.0])}
    for t in hist:
        pnl = t.get("pnl", 0.0)
        total += pnl
        wins += pnl > 0
        losses += pnl < 0
        for k, key in (("kol", t.get("sleeve", "?")), ("sembol", t.get("symbol", "?")),
                       ("sebep", t.get("reason", "?"))):
            by[k][key][0] += 1
            by[k][key][1] += pnl
        yon = "L" if t.get("side") == LONG else "S"
        print(f"  {_fmt_ts(t.get('entry_time')):<17}-> {_fmt_ts(t.get('exit_time')):<17}"
              f"{t.get('symbol','?'):<9}{t.get('sleeve','?'):<9}{yon}  "
              f"{t.get('entry',0):>10,.2f} -> {t.get('exit',0):>10,.2f}  "
              f"{t.get('pnl',0):>+9,.2f} USDT  ({t.get('reason','?')})")

    print(line)
    n = len(hist)
    print(f"  TOPLAM: {total:+,.2f} USDT (fiyat bazli, komisyon/funding haric) | "
          f"kazanan {wins} ({wins/n*100:.0f}%) / kaybeden {losses}")
    for grp in ("kol", "sembol", "sebep"):
        parts = [f"{k}: {v[0]} islem {v[1]:+,.0f}$" for k, v in sorted(by[grp].items())]
        print(f"  {grp.upper():<7}: " + " | ".join(parts))
    print(line)
    if state.positions:
        print("  ACIK POZISYONLAR (defterde):")
        for k, v in state.positions.items():
            print(f"    {k.replace('|', ' ')} {v['side'].upper()}  qty={v['qty']}  "
                  f"giris={v['entry']:.4f}  stop={v['stop']:.4f}  "
                  f"acilis={_fmt_ts(v.get('entry_time'))}")
    else:
        print("  Acik pozisyon yok.")
    print(line)


if __name__ == "__main__":
    main()
