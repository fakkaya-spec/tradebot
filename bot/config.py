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

    symbols: tuple = tuple(
        s.strip() for s in os.getenv("SYMBOLS", "BTC/USDT,ETH/USDT,SOL/USDT,LTC/USDT").split(",") if s.strip()
    )
    timeframe: str = "4h"

    # Risk cekirdegi - strateji ne derse desin bu sinirlar asilamaz.
    risk_per_trade: float = float(os.getenv("RISK_PER_TRADE", "0.01"))
    max_leverage: float = float(os.getenv("MAX_LEVERAGE", "3"))
    max_position_notional_pct: float = 1.0  # tek pozisyon nominali en fazla 1x ozsermaye
    monthly_kill_switch: float = float(os.getenv("MONTHLY_KILL_SWITCH", "0.08"))
    funding_limit: float = float(os.getenv("FUNDING_LIMIT", "0.001"))  # 8 saatlik oran

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

    telegram_token: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
    telegram_chat_id: str = os.getenv("TELEGRAM_CHAT_ID", "")

    state_dir: str = os.getenv("STATE_DIR", "state")
