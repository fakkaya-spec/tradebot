"""Stop dogrulama araci: defter vs borsa karsilastirmasi.

Her defter pozisyonu icin borsada STOP emri var mi, fiyati/miktari dogru mu
kontrol eder; sahipsiz (pozisyonsuz) stop emirlerini de raporlar.

Kullanim (Railway Console): source /root/.profile && python -m bot.checkstops
"""
import os

from .config import Config
from .exchange import fetch_net_positions, make_exchange
from .main import State


def stop_orders(ex, symbol):
    """Kosullu emir deposundan okur ({'stop': True}) - STOP_MARKET'lerin
    gercekten yasadigi yer (probe ile dogrulandi)."""
    from .exchange import fetch_conditional_orders
    out = []
    for o in fetch_conditional_orders(ex, symbol):
        info = o.get("info") or {}
        price = (o.get("stopPrice") or o.get("triggerPrice")
                 or info.get("stopPrice") or info.get("triggerPrice") or 0)
        out.append({"price": float(price or 0),
                    "qty": float(o.get("amount") or info.get("origQty") or 0),
                    "side": (o.get("side") or "").lower()})
    return out


def main():
    cfg = Config()
    ex = make_exchange(cfg)
    ex.load_markets()
    state = State(os.path.join(cfg.state_dir, "positions.json"))

    symbols = sorted({k.split("|")[0] for k in state.positions} | set(cfg.symbols))
    net, seen = fetch_net_positions(ex, symbols)
    print("=" * 64)
    print("  STOP DENETIMI  (defter vs borsa)")
    print("=" * 64)

    if not state.positions:
        print("  Defterde acik pozisyon yok.")
    for key, pos in state.positions.items():
        sym = key.split("|")[0]
        stops = stop_orders(ex, sym)
        exch_qty = net.get(sym, 0.0)
        print(f"  {key} {pos['side'].upper()} qty={pos['qty']} giris={pos['entry']:.4f}")
        print(f"    defter stop : {pos['stop']:.4f}")
        print(f"    borsa poz   : {exch_qty:+.6f} "
              f"{'OK' if abs(abs(exch_qty) - pos['qty']) < 1e-6 else 'UYARI: miktar uyusmuyor!'}")
        if not stops:
            print("    borsa stop  : YOK  !!! KORUMASIZ - bot bir sonraki dongude koyar,")
            print("                  beklemek istemezsen: python -m bot.adopt")
        else:
            for s in stops:
                diff = abs(s["price"] - pos["stop"]) / pos["stop"] * 100 if pos["stop"] else 0
                durum = "OK" if diff < 0.5 else f"UYARI: defterden %{diff:.1f} sapma"
                print(f"    borsa stop  : {s['price']:.4f} ({s['side']}, qty={s['qty']}) {durum}")

    # sahipsiz stop emirleri (pozisyonu olmayan sembollerde)
    for sym in symbols:
        if any(k.split("|")[0] == sym for k in state.positions):
            continue
        stops = stop_orders(ex, sym)
        if stops:
            print(f"  {sym}: pozisyon yok ama {len(stops)} stop emri duruyor -> SAHIPSIZ,")
            print(f"    temizlemek icin bot bir sonraki mutabakatta iptal eder.")
    print("=" * 64)


if __name__ == "__main__":
    main()
