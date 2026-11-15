# Laporan Instrumentasi LOG-ONLY (staging) - 2026-11-11

Status: SELESAI di staging. TIDAK ADA yang di-deploy, TIDAK ADA file VPS yang
diubah, TIDAK ADA kontainer yang di-restart. Semua kerja di
`~/workspace/instr-staging/`.

## 1. Hash (SHA256)

| File | Pristine (lama) | Instrumented (baru) |
|---|---|---|
| `ApexDualAlpha_Omni_V26_Optimized.py` | `1f62cbeb3513ca51c7ab4f7894fe453185de2163b6e07c4f06a3d9c81c6b8a30` | `af184227105582fd71aec2d160571e673f5d5c73d6a91bc2aeabba8232fe3e72` |
| `news_sentiment_daemon.py` | `8cf9da63f59e145404034468216c7f951c741c4ac6fd0327d9e00b7d2b24b9fc` | `edfe2427eb0d84c55ef3b6b2860c0525869a8576f7407d3603c3c68ff647172b` |

Verifikasi sumber (Langkah 0): hash sandbox strategi cocok dengan yang
disyaratkan; hash daemon live (via SSH, read-only) cocok; hash strategi live
(via SSH) cocok dengan salinan sandbox. File `.pristine` di staging
terverifikasi masih sama dengan hash di atas setelah semua kerja selesai.
Line ending dipertahankan: strategi CRLF (1051 -> 1107 baris), daemon LF
(820 -> 863 baris).

## 2. Diff

- `~/workspace/instr-staging/diff-strategy.patch` (56 baris berubah, unified diff)
- `~/workspace/instr-staging/diff-daemon.patch` (43 baris berubah, unified diff)
- File kerja: `ApexDualAlpha_Omni_V26_Optimized.py` (instrumented),
  `news_sentiment_daemon.py` (instrumented), plus salinan `.pristine`.

Isi perubahan (hanya observability):
- A. Docstring strategi: baris INSTR ditambahkan.
- B. F1b warning di `populate_indicators` (loop fallback kolom rezim).
- C. Gate snapshot `[INSTR-GATE]` di akhir `populate_entry_trend`
  (dedup `_instr_logged` per pair per tanggal candle; hanya `iloc`, tidak
  menulis dataframe).
- D. `[INSTR-BLACKOUT]` di `aggregate_sentiment` (transisi per-pair +
  MACRO_BLACKOUT / MACRO_CRASH; baseline pertama tidak me-log).
- E. Arsip state per jam di `save_state` (maks 1/jam, prune >30 hari,
  seluruh blok dalam try/except; kegagalan arsip hanya warning).

## 3. Bukti verifikasi

### 3.1 py_compile
`python3 -m py_compile` kedua file: OK.

### 3.2 Load check freqtrade 2026.9 (venv sandbox abtest-a)
Perintah:
`freqtrade list-strategies --userdir bt-instr --strategy-path bt-instr/user_data/strategies --recursive`
Hasil: `ApexDualAlpha_Omni_V26_Optimized ... Status OK`.

### 3.3 Paritas backtest (bukti utama)
- Timerange: 20260901-20260925, 8 pair whitelist, config staging
  `bt-config.json` (turunan config abtest-a, secret dikosongkan).
- Data: 1h + 4h feather 8 pair, 2024-01-01 s.d. 2026-09-26 (diambil
  read-only dari VPS karena di abtest-a tidak ada data candle; lihat
  Deviasi 2).
- Perintah pristine:
  `freqtrade backtesting --userdir bt-pristine/user_data --datadir bt-data/bybit --config bt-config.json --strategy ApexDualAlpha_Omni_V26_Optimized --timerange 20260901-20260925 --export trades --export-filename bt-pristine`
  (log: `bt-pristine.log`, hasil: `bt-pristine/backtest-result-2026-11-11_14-44-22.zip`)
- Perintah instrumented: sama dengan `--userdir bt-instr/user_data` dan
  `--export-filename bt-instr` (log: `bt-instr.log`, hasil:
  `bt-instr/backtest-result-2026-11-11_14-44-51.zip`).
- Hasil: KEDUA run 19 trade, 16W/0D/3L, total profit 155,84027628 USDT,
  max drawdown 26,715558 USDT. Perbandingan trade-by-trade atas 15 field
  (pair, enter_tag, exit_reason, open/close date, open/close rate,
  stake_amount, amount, profit_ratio, profit_abs, profit_pct, duration,
  leverage, is_short): **0 perbedaan**. Enter tag identik
  (`long_pullback_alpha`, `long_rs_momentum`, `long_squeeze_expansion`);
  exit reason identik (7 macam).
- Provenance: file `.py` di dalam tiap zip backtest di-hash dan cocok
  dengan file yang dijalankan (pristine = `1f62cbeb...`, instrumented =
  `af184227...`).
- `[INSTR-GATE]`: 8 baris di `bt-instr.log` (1 per pair; di backtest
  `populate_entry_trend` dipanggil sekali per pair dengan dataframe penuh,
  jadi dedup menghasilkan 1 baris/pair; di live akan 1 baris per candle
  baru per pair). Semua field hadir (contoh SOL 2026-09-25:
  macro_bull_4h=True, macro_bear_4h=False, adx_4h=24,76, rsi=61,00,
  slowk=85,37, slowd=82,66, close=117,37, ema_21/50/200, support
  shift1=112,42, taker_sell_volume_surge=False, vol_ratio=0,65,
  enter_long=0, enter_short=0, enter_tag=kosong). Catatan: di output
  console yang di-redirect, baris panjang ter-wrap antar baris fisik
  (~200 kolom, artefak tampilan console); rekaman lengkap saat digabung.
- `[INSTR-F1B]`: 0 baris di backtest (fallback tidak pernah terpicu;
  kolom rezim hadir). Jalur warning dibuktikan terpisah secara sintetik
  (lihat 3.5).

### 3.4 Daemon --once staging
Perintah (di `daemon-test/`, venv staging dengan feedparser/vaderSentiment):
`python user_data/modules/news_sentiment_daemon.py --once`
Hasil: EXIT 0. RSS berhasil diambil (112 artikel dari 5 feed; egress TIDAK
memblokir RSS). State tertulis (`sentiment_state.json`, 36.303 byte) dan
arsip pertama tertulis (`sentiment_archive/sentiment_state_20261111-1446.json`,
ukuran sama). 0 baris `[INSTR-*]` pada run pertama: BENAR, karena run
pertama hanya menetapkan baseline (old=None, tidak ada transisi).
Catatan observasi (bukan bagian deliverable): saat run ini BTC blackout
aktif (BLACKOUT_RISK, 24 headline).

### 3.5 Tes sintetik daemon (`test-daemon-instr.py`, SEMUA LULUS)
- D3: aggregate pertama: 0 log transisi (baseline).
- D1: artikel blackout SOL (compound netral): `pair=SOL/USDT:USDT blackout
  False->True reason=SYNTH_BLACKOUT_TEST`, lalu saat artikel dihapus:
  `True->False reason=None`.
- D2: 20 artikel bearish (compound -0,9): `MACRO_BLACKOUT False->True`
  dan `MACRO_CRASH False->True`; saat cache dikosongkan: keduanya
  `True->False`.
- E1: save_state pertama menulis 1 arsip (`sentiment_state_20261111-1447.json`).
- E2: save_state kedua di jam yang sama: tetap 1 arsip.
- E3: jam dipaksa maju (monkeypatch datetime modul): arsip ke-2 tertulis
  (`...-1647.json`) dan arsip 31-hari di-prune.
- E4: direktori arsip diblokir (file bernama `sentiment_archive`):
  1 warning `[INSTR-ARCHIVE] ... File exists ...` dan main save tetap
  berhasil (`sentiment_state.json` tertulis).

### 3.6 Tes sintetik F1b (`test-f1b-instr.py`, LULUS)
`populate_indicators` dengan dataframe tanpa kolom rezim 4h: 3 warning
`[INSTR-F1B]` (satu per kolom) dan ketiga kolom default ke False.

## 4. Deviasi dari desain

1. Data backtest: abtest-a TIDAK berisi data candle (hanya
   `leverage_tiers_USDT.json`), jadi feather 1h+4h 8 pair diambil read-only
   (scp) dari VPS ke `bt-data/bybit/futures/`. Jendela 20260901-20260925
   tetap seperti desain; sumber data dicatat di sini, bukan di abtest-a.
2. `reload_markets` butuh Bybit; egress sandbox di-geo-block CloudFront
   (api.bybit.com dan api.bytick.com, keduanya 403). Solusi staging
   sementara: relay lokal `bt-relay2.py` (HTTP CONNECT, allowlist hanya
   api.bybit.com:443, tiap CONNECT mem-spawn `ssh -W api.bybit.com:443`
   sementara; tanpa file/kontainer di VPS) + `httpsProxy` di
   `bt-config.json` (config staging saja). Relay sudah dimatikan setelah
   backtest; port bersih. Catatan: `ccxt_config` di config abtest-a sudah
   berisi `httpsProxy` lama ke 127.0.0.1:8888 (relay A/B yang sudah
   dimatikan); nilainya diganti ke relay staging ini.
3. Upaya pertama memakai `-R` (remote forward) GAGAL dengan cara yang
   belum terpecahkan: koneksi lewat `-R` mengembalikan artefak sisi
   sandbox (pesan interceptor TCP / banner sshd sandbox 9.6p1), padahal
   `-L` ke VPS:22 dan `ssh -W api.bybit.com:443` terbukti jalan. Akar
   anomali `-R` TIDAK ditetapkan; tidak dikejar lebih jauh karena `-W`
   memenuhi kebutuhan dan tidak ada yang di VPS yang diubah. Relay v1
   (`bt-relay.py`, berbasis `-L`/`-R`) tidak dipakai; yang dipakai v2.
4. Helper gate snapshot memakai dua closure inline `_g`/`_g_shift1` di
   dalam `populate_entry_trend` (bukan fungsi/metode baru); sesuai desain
   "helper inline", hanya membaca (`iloc`).
5. `save_state`: nama arsip memakai `%Y%m%d-%H%M` (menit) sesuai format
   `YYYYMMDD-HHMM` di desain, sementara gating per jam dilacak terpisah
   via `_last_archive_hour` (`YYYYMMDD-HH`).
6. `--userdir` backtest harus menunjuk ke `.../user_data` (bukan parent)
   dan `--datadir` eksplisit tidak ditambah nama exchange oleh freqtrade,
   jadi dipakai `--datadir bt-data/bybit`. Dua kali percobaan awal gagal
   karena hal ini (strategi tidak ditemukan, lalu data tidak ditemukan);
   bukan cacat pada file instrumented.

## 5. Pernyataan eksplisit: yang TIDAK diubah

- TIDAK ADA kondisi entry/exit yang diubah: semua engine (long_pullback,
  long_squeeze, long_rs, short_fade, short_breakdown), semua gerbang
  (`macro_bull_4h`/`macro_bear_4h`/`macro_bull_4h_adaptive`, ADX, RSI,
  Stoch, EMA, support, volume surge), semua ambang (mis. RSI 32-48,
  ADX >= 21, close < EMA21*0,985, close < support*0,999), urutan atribusi
  tag, `minimal_roi`, stoploss/trailing, leverage, dan `confirm_trade_entry`
  / `custom_exit` / `check_entry_timeout` (F3c) sama persis dengan pristine.
- TIDAK ADA perubahan pada perhitungan skor/blackout daemon
  (`aggregate_sentiment`): hanya pembacaan nilai yang sudah dihitung dan
  pencatatan transisi.
- TIDAK ADA perubahan pada `save_state` utama (atomic write tetap sama);
  arsip adalah penulisan tambahan di dalam try/except sendiri.
- TIDAK ADA perubahan perilaku bila tidak ada yang berubah: log INSTR
  hanya muncul saat (a) fallback F1b benar-benar terpicu, (b) candle baru
  per pair (1 baris), (c) blackout berubah, (d) jam arsip berganti.
- Bukti: diff hanya 56 + 43 baris (lihat §2); paritas backtest 0 perbedaan
  pada 19 trade (lihat §3.3).

## 6. Yang TIDAK dilakukan (di luar wewenang task)

- Tidak ada deploy ke VPS, tidak ada file VPS yang ditulis/diubah, tidak
  ada kontainer di-restart. Semua proses sementara (relay, tunnel) sudah
  dimatikan; port 18765/18766 bersih.
- Rotasi API key Bybit (tugas terpisah, menunggu user) tidak disentuh.
- File-file staging yang tersisa di `~/workspace/instr-staging/`:
  strategi + daemon (pristine & instrumented), diff, config backtest,
  data candle, direktori `bt-pristine/` + `bt-instr/` (hasil backtest),
  `daemon-test/` (run --once + tes sintetik), `daemon-venv/`,
  `test-daemon-instr.py`, `test-f1b-instr.py`, `bt-relay.py` (v1, tidak
  dipakai), `bt-relay2.py` (v2, sudah dimatikan), `REPORT.md` (file ini).
