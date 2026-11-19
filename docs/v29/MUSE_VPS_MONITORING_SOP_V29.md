# SOP & Instruksi Monitoring Berkala VPS Produksi untuk Muse: ApexDualAlpha_Omni_V29_LiveReady (Core-8)

**Dokumen Handoff Operasional untuk:** Agent Executor `Muse`  
**Target Sistem Produksi:** VPS Bybit Linear Futures Mainnet (`<VPS_HOST>`, `/home/ubuntu/freqtrade/`)  
**Strategi Aktif:** `ApexDualAlpha_Omni_V29_LiveReady` (`SHA-256: c3209a5917003770c34d084e4b33bb57ae3f1677792baee813b56088138d86e3`)  
**Jadwal Monitoring:** Berkala setiap **2 hingga 4 jam sekali** (atau tepat 5 menit setelah pergantian candle 1H/4H UTC)  
**Skrip Otomatis Siap Pakai:** `vps_healthcheck_v29.py`

---

## 1. Konteks Pembaruan Model (Wajib Dipahami Muse Sebelum Monitoring)

Pada **19 November 2026 (`01:05:51 UTC` / `08:05:51 WIB`)**, engine trading live di VPS telah dimigrasikan dari `ApexDualAlpha_Omni_V26_Optimized` ke model generasi terbaru yang telah melewati *live-hardening* 4 pilar: [ApexDualAlpha_Omni_V29_LiveReady.py](../../user_data/strategies/ApexDualAlpha_Omni_V29_LiveReady.py).

Agar Muse tidak salah mengartikan perilaku normal V29 sebagai bug atau anomali, pahami **6 perubahan arsitektur utama** berikut:

### 1.1 Whitelist Ketat Core-8 (`StaticPairList`)
Model V29 dikalibrasi dan disertifikasi khusus untuk **8 aset Core-8**:
`BTC/USDT:USDT`, `ETH/USDT:USDT`, `SOL/USDT:USDT`, `ADA/USDT:USDT`, `DOGE/USDT:USDT`, `LINK/USDT:USDT`, `PAXG/USDT:USDT`, dan `HYPE/USDT:USDT`.
- **Invarian Wajib:** Whitelist tidak boleh ditambah ke koin altcoin di luar Core-8.

### 1.2 Multi-Stage Position Lifecycle Engine (`max_open_trades = 8`)
Berbeda dari V26 yang menggunakan `max_open_trades = 4` tanpa *position adjustment*, V29 mengaktifkan **`position_adjustment_enable = True`** dan **`max_entry_position_adjustment = 1`** dengan arsitektur **5 + 3 Slot**:
- **`ACTIVE_RISK_SLOTS = 5`:** Maksimal 5 posisi aktif yang belum di-*scale-out* (*unscaled risk slots*).
- **Hingga `3` Slot House-Money Macro Runner (`total max_open_trades = 8`):** Ketika posisi profit mencapai target *Stage-1B Partial Scale-Out*, strategi menjual/menutup **58% hingga 62%** ukuran posisi untuk mengunci uang tunai ke wallet, lalu menyisakan **38% hingga 42%** posisi sebagai *Free-Roll Macro Runner* (`nr_of_successful_exits >= 1`).
- **Perilaku Runner:** Posisi runner yang sudah pernah *scale-out* dibebaskan dari kuota risiko utama, memiliki batas bawah profit keras (*hard profit floor* `>= +1.55% ROE`), pengunci *MFE ratchet* (`58% - 68%`), serta trailing berbasis `EMA9/EMA21/Chandelier` T-1 agar runner pemenang tidak pernah berubah menjadi transaksi rugi (*0 winner-to-loser round-trip*).
- **Stage-1A De-Risked Winner Pyramiding (`+0.35x`):** Khusus pada sinyal breakout awal yang terkonfirmasi (`long_squeeze_expansion`, `long_rs_momentum`, `short_squeeze_expansion`) saat `ADX 4H` menanjak dan `EMA9 > EMA21`, strategi dapat melakukan penambahan posisi (*add-on*) maksimal 1 kali (`nr_of_successful_entries == 2`). **Ini adalah fitur resmi V29, bukan duplikasi order.**

### 1.3 Kemampuan Eksekusi Dual-Alpha (Long & Short Aktif)
Strategi berjalan penuh dua arah (`can_short = True`, `trading_mode = futures`, `margin_mode = isolated`) dengan batas leverage institusional (**maksimal `4.0x` untuk BTC Long**, dan **`3.0x` untuk seluruh Short serta Altcoin/PAXG Long**):
- **Engine Long (`enter_long = 1`):**
  - `long_pullback_alpha` (aktif di seluruh 8 pair Core-8 saat tren makro 4H bullish).
  - `long_bb_pullback` (oversold lower-BB bounce & washout funding negatif).
  - `long_breakout_expansion` (squeeze breakout, relative strength momentum, dan EMA9 continuation pada Altcoin).
  - `range_sweep_alpha` (liquidity sweep sisi Long saat 4H non-trending/sideways pada Altcoin).
- **Engine Short (`enter_short = 1`):**
  - `short_atr_fade_alpha` (pantulan ke resistance `EMA21`/`BB Middle` saat 4H bearish pada Altcoin & PAXG; sengaja diblokir pada BTC dan HYPE).
  - `short_crowded_fade_alpha` (overbought + euforia funding positif di Upper BB pada Altcoin).
  - `short_bb_fade` (exhaustion fade di Upper BB pada Altcoin & BTC; diblokir pada PAXG & HYPE).
  - `short_breakdown_alpha` (penembusan support 20-bar dengan lonjakan *taker sell volume* `> 1.75x` atau *bear squeeze expansion* pada Altcoin).
  - `range_sweep_alpha` (distribusi rejection sisi Short saat 4H sideways pada Altcoin).

### 1.4 Hardening Ekuitas Riil & Guard Minimum Lot Bursa (`_estimate_pair_min_stake`)
- **Rekonstruksi Ekuitas Live Akurat:** `V29_LiveReady` menghitung total ekuitas langsung dari formula `max(tied_up + max_stake, proposed_stake * eff_mo) / tradable_balance_ratio`, sehingga ukuran stake selalu presisi pada saldo riil saat ini (`~205.20 USDT`) tanpa bergantung pada `dry_run_wallet`.
- **Perilaku Normal Guard Lot Minimum BTC pada Saldo `~205 USDT`:** Kontrak minimum `BTC/USDT:USDT` di Bybit adalah `0.001 BTC` (sekitar `63.16 USDT` notional; pada leverage `4.0x` + buffer `5%` membutuhkan stake minimum **`16.58 USDT`**). Ketika sinyal BTC Long berstatus *low-conviction* (`RSI < 49`), batas alokasi defensif V29 adalah `8.2%` ekuitas (`~15.99 USDT < 16.58 USDT`). Dalam kondisi ini, `custom_stake_amount` akan mengembalikan **`0.0` (membatalkan entry secara sengaja)** agar tidak ditolak Bybit (`ErrCode 110007`). Sebaliknya, saat BTC Long berstatus *high-conviction* (`RSI >= 49`, `slowk >= 24`, `ADX 4H >= 23`), alokasi naik ke `12.5%` (`~24.37 USDT >= 16.58 USDT`) dan dieksekusi normal.
- **Sinkronisasi Scale-Out vs Minimum Lot:** Jika sisa posisi pasca *partial scale-out* akan jatuh di bawah `est_min_stake` pair tersebut, `adjust_trade_position` otomatis membatalkan *partial scale-out* agar posisi tetap utuh sampai ditutup `100%` oleh `custom_exit`.

### 1.5 Fail-Closed Safety & Kausalitas T-1 (`startup_candle_count = 840`)
- Strategi membutuhkan **`840` candle** pada awal boot (`1h`, `4h`, dan `1h FUNDING_RATE` langsung dari Bybit CCXT) untuk menghitung Z-score funding 168h/336h, volatilitas relatif 672h, dan *cross-sectional tensor* 8 pair (`130` kolom indikator di RAM).
- `confirm_trade_entry` dan `custom_stake_amount` menggunakan arsitektur **Fail-Closed**: jika terjadi exception tak terduga, fungsi otomatis mencatat `logger.error("Fail-closed in ...")` dan menolak entry (`False` / `0.0`).
- File feed eksternal (`sentiment_state.json` dan `derivatives_state.json`) memiliki guard **TTL `180.0` detik**.

---

## 2. Invarian Keamanan & Batasan Mutlak untuk Muse (Guardrails)

Saat menjalankan tugas monitoring, Muse **WAJIB** mematuhi 6 aturan keras berikut:

1. **Zero Secret Exposure:** Kredensial SSH (`VPS_SSH_PASSWORD`) dan PIN Dashboard (`DASHBOARD_PASSCODE`) diambil di memori dari environment variable atau brankas lokal `<VAULT_DIR>\credentials.txt`. **DILARANG KERAS** mencetak password, API key Bybit, secret, JWT, atau session cookie ke terminal, log, maupun laporan chat.
2. **DILARANG Menyalakan `freqtrade-paper`:** VPS memiliki RAM fisik `1.93 GiB` (tersedia `~460-515 MiB` saat 5 kontainer utama aktif) dan Swap `2.0 GiB`. Kontainer `freqtrade-paper` sengaja dimatikan. Jika pernah menjalankan perintah `docker compose`, **wajib** selalu sertakan `--no-deps freqtrade-core` (jangan pernah menjalankan `docker compose up -d` polos karena akan menyalakan `freqtrade-paper` dan menghabiskan RAM).
3. **DILARANG Mereset atau Menghapus `tradesv3.live.sqlite`:** Database `/home/ubuntu/freqtrade/user_data/tradesv3.live.sqlite` menyimpan uang riil dan `45+` histori transaksi live. Selalu buka koneksi SQLite dengan mode *read-only* (`file:...?mode=ro`, `uri=True`).
4. **DILARANG Mengubah `EXECUTION_DISABLED=1` pada `freqtrade-supervisor`:** Kontainer `freqtrade-supervisor` berjalan murni dalam mode radar pemantauan (*monitoring-only*). Seluruh eksekusi order live sepenuhnya menjadi wewenang `freqtrade-core` (`ApexDualAlpha_Omni_V29_LiveReady`).
5. **Mode Default adalah Read-Only:** Selama semua 10 gerbang kesehatan berstatus `true` / `HEALTHY`, Muse **tidak boleh** me-restart kontainer atau memodifikasi file apa pun di VPS.
6. **Backup Pra-V29 Tersimpan Aman:** Jangan menghapus folder `/home/ubuntu/freqtrade/backups/pre_v29_20261119_010551/` di VPS karena berisi snapshot rollback atomik (`docker-compose.yml`, `config_futures.live.json`, `tradesv3.live.sqlite`).

---

## 3. Cara Eksekusi Health Check Berkala (1 Perintah Otomatis)

Pada setiap siklus monitoring (setiap 2 hingga 4 jam), jalankan skrip pemeriksaan 10 gerbang yang sudah disiapkan di repo lokal:

```powershell
<REPO_ROOT>\.venv\Scripts\python.exe "<REPO_ROOT>\vps_healthcheck_v29.py"
```

Skrip `vps_healthcheck_v29.py` bekerja `100%` *read-only*, membaca kredensial dari vault secara diam-diam di memori, memeriksa VPS + REST API internal (`127.0.0.1:8080`) + SQLite + Dashboard Zero-Trust (`:5050`), lalu mencetak laporan JSON terstruktur dan keluar dengan:
- **`Exit Code 0` (`"overall_status": "HEALTHY"`):** Seluruh 10 gerbang lulus tanpa alert.
- **`Exit Code 1` (`"overall_status": "ACTION_REQUIRED"`):** Ada satu atau lebih gerbang gagal atau alert terdeteksi.

---

## 4. Penjelasan 10 Gerbang Pemeriksaan (Checklist Audit Muse)

Setiap kali membaca output JSON dari `vps_healthcheck_v29.py`, Muse wajib memverifikasi 10 gerbang berikut:

| No | Kunci Gerbang JSON | Kriteria Lulus (`true`) | Arti Jika Gagal (`false`) |
| :-: | :--- | :--- | :--- |
| 1 | `containers_healthy_ok` | Ke-5 kontainer (`freqtrade-core`, `freqtrade-dashboard`, `freqtrade-supervisor`, `freqtrade-derivatives`, `freqtrade-sentiment`) berstatus `Up`, dan `freqtrade-paper` **tidak** aktif. | Ada kontainer crash/stopped atau `freqtrade-paper` tidak sengaja menyala. |
| 2 | `system_resources_ok` | RAM `available >= 200 MiB` (normal `420 - 530 MiB`) dan penggunaan Disk `/ < 85%` (normal `~41%`). | Kebocoran memori (*memory leak*) atau log memenuhi disk. |
| 3 | `strategy_sha256_match` | File `/home/ubuntu/freqtrade/user_data/strategies/ApexDualAlpha_Omni_V29_LiveReady.py` memiliki SHA-256 `c3209a5917003770...`. | File strategi di VPS termodifikasi atau korup. |
| 4 | `freqtrade_api_and_config_ok` | REST API `127.0.0.1:8080` merespons `pong`, `strategy=ApexDualAlpha_Omni_V29_LiveReady`, `state=running`, `dry_run=False`, `max_open_trades=8`, `position_adjustment_enable=True`, `max_entry_position_adjustment=1`, `stoploss_on_exchange=True`, `trailing_stop=False`, `whitelist == Core-8`. | Konfigurasi runtime bergeser atau bot dalam status `stopped`/`paused`. |
| 5 | `core_logs_clean_ok` | Dalam 250 baris log terakhir `freqtrade-core` terdapat `Bot heartbeat` dengan `state='RUNNING'`, serta **`0`** kemunculan `Fail-closed`, `CRITICAL`, `Empty dataframe`, `Ignoring stale`, `ErrCode`, maupun `Traceback` non-WebSocket. | Terjadi exception di dalam hook strategi, data kosong, atau penolakan order dari Bybit. |
| 6 | `all_8_pairs_analyzed_ok` | Ke-8 pair Core-8 pada `/api/v1/pair_candles` memiliki dataframe teranalisis di RAM lengkap dengan kolom V29 (`funding_rate`, `fr_zscore_168`, `cs_bull_breadth`, `cs_mom_rank`, `adx_4h`). | Kegagalan unduh candle 1H/4H/Funding Rate atau kegagalan `populate_indicators`. |
| 7 | `sqlite_integrity_and_parity_ok` | `tradesv3.live.sqlite` lulus `PRAGMA integrity_check == ok`, `closed_trades >= 45`, dan jumlah `is_open=1` di SQLite sama persis dengan jumlah posisi aktif di `/api/v1/status`. | Korupsi database atau desinkronisasi state posisi antara SQLite dan memori bot. |
| 8 | `auxiliary_feeds_fresh_ok` | Usia `derivatives_state.json` (update tiap `60s`), `sentiment_state.json` (update tiap `90s`), dan `ai_supervisor_state.json` (update tiap `60s`) semuanya **`< 180.0` detik**. |Salah satu daemon kolektor (`freqtrade-derivatives`, `freqtrade-sentiment`, atau `freqtrade-supervisor`) macet/hang. |
| 9 | `port8080_localhost_only_ok` | Port `8080` (Freqtrade API) hanya listen pada `127.0.0.1:8080` dan **tidak** terekspos ke `0.0.0.0:8080`. | Celah keamanan jaringan (API internal terekspos keluar). |
| 10 | `dashboard_zerotrust_and_payload_ok` | Request tanpa cookie ke `:5050/api/data` ditolak dengan `HTTP 401`, login sah mengembalikan `strategy=ApexDualAlpha_Omni_V29_LiveReady`, `max_open_trades=8`, `core_status=ONLINE`, dan logout mengunci kembali sesi. | Gerbang Zero-Trust Dashboard bermasalah atau `dashboard_server.py` gagal membaca proses `freqtrade-core`. |

---

## 5. Pemeriksaan Tambahan Saat Ada Posisi Terbuka (`open_trades_count > 0`)

Apabila `trades_summary.open_trades_count > 0` pada output health check, Muse wajib memeriksa daftar `trades_summary.open_positions`:

1. **Verifikasi Stop Loss di Bursa (`stop_loss_abs`):**
   - Pastikan setiap posisi memiliki nilai `stop_loss_abs` yang valid (tidak `null` / `0.0`), karena `stoploss_on_exchange = True` wajib memasang hard emergency stoploss (`-29.7%` ROE / sekitar `-9.9%` jarak harga pada leverage `3x`) langsung di server Bybit.
2. **Verifikasi Leverage Institusional:**
   - Posisi Short pada pair apa pun: wajib `leverage == 3.0`.
   - Posisi Long pada Altcoin/PAXG: wajib `leverage == 3.0`.
   - Posisi Long pada `BTC/USDT:USDT`: wajib `leverage == 4.0`.
3. **Verifikasi Siklus Hidup Posisi (Unscaled vs Pyramided vs Scaled Runner):**
   - `nr_of_successful_entries == 1, nr_of_successful_exits == 0`: Posisi standar (*Stage-1 initial*).
   - `nr_of_successful_entries == 2, nr_of_successful_exits == 0`: Posisi yang telah mendapatkan *Stage-1A Winner Pyramid* (`+0.35x` add-on).
   - `nr_of_successful_exits >= 1`: Posisi *Stage-2 House-Money Macro Runner* (sudah *partial scale-out* `-58%` s/d `-62%` dan dikawal ketat oleh profit floor `>= +1.55% ROE` + MFE ratchet).
4. **Verifikasi Order Menggantung (`has_open_order`):**
   - Karena V29 menggunakan Maker Limit (`GTC`), `has_open_order = true` adalah **normal** selama durasi order belum melewati batas `unfilledtimeout` (`120 menit` untuk entry, `60 menit` untuk exit). Jika sebuah order menggantung lebih dari `130 menit` tanpa dibatalkan otomatis oleh Freqtrade, catat sebagai anomali dan laporkan ke User.

---

## 6. Matriks Tindak Lanjut: Kapan Auto-Recovery vs Kapan Eskalasi ke User

Muse harus membedakan gangguan ringan yang boleh dipulihkan otomatis (*Low-Risk Auto-Heal*) dari gangguan kritis pada modal riil yang **wajib langsung dilaporkan ke User tanpa tindakan sepihak**:

### 6.1 Gangguan yang Boleh Di-Auto-Heal oleh Muse (Tanpa Menyentuh `freqtrade-core`)

| Gejala Terdeteksi | Tindakan Auto-Heal yang Diizinkan | Verifikasi Pasca-Tindakan |
| :--- | :--- | :--- |
| `auxiliary_feeds_fresh_ok = false` karena `derivatives_state.json >= 180s` | Jalankan via SSH: `docker restart freqtrade-derivatives`, tunggu `15` detik. | Jalankan ulang `vps_healthcheck_v29.py`, pastikan usia file `< 65s`. |
| `auxiliary_feeds_fresh_ok = false` karena `sentiment_state.json >= 180s` | Jalankan via SSH: `docker restart freqtrade-sentiment`, tunggu `20` detik. | Jalankan ulang `vps_healthcheck_v29.py`, pastikan usia file `< 95s`. |
| `auxiliary_feeds_fresh_ok = false` karena `ai_supervisor_state.json >= 180s` | Jalankan via SSH: `docker restart freqtrade-supervisor`, tunggu `15` detik. | Jalankan ulang `vps_healthcheck_v29.py`, pastikan usia file `< 65s` dan log supervisor tetap `EXECUTION_DISABLED`. |
| `dashboard_zerotrust_and_payload_ok = false` karena port `5050` timeout | Jalankan via SSH: `docker restart freqtrade-dashboard`, tunggu `10` detik. | Jalankan ulang `vps_healthcheck_v29.py`, pastikan gerbang ke-10 kembali `true`. |
| Muncul `warnings` berupa `transient_ws_reconnects_in_tail > 0` (`continuously_async_watch_ohlcv`) | **Jangan restart apa pun.** Ini hanya reset koneksi WebSocket sesaat dari sisi server Bybit; CCXT otomatis reconnect dan REST fallback tetap menjaga candle 1H/4H. | Cukup pastikan `all_8_pairs_analyzed_ok = true` dan `last_candle` tetap jam tutup terakhir. |

### 6.2 Kondisi Kritis yang WAJIB Eskalasi ke User (DILARANG Restart `freqtrade-core` Sepihak Saat Ada Posisi)

Segera hentikan tindakan otomatis dan kirim laporan eskalasi ke User apabila ditemukan salah satu kondisi berikut:
1. **`core_logs_clean_ok = false` dengan log `Fail-closed in confirm_trade_entry` atau `Fail-closed in custom_stake_amount`:** Ambil cuplikan traceback 15 baris di sekitar pesan tersebut dan laporkan ke User.
2. **`sqlite_integrity_and_parity_ok = false`:** Ada ketidaksesuaian antara jumlah open trade di SQLite vs `/api/v1/status` atau `PRAGMA integrity_check != ok`.
3. **`freqtrade-core` berstatus `Exited` / `Restarting`:**
   - Jika `open_trades_count == 0`, Muse boleh menghidupkan kembali dengan `cd /home/ubuntu/freqtrade && docker compose up -d --no-deps freqtrade-core` lalu segera melaporkan penyebab keluarnya dari `docker logs freqtrade-core`.
   - Jika sedang ada posisi terbuka (`open_trades_count > 0`) dan terjadi error berulang, **jangan** melakukan perubahan konfigurasi atau rollback strategi tanpa persetujuan User.

---

## 7. Format Laporan Monitoring Berkala Muse ke User

Setiap selesai menjalankan siklus monitoring, kirimkan ringkasan singkat dan padat dengan format berikut:

```markdown
### [MUSE VPS HEALTH CHECK - V29 LiveReady]
- **Waktu Cek:** YYYY-MM-DD HH:MM WIB (`HH:MM UTC`)
- **Status Keseluruhan:** `HEALTHY (10/10 Gerbang Lulus)` / `ACTION_REQUIRED`
- **Engine & Kontainer:** `freqtrade-core` (`Up ...`, `RUNNING`), `dashboard`/`supervisor`/`derivatives`/`sentiment` (`ONLINE`), RAM Tersedia `... MiB`, Disk `...%`
- **Saldo Bybit Live:** Total `... USDT` | Free `... USDT` | Used Margin `... USDT`
- **Posisi & Histori:** Open `... / 8` | Closed `...` (Total PnL Histori: `... USDT`) | Active Locks: `...`
- **Detail Posisi Aktif (jika ada):**
  - `[Pair]` (`LONG/SHORT`, `tag`, `...x`, stake `... USDT`, PnL `...% / ... USDT`, SL `...`, stage `Initial / Pyramided / Scaled Runner`)
- **Snapshot Sinyal & Pasar (Candle 1H Terakhir `HH:00 UTC`):**
  - BTC `close=...` (`RSI=...`, `ADX 4H=...`) | Sinyal Entry Baru: `Tidak ada (0/8 pair)` atau `[Pair: tag]`
- **Kesegaran Feed & Log:** Derivatives `...s` | Sentiment `...s` | Supervisor `...s` | Error/Fail-Closed Log: `0`
```
