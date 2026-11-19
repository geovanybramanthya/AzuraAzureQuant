# Laporan Deployment Produksi dan Audit Live VPS: ApexDualAlpha_Omni_V29_LiveReady (Core-8)

**Tanggal Eksekusi:** 19 November 2026 (`01:05:51 UTC` / `08:05:51 WIB`)  
**Target VPS:** `<VPS_HOST>` (`/home/ubuntu/freqtrade/`)  
**Strategi Sebelumnya:** `ApexDualAlpha_Omni_V26_Optimized` (`max_open_trades=4`, `position_adjustment_enable=False`)  
**Strategi Aktif Sekarang:** `ApexDualAlpha_Omni_V29_LiveReady` (`max_open_trades=8`, `position_adjustment_enable=True`, `startup_candle_count=840`)  
**Status Akhir:** **LIVE ONLINE, 100% LULUS SELURUH GERBANG AUDIT (0 ERROR, 0 TRACEBACK, SALDO RIIL UTUH)**

---

## 1. Ringkasan Eksekutif

Migrasi produksi dari `ApexDualAlpha_Omni_V26_Optimized` ke [ApexDualAlpha_Omni_V29_LiveReady.py](../../user_data/strategies/ApexDualAlpha_Omni_V29_LiveReady.py) pada VPS Bybit Linear Futures Mainnet (`<VPS_HOST>`) telah selesai dilaksanakan secara bertahap dengan pendekatan *zero-downtime risk* dan *real-money preservation*. Sebelum perubahan dilakukan, seluruh sistem diaudit dalam mode *read-only*, diuji dengan *isolated live-Bybit public-data smoke test* di dalam image container produksi (`freqtrade-apex:latest`), dan diamankan melalui backup atomik sebelum cutover.

Hasil verifikasi pasca-deployment memastikan seluruh 5 kontainer (`freqtrade-core`, `freqtrade-dashboard`, `freqtrade-supervisor`, `freqtrade-derivatives`, `freqtrade-sentiment`) berjalan stabil, 8 pair Core-8 memiliki cache indikator live V29 lengkap (`130` kolom termasuk *live funding rate* dan *cross-sectional tensor*), histori `45` closed trades pada `tradesv3.live.sqlite` utuh `100%`, saldo riil `205.1977 USDT` tidak terganggu, dan gerbang keamanan Zero-Trust Dashboard (`Sentinel Security Gate`) tetap aktif tanpa celah.

---

## 2. Audit Pra-Migrasi dan Smoke Test Terisolasi (Sebelum Cutover)

### 2.1 Kondisi Awal VPS (Read-Only Pre-Flight)

| Parameter | Nilai Terukur Pra-Migrasi | Evaluasi |
| :--- | :--- | :--- |
| Open Trades (DB & REST API) | `0` posisi terbuka, `0` order terbuka, `0` active pairlocks | Aman total untuk cutover tanpa risiko memutus posisi aktif |
| Saldo Riil Bybit Mainnet | `205.19770867 USDT` (`free=205.1977`, `used=0.0`, `bot_owned=194.9378`) | Sesuai `tradable_balance_ratio = 0.95` |
| Histori Live Database | `tradesv3.live.sqlite` (`172,032` byte, `45` closed trades, `0` open) | Wajib dipertahankan utuh (dilarang reset DB live) |
| Runtime Container | `freqtrade 2026.9`, `CCXT 4.5.88`, `pandas 3.0.6`, `numpy 2.4.6`, `talib 0.8.2` | Identik dengan lingkungan sertifikasi lokal (`pandas 3.0.5`) |
| Feed Eksternal (`derivatives` & `sentiment`) | `derivatives_state.json` (interval `60s`, usia `58.9s`), `sentiment_state.json` (interval `90s`, usia `62.2s`) | Kompatibel penuh dengan guard TTL `180s` dan skema kunci `last_updated` |
| Sumber Daya VPS | Disk `41%` (`23G` kosong), RAM tersedia `507 MiB` + Swap kosong `1,407 MiB` | `freqtrade-paper` tidak dijalankan agar memori tetap lega |

### 2.2 Verifikasi Integritas File dan Smoke Test Data Live Bybit

File strategi [ApexDualAlpha_Omni_V29_LiveReady.py](../../user_data/strategies/ApexDualAlpha_Omni_V29_LiveReady.py) diunggah secara atomik (`.uploading` lalu `mv`) ke `/home/ubuntu/freqtrade/user_data/strategies/ApexDualAlpha_Omni_V29_LiveReady.py`:

- **Ukuran File:** `183,442` byte (`0` UTF-8 BOM)
- **SHA-256 Lokal vs VPS:** `c3209a5917003770c34d084e4b33bb57ae3f1677792baee813b56088138d86e3` (**MATCH 100%**)
- **Kompilasi Container (`py_compile`):** `PY_COMPILE_OK`

Sebelum `freqtrade-core` di-restart, dijalankan *isolated read-only smoke test* (`vps_stage_ab_smoke.py`) di dalam `freqtrade-apex:latest` terhadap market data publik Bybit Mainnet (`24` seri: `8 x 1h`, `8 x 4h`, `8 x 1h FUNDING_RATE` selesai dalam `2.69s`):

| Pair Core-8 | Bar `1h` / `4h` / `FR` | Harga Live (USDT) | Min Lot (`min_amt` / `min_cost`) | `est_min_stake` (Live Guard) | Simulasi `custom_stake_amount` (`205.20 USDT`, `long_pullback_alpha`) | Status |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| `BTC/USDT:USDT` | `999 / 999 / 999` | `63,165.80` | `0.001 / 5.0` | `16.58 USDT` (`4.0x`) | `0.00 USDT` (ditolak rapi saat low-conviction `rsi=44.4`, `cap=15.99 < 16.58`; high-conviction `cap=24.37` lolos) | PASS |
| `ETH/USDT:USDT` | `999 / 999 / 999` | `1,784.16` | `0.01 / 5.0` | `6.24 USDT` (`3.0x`) | `37.23 USDT` (`reconstructed=205.22`) | PASS |
| `SOL/USDT:USDT` | `999 / 999 / 999` | `73.66` | `0.1 / 5.0` | `2.58 USDT` (`3.0x`) | `37.23 USDT` (`reconstructed=205.22`) | PASS |
| `ADA/USDT:USDT` | `999 / 999 / 999` | `0.1517` | `1.0 / 5.0` | `1.75 USDT` (`3.0x`) | `37.23 USDT` (`reconstructed=205.22`) | PASS |
| `DOGE/USDT:USDT` | `999 / 999 / 999` | `0.0590` | `1.0 / 5.0` | `1.75 USDT` (`3.0x`) | `37.23 USDT` (`reconstructed=205.22`) | PASS |
| `LINK/USDT:USDT` | `999 / 999 / 999` | `9.0970` | `0.1 / 5.0` | `1.75 USDT` (`3.0x`) | `37.23 USDT` (`reconstructed=205.22`) | PASS |
| `PAXG/USDT:USDT` | `999 / 999 / 999` | `3,747.37` | `0.001 / 5.0` | `1.75 USDT` (`3.0x`) | `15.99 USDT` (`reconstructed=205.22`) | PASS |
| `HYPE/USDT:USDT` | `999 / 999 / 999` | `67.1110` | `0.01 / 5.0` | `1.75 USDT` (`3.0x`) | `40.94 USDT` (`reconstructed=205.22`) | PASS |

> [!NOTE]
> Guard `_estimate_pair_min_stake` terbukti langsung bekerja pada akun riil `205.20 USDT`: untuk `BTC/USDT:USDT` pada kondisi low-conviction (`RSI 44.4`), target margin defensif `0.082 x 194.96 = 15.99 USDT` berada sedikit di bawah batas minimum kontrak Bybit (`0.001 BTC` pada `4x` leverage + buffer `5%` = `16.58 USDT`), sehingga strategi menolaknya secara bersih (`0.00`) daripada memicu penolakan order di bursa. Pada kondisi high-conviction BTC (`RSI >= 49`, `slowk >= 24`, `ADX 4H >= 23`), alokasi `0.125 x 194.96 = 24.37 USDT` melampaui batas minimum dengan nyaman.

---

## 3. Backup Atomik dan Perubahan Bedah (Surgical Migration)

### 3.1 Backup Pra-Cutover

Seluruh berkas kritis dibackup secara atomik ke `/home/ubuntu/freqtrade/backups/pre_v29_20261119_010551/`:
- `docker-compose.yml`
- `config_futures.live.json`
- `tradesv3.live.sqlite` (disalin menggunakan API `sqlite3.backup()` dengan verifikasi `PRAGMA integrity_check = ok`, `45` trades, `0` open)

### 3.2 Perubahan Konfigurasi Produksi

Seluruh API key, secret, password, dan JWT dipertahankan utuh di VPS tanpa modifikasi maupun pencetakan ke log. Perubahan bedah terbatas pada:

1. **`/home/ubuntu/freqtrade/user_data/config_futures.live.json`:**
   - `max_open_trades`: dinaikkan dari `4` menjadi `8` (selaras dengan arsitektur V29 `ACTIVE_RISK_SLOTS = 5` + hingga `3` runner *house-money* pasca scale-out).
   - `order_types`: dilengkapi secara eksplisit menjadi `entry: limit`, `exit: limit`, `emergency_exit: market`, `force_exit: market`, `stoploss: market`, `stoploss_on_exchange: true`, `stoploss_on_exchange_interval: 60`.
   - `exchange.ccxt_config` dan `exchange.ccxt_async_config`: diaktifkan `enableRateLimit: true`, `adjustForTimeDifference: true`, `recvWindow: 10000` untuk kestabilan REST/pagination Bybit Mainnet.
   - Invarian tetap terjaga: `dry_run: false`, `trading_mode: futures`, `margin_mode: isolated`, `stake_amount: unlimited`, `tradable_balance_ratio: 0.95`, `entry_pricing.price_side: same`, `exit_pricing.price_side: other`, `api_server.listen_ip_address: 127.0.0.1`, dan `pair_whitelist` ketat pada 8 koin Core-8.
2. **`/home/ubuntu/freqtrade/docker-compose.yml`:**
   - Hanya mengganti argumen strategi pada service `freqtrade-core` dari `--strategy ApexDualAlpha_Omni_V26_Optimized` menjadi `--strategy ApexDualAlpha_Omni_V29_LiveReady` (tervalidasi `docker compose config -q`).
3. **Eksekusi Recreate Terisolasi:**
   - Hanya menjalankan `docker compose up -d --no-deps --force-recreate freqtrade-core` (memastikan `freqtrade-paper` tetap mati) dan `docker restart freqtrade-supervisor` agar daemon monitoring membaca batas slot baru `0 / 8` sambil tetap terkunci pada `EXECUTION_DISABLED=1`.

---

## 4. Hasil Audit Komprehensif Pasca-Deployment

Skrip audit pasca-deployment (`vps_postdeploy_audit.py`, log lengkap di `vps_postdeploy_audit_out.txt`) dijalankan dengan seluruh assertion lulus `100%` (`exit code 0`):

| Gerbang Audit Live | Hasil Terverifikasi | Status |
| :--- | :--- | :---: |
| **1. Identitas & SHA-256 Strategi** | `ApexDualAlpha_Omni_V29_LiveReady`, SHA-256 `c3209a5917003770...` cocok `100%` | **PASS** |
| **2. Boot Log & Parameter Runtime** | `startup_candle_count=840`, `position_adjustment_enable=True`, `max_entry_position_adjustment=1`, `max_open_trades=8`, `trailing_stop=False`, `stoploss=-0.297`, `stoploss_on_exchange=True`, `Time offset to Bybit: -17ms`, `state='RUNNING'`, `2` heartbeat sukses (`01:06:25` & `01:07:25 UTC`) | **PASS** |
| **3. Kebersihan Log Live** | `0` `ERROR`, `0` `CRITICAL`, `0` `Traceback`, `0` `Fail-closed`, `0` `Ignoring stale`, `0` `Empty dataframe` | **PASS** |
| **4. Freqtrade REST API (`127.0.0.1:8080`)** | `ping=pong`, `dry_run=False`, `whitelist=Core-8 (8 pairs)`, `balance=205.19770867 USDT`, `used=0.0`, `open=0`, `locks=0`, `closed_trades=45` | **PASS** |
| **5. Cache Indikator Live 8/8 Pair** | Semua 8 pair Core-8 terpopulasi di RAM (`130` kolom per pair, `last_candle=2026-11-19T00:00:00Z`, `has_fr=True`, `has_cs=True`) | **PASS** |
| **6. Integritas Database Live** | `tradesv3.live.sqlite` `PRAGMA integrity_check = ok`, `45` closed trades utuh, `0` open trades (sinkron dengan API) | **PASS** |
| **7. Kesehatan 5 Kontainer & Sumber Daya** | `freqtrade-core` (`336.4 MiB`, `0.44% CPU`), `dashboard` (`391.6 MiB`), `supervisor` (`154.7 MiB`, `0 / 8`, `EXECUTION_DISABLED`), `derivatives` (`38.84 MiB`, usia `37.7s`), `sentiment` (`20.65 MiB`, usia `35.0s`); RAM tersedia `513 MiB`, Swap kosong `1,599 MiB` | **PASS** |
| **8. Keamanan Jaringan & Dashboard (`:5050`)** | Port `8080` hanya terikat di `127.0.0.1:8080`; akses tidak terautentikasi ke `:5050/` mengembalikan `Sentinel Security Gate` dan `:5050/api/data` menolak dengan HTTP `401`; sesi login sah menampilkan `strategy=ApexDualAlpha_Omni_V29_LiveReady`, `max_open_trades=8`, `core_status=ONLINE`, `wallet=205.2 USDT`, `closed_trades=45`, dan logout mengunci kembali ke HTTP `401` | **PASS** |

---

## 5. Catatan Operasional Minor dan Prosedur Rollback Instan

### 5.1 Catatan Minor (Kosmetik UI)

Pada `dashboard_server.py` baris `2444`, string label informasi statis untuk leverage masih tertulis `3.0x (Altcoins, PAXG & HYPE) / 7.0x (BTC Dynamic)`. Hal ini bersifat murni teks tampilan UI dashboard; eksekusi leverage riil di bursa Bybit tetap dikendalikan sepenuhnya oleh fungsi `leverage()` di `ApexDualAlpha_Omni_V29_LiveReady.py`, yaitu **maksimal `4.0x` untuk BTC Long dan `3.0x` untuk semua Short serta Altcoin Long**.

### 5.2 Prosedur Rollback Cepat (Jika Diperlukan)

Apabila sewaktu-waktu diperlukan pengembalian ke `ApexDualAlpha_Omni_V26_Optimized`, cukup jalankan dari VPS (saat tidak ada posisi terbuka):

```bash
cd /home/ubuntu/freqtrade
cp backups/pre_v29_20261119_010551/docker-compose.yml ./docker-compose.yml
cp backups/pre_v29_20261119_010551/config_futures.live.json ./user_data/config_futures.live.json
docker compose up -d --no-deps --force-recreate freqtrade-core
docker restart freqtrade-supervisor
```
