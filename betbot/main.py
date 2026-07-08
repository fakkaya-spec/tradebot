"""Canlı bahis öneri botu.

Her sabah (RUN_HOUR_UTC):
  1. Son 3+ sezonun sonuçlarını indirir, modelleri kurar (backtest ile aynı kod)
  2. The Odds API'den günün oranlarını çeker
  3. Edge >= eşik olan en iyi N seçimi Kelly stake'iyle Telegram'a gönderir

Bot BAHİS OYNAMAZ — sadece öneri üretir. Para her zaman sizin kontrolünüzdedir.

  python -m betbot.main            # tek koşu
  python -m betbot.main --loop     # Railway worker modu (her gün otomatik)
"""
import argparse
import time
from datetime import date, datetime, timedelta, timezone

from betbacktest import data as hist
from . import config, data, notifier
from .models.elo import NbaElo, TennisElo
from .models.football import FootballPoisson
from .picker import Candidate, blend, daily_picks, demargin

# Odds API tam adı -> sportsbookreviews kısa adı
NBA_ALIASES = {
    "Atlanta Hawks": "Atlanta", "Boston Celtics": "Boston", "Brooklyn Nets": "Brooklyn",
    "Charlotte Hornets": "Charlotte", "Chicago Bulls": "Chicago",
    "Cleveland Cavaliers": "Cleveland", "Dallas Mavericks": "Dallas",
    "Denver Nuggets": "Denver", "Detroit Pistons": "Detroit",
    "Golden State Warriors": "GoldenState", "Houston Rockets": "Houston",
    "Indiana Pacers": "Indiana", "Los Angeles Clippers": "LAClippers",
    "Los Angeles Lakers": "LALakers", "Memphis Grizzlies": "Memphis",
    "Miami Heat": "Miami", "Milwaukee Bucks": "Milwaukee",
    "Minnesota Timberwolves": "Minnesota", "New Orleans Pelicans": "NewOrleans",
    "New York Knicks": "NewYork", "Oklahoma City Thunder": "OklahomaCity",
    "Orlando Magic": "Orlando", "Philadelphia 76ers": "Philadelphia",
    "Phoenix Suns": "Phoenix", "Portland Trail Blazers": "Portland",
    "Sacramento Kings": "Sacramento", "San Antonio Spurs": "SanAntonio",
    "Toronto Raptors": "Toronto", "Utah Jazz": "Utah", "Washington Wizards": "Washington",
}

SURFACE_HINTS = {"french": "Clay", "roland": "Clay", "monte": "Clay", "rome": "Clay",
                 "madrid": "Clay", "wimbledon": "Grass", "halle": "Grass",
                 "queen": "Grass", "eastbourne": "Grass"}


def build_models() -> tuple[FootballPoisson, TennisElo, NbaElo]:
    """Tarihsel sonuçları modellere kronolojik sırayla işler."""
    year = date.today().year
    fb, tn, nb = FootballPoisson(), TennisElo(), NbaElo()
    matches: list[dict] = []
    if "football" in config.SPORTS:
        matches += hist.load_football(year - 3, year, quiet=True)
    if "tennis" in config.SPORTS:
        matches += hist.load_tennis(year - 3, year, quiet=True)
    if "basketball" in config.SPORTS:
        matches += hist.load_nba(year - 4, year - 1, quiet=True)
    matches.sort(key=lambda m: m["date"])
    season = None
    for m in matches:
        if m["sport"] == "football":
            fb.update(m["league"], m["home"], m["away"], m["gh"], m["ga"])
        elif m["sport"] == "tennis":
            tn.update(m["winner"], m["loser"], m["surface"])
        else:
            if season is not None and m.get("season") != season:
                nb.new_season()
            season = m.get("season")
            nb.update(m["home"], m["away"], m["hp"], m["ap"])
    print(f"modeller hazır: {len(fb.n)} takım, {len(tn.n)} oyuncu, {len(nb.n)} NBA takımı")
    return fb, tn, nb


def _consensus_and_best(books: list[dict], market: str,
                        outcomes: list[str]) -> tuple[list[float], list[float]] | None:
    """Kitapçılar arası ortalama (konsensüs) ve en iyi oranlar."""
    prices: list[list[float]] = []
    for name in outcomes:
        ps = [b[market][name] for b in books if b[market].get(name)]
        if not ps:
            return None
        prices.append(ps)
    avg = [sum(p) / len(p) for p in prices]
    best = [max(p) for p in prices]
    return avg, best


def candidates_for(ev: dict, fb: FootballPoisson, tn: TennisElo,
                   nb: NbaElo) -> list[Candidate]:
    today = str(date.today())
    out: list[Candidate] = []
    if ev["sport"] == "football":
        home = data.match_name(ev["home"], list(fb.n))
        away = data.match_name(ev["away"], list(fb.n))
        if not (home and away):
            return []
        if (fb.matches(home) < config.MIN_TEAM_MATCHES
                or fb.matches(away) < config.MIN_TEAM_MATCHES):
            return []
        probs = fb.probs(ev["league"], home, away)
        cb = _consensus_and_best(ev["books"], "h2h", [ev["home"], "Draw", ev["away"]])
        if cb:
            fair = demargin(cb[0])
            for i, k in enumerate("HDA"):
                out.append(Candidate(today, "football", ev["league"],
                                     f"{ev['home']} - {ev['away']}", "1X2", k,
                                     cb[1][i], blend(probs[k], fair[i])))
        cb = _consensus_and_best(ev["books"], "totals", ["Over", "Under"])
        if cb:
            fair = demargin(cb[0])
            for i, k in enumerate(("O2.5", "U2.5")):
                out.append(Candidate(today, "football", ev["league"],
                                     f"{ev['home']} - {ev['away']}", "O/U 2.5", k,
                                     cb[1][i], blend(probs[k], fair[i])))
    elif ev["sport"] == "tennis":
        a = data.match_name(data.tennis_to_hist(ev["home"]), list(tn.n))
        b = data.match_name(data.tennis_to_hist(ev["away"]), list(tn.n))
        if not (a and b):
            return []
        if tn.matches(a) < config.MIN_TEAM_MATCHES or tn.matches(b) < config.MIN_TEAM_MATCHES:
            return []
        surface = next((s for h, s in SURFACE_HINTS.items()
                        if h in ev["league"].lower()), "Hard")
        cb = _consensus_and_best(ev["books"], "h2h", [ev["home"], ev["away"]])
        if cb:
            fair = demargin(cb[0])
            pa = tn.prob(a, b, surface)
            for i, (name, p) in enumerate(((ev["home"], pa), (ev["away"], 1 - pa))):
                out.append(Candidate(today, "tennis", ev["league"],
                                     f"{ev['home']} - {ev['away']}", "ML", name,
                                     cb[1][i], blend(p, fair[i])))
    else:  # basketball
        home = NBA_ALIASES.get(ev["home"]) or data.match_name(ev["home"], list(nb.n))
        away = NBA_ALIASES.get(ev["away"]) or data.match_name(ev["away"], list(nb.n))
        if not (home and away):
            return []
        if (nb.matches(home) < config.MIN_TEAM_MATCHES
                or nb.matches(away) < config.MIN_TEAM_MATCHES):
            return []
        cb = _consensus_and_best(ev["books"], "h2h", [ev["home"], ev["away"]])
        if cb:
            fair = demargin(cb[0])
            ph = nb.prob_home(home, away)
            for i, (name, p) in enumerate(((ev["home"], ph), (ev["away"], 1 - ph))):
                out.append(Candidate(today, "basketball", "NBA",
                                     f"{ev['away']} @ {ev['home']}", "ML", name,
                                     cb[1][i], blend(p, fair[i])))
    return out


EMOJI = {"football": "⚽", "tennis": "🎾", "basketball": "🏀"}


def run_once() -> None:
    profile = config.PROFILES[config.LIVE_PROFILE]
    fb, tn, nb = build_models()
    print("günün oranları çekiliyor ...")
    events = data.upcoming()
    cands: list[Candidate] = []
    for ev in events:
        cands.extend(candidates_for(ev, fb, tn, nb))
    picks = daily_picks(cands, profile)
    print(f"{len(events)} maç, {len(cands)} aday, {len(picks)} seçim")
    if not picks:
        notifier.send("🎯 Bugün değer bulunamadı — bahis yok. (Bu da bir sinyaldir:"
                      " edge yoksa oynamamak kazandırır.)")
        return
    lines = [f"🎯 <b>Günün kuponu</b> — {date.today()} (profil: {profile.name})", ""]
    for c, frac in picks:
        stake = frac * config.BANKROLL
        lines.append(f"{EMOJI[c.sport]} <b>{c.event}</b> ({c.league})")
        lines.append(f"   {c.market}: <b>{c.selection}</b> @ {c.price:.2f}"
                     f" | edge {c.edge * 100:+.1f}%"
                     f" | stake %{frac * 100:.1f} ≈ {stake:.0f}")
    lines += ["", f"Banka: {config.BANKROLL:.0f} | günlük toplam risk "
              f"%{sum(f for _, f in picks) * 100:.1f}",
              "⚠️ Öneridir, garanti değildir. Oran düştüyse oynamayın."]
    msg = "\n".join(lines)
    if config.DRY_RUN:
        print("[DRY_RUN] mesaj gönderilmedi:\n" + msg)
    else:
        notifier.send(msg)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--loop", action="store_true", help="her gün RUN_HOUR_UTC'de koş")
    args = ap.parse_args()
    if not config.ODDS_API_KEY:
        print("uyarı: ODDS_API_KEY boş — canlı oran çekilemez")
    if not args.loop:
        run_once()
        return
    print(f"Bahis botu başladı [{'DRY_RUN' if config.DRY_RUN else 'CANLI'}] "
          f"— her gün {config.RUN_HOUR_UTC:02d}:00 UTC")
    while True:
        now = datetime.now(timezone.utc)
        target = now.replace(hour=config.RUN_HOUR_UTC, minute=0, second=0, microsecond=0)
        if target <= now:
            target += timedelta(days=1)
        time.sleep((target - now).total_seconds())
        try:
            run_once()
        except Exception as e:
            print(f"koşu hatası: {e}")
            notifier.send(f"⚠️ Bahis botu hatası: {e}")


if __name__ == "__main__":
    main()
