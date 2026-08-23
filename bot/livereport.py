"""Canli performans raporu: Binance income kayitlarindan ay ay P&L.

Gercek kaynak borsanin kendisidir: gerceklesen kar/zarar, funding, komisyon
ve transferler (yatirilan/cekilen) ay bazinda ayristirilir; backtest'teki
aylik tablo formatinda basilir. Yatirimlar karla KARISTIRILMAZ.

Kullanim (Railway Console): source /root/.profile && python -m bot.livereport
"""
from collections import defaultdict
from datetime import datetime, timezone

from .config import Config
from .exchange import make_exchange

START = datetime(2026, 7, 1, tzinfo=timezone.utc)  # canliya cikis ayi

TRADE_TYPES = {"REALIZED_PNL"}
FUNDING_TYPES = {"FUNDING_FEE"}
FEE_TYPES = {"COMMISSION"}
TRANSFER_TYPES = {"TRANSFER", "INTERNAL_TRANSFER", "WELCOME_BONUS"}


def fetch_income(ex, start_ms: int):
    rows, cursor = [], start_ms
    while True:
        batch = ex.fapiPrivateGetIncome({"startTime": cursor, "limit": 1000})
        if not batch:
            break
        rows.extend(batch)
        if len(batch) < 1000:
            break
        cursor = int(batch[-1]["time"]) + 1
    return rows


def main():
    cfg = Config()
    ex = make_exchange(cfg)
    ex.load_markets()

    rows = fetch_income(ex, int(START.timestamp() * 1000))
    monthly = defaultdict(lambda: {"pnl": 0.0, "funding": 0.0, "fee": 0.0,
                                   "transfer": 0.0, "win": 0, "loss": 0})
    for r in rows:
        t = datetime.fromtimestamp(int(r["time"]) / 1000, tz=timezone.utc)
        key = f"{t.year}-{t.month:02d}"
        amt = float(r["income"])
        it = r["incomeType"]
        m = monthly[key]
        if it in TRADE_TYPES:
            m["pnl"] += amt
            if amt > 0:
                m["win"] += 1
            elif amt < 0:
                m["loss"] += 1
        elif it in FUNDING_TYPES:
            m["funding"] += amt
        elif it in FEE_TYPES:
            m["fee"] += amt
        elif it in TRANSFER_TYPES:
            m["transfer"] += amt

    bal = ex.fetch_balance()
    info = bal.get("info", {})
    wallet_now = float(info.get("totalWalletBalance") or 0.0)
    equity_now = float(info.get("totalMarginBalance") or wallet_now)
    unrealized = equity_now - wallet_now

    keys = sorted(monthly.keys())
    # ay basi cuzdanlarini bugunden geriye dogru kur
    starts = {}
    w = wallet_now
    for key in reversed(keys):
        m = monthly[key]
        w -= (m["pnl"] + m["funding"] + m["fee"] + m["transfer"])
        starts[key] = w

    line = "=" * 74
    print(line)
    print("  CANLI PERFORMANS  (kaynak: Binance income kayitlari, Temmuz'dan itibaren)")
    print(line)
    print(f"  Su an: ozsermaye {equity_now:,.2f} USDT "
          f"(cuzdan {wallet_now:,.2f} + acik poz. {unrealized:+,.2f})")
    print(line)
    print(f"  {'AY':<8}{'baslangic':>11}{'islem PnL':>11}{'funding':>9}"
          f"{'komisyon':>10}{'NET':>10}{'net %':>8}{'yatirilan':>11}")
    total_net, total_dep = 0.0, 0.0
    for key in keys:
        m = monthly[key]
        net = m["pnl"] + m["funding"] + m["fee"]
        # yuzde tabani: ay basi cuzdan + o ay yatirilan (ay ortasi yatirimlar
        # yuzdeyi sacmalatmasin)
        base = max(starts[key] + m["transfer"], 1.0)
        pct = net / base * 100
        bar = "#" * min(60, int(abs(pct) * 2))
        total_net += net
        total_dep += m["transfer"]
        print(f"  {key:<8}{starts[key]:>11,.0f}{m['pnl']:>+11,.2f}{m['funding']:>+9,.2f}"
              f"{m['fee']:>+10,.2f}{net:>+10,.2f}{pct:>+7.1f}%{m['transfer']:>+11,.0f}  {bar}")
        print(f"  {'':8}kapanis kayitlari: arti {m['win']} / eksi {m['loss']}")
    print(line)
    print(f"  TOPLAM: islem neti {total_net:+,.2f} USDT (gerceklesen) | "
          f"yatirilan {total_dep:+,.0f} USDT | acik poz. {unrealized:+,.2f} USDT")
    print(line)
    print("  Backtest karakteri (kiyas): tipik ay -8%..+10%, yilda 2-4 ay +20%+;")
    print("  ayrinti: not 'kapanis kayitlari' fill bazlidir (bir islem birden cok")
    print("  fill olabilir), islem sayisiyla birebir degildir.")


if __name__ == "__main__":
    main()
