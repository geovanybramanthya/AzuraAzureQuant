# Simulasi Monte Carlo - V26 v2 (ApexDualAlpha_Omni_V26_Optimized)

Tanggal: 2026-10-20. Komputasi dari file ekspor backtest yang sudah ada (tanpa backtest ulang).

## Sumber data dan anchor check

- File zip sumber: `v26_v2_export.zip` (JSON di dalamnya: `backtest-result-2026-10-20_12-23-12.json`, strategi `ApexDualAlpha_Omni_V26_Optimized`) - zip yang sama yang dipakai auditor final di `v26_audit_final.md`.
- Jumlah trade tertutup: 1043 (anchor 1043) - COCOK
- Total profit_abs: 134,505.64 USDT (anchor +134.505,64) - COCOK
- Menang/kalah: 771/272; win rate 73.92% (anchor 73,92%) - COCOK
- Profit factor: 1.9280 (gross profit 279,441.17 / gross loss 144,935.53; anchor 1,9280) - COCOK
- **Anchor check keseluruhan: LOLOS**

## Metode dan asumsi

- Return per trade: $r_i = profit\_abs_i / stake\_amount_i$ (bebas skala terhadap compounding backtest).
- Model ekuitas: wallet awal 1,000 USDT; setiap trade memasang fraksi $1/K$ dari ekuitas berjalan dengan $K = max\_open\_trades = 4$; ekuitas diperbarui $E \leftarrow E \times (1 + r_i/K)$. **Ini aproksimasi** terhadap perilaku stake unlimited freqtrade (di backtest riil, beberapa trade bisa tumpang tindih dan ukuran stake mengikuti saldo saat order dibuat, bukan urutan close_date); lihat bagian sanity check.
- Urutan acuan (termasuk sanity check): trade diurutkan berdasarkan close_date.
- Iterasi: 10,000 per metode. Seed acak tetap: 42 (satu aliran generator numpy default_rng(42) untuk permutasi, bootstrap, dan bootstrap bulanan; indeks permutasi yang sama dipakai ulang untuk metode D).
- Metode A (permutasi): urutan 1.043 trade diacak tanpa pengembalian - menguji sensitivitas terhadap urutan realisasi saja; himpunan trade identik dengan sejarah, jadi ekuitas akhir hanya berubah karena jalur compounding fraksi tetap.
- Metode B (bootstrap): 1.043 trade disampling dengan pengembalian - memuat urutan dan komposisi yang lebih buruk (dan lebih baik) dari sejarah, termasuk trade yang sama muncul berkali-kali atau tidak sama sekali.
- Metode C (bulanan): PnL bulanan aktual dari close_date (USDT absolut hasil compounding), lalu 32 bulan disampling dengan pengembalian. **Catatan asumsi**: bulan diperlakukan saling bebas dan dapat dipertukarkan; karena PnL absolut membesar seiring compounding, bulan-bulan akhir berbobot jauh lebih besar - distribusi ini membaca risiko pada skala absolut campuran, bukan bulan yang setara.
- Metode D (streak): dari 10.000 permutasi yang sama dengan metode A, hanya urutan menang/kalah yang dipakai (kalah = profit_abs <= 0).
- Max drawdown dihitung dari kurva ekuitas simulasi (puncak berjalan vs nilai berjalan), dalam persen.
- Keterbatasan umum: resampling hanya mengacak trade historis yang sama; tidak memuat rezim pasar baru, slippage di luar fee backtest, kegagalan eksekusi, atau korelasi antar-trade yang tumpang tindih di dunia nyata (pengacakan justru memutus klaster volatilitas historis). Angka-angka ini indikator distribusi risiko dari sampel historis, bukan jaminan batas risiko live.

## Sanity check (urutan asli, model 1/K)

- Model 1/K urutan asli: ekuitas akhir 182.61x lipat, max DD 11.79%.
- Backtest riil: ekuitas akhir 135.51x lipat (1000 menjadi 135,505.64), max DD akun 9.74%.
- Selisih: kelipatan akhir model 1.35x dari riil; DD model +2.05 poin persen dari riil. Sumber selisih utama: (1) di backtest riil stake unlimited dihitung dari saldo tersedia saat trade DIBUKA (dan trade tumpang tindih berbagi saldo yang sama), sedangkan model memasang tepat 1/4 ekuitas pada urutan close; (2) profit_abs historis sendiri sudah merupakan hasil jalur compounding riil, sehingga r_i mengandung pertumbuhan stake historis - menerapkannya pada ekuitas fraksi-tetap mencampur dua rezim ukuran posisi. Selisih dilaporkan apa adanya; metode tidak disesuaikan agar cocok.

## Hasil A & B - Max drawdown (%)

| Persentil | A: Permutasi | B: Bootstrap |
|---|---:|---:|
| p1 | 11.17 | 10.71 |
| p5 | 12.39 | 11.95 |
| p25 | 14.54 | 14.32 |
| p50 | 16.47 | 16.48 |
| p75 | 18.94 | 19.18 |
| p95 | 23.65 | 24.34 |
| p99 | 28.07 | 29.10 |
| Terburuk teramati | 36.34 | 42.76 |

## Hasil A & B - Ekuitas akhir (kelipatan dari wallet awal)

| Persentil | A: Permutasi | B: Bootstrap |
|---|---:|---:|
| p1 | 182.61x | 43.46x |
| p5 | 182.61x | 66.26x |
| p25 | 182.61x | 119.07x |
| p50 | 182.61x | 181.11x |
| p75 | 182.61x | 270.22x |
| p95 | 182.61x | 485.14x |
| p99 | 182.61x | 741.95x |
| Terburuk teramati (minimum) | 182.61x | 15.58x |

Catatan metode A: kolom ekuitas akhir A konstan di semua persentil BUKAN kesalahan hitung - pada model fraksi-tetap multiplikatif, perkalian bersifat komutatif sehingga urutan trade tidak mengubah ekuitas akhir sama sekali; yang berubah hanya jalurnya (drawdown). Seluruh informasi metode A ada di distribusi drawdown; variasi ekuitas akhir hanya muncul di metode B yang mengubah komposisi trade.

## Hasil C - Risiko bulanan (bootstrap 32 bulan)

- Bulan dengan trade tertutup: 32 (2024-02 s.d. 2026-09); bulan negatif aktual: 4; bulan terburuk aktual: -4,779.28 USDT (2026-01 menurut audit: -4.779,28); bulan terbaik aktual: 20,828.56 USDT.

| Persentil | Bulan terburuk dalam sampel 32 bulan (USDT) |
|---|---:|
| p1 | -4,779.28 |
| p5 | -4,779.28 |
| p25 | -4,779.28 |
| p50 | -4,779.28 |
| p75 | -2,271.43 |
| p95 | -57.94 |
| p99 | 84.21 |
| Terburuk teramati | -4,779.28 |

## Hasil D - Streak kalah terpanjang (permutasi)

| Persentil | Streak kalah terpanjang (trade) |
|---|---:|
| p1 | 3 |
| p5 | 4 |
| p25 | 4 |
| p50 | 5 |
| p75 | 5 |
| p95 | 7 |
| p99 | 8 |
| Terburuk teramati | 12 |
| Streak kalah terpanjang pada urutan asli (close_date) | 7 |

## Probabilitas kunci

- P(ekuitas akhir < wallet awal): A 0.00% | B 0.00%
- P(max DD > 15%): A 69.14% | B 67.06%
- P(max DD > 20%): A 17.96% | B 19.51%
- P(max DD > 30%): A 0.38% | B 0.77%
- P(minimal satu bulan negatif dalam 32 bulan): 98.56%
- P(tahun negatif, jumlah 12 bulan sampling): 0.17%

