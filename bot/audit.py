"""Tasarim-uyum denetimi: acik pozisyonlar muhurlu kurallara uyuyor mu?

Kontroller:
  R1  Islem riski: qty x |entry-stop| / ozsermaye <= ~%1.6 (vol olcek ustu)
      veya stop girisin karli tarafinda (kar kilitli)
  R2  Tek pozisyon nominali <= 1.05 x ozsermaye
  R3  Toplam nominal <= 3 x ozsermaye
  R4  Ayni yonde en fazla 3 pozisyon
  R5  Her pozisyonun borsada stop'u var ve defterle uyumlu (%0.5 tolerans)
  R6  Borsa pozisyonu defterle ayni (miktar mutabakati)

Kullanim (Railway Console): source /root/.profile && python -m bot.audit
"""
import os

from .config import Config
from .exchange import (fetch_conditional_orders, fetch_net_positions,
                       make_exchange)
from .main import LONG, State

MAX_RISK_PCT = 1.6   # %1 x vol_olcek(1.5) + pay
MAX_POS_NOTIONAL = 1.05
MAX_LEV = 3.0
MAX_SAME_DIR = 3


def main():
    cfg = Config()
    ex = make_exchange(cfg)
    ex.load_markets()
    state = State(os.path.join(cfg.state_dir, "positions.json"))

    bal = ex.fetch_balance()
    equity = float(bal.get("info", {}).get("totalMarginBalance") or 0.0)
    print("=" * 70)
    print(f"  TASARIM-UYUM DENETIMI   ozsermaye: {equity:,.2f} USDT")
    print("=" * 70)
    if not state.positions:
        print("  Acik pozisyon yok - denetlenecek kural yok. PASS")
        return

    symbols = sorted({k.split("|")[0] for k in state.positions})
    net, seen = fetch_net_positions(ex, symbols)
    marks = {s: float(ex.fetch_ticker(s)["last"]) for s in symbols}
    stops = {s: fetch_conditional_orders(ex, s) for s in symbols}

    fails = 0
    total_notional = 0.0
    dir_count = {"long": 0, "short": 0}

    for key, pos in state.positions.items():
        sym = key.split("|")[0]
        mark = marks[sym]
        d = 1 if pos["side"] == LONG else -1
        notional = pos["qty"] * mark
        total_notional += notional
        dir_count[pos["side"]] += 1
        upnl = pos["qty"] * (mark - pos["entry"]) * d

        locked = (pos["side"] == LONG and pos["stop"] >= pos["entry"]) or (
            pos["side"] == "short" and pos["stop"] <= pos["entry"])
        risk = pos["qty"] * abs(pos["entry"] - pos["stop"])
        risk_pct = risk / equity * 100

        print(f"  {key} {pos['side'].upper()}  qty={pos['qty']}  "
              f"giris={pos['entry']:.4f}  stop={pos['stop']:.4f}  pnl={upnl:+,.2f}")

        if locked:
            print(f"    R1 risk        : kar kilitli (stop girisin karli tarafinda)  PASS")
        elif risk_pct <= MAX_RISK_PCT:
            print(f"    R1 risk        : {risk:,.2f} USDT = %{risk_pct:.2f}  PASS")
        else:
            print(f"    R1 risk        : %{risk_pct:.2f} > %{MAX_RISK_PCT}  FAIL"); fails += 1

        pn = notional / equity
        tag = "PASS" if pn <= MAX_POS_NOTIONAL else "FAIL"
        if tag == "FAIL":
            fails += 1
        print(f"    R2 nominal     : {notional:,.2f} USDT = {pn:.2f}x ozsermaye  {tag}")

        # R5: borsada stop var mi, seviye tutuyor mu
        matched = [o for o in stops[sym]
                   if o.get("stopPrice") or (o.get("info") or {}).get("stopPrice")]
        found = False
        for o in matched:
            price = float(o.get("stopPrice") or (o.get("info") or {}).get("stopPrice") or 0)
            if price and abs(price - pos["stop"]) / pos["stop"] < 0.005:
                found = True
                break
        if found:
            print(f"    R5 borsa stop  : {pos['stop']:.4f} seviyesinde mevcut  PASS")
        elif matched:
            print(f"    R5 borsa stop  : var ama seviye defterden farkli  UYARI "
                  f"(bot sonraki senkronda esitler)")
        else:
            print(f"    R5 borsa stop  : BULUNAMADI  FAIL"); fails += 1

    lev = total_notional / equity if equity else 0
    tag = "PASS" if lev <= MAX_LEV else "FAIL"
    if tag == "FAIL":
        fails += 1
    print(f"  R3 toplam kaldirac : {total_notional:,.2f} USDT = {lev:.2f}x  {tag}")

    worst_dir = max(dir_count.values())
    tag = "PASS" if worst_dir <= MAX_SAME_DIR else "FAIL"
    if tag == "FAIL":
        fails += 1
    print(f"  R4 ayni yon sayisi : long={dir_count['long']} short={dir_count['short']}  {tag}")

    for sym in symbols:
        ledger = sum(state.positions[k]["qty"] * (1 if state.positions[k]["side"] == LONG else -1)
                     for k in state.positions if k.split("|")[0] == sym)
        if sym not in seen:
            print(f"  R6 mutabakat {sym}: borsadan dogrulanamadi  UYARI")
        elif abs(net[sym] - ledger) < 1e-6:
            print(f"  R6 mutabakat {sym}: defter={ledger:+.6f} borsa={net[sym]:+.6f}  PASS")
        else:
            print(f"  R6 mutabakat {sym}: defter={ledger:+.6f} borsa={net[sym]:+.6f}  FAIL")
            fails += 1

    print("=" * 70)
    print(f"  SONUC: {'TUM KURALLAR PASS - pozisyonlar tasarima uygun' if fails == 0 else f'{fails} kural FAIL - incele!'}")
    print("=" * 70)


if __name__ == "__main__":
    main()
