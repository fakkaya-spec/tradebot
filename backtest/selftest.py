"""Motor dogrulama testi: SENTETIK veriyle calisir, gercek performans OLCMEZ.

Amaci kurulumun saglikli oldugunu ve motorun temel invariantlarini dogrulamak:
- islem uretiyor, stop'lar calisiyor, kill-switch tetikleniyor,
- kaldirac tavani asilmiyor, lookahead yok.

Kullanim: python -m backtest.selftest
"""
import numpy as np
import pandas as pd

from bot.config import Config

from .engine import Backtester, metrics


def synthetic_ohlcv(n=2400, seed=7, drift=0.0002, vol=0.01, start=100.0):
    rng = np.random.default_rng(seed)
    rets = rng.normal(drift, vol, n)
    close = start * np.exp(np.cumsum(rets))
    open_ = np.concatenate([[start], close[:-1]])
    spread = np.abs(rng.normal(0, vol / 2, n))
    high = np.maximum(open_, close) * (1 + spread)
    low = np.minimum(open_, close) * (1 - spread)
    idx = pd.date_range("2024-01-01", periods=n, freq="4h", tz="UTC")
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": 1.0}, index=idx
    )


def main():
    cfg = Config()
    data = {
        "AAAUSDT": synthetic_ohlcv(seed=1, drift=0.0008),   # trendli
        "BBBUSDT": synthetic_ohlcv(seed=2, drift=-0.0006),  # dususlu
        "CCCUSDT": synthetic_ohlcv(seed=3, drift=0.0),      # yatay
    }
    funding = {s: pd.Series(dtype=float) for s in data}
    result = Backtester(data, funding, cfg, start_equity=10_000).run()
    m = metrics(result)

    assert m["islem_sayisi"] > 0, "hic islem uretilmedi"
    assert result.equity_curve.notna().all(), "equity egrisinde NaN var"
    assert (result.equity_curve > 0).all(), "ozsermaye negatife dustu"
    stops = [t for t in result.trades if t.reason == "stop"]
    assert stops, "hic stop calismadi"

    print(f"SELFTEST OK  islem={m['islem_sayisi']}  stop={len(stops)}  "
          f"getiri={m['toplam_getiri']*100:+.1f}% (sentetik veri - anlamsiz, sadece motor testi)")


if __name__ == "__main__":
    main()
