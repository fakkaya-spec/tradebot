"""Binance USDT-M Futures baglantisi (ccxt) + guvenlik kontrolleri."""
import logging

import ccxt

log = logging.getLogger("exchange")


def make_exchange(cfg):
    ex = ccxt.binanceusdm(
        {
            "apiKey": cfg.api_key,
            "secret": cfg.api_secret,
            "enableRateLimit": True,
            "options": {"adjustForTimeDifference": True},
        }
    )
    if cfg.testnet:
        ex.set_sandbox_mode(True)
    return ex


def assert_key_safety(ex, testnet: bool) -> None:
    """API anahtarinda cekim yetkisi varsa botu calistirmayi REDDET.

    Anahtar sizsa bile para cekilememesi bu botun temel guvenlik varsayimidir.
    (Testnet anahtarlarinda bu endpoint yoktur, kontrol atlanir.)
    """
    if testnet:
        return
    restrictions = ex.sapi_get_account_apirestrictions()
    if restrictions.get("enableWithdrawals"):
        raise SystemExit(
            "GUVENLIK: API anahtarinda 'Enable Withdrawals' acik. "
            "Binance'te anahtari sadece Futures yetkisiyle yeniden olusturun."
        )
    if not restrictions.get("ipRestrict"):
        log.warning(
            "UYARI: API anahtarinda IP kisitlamasi yok. Railway'de sabit cikis IP'si "
            "kullaniyorsaniz anahtari o IP ile sinirlandirmaniz onerilir."
        )


def fetch_ohlcv_df(ex, symbol: str, timeframe: str, limit: int = 300):
    import pandas as pd

    raw = ex.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
    df = pd.DataFrame(raw, columns=["ts", "open", "high", "low", "close", "volume"])
    df["ts"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
    df = df.set_index("ts")
    # Son bar henuz kapanmamis olabilir; sinyaller sadece kapanmis barlardan uretilir.
    return df.iloc[:-1]


def current_funding_rate(ex, symbol: str) -> float:
    try:
        fr = ex.fetch_funding_rate(symbol)
        return float(fr.get("fundingRate") or 0.0)
    except Exception as exc:
        log.warning("%s funding orani alinamadi: %s", symbol, exc)
        return 0.0
