"""Ortam degiskenlerinden okunan konfigurasyon. Sirlar sadece env'de yasar."""
import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

load_dotenv()


def _bool(name: str, default: str = "true") -> bool:
    return os.getenv(name, default).strip().lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class Config:
    api_key: str = os.getenv("BINANCE_API_KEY", "")
    api_secret: str = os.getenv("BINANCE_API_SECRET", "")
    testnet: bool = _bool("TESTNET", "true")
    dry_run: bool = _bool("DRY_RUN", "true")

    # LTC 5 yillik backtest'te tum pencerelerde zarar uretti, evrenden cikarildi
    symbols: tuple = tuple(
        s.strip() for s in os.getenv("SYMBOLS", "BTC/USDT,ETH/USDT,SOL/USDT").split(",") if s.strip()
    )
    timeframe: str = "4h"

    # Risk cekirdegi - strateji ne derse desin bu sinirlar asilamaz.
    risk_per_trade: float = float(os.getenv("RISK_PER_TRADE", "0.01"))
    max_leverage: float = float(os.getenv("MAX_LEVERAGE", "3"))
    max_position_notional_pct: float = 1.0  # tek pozisyon nominali en fazla 1x ozsermaye
    monthly_kill_switch: float = float(os.getenv("MONTHLY_KILL_SWITCH", "0.08"))
    funding_limit: float = float(os.getenv("FUNDING_LIMIT", "0.001"))  # 8 saatlik oran
    max_same_direction: int = int(os.getenv("MAX_SAME_DIRECTION", "3"))  # ayni yonde acik pozisyon tavani
    cooldown_bars: int = int(os.getenv("COOLDOWN_BARS", "6"))  # stop sonrasi ayni yone giris yasagi (bar)

    # Strateji parametreleri (backtest ile birebir ayni)
    ema_fast: int = 20
    ema_slow: int = 50
    adx_period: int = 14
    adx_threshold: float = 25.0
    atr_period: int = 14
    stop_atr: float = 2.0
    breakeven_atr: float = 1.5
    trail_atr: float = 3.0
    donchian_entry: int = 120  # 4h barda 20 gun
    donchian_exit: int = 60    # 4h barda 10 gun
    ema_macro: int = 200       # makro yon filtresi (~33 gun): ustunde sadece long, altinda sadece short

    # --- v2 deneyi, izole holdout (2025+) sinavi sonuclari ---
    # vol_target: v1 +49.3% -> +64.0%, PF 1.37 -> 1.47, ayni islem sayisi -> ACIK
    # regime:     holdout +7.6%, PF 1.07 -> reddedildi
    # meanrev:    her iki pencerede de zararda -> reddedildi
    enable_regime: bool = _bool("ENABLE_REGIME", "false")        # rejim bazli kol kapilama
    enable_meanrev: bool = _bool("ENABLE_MEANREV", "false")      # yatay rejimde ortalamaya donus kolu
    enable_vol_target: bool = _bool("ENABLE_VOL_TARGET", "true")  # volatiliteye gore boyut olcekleme
    regime_slope_bars: int = 30    # EMA200 egimi bakis penceresi (bar)
    meanrev_risk_mult: float = 0.5  # MR kolu islem riski carpani (trend/breakout'un yarisi)
    rsi_period: int = 2
    rsi_oversold: float = 10.0
    rsi_overbought: float = 90.0
    bb_period: int = 20
    bb_std: float = 2.0
    vol_window: int = 200          # goreli ATR medyani penceresi (vol hedeflemesi)

    telegram_token: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
    telegram_chat_id: str = os.getenv("TELEGRAM_CHAT_ID", "")
    heartbeat: bool = _bool("HEARTBEAT", "true")  # gunluk nabiz mesaji
    heartbeat_hour: int = int(os.getenv("HEARTBEAT_HOUR", "12"))  # UTC saat (12 = TR 15:00)

    # Cekirdek (spot) uyarilari: EMA200 gecislerinde Telegram'dan haber ver.
    # Spot islemi bot YAPMAZ - kullanici elle yapar (70/30 cekirdek+uydu yapisi).
    core_alerts: bool = _bool("CORE_ALERTS", "true")
    core_symbols: tuple = tuple(
        s.strip() for s in os.getenv("CORE_SYMBOLS", "BTC/USDT,ETH/USDT").split(",") if s.strip()
    )

    state_dir: str = os.getenv("STATE_DIR", "state")
