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


def prepare_symbol(ex, symbol: str, leverage: int) -> None:
    """Sembol icin kaldirac ve marjin modunu ayarlar (bir kez, acilista).
    Binance varsayilani 20x olabilir - biz dusuk kaldiracla genis likidasyon
    tamponu istiyoruz."""
    try:
        ex.set_margin_mode("cross", symbol)
    except Exception:
        pass  # zaten cross ise borsa hata dondurur, sorun degil
    try:
        ex.set_leverage(leverage, symbol)
    except Exception as exc:
        log.warning("%s kaldirac ayarlanamadi: %s", symbol, exc)


def adjust_quantity(ex, symbol: str, qty: float, price: float,
                    stop_distance: float, intended_risk: float) -> float:
    """Miktari borsa hassasiyetine ve minimum emir kurallarina uydurur.

    Kucuk sermayede istenen miktar borsa minimumunun altinda kalabilir.
    Minimuma YUKARI yuvarlamaya ancak gercek risk hedeflenen riskin 2 katini
    asmiyorsa izin verilir; asiyorsa islem atlanir (0 doner).
    """
    market = ex.market(symbol)
    limits = market.get("limits", {})
    min_qty = (limits.get("amount") or {}).get("min") or 0.0
    min_notional = (limits.get("cost") or {}).get("min") or 5.0

    candidate = max(qty, min_qty, min_notional / price)
    candidate = float(ex.amount_to_precision(symbol, candidate))
    if candidate < min_qty or candidate * price < min_notional:
        # hassasiyet asagi yuvarladi, bir adim yukari dene
        step = (market.get("precision") or {}).get("amount")
        step = step if isinstance(step, float) and step > 0 else 10 ** -(step or 3)
        candidate = float(ex.amount_to_precision(symbol, candidate + step))
    actual_risk = candidate * stop_distance
    if candidate <= 0 or actual_risk > 2 * intended_risk:
        log.info("%s: miktar borsa minimumuna sigmadi (risk %.2f > 2x hedef %.2f), atlandi",
                 symbol, actual_risk, intended_risk)
        return 0.0
    return candidate


def _position_risk(ex, market_id: str):
    """Ham positionRisk sorgusu (sembol bazli). Basarili cagri sifir
    pozisyonu da ACIKCA soyler - ccxt'nin sifirlari gizlemesinden etkilenmez.
    Basarisizsa None doner."""
    for meth in ("fapiPrivateV2GetPositionRisk", "fapiPrivateV3GetPositionRisk",
                 "fapiPrivateGetPositionRisk"):
        fn = getattr(ex, meth, None)
        if fn is None:
            continue
        try:
            return fn({"symbol": market_id})
        except Exception as exc:
            log.debug("%s %s basarisiz: %s", meth, market_id, exc)
    return None


def fetch_net_positions(ex, symbols):
    """Borsadaki net pozisyonlar: ({symbol: +qty/-qty/0}, dogrulanan semboller).

    Sembol basina ham positionRisk sorgusu yapilir: basarili cevap (bos liste
    dahil) kesin bilgidir - bos/sifir = pozisyon kapali. Yalnizca sorgunun
    kendisi basarisizsa sembol 'seen' disinda kalir ve cagiran taraf onu
    SONUCSUZ sayar (silme/iptal yapmaz).
    """
    result = {s: 0.0 for s in symbols}
    seen = set()
    for s in symbols:
        rows = _position_risk(ex, ex.market_id(s))
        if rows is None:
            continue
        result[s] = sum(float(r.get("positionAmt") or 0.0) for r in rows)
        seen.add(s)
    return result, seen


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
