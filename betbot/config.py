"""Bahis botu konfigürasyonu — tüm değerler ortam değişkeninden okunur.

Kripto bottaki (bot/config.py) kuralın aynısı: sırlar sadece ortamda yaşar,
repoya asla yazılmaz.
"""
import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


def _f(name: str, default: float) -> float:
    return float(os.getenv(name, str(default)))


def _i(name: str, default: int) -> int:
    return int(os.getenv(name, str(default)))


@dataclass(frozen=True)
class Profile:
    """Bahis boyutlama profili. Backtest iki profili yan yana koşturur."""
    name: str
    kelly_mult: float      # tam Kelly'nin çarpanı (0.25 = çeyrek Kelly)
    stake_cap: float       # tek bahis, bankonun en fazla bu oranı
    min_edge: float        # beklenen değer eşiği (0.05 = %5)
    daily_risk_cap: float  # aynı gün toplam stake tavanı
    monthly_kill: float    # ay içi zarar bu orana ulaşırsa ay biter


MEDIUM = Profile("orta", kelly_mult=0.25, stake_cap=0.02, min_edge=0.05,
                 daily_risk_cap=0.10, monthly_kill=0.15)
AGGRESSIVE = Profile("agresif", kelly_mult=0.70, stake_cap=0.07, min_edge=0.04,
                     daily_risk_cap=0.30, monthly_kill=0.30)

PROFILES = {"orta": MEDIUM, "agresif": AGGRESSIVE}

# --- Seçim kuralları (her iki profilde ortak) ---
MAX_DAILY_PICKS = _i("MAX_DAILY_PICKS", 5)
MIN_ODDS = _f("MIN_ODDS", 1.50)
MAX_ODDS = _f("MAX_ODDS", 4.50)
MIN_TEAM_MATCHES = _i("MIN_TEAM_MATCHES", 10)   # model bu kadar maç görmeden bahis önermez
MARKET_BLEND = _f("MARKET_BLEND", 0.50)         # olasılık = (1-w)*model + w*piyasa konsensüsü

# --- Canlı mod ---
LIVE_PROFILE = os.getenv("LIVE_PROFILE", "orta")
BANKROLL = _f("BANKROLL", 1000.0)
ODDS_API_KEY = os.getenv("ODDS_API_KEY", "")
SPORTS = [s.strip() for s in os.getenv("SPORTS", "football,tennis,basketball").split(",") if s.strip()]
RUN_HOUR_UTC = _i("RUN_HOUR_UTC", 8)            # günlük kupon saati (UTC)
DRY_RUN = os.getenv("DRY_RUN", "true").lower() == "true"

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
