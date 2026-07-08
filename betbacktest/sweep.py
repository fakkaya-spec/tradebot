"""Harman ağırlığı (MARKET_BLEND) ve piyasa taraması.

Aday üretimi bir kez yapılır; her (w, piyasa) kombinasyonu için olasılıklar
yeniden harmanlanıp ORTA profil simüle edilir. Sezon bazlı ROI'ler yan yana
basılır ki hangi ayarın şansa değil istikrara dayandığı görülsün.

  python -m betbacktest.sweep --start 2023-07-01 --sports football
"""
import argparse
from collections import defaultdict
from datetime import date

from betbot import config
from . import data
from .engine import build_daily_candidates, metrics, simulate
from .run import WARMUP_SEASONS, yearly_breakdown


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2023-07-01")
    ap.add_argument("--end", default=str(date.today()))
    ap.add_argument("--sports", default="football,tennis")
    ap.add_argument("--profile", default="orta")
    args = ap.parse_args()
    start, end = date.fromisoformat(args.start), date.fromisoformat(args.end)
    sports = set(args.sports.split(","))
    first_year = start.year - WARMUP_SEASONS

    matches: list[dict] = []
    if "football" in sports:
        matches += data.load_football(first_year, end.year if end.month >= 8 else end.year - 1, quiet=True)
    if "tennis" in sports:
        matches += data.load_tennis(first_year, end.year, quiet=True)
    if "basketball" in sports:
        matches += data.load_nba(first_year, end.year - 1, quiet=True)
    matches.sort(key=lambda m: m["date"])
    prof = config.PROFILES[args.profile]
    import dataclasses
    print(f"{sum(1 for m in matches)} maç yüklendi\n")
    header = (f"{'fair':>5} {'fiyat':>5} {'w':>5} {'minE':>5} {'bahis':>6} "
              f"{'ROI':>7} {'çarpan':>7} {'maxDD':>6}  sezonlar")
    for fair_src in ("avg", "ps"):
        for best_price in (False, True):
            daily = build_daily_candidates(matches, start, end,
                                           best_price=best_price, fair_src=fair_src)
            print(header)
            for w in (0.85, 0.9, 0.95, 1.0):
                for me in (0.03, 0.05):
                    p2 = dataclasses.replace(prof, min_edge=me)
                    d2 = {}
                    for day, cands in daily.items():
                        for c in cands:
                            c.reblend(w)
                        d2[day] = cands
                    res = simulate(d2, p2)
                    m = metrics(res)
                    seasons = "  ".join(f"{y[2:4]}/{y[7:9]}:{r * 100:+.0f}%"
                                        for y, r in yearly_breakdown(res).items())
                    print(f"{fair_src:>5} {'max' if best_price else 'b365':>5} "
                          f"{w:>5.2f} {me:>5.0%} {m['bets']:>6} {m['roi'] * 100:>+6.1f}% "
                          f"{m['final_mult']:>6.2f}x {-m['maxdd'] * 100:>5.0f}%  {seasons}")
            print()


if __name__ == "__main__":
    main()
