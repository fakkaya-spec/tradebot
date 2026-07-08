"""Canlı oran kaynağı: The Odds API (https://the-odds-api.com).

Ücretsiz katman ayda 500 istek verir; günde 1 koşu + ~15 lig sorgusu bu
limitin rahat içindedir. Anahtar sadece ortam değişkeninde (ODDS_API_KEY).
"""
import unicodedata
from datetime import datetime, timedelta, timezone
from difflib import get_close_matches

import requests

from . import config

BASE = "https://api.the-odds-api.com/v4"

# Odds API spor anahtarı -> backtest/model lig adı (betbacktest.data ile aynı)
SOCCER_KEYS = {
    "soccer_epl": "İngiltere PL",
    "soccer_efl_champ": "İngiltere Ch",
    "soccer_germany_bundesliga": "Almanya BL",
    "soccer_germany_bundesliga2": "Almanya BL2",
    "soccer_spain_la_liga": "İspanya LaLiga",
    "soccer_spain_segunda_division": "İspanya LaLiga2",
    "soccer_italy_serie_a": "İtalya SerieA",
    "soccer_italy_serie_b": "İtalya SerieB",
    "soccer_france_ligue_one": "Fransa L1",
    "soccer_france_ligue_two": "Fransa L2",
    "soccer_netherlands_eredivisie": "Hollanda",
    "soccer_portugal_primeira_liga": "Portekiz",
    "soccer_turkey_super_league": "Türkiye SL",
}


def _get(path: str, **params) -> list | dict:
    params["apiKey"] = config.ODDS_API_KEY
    r = requests.get(f"{BASE}{path}", params=params, timeout=30)
    r.raise_for_status()
    return r.json()


def normalize(name: str) -> str:
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    s = s.lower()
    for junk in (" fc", " cf", " afc", " ac ", " as ", " sc", " bk", " if"):
        s = s.replace(junk, " ")
    return " ".join(s.split())


def match_name(api_name: str, known: list[str], cutoff: float = 0.72) -> str | None:
    """Odds API adını modelin tanıdığı ada eşler (normalize + fuzzy)."""
    norm_known = {normalize(k): k for k in known}
    n = normalize(api_name)
    if n in norm_known:
        return norm_known[n]
    hit = get_close_matches(n, list(norm_known), n=1, cutoff=cutoff)
    return norm_known[hit[0]] if hit else None


def tennis_to_hist(api_name: str) -> str:
    """'Novak Djokovic' -> tennis-data biçimi 'Djokovic N.'"""
    parts = api_name.strip().split()
    if len(parts) < 2:
        return api_name
    return f"{' '.join(parts[1:])} {parts[0][0]}."


def upcoming(hours: int = 36) -> list[dict]:
    """Önümüzdeki `hours` saat içinde başlayacak maçları oranlarıyla döndürür.

    Kayıt: {sport, league, key, home, away, commence, books:
            [{name, h2h: {takım/çıktı: oran}, totals: {...}}]}
    """
    sports_cfg = set(config.SPORTS)
    keys: dict[str, str] = {}
    if "football" in sports_cfg:
        keys.update(SOCCER_KEYS)
    if "basketball" in sports_cfg:
        keys["basketball_nba"] = "NBA"
    if "tennis" in sports_cfg:
        for s in _get("/sports"):
            if s.get("group") == "Tennis" and s.get("active"):
                keys[s["key"]] = s.get("title", "Tenis")

    horizon = datetime.now(timezone.utc) + timedelta(hours=hours)
    out = []
    for key, league in keys.items():
        try:
            events = _get(f"/sports/{key}/odds", regions="eu",
                          markets="h2h,totals", oddsFormat="decimal")
        except Exception as e:
            print(f"  uyarı: {key} oranları alınamadı ({e})")
            continue
        for ev in events:
            t = datetime.fromisoformat(ev["commence_time"].replace("Z", "+00:00"))
            if t > horizon:
                continue
            books = []
            for bm in ev.get("bookmakers", []):
                entry = {"name": bm["title"], "h2h": {}, "totals": {}}
                for mk in bm.get("markets", []):
                    for oc in mk.get("outcomes", []):
                        if mk["key"] == "h2h":
                            entry["h2h"][oc["name"]] = oc["price"]
                        elif mk["key"] == "totals" and oc.get("point") == 2.5:
                            entry["totals"][oc["name"]] = oc["price"]
                books.append(entry)
            if books:
                out.append({"sport": ("basketball" if key == "basketball_nba"
                                      else "tennis" if key.startswith("tennis") else "football"),
                            "league": league, "key": key,
                            "home": ev["home_team"], "away": ev["away_team"],
                            "commence": t, "books": books})
    return out
