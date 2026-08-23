"""Emir deposu sondasi: kosullu (stop) emirleri HANGI endpoint goruyor?

Hicbir sey degistirmez, sadece okur ve raporlar.
Kullanim (Railway Console): source /root/.profile && python -m bot.probe
"""
import ccxt

from .config import Config
from .exchange import make_exchange

SYMBOL = "BTC/USDT"


def try_call(label, fn):
    try:
        rows = fn()
        n = len(rows) if isinstance(rows, list) else "?"
        print(f"  {label:<48} -> {n} emir")
        if isinstance(rows, list) and rows:
            r0 = rows[0]
            info = r0.get("info", r0) if isinstance(r0, dict) else {}
            print(f"      ornek: type={info.get('type') or info.get('strategyType')} "
                  f"stopPrice={info.get('stopPrice')} id={info.get('orderId') or info.get('strategyId')}")
    except Exception as exc:
        print(f"  {label:<48} -> HATA: {str(exc)[:90]}")


def main():
    cfg = Config()
    ex = make_exchange(cfg)
    ex.load_markets()
    mid = ex.market_id(SYMBOL)
    print(f"ccxt {ccxt.__version__} | sembol {SYMBOL} (id={mid}) | "
          f"portfolioMargin={ex.options.get('portfolioMargin')}")
    print("-" * 70)
    try_call("unified fetch_open_orders", lambda: ex.fetch_open_orders(SYMBOL))
    try_call("unified + {'stop': True}", lambda: ex.fetch_open_orders(SYMBOL, params={"stop": True}))
    try_call("unified + {'trigger': True}", lambda: ex.fetch_open_orders(SYMBOL, params={"trigger": True}))
    try_call("unified + {'conditional': True}", lambda: ex.fetch_open_orders(SYMBOL, params={"conditional": True}))
    try_call("raw fapi GET openOrders", lambda: ex.fapiPrivateGetOpenOrders({"symbol": mid}))
    for meth, arg in [
        ("fapiPrivateGetConditionalOpenOrders", {"symbol": mid}),
        ("papiGetUmOpenOrders", {"symbol": mid}),
        ("papiGetUmConditionalOpenOrders", {"symbol": mid}),
    ]:
        fn = getattr(ex, meth, None)
        if fn is None:
            print(f"  {meth:<48} -> metod yok")
        else:
            try_call(meth, lambda f=fn, a=arg: f(a))
    print("-" * 70)
    print("Bu ciktiyi Claude'a yapistir - 3 stop'u goren satir, dogru endpoint'tir.")


if __name__ == "__main__":
    main()
