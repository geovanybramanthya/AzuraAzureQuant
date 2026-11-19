# Laporan Optimasi & Audit Kelaikan `ApexDualAlpha_Omni_V29_Champion`

> [!WARNING]
> **Keputusan Kelayakan Audit (Jujur & Tidak Diperhalus): CONDITIONAL PASS untuk Core-8 Backtest & Temporal OOS | REJECT untuk Generalisasi Lintas-Koin (`Unseen-9`) dan Deployment Live Langsung Tanpa Patch Hardening**
>
> Pada universe **Core-8** (`BTC, ETH, SOL, DOGE, ADA, LINK, HYPE, PAXG`), [ApexDualAlpha_Omni_V29_Champion.py](../../user_data/strategies/ApexDualAlpha_Omni_V29_Champion.py) mengungguli `V28_Champion` dan `V26_Optimized` di seluruh metrik utama (Suite A `+480,629.39 USDT` vs `+319,815.27` / `+134,505.64`, `1,097` trades, `79.03%` WR, Dollar PF `3.0521`, `%PF 2.2154`, Max DD `4.60%`, `32/32` bulan hijau, `8/8` koin positif) sekaligus unggul pada **Blind Holdout** (`20261031-20261118`: `+53.02 USDT`, Dollar PF `1.4437` vs `V28` `+4.67` dan `V26` `-4.22`).
>
> Namun audit adversarial 4-agent paralel (`task-18672.log`) mengonfirmasi empat temuan kritis:
> 1. **Gagal Single-Shot Gate 1 `Unseen-9`:** Pada `9` altcoin baru, `V29` rugi `-463.72 USDT` (`5/9` hijau, Dollar PF `0.9539`, DD `82.81%`) karena tag warisan `V28` (`short_atr_fade_alpha -672.54 USDT`, `long_pullback_alpha -623.26 USDT`). Sesuai disiplin Single-Shot OOS, strategi **tidak di-retune** setelah menyentuh data OOS/Unseen-9.
> 2. **Live Execution Fail-Open & Wallet Latch Risk:** Handler pengecualian pada `confirm_trade_entry` (fail-open `return True`) dan `custom_stake_amount` (fallback ke `proposed_stake`), ketidakseragaman `min_stake` antara `custom_exit` dan `adjust_trade_position`, serta timpaan `_last_known_wallet` tanpa penjagaan di `_eval_scaleout_plan` berisiko pada akun live.
> 3. **Gate 8 CLI `lookahead-analysis` Parity:** `__init__` baris 135 memaksa `entry_pricing.price_side = "same"` sehingga perintah CLI standar Freqtrade `lookahead-analysis` (yang memakai `market` entry) gagal `Configuration error` (`lookahead_analysis.txt`). Wrapper non-CS 1-koin (`ETH/USDT:USDT`) lulus bersih `has_bias = No` (`0` bias), dan uji pemotongan MockDP multi-koin mencatat selisih indikator `0.0`, namun wrapper 8-koin penuh tetap memicu perbedaan kolom `cs_*` akibat isolasi 1-pair per iterasi pada mesin internal Freqtrade serta `cs_er12_mean`/`cs_bear_breadth` yang belum dimatikan oleh `ABLATE_CROSS_SECTIONAL`.
> 4. **Audit AST Strict `<= 6` Term Terlewat pada Ekspresi Bersarang:** Script audit internal (`strict_gate6_audit.py`) melaporkan `max_conds = 6` karena tidak masuk ke dalam ekspresi `IfExp` (`... if cond else ...`), operator unary `~(...)`, dan assignment subscript `dataframe[...] = ...`. Pengecekan AST rekursif oleh `code-reviewer` menemukan ekspresi gabungan hingga `20-22` kondisi di `sf_curl_u` dan `sf_zcurl_u`.

---

## 1. Status Akhir & Peta Artefak

| Komponen | Lokasi File | Hash / Status | Keterangan |
|---|---|---|---|
| **Champion V29 (Locked)** | [ApexDualAlpha_Omni_V29_Champion.py](../../user_data/strategies/ApexDualAlpha_Omni_V29_Champion.py) | SHA-256 `618ec24cd7b582ff` (`0` BOM, `2,860` baris) | Identik dengan `V29_M5_CleanLocked.py`; dikunci pasca Gate 1 tanpa tuning ulang. |
| **Baseline V26** | [ApexDualAlpha_Omni_V26_Optimized.py](../../user_data/strategies/ApexDualAlpha_Omni_V26_Optimized.py) | SHA-256 `1f62cbeb3513ca51` (utuh) | Lebih lemah di Core-8 & Blind Holdout, namun paling stabil di `Unseen-9` (`8/9`, `+48,220.50 USDT`). |
| **Baseline V28** | [ApexDualAlpha_Omni_V28_Champion.py](../../user_data/strategies/ApexDualAlpha_Omni_V28_Champion.py) | SHA-256 `d07e80b798f5bcce` (utuh) | Dikalahkan telak oleh `V29` di Core-8, Fee 10 bps, BTC, dan Blind Holdout; juga gagal di `Unseen-9` (`4/9`). |
| **Baseline V29 P1-P6** | `ApexDualAlpha_Omni_V29_P1..P6` (`user_data/strategies/`) | `6/6` SHA-256 cocok (`match: true`, utuh) | Paradigma komponen dasar pembentuk `V29_Champion`. |
| **Laporan M5 In-Sample** | `m5_insample_gate_report.json` | `exit code 0` | Mengandung hasil lengkap Suite A/B/C/D/Micro, Gate 2-8, `wick_8bps`, `DSR(300)`. |
| **Laporan Sertifikasi M6** | `m6_certification_report.json` | Catatan: `lookahead_analysis.has_bias_false` usang | Lihat Bagian 4 untuk koreksi jujur status CLI `lookahead-analysis` (detector pada `run_v29_m6_cert.py` sudah diperbaiki setelah JSON ini ditulis). |
| **Laporan Baseline Gate 1** | `task-18607.log` | Verifikasi langsung | Perbandingan `V26` vs `V28` vs `V29` pada Blind Holdout dan `Unseen-9` per tag & per koin. |

---

## 2. Perbaikan Masalah Struktural "Out-of-the-Box" (Sebelum Lock M5)

Sesuai arahan untuk mencari akar masalah di luar parameter indikator biasa, fase optimasi dan pembersihan M5 membongkar dan memperbaiki tiga hambatan nyata:

1. **Bug "PAXG Contract-Cap Wallet Compression" & Dust Trades (`Gate 7`)**
   - *Akar masalah:* Pada compounding besar (`> 350,000 USDT`), batas kontrak Bybit `PAXG/USDT:USDT` (`11.48` PAXG) membatasi `max_stake` dari Freqtrade di kisaran `16.3k - 17.5k USDT`. Rumus lama memakai `tied_up + max_stake` untuk mengestimasi saldo portfolio dan langsung menimpa `self._last_known_wallet`. Akibatnya, `PAXG` masuk dengan ukuran `< 5%` ekuitas (`5` dust trades) sekaligus merusak estimasi dompet trade berikutnya.
   - *Perbaikan ([ApexDualAlpha_Omni_V29_Champion.py](../../user_data/strategies/ApexDualAlpha_Omni_V29_Champion.py#L909-L917), [L597-601](../../user_data/strategies/ApexDualAlpha_Omni_V29_Champion.py#L597-L601)):* Menambah penjaga penurunan mendadak `> 25%` (`new_w < 0.75 * prev_w`) pada `custom_stake_amount`, menaikkan lantai instansi (`min_inst_stake >= 0.054 * true_total_tradable`), serta memblokir order `< 5.6%` dari `_last_known_wallet` di `confirm_trade_entry`. (Catatan temuan auditor: mekanisme `_eval_scaleout_plan` baris 1322-1325 masih perlu disamakan pada patch live berikutnya; lihat Bagian 5).
2. **Post-Scaleout Gap-Loss pada `short_bb_fade` di Rezim Choppy (`SOL 2025-01-15`)**
   - *Akar masalah:* Pada kondisi choppy oversold (`slowk < 21.5`, `er_24 < 0.025`, `cs_bear_breadth < 0.05`), `short_bb_fade` mengambil partial scale-out `62%` saat profit `+4.44%`, tepat di bawah ambang penuh `+4.50%`, lalu runner terkena gap memantul hingga total trade rugi `-35.02 USDT`.
   - *Perbaikan ([L1307-1311](../../user_data/strategies/ApexDualAlpha_Omni_V29_Champion.py#L1307-L1311), [L1458-1461](../../user_data/strategies/ApexDualAlpha_Omni_V29_Champion.py#L1458-L1461)):* Menekan partial scale-out dalam kondisi chop dan langsung mengeksekusi full exit `short_fade_chop_oversold_tp` pada `current_profit >= 0.040`. Hasil simulasi urutan live (`gate7_live_order_audit`): `dur0_count = 0`, `roundtrip_losses_after_scaleout = 0`, `dust_violations_lt_5pct = 0`, `order_notional_violations = 0`.
3. **Inversi Parameter Stabilitas `KNOB_GLOBAL_THRESH_SCALE` (`Gate 5`)**
   - *Akar masalah:* Uji sensitivitas `0.80 / 0.90 / 1.10 / 1.20` sebelumnya menjatuhkan `%PF` sebesar `-31.9%` karena skala global membalik arah threshold RSI/SlowK (`< 50`), mempersempit jendela `.between()` dari dua sisi, dan menggeser batas atas `adx_4h` secara berlawanan.
   - *Perbaikan:* Menyelaraskan skalabilitas pada rasio `adx_4h_s = adx_4h_adx / g_thr` dan `fr_zscore` (`fz168`, `fz336`), menghasilkan degradasi jumlah trade yang monoton ketat (`1,192 -> 1,137 -> 1,064 -> 988 -> 914`) dengan penurunan `%PF` terjaga di bawah `15%` (terendah `-14.85%` pada `0.80x`).

---

## 3. Benchmark Head-to-Head: `V26_Optimized` vs `V28_Champion` vs `V29_Champion`

### A. Skenario Core-8 & Biaya Eksekusi Tinggi

| Skenario Uji | Strategi | Trades | Winrate | Dollar PF | `%PF` | Net PnL (USDT) | Max DD | Bulan Hijau | Koin (+) | HYPE Share |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| **Suite A Comp** (`20240101-20260925`) | `V26_Optimized` | `1,043` | `73.92%` | `1.9280` | `2.0471` | `+134,505.64` | `9.74%` | `28/32` | `8/8` | `26.46%` |
| **Suite A Comp** (`20240101-20260925`) | `V28_Champion` | `1,076` | `77.51%` | `2.5289` | `2.1357` | `+319,815.27` | `5.29%` | `31/32` | `7/8` (BTC `-1,737.01`) | `29.61%` |
| **Suite A Comp** (`20240101-20260925`) | **`V29_Champion`** | **`1,097`** | **`79.03%`** | **`3.0521`** | **`2.2154`** | **`+480,629.39`** | **`4.60%`** | **`32/32`** | **`8/8` (BTC `+7,852.31`)** | **`20.07%`** |
| **Suite A Fee 10 bps** | `V28_Champion` | `1,070` | `75.89%` | `2.2162` | `1.9116` | `+157,963.83` | `8.29%` | `31/32` | `6/8` | `32.66%` |
| **Suite A Fee 10 bps** | **`V29_Champion`** | **`1,096`** | **`77.37%`** | **`2.5337`** | **`1.9836`** | **`+211,589.71`** | **`4.71%`** | **`30/32`** | **`8/8`** | **`20.20%`** |
| **Suite C Fixed 250 USDT** | `V26_Optimized` | `1,043` | `73.92%` | `2.0545` | `2.0471` | `+5,355.06` | `5.47%` | `28/32` | `8/8` | `11.94%` |
| **Suite C Fixed 250 USDT** | `V28_Champion` | `1,078` | `77.09%` | `2.4868` | `2.1226` | `+5,739.71` | `3.03%` | `31/32` | `8/8` | `23.84%` |
| **Suite C Fixed 250 USDT** | **`V29_Champion`** | **`1,064`** | **`78.38%`** | **`2.2652`** | **`2.1498`** | **`+7,014.77`** | `5.26%` | **`32/32`** | **`8/8`** | **`18.17%`** |
| **Suite C Fee 12 bps** | **`V29_Champion`** | `1,051` | `76.50%` | `1.9381` | `1.8634` | `+5,766.90` | `6.38%` | `30/32` | `8/8` | `19.53%` |
| **Suite C Ex-HYPE** (`V28` target `+4,537.51`) | **`V29_Champion`** | `974` | `77.52%` | `2.1666` | `2.0537` | **`+5,990.80`** | `5.88%` | `31/32` | `7/7` | `0.00%` |
| **Suite B Extended** (`20240101-20261031`) | **`V29_Champion`** | `1,135` | `78.68%` | `2.5341` | `2.1951` | `+531,623.86` | `6.59%` | `33/33` | `8/8` | `8.07%` |
| **Suite D Akun 203.38 USDT** | **`V29_Champion`** | `1,098` | `78.96%` | `3.1133` | `2.1989` | `+95,851.06` | `4.60%` | `32/32` | `8/8` | `19.21%` |
| **Micro Akun 100 USDT** | **`V29_Champion`** | `1,096` | `78.92%` | `3.1142` | `2.2143` | `+45,913.15` | `4.60%` | `32/32` | `8/8` | `19.22%` |

Distribusi 8 pair pada `V29` Suite A (`+480,629.39 USDT`): `ADA +116,815.57`, `HYPE +96,479.47`, `SOL +81,555.74`, `ETH +74,237.47`, `DOGE +53,700.09`, `LINK +49,332.18`, `BTC +7,852.31`, `PAXG +656.56`. Seluruh `8` entry tag aktif dengan `N >= 40` baik di Suite A maupun Suite C (`min_tag_count = 40`, berbeda jauh dari `V28` yang memiliki tag dengan `N = 8`).

### B. Out-of-Sample Temporal & Generalisasi Lintas-Koin (`Single-Shot Gate 1`)

| Uji Gate 1 & Temporal OOS | Strategi | Trades | Winrate | Dollar PF | `%PF` | Net PnL (USDT) | Max DD | Koin (+) | Catatan Penting |
|---|---|---:|---:|---:|---:|---:|---:|---:|---|
| **Oct-OOS** (`20260925-20261031`) | `V29_Champion` | `40` | `67.50%` | `1.6856` | `1.6698` | `+161.82` | `6.43%` | `4/8` | `2/2` bulan hijau; `HYPE` rugi `-121.72 USDT`. |
| **Blind Holdout** (`20261031-20261118`) | `V26_Optimized` | `14` | `64.29%` | `0.9571` | `0.9681` | `-4.22` | `5.09%` | `4/6` | Negatif pada regime terbaru. |
| **Blind Holdout** (`20261031-20261118`) | `V28_Champion` | `13` | `61.54%` | `1.0370` | `1.2388` | `+4.67` | `6.85%` | `5/7` | Nyaris impas. |
| **Blind Holdout** (`20261031-20261118`) | **`V29_Champion`** | **`17`** | **`64.71%`** | **`1.4437`** | **`1.3388`** | **`+53.02`** | `8.08%` | `4/6` | Semua `17` sinyal berupa `SHORT`; funding `+0.47303 USDT`; `LINK +39.02, ADA +29.16, SOL -37.18`. |
| **Unseen-9 Comp** (`APT, ARB, AVAX, BNB, INJ, NEAR, OP, SUI, XRP`) | `V26_Optimized` | `1,437` | `66.60%` | `1.3004` | `1.4293` | **`+48,220.50`** | `29.71%` | **`8/9`** | **LULUS** generalisasi lintas-koin (`>= 6/9`, PF `>= 1.15`). |
| **Unseen-9 Comp** (`9 altcoin`) | `V28_Champion` | `1,244` | `60.13%` | `0.9232` | `0.9272` | `-258.37` | `34.32%` | `4/9` | **GAGAL**; penyakit overfit Core-8 sudah berasal dari era `V28`. |
| **Unseen-9 Comp** (`9 altcoin`) | `V29_Champion` | `1,336` | `68.34%` | `0.9539` | `0.9648` | `-463.72` | `82.81%` | `5/9` | **GAGAL**: `APT +331.32, XRP +226.65, AVAX +209.06, ARB +160.05, NEAR +11.83` vs `SUI -589.21, OP -453.52, INJ -346.51, BNB -13.39`. |
| **Unseen-9 Fixed 250 USDT** | `V28_Champion` | `1,143` | `60.72%` | `0.9363` | `0.9466` | `-743.52` | `83.26%` | `5/9` | **GAGAL**. |
| **Unseen-9 Fixed 250 USDT** | `V29_Champion` | `792` | `68.18%` | `0.9218` | `0.9041` | `-738.68` | `83.76%` | `4/9` | **GAGAL**; berhenti membuka trade setelah `2025-09-29`. |

**Diagnosis Forensik Kegagalan `Unseen-9` (`task-18607.log`):**
Seluruh modul alfa baru yang dikembangkan pada riset `V29` justru **terbukti untung secara cross-universe** di `Unseen-9` compounding:
- `range_sweep_alpha`: `62 tr, 80.6% WR, +381.91 USDT`
- `long_bb_pullback`: `150 tr, 78.0% WR, +181.45 USDT`
- `short_crowded_fade_alpha`: `59 tr, 79.7% WR, +157.25 USDT`
- `long_breakout_expansion`: `164 tr, 64.6% WR, +112.24 USDT`
- `short_bb_fade`: `97 tr, 74.2% WR, +30.22 USDT`

Sebaliknya, dua mesin warisan `V28` yang masih memakai kalibrasi ketat Core-8 menarik portofolio `Unseen-9` menjadi rugi:
- `short_atr_fade_alpha`: `283 tr, 69.6% WR, -672.54 USDT`
- `long_pullback_alpha`: `351 tr, 64.7% WR, -623.26 USDT`
- `short_breakdown_alpha`: `170 tr, 57.1% WR, -30.99 USDT`

---

## 4. Rangkuman Verifikasi Gate 2 sampai Gate 8 & Uji Ketahanan Statistik

1. **Gate 2 (Stres Biaya & Waktu Panjang):**
   - `suite_a_fee10` `+211,589.71 USDT` (`> V28 +157,963.83 USDT`); `suite_c_fee12` `%PF = 1.8634` (`>= 1.35`).
   - `suite_b` (`33` bulan) `+531,623.86 USDT`, `33/33` bulan hijau, DD `6.59%`.
2. **Gate 3 (Eksekusi Konservatif):**
   - **8 bps Wick Penetration Haircut:** Suite A `878/1,097` (`80.04%`) tembus limit, `epf = 2.0785`, setelah haircut `8 bps * leverage` menjadi `%PF 1.9399` (`>= 1.50`); Suite C `850/1,064` (`79.89%`), `2.0409 / 1.9037`.
   - **+1 Bar Entry Delay (Suite C):** `1,045 tr, 74.35% WR, Dollar PF 1.4145, %PF 1.4541` (`>= 1.15`), `+3,177.82 USDT`, DD `8.87%`, `7/8` koin positif (`PAXG -4.51 USDT`).
3. **Gate 4 (Konsistensi Regime Tahunan & Bebas HYPE):**
   - `2024`: `389 tr, 77.38% WR, %PF 2.0597, +2,762.43 USDT, DD 4.26%, 11/11 mo`
   - `2025`: `422 tr, 76.54% WR, %PF 1.9009, +2,252.66 USDT, DD 14.91%, 12/12 mo`
   - `2026 (Jan-Sep)`: `253 tr, 83.00% WR, %PF 3.1561, +1,994.56 USDT, DD 6.25%, 9/9 mo` (peringatan: pada tahun `2026` saja, porsi `HYPE` mencapai `44.82%` meskipun akumulasi 32 bulan tetap `20.07%`).
   - `suite_c_ex_hype`: `+5,990.80 USDT` (`%PF 2.0537`, melampaui `V28 +4,537.51 USDT`).
4. **Gate 5 (Sensitivitas Parameter & LOO Ablation):**
   - Skala ambang global (`0.80 / 0.90 / 1.00 / 1.10 / 1.20`): trades `1,192 -> 1,137 -> 1,064 -> 988 -> 914` dengan perubahan `%PF` `-14.85% / -9.93% / 0% / +1.54% / -2.51%`. Semua titik di bawah batas penurunan `15%`, meski titik `0.80x` hanya menyisakan margin `0.15` poin persentase.
   - Seluruh `8` knob `+-15%` menghasilkan PnL `+5,877.13 USDT` s.d. `+7,474.21 USDT` (`%PF 1.8718 - 2.1587`, `8/8` pair). Peringatan uji: `KNOB_SLOT01_BOOST` pada Suite C (`--fixed-stake 250`) bersifat vacuous (`0.00` delta) karena hanya beroperasi di mode compounding.
   - Seluruh `5` ablasi LOO mengurangi PnL (`ZSCORE 7,014 -> 6,078.93`, `CS 7,014 -> 6,451.98`, `RANGE_SWEEP 7,014 -> 6,716.60`, `INTRINSIC_MICRO 7,014 -> 6,912.78`, `LIFECYCLE 7,014 -> 7,002.25`), meskipun `ABLATE_CROSS_SECTIONAL` masih menyisakan `cs_er12_mean` dan `cs_bear_breadth` tetap aktif.
5. **Statistik Lanjut (`DSR`, `Monte Carlo`, `Wallet Invariance`):**
   - `DSR(n=300) = 1.0 > 0.95` di Suite A (`SR_daily 0.298824`, `SR_ann 5.709`, `skew 0.6919`, `kurt 7.1902`) dan Suite C (`SR_daily 0.248589`, `SR_ann 4.7493`, `skew 1.6759`, `kurt 17.4235`).
   - `10,000` iterasi Monte Carlo Suite A (model proxy compounding `0.22x`): median `%PF 2.2173` (`p05 1.9261`, `p95 2.5619`), DD median `13.93%` (`p95 20.61%`, `p99 24.32%`), probabilitas positif `100.0%`.
   - Invariansi wallet Suite C (`1,000 USDT` vs `500,000 USDT`): selisih absolut `0.0` (`1,064 tr, +7,014.77 USDT, %PF 2.1498`).
6. **Gate 8 (Causal Lookahead & Warmup Parity):**
   - **MockDP Multi-Pair Truncation & 840-Bar Warmup:** `0` perbedaan sinyal dan `0.0` selisih nilai indikator di `3` titik potong (`2025-06-30`, `2026-03-15`, `2026-09-25`), serta `308/308` (`100.0%`) paritas warmup `840` candle `1h` + `840` candle `4h`.
   - **Freqtrade `lookahead-analysis` CLI:**
     - Pada `V29_Champion` langsung: crash `ERROR - Configuration error: Market entry orders require entry_pricing.price_side = "other".` karena `__init__` [L135](../../user_data/strategies/ApexDualAlpha_Omni_V29_Champion.py#L135) menimpa `price_side = "same"` meski helper lookahead memaksa order `market`.
     - Pada wrapper multi-koin (`V29_LA_Check`, `lookahead_analysis_real.txt`) maupun wrapper `V29_LA_NonCS` multi-koin (`lookahead_analysis_noncs.txt`): Freqtrade memanggil `prepare_data(..., [pair])` per 1 koin pada setiap trade yang diisolasi sehingga tensor cross-sectional (`< 3` peers) menjadi netral dan `cs_er12_mean`/`cs_bear_breadth` masih terbaca (`has_bias = Yes`, `1` entry & `3` exit).
     - Pada uji 1-koin `ETH/USDT:USDT` dengan `V29_LA_NonCS` (`task-18663.log`): `has_bias = No`, `20/20` sinyal bersih (`0` entry bias, `0` exit bias, `0` indikator bias).

---

## 5. Hasil Audit Independen 4-Agent `run_workflow` (`task-18672`)

Eksekusi audit paralel melalui `run_workflow` menghasilkan keputusan **`OVERALL WORKFLOW VERDICT: REJECT`** dengan perincian:

| Auditor Agent | Verdict | Temuan Kunci Terverifikasi |
|---|---|---|
| **`security-reviewer`** | **`ACCEPT`** | File `ApexDualAlpha_Omni_V29_Champion.py` (`178,819` bytes, `0` BOM, `py_compile` bersih) cocok dengan SHA-256 `618ec24cd7b582ff`. Seluruh `8` baseline utuh `100%`. Nihil kredensial/URL/IP/HTTP/SQLite/VPS di kode. Funding reader backtest terisolasi ke `.feather` lokal dengan merge causal `+1h` backward. |
| **`code-reviewer`** | **`REJECT`** | (1) Pelaporan `max_conds = 6` di laporan AST tidak mencerminkan sub-ekspresi di dalam `IfExp`, unary `~(...)`, dan subscript `dataframe[...] = ...` (`sf_zcurl_u` 22 term, `sf_curl_u` 20 term, `scf_zscore` 14 term, `lbb_sawtooth` 10-11 term, `sbb_bench` 9 term, `kauf_alt_ok` 7 term, `l_sup_bb_sweep_regime` 7 term, `l_negfr_reaccum_regime` 7 term, `squeeze_followthrough_down_3` 7 term). (2) `_eval_scaleout_plan` [L1322-1325](../../user_data/strategies/ApexDualAlpha_Omni_V29_Champion.py#L1322-L1325) menimpa `_last_known_wallet` tanpa penjaga `0.75 * prev_w`; sebaliknya pada akun berawal `< 750 USDT` tanpa `dry_run_wallet`, default peak latch `1000.0` dapat memblokir entry valid. |
| **`silent-failure-hunter`** | **`REJECT`** | (1) `confirm_trade_entry` ([L610](../../user_data/strategies/ApexDualAlpha_Omni_V29_Champion.py#L610) / [L701-704](../../user_data/strategies/ApexDualAlpha_Omni_V29_Champion.py#L701-L704), `return True`) dan `custom_stake_amount` ([L876](../../user_data/strategies/ApexDualAlpha_Omni_V29_Champion.py#L876) / [L1037-1039](../../user_data/strategies/ApexDualAlpha_Omni_V29_Champion.py#L1037-L1039), `stake = proposed_stake`) bersifat fail-open saat terjadi exception. (2) Pada pengujian skenario akun besar (`> 450k-520k USDT`) oleh auditor, limit kontrak Bybit `PAXG`/`LINK` menyebabkan estimasi `tied_up + max_stake` jatuh di bawah `0.056 * _last_known_wallet` sehingga `PAXG`/`LINK` dapat tertolak. (3) Perbedaan `min_stake=None` di `custom_exit` ([L1471](../../user_data/strategies/ApexDualAlpha_Omni_V29_Champion.py#L1471)) vs `min_stake` exchange pada `adjust_trade_position` ([L1237](../../user_data/strategies/ApexDualAlpha_Omni_V29_Champion.py#L1237)) berpotensi menjatuhkan exit penuh sekaligus gagal partial pada akun mikro. (4) `L992-993` memakai `max(min_inst_stake, 0.43 * cap_knob * ...)` alih-alih `0.0` saat short heat penuh. (5) Fallback funding `_merge_causal_funding` belum memvalidasi staleness `derivatives_state.json`. |
| **`quant-analyst`** | **`REJECT`** | Mengonfirmasi dominasi penuh `V29` di Suite A/B/C/D/Micro, Gate 2-4, `DSR=1.0`, dan `10k MC`, namun menolak persetujuan penuh akibat kegagalan total `Gate 1 Unseen-9` (`5/9` comp, `-463.72 USDT`, DD `82.81%`), perbedaan `Gate 7` (`+556,756 USDT`) vs backtest standar (`+480,629 USDT`), margin tipis `gate5_m20` (`-14.85%`), ketergantungan `HYPE` `44.82%` di slice 2026, serta isu `lookahead-analysis` CLI. |

---

## 6. Rekomendasi Teknis & Opsi Langkah Lanjut

`ApexDualAlpha_Omni_V29_Champion.py` tetap dipertahankan sebagai snapshot kandidat **V29** pasca-Gate 1 yang jujur dan tidak disentuh ulang (untuk mencegah kebocoran OOS/post-hoc tuning). Rekomendasi operasional:

1. **Lingkup Operasional Terbatas (Saat Ini):** Strategi ini hanya terbukti unggul kuat di **Core-8** (`BTC, ETH, SOL, DOGE, ADA, LINK, HYPE, PAXG`), dan **tidak layak** dipasang ke pairlist altcoin acak atau universe baru sebelum siklus `V30`.
2. **Syarat Wajib Sebelum Mengaktifkan Live/Dry-Run Core-8 (Live Hardening Patch tanpa Mengubah Alpha):**
   - Ubah `except Exception` di `confirm_trade_entry` (`return False` / fail-closed) dan `custom_stake_amount` (`return 0.0` + `logger.error`).
   - Hindari menyimpulkan saldo portofolio hanya dari `tied_up + max_stake` yang terpotong cap kontrak (atau tinjau ulang pemakaian ekuitas dompet langsung beserta aturan audit internalnya), serta samakan logika `_last_known_wallet` di `_eval_scaleout_plan` dengan `custom_stake_amount`.
   - Jaga `ep["price_side"] = "same"` hanya jika `order_types.get("entry") != "market"` di `__init__`.
   - Samakan perhitungan `min_stake` di `custom_exit` sebelum menunda full exit ke `adjust_trade_position`, serta perbaiki [L992-993](../../user_data/strategies/ApexDualAlpha_Omni_V29_Champion.py#L992-L993) `max(min_inst_stake, ...)` agar `eff_budget` tidak memaksa masuk saat short heat penuh.
   - Tambahkan validasi usia timestamp (TTL) pada `derivatives_state.json` di mode live.
3. **Arah Riset `V30` (Jika Ingin Lolos Lintas-Koin `Unseen-9` Secara Bersih):**
   - Pertahankan 3 alfa `V29` yang sudah terbukti tangguh lintas-koin (`range_sweep_alpha`, `long_bb_pullback`, `short_crowded_fade_alpha`), lalu sederhanakan aturan `short_atr_fade_alpha` dan `long_pullback_alpha` dari era `V28` serta pecah seluruh blok kondisi `IfExp`/`UnaryOp`/`Subscript` hingga lolos AST rekursif `<= 6` term dan 100% kompatibel dengan `freqtrade lookahead-analysis` CLI sebelum menjalankan Gate 1 baru pada holdout/koin baru yang bersih.
