# tradebot — Binance Futures Hibrit Trend + Kırılım Botu

Binance USDT-M Futures üzerinde 4 saatlik mumlarla çalışan, orta risk profilli
otomatik işlem botu. Railway üzerinde 7/24 worker olarak koşar.

**Pariteler:** BTCUSDT, ETHUSDT, SOLUSDT
(LTC 5 yıllık backtest'te tüm pencerelerde zarar üretti ve evrenden çıkarıldı)
**Hedef:** yıllık ~%50 (garanti değildir; trend yıllarında aşılabilir, yatay
yıllarda drawdown normaldir)

## Strateji

Sermaye iki bağımsız "kol" arasında paylaşılır; ikisi de aynı risk çekirdeğini
kullanır:

| | TREND kolu | BREAKOUT kolu |
|---|---|---|
| Giriş | EMA20/EMA50 yönü + ADX(14) > 25 + kapanış EMA20'nin doğru tarafında | 20 günlük Donchian kanal kırılımı (turtle) |
| Stop | 2×ATR(14) | 2×ATR(14) sabit |
| Yönetim | 1.5×ATR kârda başabaş, sonra 3×ATR iz süren stop | 10 günlük karşı kanal kapanışında çıkış |
| Çıkış | EMA kesişimi tersine dönerse | Donchian exit kanalı |

Ek filtreler:
- **Makro yön filtresi:** fiyat EMA200(4h) üstündeyken sadece long, altındayken
  sadece short alınır — büyük resme karşı işlem yapılmaz
- **Cooldown:** stop yiyen pozisyon 24 saat (6 bar) boyunca aynı yöne yeniden
  giremez — testere piyasada öğütülmeyi engeller
- Pozisyon yönündeki funding oranı > %0.1/8s ise yeni giriş yapılmaz

### Risk çekirdeği (strateji ne derse desin aşılamaz)

- İşlem başına risk: özsermayenin **%1**'i (stop mesafesine göre boyutlama)
- Toplam kaldıraç tavanı: **3x** — tek pozisyon nominali en fazla 1× özsermaye
- **Aynı yön tavanı:** aynı anda aynı yönde en fazla 3 pozisyon — pariteler
  korele olduğu için hepsinin birlikte stop yemesini sınırlar
- **Aylık kill-switch:** ay içi zarar %8'e ulaşırsa tüm pozisyonlar kapatılır,
  ay sonuna kadar işlem yapılmaz
- Stop emirleri borsa tarafında `STOP_MARKET` olarak durur — bot çökse bile
  pozisyon korumasız kalmaz

## Güvenlik

1. **API anahtarını sadece "Enable Futures" yetkisiyle oluşturun.**
   "Enable Withdrawals" işaretliyse bot açılışta bunu tespit eder ve
   **çalışmayı reddeder** (`bot/exchange.py: assert_key_safety`).
2. Anahtarlar yalnızca ortam değişkenlerinde yaşar (Railway → Variables).
   Repoya asla anahtar koymayın; `.env` dosyası `.gitignore`'dadır.
3. Mümkünse anahtara IP kısıtlaması ekleyin (Railway static outbound IP).
4. Önce `TESTNET=true` + `DRY_RUN=true` ile başlayın; sırasıyla
   DRY_RUN → testnet gerçek emir → küçük gerçek sermaye şeklinde ilerleyin.
5. Binance hesabınızda 2FA açık olsun.

## Kurulum

```bash
pip install -r requirements.txt
cp .env.example .env   # doldurun (sadece lokal; Railway'de Variables kullanın)
```

## Backtest

```bash
python -m backtest.selftest              # motor doğrulaması (sentetik veri)
python -m backtest.run --days 365        # son 1 yıl, 4 parite, gerçek Binance verisi
python -m backtest.run --days 730 --symbols BTCUSDT,ETHUSDT
```

Backtest, canlı botla **birebir aynı** strateji ve risk modüllerini kullanır.
Komisyon (%0.05 taker), slippage (%0.03) ve gerçek funding oranları dahildir.
Veri `backtest/cache/` altına indirilir ve tekrar kullanılır.

> Not: Claude Code cloud ortamında çalıştırıyorsanız ortamın ağ politikasında
> `fapi.binance.com` adresine izin verilmiş olmalıdır.

## Railway'e kurulum

1. Bu repoyu Railway'de **New Project → Deploy from GitHub repo** ile bağlayın.
2. Railway `railway.json` + `Procfile`'ı algılar; başlangıç komutu
   `python -m bot.main` (worker, HTTP portu yok).
3. **Variables** bölümüne `.env.example`'daki değişkenleri girin
   (`TESTNET=true`, `DRY_RUN=true` ile başlayın).
4. Deploy loglarında `Bot basladi [DRY_RUN]` mesajını ve 4 saatlik bar
   kapanışlarında sinyal loglarını doğrulayın.
5. Telegram bildirimi için `TELEGRAM_BOT_TOKEN` ve `TELEGRAM_CHAT_ID` girin
   (BotFather'dan bot oluşturun; chat id için @userinfobot).
6. Kademeli geçiş: `DRY_RUN=false` (testnet) → gözlem → `TESTNET=false` ve
   küçük sermaye.

## Mimari

```
bot/
  config.py      # env tabanlı konfigürasyon
  indicators.py  # EMA, ATR, ADX (Wilder), Donchian — backtest ile ortak
  strategy.py    # hibrit sinyal mantığı — backtest ile ortak
  risk.py        # boyutlama, kaldıraç tavanı, aylık kill-switch — ortak
  exchange.py    # ccxt Binance USDT-M + anahtar güvenlik kontrolü
  notifier.py    # Telegram
  main.py        # canlı döngü (4h bar kapanışında)
backtest/
  data.py        # Binance public API'den mum + funding indirme (cache'li)
  engine.py      # portföy backtest motoru (komisyon/slippage/funding dahil)
  run.py         # rapor: getiri, drawdown, aylık kırılım, kol/sembol bazında
  selftest.py    # sentetik veriyle motor doğrulaması
```

## Sorumluluk reddi

Bu yazılım eğitim amaçlıdır. Kaldıraçlı kripto türevleri yüksek risk içerir;
geçmiş performans (backtest dahil) gelecek getirinin garantisi değildir.
Kaybetmeyi göze alamayacağınız parayla işlem yapmayın.
