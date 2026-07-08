"""Walk-forward bahis backtest motoru.

Akış: tüm maçlar tarih sırasına dizilir; her gün için önce O GÜNÜN maçlarına
dair adaylar üretilir (modeller yalnızca önceki maçları bilir), sonra günün
sonuçlarıyla modeller güncellenir. Bahisler start_date'ten önce açılmaz —
öncesi modellerin ısınma dönemidir.

Aday üretimi ve seçim, canlı botla AYNI kod (betbot.picker, betbot.models)
üzerinden yapılır.
"""
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date

from betbot import config
from betbot.models.elo import NbaElo, TennisElo
from betbot.models.football import FootballPoisson
from betbot.picker import Candidate, blend, daily_picks, demargin


# ------------------------------------------------------------ aday üretimi
def football_candidates(m: dict, model: FootballPoisson,
                        best_price: bool = False,
                        fair_src: str = "avg") -> list[Candidate]:
    if (model.matches(m["home"]) < config.MIN_TEAM_MATCHES
            or model.matches(m["away"]) < config.MIN_TEAM_MATCHES):
        return []
    o = m["odds"]
    probs = model.probs(m["league"], m["home"], m["away"])
    out = []
    # 1X2
    b365 = [o["b365"].get(k) for k in "HDA"]
    cons = [o.get(fair_src, o["avg"]).get(k) for k in "HDA"]
    if not all(cons):
        cons = [o["avg"].get(k) for k in "HDA"]
    if all(b365) and all(cons):
        fair = demargin(cons)
        price_src = [o["max"].get(k) for k in "HDA"] if best_price else b365
        results = {"H": m["gh"] > m["ga"], "D": m["gh"] == m["ga"], "A": m["gh"] < m["ga"]}
        for i, k in enumerate("HDA"):
            price = price_src[i] or b365[i]
            p = blend(probs[k], fair[i])
            c = Candidate(str(m["date"]), "football", m["league"],
                          f"{m['home']} - {m['away']}", "1X2", k, price, p)
            c.won = results[k]
            c.p_model, c.p_fair = probs[k], fair[i]
            out.append(c)
    # Üst/Alt 2.5
    ou_b = o["ou_b365"]
    ou_a = o.get("ou_ps" if fair_src == "ps" else "ou_avg") or o["ou_avg"]
    if not (ou_a.get("O") and ou_a.get("U")):
        ou_a = o["ou_avg"]
    if ou_b.get("O") and ou_b.get("U") and ou_a.get("O") and ou_a.get("U"):
        fair = demargin([ou_a["O"], ou_a["U"]])
        total = m["gh"] + m["ga"]
        for i, (k, mk) in enumerate((("O2.5", "O"), ("U2.5", "U"))):
            p = blend(probs[k], fair[i])
            c = Candidate(str(m["date"]), "football", m["league"],
                          f"{m['home']} - {m['away']}", "O/U 2.5", k, ou_b[mk], p)
            c.won = (total >= 3) if mk == "O" else (total <= 2)
            c.p_model, c.p_fair = probs[k], fair[i]
            out.append(c)
    return out


def tennis_candidates(m: dict, model: TennisElo,
                      best_price: bool = False) -> list[Candidate]:
    if (model.matches(m["winner"]) < config.MIN_TEAM_MATCHES
            or model.matches(m["loser"]) < config.MIN_TEAM_MATCHES):
        return []
    o = m["odds"]
    pw_price = o["avg"].get("W") or o["ps"].get("W") or o["b365"].get("W")
    pl_price = o["avg"].get("L") or o["ps"].get("L") or o["b365"].get("L")
    bw = o["b365"].get("W")
    bl = o["b365"].get("L")
    if not (pw_price and pl_price and bw and bl):
        return []
    fair = demargin([pw_price, pl_price])
    p_model_w = model.prob(m["winner"], m["loser"], m["surface"])
    out = []
    for name, opp_idx, p_model, price_b365, price_max, won in (
        (m["winner"], 0, p_model_w, bw, o["max"].get("W"), True),
        (m["loser"], 1, 1.0 - p_model_w, bl, o["max"].get("L"), False),
    ):
        price = (price_max or price_b365) if best_price else price_b365
        p = blend(p_model, fair[opp_idx])
        c = Candidate(str(m["date"]), "tennis", m["league"],
                      f"{m['winner']} - {m['loser']}", "ML", name, price, p)
        c.won = won
        c.p_model, c.p_fair = p_model, fair[opp_idx]
        out.append(c)
    return out


def nba_candidates(m: dict, model: NbaElo) -> list[Candidate]:
    if (model.matches(m["home"]) < config.MIN_TEAM_MATCHES
            or model.matches(m["away"]) < config.MIN_TEAM_MATCHES):
        return []
    mh, ma = m["odds"].get("ml_home"), m["odds"].get("ml_away")
    if not (mh and ma):
        return []
    fair = demargin([mh, ma])
    ph = model.prob_home(m["home"], m["away"])
    out = []
    home_won = m["hp"] > m["ap"]
    for team, p_model, p_fair, price, won in (
        (m["home"], ph, fair[0], mh, home_won),
        (m["away"], 1 - ph, fair[1], ma, not home_won),
    ):
        c = Candidate(str(m["date"]), "basketball", "NBA",
                      f"{m['away']} @ {m['home']}", "ML", team, price, blend(p_model, p_fair))
        c.won = won
        c.p_model, c.p_fair = p_model, p_fair
        out.append(c)
    return out


# ------------------------------------------------------------ simülasyon
@dataclass
class BetRecord:
    date: date
    sport: str
    event: str
    market: str
    selection: str
    price: float
    prob: float
    edge: float
    stake: float     # para cinsinden
    pnl: float


@dataclass
class SimResult:
    profile: str
    start_bank: float
    equity: list[tuple[date, float]] = field(default_factory=list)
    bets: list[BetRecord] = field(default_factory=list)
    kill_months: list[str] = field(default_factory=list)

    @property
    def final_bank(self) -> float:
        return self.equity[-1][1] if self.equity else self.start_bank


def build_daily_candidates(matches: list[dict], start: date, end: date,
                           best_price: bool = False, fair_src: str = "avg",
                           progress: bool = False) -> dict[date, list[Candidate]]:
    """Tüm sporları tek kronolojik akışta işler; gün -> adaylar döndürür."""
    fb = FootballPoisson()
    tn = TennisElo()
    nb = NbaElo()
    by_day: dict[date, list[dict]] = defaultdict(list)
    for m in matches:
        by_day[m["date"]].append(m)
    days = sorted(by_day)
    out: dict[date, list[Candidate]] = {}
    nba_season = None
    for i, d in enumerate(days):
        if d > end:
            break
        todays = by_day[d]
        # 1) tahmin (model henüz bu maçları görmedi)
        if d >= start:
            cands: list[Candidate] = []
            for m in todays:
                if m["sport"] == "football":
                    cands.extend(football_candidates(m, fb, best_price, fair_src))
                elif m["sport"] == "tennis":
                    cands.extend(tennis_candidates(m, tn, best_price))
                else:
                    cands.extend(nba_candidates(m, nb))
            if cands:
                out[d] = cands
        # 2) günün sonuçlarıyla güncelle
        for m in todays:
            if m["sport"] == "football":
                fb.update(m["league"], m["home"], m["away"], m["gh"], m["ga"])
            elif m["sport"] == "tennis":
                tn.update(m["winner"], m["loser"], m["surface"])
            else:
                if nba_season is not None and m.get("season") != nba_season:
                    nb.new_season()
                nba_season = m.get("season")
                nb.update(m["home"], m["away"], m["hp"], m["ap"])
        if progress and i % 200 == 0:
            print(f"  ... {d} işlendi")
    return out


def simulate(daily: dict[date, list[Candidate]], profile: config.Profile,
             start_bank: float = 1000.0, max_picks: int = None) -> SimResult:
    res = SimResult(profile.name, start_bank)
    bank = start_bank
    month = None
    month_start_bank = start_bank
    killed = False
    for d in sorted(daily):
        mkey = f"{d.year}-{d.month:02d}"
        if mkey != month:
            month = mkey
            month_start_bank = bank
            killed = False
        if not killed and bank > 0:
            for c, frac in daily_picks(daily[d], profile, max_picks):
                stake = frac * bank
                pnl = stake * (c.price - 1.0) if c.won else -stake
                bank += pnl
                res.bets.append(BetRecord(d, c.sport, c.event, c.market,
                                          c.selection, c.price, c.prob, c.edge,
                                          stake, pnl))
            if bank <= month_start_bank * (1.0 - profile.monthly_kill):
                killed = True
                res.kill_months.append(mkey)
        res.equity.append((d, bank))
    return res


def metrics(res: SimResult) -> dict:
    eq = [b for _, b in res.equity]
    if not eq:
        return {}
    peak, maxdd = eq[0], 0.0
    for v in eq:
        peak = max(peak, v)
        maxdd = max(maxdd, 1.0 - v / peak)
    n = len(res.bets)
    staked = sum(b.stake for b in res.bets)
    pnl = sum(b.pnl for b in res.bets)
    days = (res.equity[-1][0] - res.equity[0][0]).days or 1
    years = days / 365.25
    mult = res.final_bank / res.start_bank
    cagr = mult ** (1 / years) - 1 if mult > 0 and years > 0 else -1.0
    return {
        "final_mult": mult, "cagr": cagr, "maxdd": maxdd, "bets": n,
        "roi": pnl / staked if staked else 0.0,
        "hit": sum(1 for b in res.bets if b.pnl > 0) / n if n else 0.0,
        "avg_odds": sum(b.price for b in res.bets) / n if n else 0.0,
        "kills": len(res.kill_months), "years": years,
    }
