"""Sentetik veriyle motor doğrulaması — ağ gerektirmez.

  python -m betbacktest.selftest
"""
import math
import random
from datetime import date, timedelta

from betbot import config
from betbot.models.elo import NbaElo, TennisElo
from betbot.models.football import FootballPoisson
from betbot.picker import Candidate, daily_picks, demargin, kelly
from .engine import metrics, simulate


def check(name: str, cond: bool, detail: str = "") -> bool:
    print(f"  [{'OK' if cond else 'HATA'}] {name}" + (f" — {detail}" if detail else ""))
    return cond


def test_math() -> bool:
    ok = True
    ok &= check("kelly p=0.55 @2.00 = 0.10", abs(kelly(0.55, 2.0) - 0.10) < 1e-9)
    ok &= check("kelly edge yoksa 0", kelly(0.4, 2.0) == 0.0)
    fair = demargin([1.9, 1.9])
    ok &= check("demargin simetrik", abs(fair[0] - 0.5) < 1e-9)
    return ok


def test_picker() -> bool:
    rng = random.Random(7)
    cands = []
    for i in range(30):
        p = rng.uniform(0.3, 0.6)
        price = (1.0 + rng.uniform(0.0, 0.15)) / p  # %0-15 edge
        cands.append(Candidate("2024-01-01", "football", "L", f"m{i}", "1X2", "H",
                               min(max(price, 1.5), 4.5), p))
    picks = daily_picks(cands, config.MEDIUM)
    ok = check("top-N seçimi <= MAX_DAILY_PICKS", len(picks) <= config.MAX_DAILY_PICKS)
    edges = [c.edge for c, _ in picks]
    ok &= check("edge'e göre sıralı", edges == sorted(edges, reverse=True))
    ok &= check("stake tavanı", all(s <= config.MEDIUM.stake_cap + 1e-9 for _, s in picks))
    ok &= check("min edge filtresi", all(c.edge >= config.MEDIUM.min_edge for c, _ in picks))
    return ok


def test_simulation_edge() -> bool:
    """Gerçek %5 edge'li bahisler -> uzun vadede banka büyümeli, ROI ~%5."""
    rng = random.Random(42)
    daily = {}
    d = date(2023, 1, 1)
    for _ in range(365 * 3):
        cands = []
        for i in range(6):
            p_true = rng.uniform(0.35, 0.6)
            price = 1.05 / p_true  # tam %5 gerçek edge
            if not (config.MIN_ODDS <= price <= config.MAX_ODDS):
                continue
            c = Candidate(str(d), "tennis", "T", f"m{d}-{i}", "ML", "A", price, p_true)
            c.won = rng.random() < p_true
            cands.append(c)
        if cands:
            daily[d] = cands
        d += timedelta(days=1)
    res = simulate(daily, config.MEDIUM)
    m = metrics(res)
    ok = check("banka büyüdü", m["final_mult"] > 1.5, f"{m['final_mult']:.2f}x")
    ok &= check("ROI %5 civarı", 0.0 < m["roi"] < 0.12, f"{m['roi']:.3f}")
    ok &= check("drawdown sınırlı", m["maxdd"] < 0.35, f"{m['maxdd']:.2f}")
    return ok


def test_kill_switch() -> bool:
    """Hep kaybeden bahisler -> kill-switch her ay tetiklenmeli, banka sıfırlanmamalı."""
    daily = {}
    d = date(2023, 1, 1)
    for _ in range(120):
        cands = []
        for i in range(5):
            c = Candidate(str(d), "football", "L", f"m{d}-{i}", "1X2", "H", 2.5, 0.55)
            c.won = False
            cands.append(c)
        daily[d] = cands
        d += timedelta(days=1)
    res = simulate(daily, config.MEDIUM)
    ok = check("kill-switch tetiklendi", len(res.kill_months) >= 3,
               f"{res.kill_months}")
    ok &= check("aylık zarar sınırlı", res.final_bank
                > res.start_bank * (1 - config.MEDIUM.monthly_kill - 0.10) ** 4,
                f"kalan: {res.final_bank:.0f}")
    return ok


def test_football_model() -> bool:
    """Bilinen güçlerdeki sentetik ligde model öğreniyor mu? (log-loss testi)"""
    rng = random.Random(1)
    teams = [f"T{i}" for i in range(16)]
    strength = {t: rng.uniform(-0.35, 0.35) for t in teams}
    model = FootballPoisson()

    def play(h, a):
        lh = 1.4 * math.exp(strength[h] - strength[a] * 0.5 + 0.25)
        la = 1.4 * math.exp(strength[a] - strength[h] * 0.5)
        gh = min(rng_poisson(rng, lh), 9)
        ga = min(rng_poisson(rng, la), 9)
        return gh, ga

    ll_model, ll_naive, n = 0.0, 0.0, 0
    for season in range(4):
        for h in teams:
            for a in teams:
                if h == a:
                    continue
                gh, ga = play(h, a)
                if season >= 2:  # ilk 2 sezon ısınma
                    pr = model.probs("SYN", h, a)
                    out = "H" if gh > ga else ("D" if gh == ga else "A")
                    ll_model += -math.log(max(pr[out], 1e-9))
                    ll_naive += -math.log(1 / 3)
                    n += 1
                model.update("SYN", h, a, gh, ga)
    ok = check("olasılıklar toplamı 1", abs(sum(model.probs("SYN", "T0", "T1")[k]
                                                for k in "HDA") - 1) < 1e-6)
    ok &= check("model naive'den iyi", ll_model < ll_naive,
                f"log-loss {ll_model / n:.3f} vs {ll_naive / n:.3f}")
    return ok


def rng_poisson(rng: random.Random, lam: float) -> int:
    l, k, p = math.exp(-lam), 0, 1.0
    while True:
        p *= rng.random()
        if p <= l:
            return k
        k += 1


def test_elo() -> bool:
    elo = TennisElo()
    for i in range(200):  # A, B'yi 3'te 3 yener, 4.'yü kaybeder (%75)
        w, l = ("B", "A") if i % 4 == 3 else ("A", "B")
        elo.update(w, l, "Hard")
    ok = check("tenis Elo güçlüyü öğrendi", elo.prob("A", "B", "Hard") > 0.6,
               f"p={elo.prob('A', 'B', 'Hard'):.2f}")
    nba = NbaElo()
    for _ in range(50):
        nba.update("GSW", "DET", 110, 95)
    ok &= check("NBA Elo güçlüyü öğrendi", nba.prob_home("GSW", "DET") > 0.7,
                f"p={nba.prob_home('GSW', 'DET'):.2f}")
    return ok


def main() -> None:
    print("betbacktest selftest\n")
    results = [test_math(), test_picker(), test_simulation_edge(),
               test_kill_switch(), test_football_model(), test_elo()]
    if all(results):
        print("\nTÜM TESTLER GEÇTİ")
    else:
        raise SystemExit("\nBAZI TESTLER BAŞARISIZ")


if __name__ == "__main__":
    main()
