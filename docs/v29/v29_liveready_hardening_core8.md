# Laporan Live-Hardening & Sertifikasi Core-8 `ApexDualAlpha_Omni_V29_LiveReady`

> [!TIP]
> **Status Kesiapan Core-8: CERTIFIED PASS (Core-8 Whitelist Only)**
>
> [ApexDualAlpha_Omni_V29_LiveReady.py](../../user_data/strategies/ApexDualAlpha_Omni_V29_LiveReady.py) (`SHA-256: c3209a5917003770`, `183,442` bytes, `0` UTF-8 BOM) dibangun dari [ApexDualAlpha_Omni_V29_Champion.py](../../user_data/strategies/ApexDualAlpha_Omni_V29_Champion.py) (`SHA-256: 618ec24cd7b582ff`, tetap utuh) dengan **nol perubahan sinyal/threshold alpha**, semata-mata menambal seluruh temuan kritis live-safety, fail-open, sinkronisasi `min_stake`, AST rekursif `<= 6` term, dan kompatibilitas CLI `freqtrade lookahead-analysis`.
>
> Hasilnya: seluruh **4 pilar audit deterministik lulus `100%`** (`final_4pillar_audit_v29_liveready.py`), dan performa Core-8 justru **meningkat** pada Suite A (`+492,080.80 USDT`), Suite A Fee 10 bps (`+213,814.65 USDT`), Suite B (`+557,490.63 USDT`, DD turun `6.46%`), Gate 7 Live Order (`+568,561.84 USDT`), serta Blind Holdout (`+59.44 USDT`, WR `68.75%`, DD turun `7.52%`).

---

## 1. Daftar Perbaikan Live-Hardening & Hygiene Kode

| # | Area Perbaikan | Masalah Sebelumnya di `V29_Champion` | Perbaikan di `V29_LiveReady` | Verifikasi |
|---|---|---|---|---|
| 1 | **Akurasi Saldo Akun & Anti-Latch (`__init__`, `custom_stake_amount`, `_eval_scaleout_plan`)** | `_last_known_wallet` default `1000.0` di live tanpa `dry_run_wallet` + guard `0.75 * prev_w` mengunci bot di akun `< 750 USDT` atau pasca DD `> 25%`; sebaliknya `_eval_scaleout_plan` menimpa saldo ke bawah saat cap kontrak Bybit aktif. | Inisialisasi `0.0` di mode live; estimasi ekuitas eksak lewat `max(tied_up + max_stake, proposed_stake * eff_mo) / tbr` (sesuai rumus `wallets.py` Freqtrade yang bebas cap kontrak per pair); `_eval_scaleout_plan` tidak pernah menimpa `_last_known_wallet` ke bawah. | Lulus unit test `100 USDT` live, `-40%` DD, `500k` PAXG cap, dan `1M` LINK cap ([test_v29_liveready_safety.py](../../tests/test_v29_liveready_safety.py)). |
| 2 | **Fail-Closed Exception Handling** | `confirm_trade_entry` (`return True`) dan `custom_stake_amount` (`stake = proposed_stake`) meloloskan order saat DB/ORM error. | Diubah menjadi fail-closed (`return False` dan `return 0.0`) disertai `logger.error(..., exc_info=True)`. | Lulus Test 4 (simulasi `RuntimeError("DB locked")`). |
| 3 | **Sinkronisasi `min_stake` & `exit_pricing` Partial vs Full Exit** | `custom_exit` menunda exit dengan `min_stake=None` (`2.0 USDT`) sementara `adjust_trade_position` memakai `min_stake` asli exchange dan `entry_profit`. | Tambah `_estimate_pair_min_stake()` (membaca `dp.market(pair)` dengan buffer `1.05x`), sinkronkan `current_exit_rate`/`current_exit_profit` di `adjust_trade_position`, serta satukan helper `_is_short_bb_chop()`. | Lulus Test 6 (ETH akun `100 USDT` langsung mengambil full TP tanpa defer) & Gate 7 `all_clean: True`. |
| 4 | **Short Heat Cap & Feed TTL** | `underisked_short_tied` di-clamp ke `max(min_inst_stake, ...)` sehingga tetap buka short saat budget habis; `derivatives_state.json` tanpa cek kadaluarsa; `_get_candle_info` rawan `NaN`/`Inf`. | Hapus clamp `max(min_inst_stake, ...)` agar budget negatif memblokir order (`0.0`); tambah validasi TTL `180` detik di `derivatives_state.json`; tambah `_safe_float()` (`math.isfinite`), normalisasi UTC `_hours_since()`, dan `_parse_fixed_stake()`. | Lulus Test 5, Test 7, Test 8, dan langsung menaikkan performa Suite A/B/Blind Holdout. |
| 5 | **Kompatibilitas `lookahead-analysis` CLI & `ABLATE_CROSS_SECTIONAL`** | `__init__` memaksa `entry_pricing.price_side = "same"` (crash saat order `market`), dan `ABLATE_CROSS_SECTIONAL` masih menyisakan `cs_er12_mean` / `cs_bear_breadth` aktif. | `price_side = "same"` hanya dipasang jika `order_types.entry != "market"`; `ABLATE_CROSS_SECTIONAL = True` kini mengosongkan tensor CS dan seluruh gate `cs_*` secara penuh. | **8/8 pair Core-8** lulus stok `freqtrade lookahead-analysis` (`no_error=True, no_bias=True`); NonCS multi-pair mencatat `0` indikator bias (`la_8pairs_tables.txt`). |
| 6 | **Strict Recursive AST `<= 6` Term & Dead Code Cleanup** | `13` ekspresi bersarang (`IfExp`, `~(...)`, `np.where`, `dataframe[...]`) melewati `6` term; terdapat variabel/cabang mati (`long_ema9_pb`, shadowed `long_squeeze_expansion`). | Seluruh `13` ekspresi dipecah secara semantik (`max_recursive_terms = 6`, `0` pelanggaran) dan kode mati dibersihkan tanpa mengubah logika sinyal. | Lulus `strict_recursive_ast_audit` & `strict_gate6_audit` (`0` BOM, `0` `wallets`, `0` koin non-BTC, `0` `_gN`). |

---

## 2. Komparasi Performa Core-8: `V28_Champion` vs `V29_Champion` vs `V29_LiveReady`

Sumber data terverifikasi: `v29_liveready_audit_report.json` dan `m6_certification_report.json`.

| Skenario Uji (Core-8) | `V28_Champion` | `V29_Champion` | **`V29_LiveReady`** | Delta `LiveReady` vs `V29_Champion` |
|---|---:|---:|---:|---|
| **Suite A Comp (`20240101-20260925`)** | `+319,815.27` (`5.29%` DD, `7/8`) | `+480,629.39` (`4.60%` DD, `8/8`) | **`+492,080.80 USDT`** (`1,095 tr, 79.00% WR, DPF 3.0945, %PF 2.2086, 4.60% DD, 32/32 mo, 8/8`) | **`+11,451.41 USDT`** (`+2.38%`), DPF `+0.0424` |
| **Suite A Fee 10 bps** | `+157,963.83` (`8.29%` DD, `6/8`) | `+211,589.71` (`4.71%` DD, `8/8`) | **`+213,814.65 USDT`** (`1,089 tr, 77.32% WR, 4.71% DD, 8/8`) | **`+2,224.94 USDT`** (`+1.05%`) |
| **Suite B Extended (`33` bln)** | - | `+531,623.86` (`6.59%` DD, `33/33`) | **`+557,490.63 USDT`** (`1,133 tr, 78.55% WR, DPF 2.5976, 6.46% DD, 33/33 mo, 8/8`) | **`+25,866.77 USDT`** (`+4.87%`), DD turun `-0.13 pp` |
| **Blind Holdout (`20261031-20261118`)** | `+4.67` (`13 tr`, `6.85%` DD) | `+53.02` (`17 tr`, `64.71%` WR, `8.08%` DD) | **`+59.44 USDT`** (`16 tr, 68.75% WR, DPF 1.5230, %PF 1.6065, 7.52% DD`) | **`+6.42 USDT`**, WR `+4.04 pp`, DD turun `-0.56 pp` |
| **Gate 7 Live Order Simulator** | - | `+556,756.41` (`all_clean: True`) | **`+568,561.84 USDT`** (`1,096 tr, 160 PE, 0 dur0, 0 rt_loss, 0 dust, all_clean: True`) | **`+11,805.43 USDT`** (`+2.12%`) |
| **Suite C Fixed 250 (`1k` & `500k`)** | `+5,739.71` (`31/32` mo) | `+7,014.77` (`0.0` diff, `32/32`) | **`+7,014.77 USDT`** (`1,064 tr, 78.38% WR, %PF 2.1498, 0.0 diff, 32/32 mo, 8/8`) | Identik `0.00 USDT` |
| **Suite C Fee 12 bps / Ex-HYPE** | - / `+4,537.51` | `+5,766.90` / `+5,990.80` | **`+5,766.90` (`%PF 1.8634`) / `+5,990.80` (`%PF 2.0537`)** | Identik `0.00 USDT` |
| **Oct-OOS (`20260925-20261031`)** | - | `+161.82` (`40 tr`) | **`+156.99 USDT`** (`39 tr, DPF 1.6652, %PF 1.6037, 2/2 mo`) | `-4.83 USDT` (`-1` trade diblokir oleh short heat cap) |
| **Suite D `203.38` / Micro `100` USDT** | - | `+95,851.06` / `+45,913.15` | **`+94,742.46` / `+44,461.16 USDT`** (`4.60% DD, 32/32 mo, 8/8, dust all_clean: True`) | `-1.16%` / `-3.16%` (`-5` s.d. `-7` trade short over-heat diblokir bersih) |

Sebaran 8 koin `V29_LiveReady` Suite A (`+492,080.80 USDT`): `ADA +117,724.90`, `HYPE +95,405.82` (`19.39%` share), `SOL +78,269.76`, `ETH +72,622.83`, `LINK +63,066.47`, `DOGE +56,580.81`, `BTC +7,693.98`, `PAXG +716.12`. Seluruh `8` tag aktif dengan `min_tag_count = 40`.

---

## 3. Verifikasi Ketahanan Statistik, Lookahead, & Integritas Workspace

- **Wick 8 bps Haircut (Gate 3):** Suite A `%PF = 1.9658` (naik dari `1.9399`), Suite C `%PF = 1.9037` (ambang `>= 1.50`). Delay `+1 bar`: `%PF = 1.4541` (`>= 1.15`).
- **Annual Consistency (Gate 4):** `2024 %PF = 2.0597`, `2025 %PF = 1.9009`, `2026 %PF = 3.1561` (semua `>= 1.35`, `100%` bulan hijau).
- **Monotonisitas Global Scale (Gate 5):** Trades `1,192 -> 1,137 -> 1,064 -> 988 -> 914` dengan perubahan `%PF` `-14.85% / -9.93% / 0% / +1.54% / -2.51%`.
- **DSR(300) & 10,000 Monte Carlo:** `DSR(300) = 1.0` di Suite A (`SR_ann = 5.6749`) dan Suite C (`SR_ann = 4.7493`); `10,000` iterasi MC Suite A: `prob_positive = 100.0%`, `pf_p05 = 1.9198`, `pf_median = 2.2089`, `dd_median = 14.02%`, `dd_p95 = 20.60%`.
- **Lookahead & Warmup (Gate 8):**
  - MockDP 8-koin truncation (`3` cutoff) & `840`-bar warmup: `0` mismatch, `0.0` selisih indikator, `308/308` (`100.0%`) paritas warmup.
  - CLI `freqtrade lookahead-analysis` stok `V29_LiveReady`: **`8/8` pair Core-8 lulus `no_error=True | no_bias=True`**. Uji multi-pair `V29_LR_NonCS` mencatat `biased_indicators` kosong (`0` indikator bias; hanya `1` entry & `3` exit berbeda akibat sifat portofolio `Trade.get_open_trades()` 8-koin vs 1-koin).
- **Catatan Transparansi Audit:** Eksekusi 4-agent `run_workflow` paralel (`task-18945` / `task-18954`) terhenti oleh kuota API `429 RESOURCE_EXHAUSTED` pada spawned subagents, sehingga seluruh 4 pilar verifikasi dijalankan secara deterministik lewat `final_4pillar_audit_v29_liveready.py` (exit code `0`, `0` assertion gagal, seluruh `9` file baseline `V26/V28/P1..P6/V29_Champion` terkonfirmasi utuh byte-for-byte, `0` temuan sekuriti).

---

## 4. Panduan Pemakaian & Batasan Operasional

1. **Whitelist Terkunci Core-8:** Jalankan [ApexDualAlpha_Omni_V29_LiveReady.py](../../user_data/strategies/ApexDualAlpha_Omni_V29_LiveReady.py) **hanya pada 8 koin Core-8** (`BTC, ETH, SOL, DOGE, ADA, LINK, HYPE, PAXG`). Strategi ini **tidak** dirancang untuk ekspansi altcoin baru di luar whitelist tersebut.
2. **Tahap Aktivasi Disarankan:** Mulai di **dry-run lokal** (atau modal live kecil) selama beberapa hari terlebih dahulu untuk mengonfirmasi fill-rate maker limit Bybit dan log runtime bersih sebelum meningkatkan modal penuh.
3. **Catatan Risiko Runner:** Setelah partial scale-out, floor profit runner (`+1.55%` ROE) dijaga melalui loop `custom_exit`, sedangkan stoploss darurat di orderbook Bybit tetap berada di `-29.7%` ROE (`stoploss_on_exchange = True`).
