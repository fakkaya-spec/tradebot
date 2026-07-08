"""Tarihsel maç + gerçek bahis oranı verisi indirme ve normalize etme.

Kaynaklar (hepsi ücretsiz, gerçek kapanış/kapanışa yakın oranlar içerir):
- Futbol:   football-data.co.uk  (lig başına sezonluk CSV, B365 + Pinnacle +
            piyasa ortalaması/maksimumu)
- Tenis:    tennis-data.co.uk    (ATP + WTA yıllık dosyalar, B365 + Pinnacle)
- NBA:      sportsbookreviewsonline.com (sezonluk Excel, moneyline)

İndirilenler betbacktest/cache/ altında saklanır; ikinci çalıştırma ağa çıkmaz.

Normalize kayıt biçimi (dict):
  {date: datetime.date, sport, league, home, away, ...sonuç..., odds:{...}}
"""
import io
import os
import zipfile
from datetime import date, datetime

import pandas as pd
import requests

CACHE = os.path.join(os.path.dirname(__file__), "cache")

# Lig kodu -> okunur ad. T1 = Türkiye Süper Lig.
FOOTBALL_LEAGUES = {
    "E0": "İngiltere PL", "E1": "İngiltere Ch", "D1": "Almanya BL", "D2": "Almanya BL2",
    "SP1": "İspanya LaLiga", "SP2": "İspanya LaLiga2", "I1": "İtalya SerieA",
    "I2": "İtalya SerieB", "F1": "Fransa L1", "F2": "Fransa L2",
    "N1": "Hollanda", "P1": "Portekiz", "T1": "Türkiye SL",
}


def _fetch(url: str, binary: bool = False):
    os.makedirs(CACHE, exist_ok=True)
    fname = os.path.join(CACHE, url.split("//", 1)[1].replace("/", "_"))
    if os.path.exists(fname):
        with open(fname, "rb") as f:
            return f.read()
    r = requests.get(url, timeout=60, headers={"User-Agent": "Mozilla/5.0 (backtest)"})
    r.raise_for_status()
    with open(fname, "wb") as f:
        f.write(r.content)
    return r.content


def _num(v):
    try:
        f = float(v)
        return f if f == f else None  # NaN eler
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------- futbol
def _parse_date(s: str) -> date:
    for fmt in ("%d/%m/%Y", "%d/%m/%y"):
        try:
            return datetime.strptime(str(s).strip(), fmt).date()
        except ValueError:
            continue
    raise ValueError(f"tarih çözülemedi: {s!r}")


def season_codes(first: int, last: int) -> list[str]:
    """2021, 2025 -> ['2122','2223','2324','2425','2526']"""
    return [f"{y % 100:02d}{(y + 1) % 100:02d}" for y in range(first, last + 1)]


def load_football(first_year: int, last_year: int,
                  leagues: dict[str, str] = None, quiet: bool = False) -> list[dict]:
    leagues = leagues or FOOTBALL_LEAGUES
    out: list[dict] = []
    for code in season_codes(first_year, last_year):
        for lg, lg_name in leagues.items():
            url = f"https://www.football-data.co.uk/mmz4281/{code}/{lg}.csv"
            try:
                raw = _fetch(url)
            except Exception as e:
                if not quiet:
                    print(f"  uyarı: {url} indirilemedi ({e})")
                continue
            df = pd.read_csv(io.BytesIO(raw), encoding="latin-1",
                             on_bad_lines="skip")
            for _, r in df.iterrows():
                try:
                    d = _parse_date(r.get("Date"))
                except Exception:
                    continue
                gh, ga = _num(r.get("FTHG")), _num(r.get("FTAG"))
                if gh is None or ga is None:
                    continue
                rec = {
                    "date": d, "sport": "football", "league": lg_name,
                    "home": str(r.get("HomeTeam")), "away": str(r.get("AwayTeam")),
                    "gh": int(gh), "ga": int(ga),
                    "odds": {
                        "b365": {k: _num(r.get(f"B365{k}")) for k in "HDA"},
                        "ps": {k: _num(r.get(f"PS{k}")) for k in "HDA"},
                        "avg": {k: _num(r.get(f"Avg{k}")) or _num(r.get(f"BbAv{k}"))
                                for k in "HDA"},
                        "max": {k: _num(r.get(f"Max{k}")) or _num(r.get(f"BbMx{k}"))
                                for k in "HDA"},
                        "ou_b365": {"O": _num(r.get("B365>2.5")),
                                    "U": _num(r.get("B365<2.5"))},
                        "ou_avg": {"O": _num(r.get("Avg>2.5")) or _num(r.get("BbAv>2.5")),
                                   "U": _num(r.get("Avg<2.5")) or _num(r.get("BbAv<2.5"))},
                        "ou_ps": {"O": _num(r.get("P>2.5")), "U": _num(r.get("P<2.5"))},
                    },
                }
                out.append(rec)
    out.sort(key=lambda m: m["date"])
    return out


# ---------------------------------------------------------------- tenis
def _tennis_year(url_base: str, year: int) -> pd.DataFrame | None:
    for suffix in (f"{year}.zip", f"{year}.xlsx", f"{year}.xls"):
        url = f"{url_base}/{suffix}"
        try:
            raw = _fetch(url, binary=True)
        except Exception:
            continue
        try:
            if suffix.endswith(".zip"):
                zf = zipfile.ZipFile(io.BytesIO(raw))
                inner = [n for n in zf.namelist() if n.endswith((".xlsx", ".xls"))][0]
                return pd.read_excel(io.BytesIO(zf.read(inner)))
            return pd.read_excel(io.BytesIO(raw))
        except Exception:
            continue
    return None


def load_tennis(first_year: int, last_year: int, quiet: bool = False) -> list[dict]:
    out: list[dict] = []
    for year in range(first_year, last_year + 1):
        for tour, base in (("ATP", f"http://www.tennis-data.co.uk/{year}"),
                           ("WTA", f"http://www.tennis-data.co.uk/{year}w")):
            df = _tennis_year(base, year)
            if df is None:
                if not quiet:
                    print(f"  uyarı: tenis {tour} {year} indirilemedi")
                continue
            for _, r in df.iterrows():
                d = r.get("Date")
                if pd.isna(d):
                    continue
                d = pd.Timestamp(d).date()
                w, l = str(r.get("Winner")).strip(), str(r.get("Loser")).strip()
                if not w or not l or w == "nan" or l == "nan":
                    continue
                out.append({
                    "date": d, "sport": "tennis",
                    "league": f"{tour} {r.get('Series', r.get('Tier', ''))}".strip(),
                    "winner": w, "loser": l,
                    "surface": str(r.get("Surface", "Hard")),
                    "odds": {
                        "b365": {"W": _num(r.get("B365W")), "L": _num(r.get("B365L"))},
                        "ps": {"W": _num(r.get("PSW")), "L": _num(r.get("PSL"))},
                        "avg": {"W": _num(r.get("AvgW")), "L": _num(r.get("AvgL"))},
                        "max": {"W": _num(r.get("MaxW")), "L": _num(r.get("MaxL"))},
                    },
                })
    out.sort(key=lambda m: m["date"])
    return out


# ---------------------------------------------------------------- NBA
def _american_to_decimal(ml) -> float | None:
    v = _num(ml)
    if v is None or v == 0:
        return None
    return 1.0 + (v / 100.0 if v > 0 else 100.0 / -v)


def load_nba(first_season: int, last_season: int, quiet: bool = False) -> list[dict]:
    """first_season=2021 -> 2021-22 sezonu. Kaynak sezon ortası satır çifti
    (üst=deplasman, alt=ev) formatındadır."""
    out: list[dict] = []
    for y in range(first_season, last_season + 1):
        url = ("https://www.sportsbookreviewsonline.com/scoresoddsarchives/nba/"
               f"nba%20odds%20{y}-{(y + 1) % 100:02d}.xlsx")
        try:
            raw = _fetch(url, binary=True)
            df = pd.read_excel(io.BytesIO(raw))
        except Exception as e:
            if not quiet:
                print(f"  uyarı: NBA {y}-{y+1} indirilemedi ({e})")
            continue
        rows = df.to_dict("records")
        for i in range(0, len(rows) - 1, 2):
            a, h = rows[i], rows[i + 1]  # visitor, home
            try:
                md = int(a["Date"])
                month, day = md // 100, md % 100
                year = y if month >= 10 else y + 1
                d = date(year, month, day)
                out.append({
                    "date": d, "sport": "basketball", "league": "NBA",
                    "home": str(h["Team"]).strip(), "away": str(a["Team"]).strip(),
                    "hp": int(h["Final"]), "ap": int(a["Final"]),
                    "season": y,
                    "odds": {"ml_home": _american_to_decimal(h.get("ML")),
                             "ml_away": _american_to_decimal(a.get("ML"))},
                })
            except Exception:
                continue
    out.sort(key=lambda m: m["date"])
    return out
