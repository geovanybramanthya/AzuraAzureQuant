# PRODUCT REQUIREMENTS DOCUMENT (PRD) & BLUEPRINT ARSITEKTUR OPTIMASI KUANTITATIF
## Sistem Perdagangan Algoritmik Apex Dual-Alpha pada Bybit Linear Perpetual Futures
### Kode Dokumen: PRD-QUANT-V25-ADAPTIVE-FLOW
**Versi Target:** `ApexDualAlpha_Omni_V25_AdaptiveFlow.py`
**Tanggal Formulasi:** 15 Oktober 2026
**Status:** DRAFT ARSITEKTUR & PENGUJIAN KOMPARATIF

---

## 1. RINGKASAN EKSEKUTIF & DIAGNOSIS ROOT CAUSE V24

### 1.1 Latar Belakang Masalah
Berdasarkan hasil audit forensik menyeluruh terhadap data perdagangan riil VPS Bybit Linear Futures (`tradesv3.live.sqlite`) dan pengujian lilin historis 672 jam pada periode 11 hingga 15 Oktober 2026:
1. **Dormansi Sinyal Ekstrem**: Bot tidak mengeksekusi satu pun transaksi selama $\approx 83\text{ jam}$ berturut-turut pada seluruh 8 pasang aset (`BTC`, `ETH`, `SOL`, `ADA`, `DOGE`, `LINK`, `PAXG`, `HYPE`).
2. **Kelumpuhan di Fase Konsolidasi Pasar (*Chop Paralysis*)**: Pasar pasca koreksi 7 Oktober mengalami kompresi volatilitas ketat ($3.42\%$ rentang pergerakan BTC), namun sistem tidak memiliki mekanisme monetisasi konsolidasi dasar atau kelanjutan tren *bearish*.
3. **Temuan Bug Sistemik (*Global Blackout Veto*)**: Sebuah artikel berita hukum eksternal pada Bitcoin memicu status `blackout_active: True` di `news_sentiment_daemon.py`, yang akibat cacat agregasi makro secara salah memblokir seluruh entri order untuk semua koin portofolio.
4. **Paper Trading Non-Aktif**: Kontainer `freqtrade-paper` terkunci dalam profil Docker pasif (`profiles: ["paper"]`), menyebabkan data pengujian simulasi terhenti sejak 28 September 2026.

### 1.2 Target Kuantitatif Model V25 (Acceptance Criteria)

| Metrik Kinerja Portofolio | Status Baseline V24 (2.5Y) | Target Optimasi V25 | Toleransi Risiko Minimum |
| :--- | :---: | :---: | :---: |
| **Total Transaksi (2.5Y)** | $711\text{ trades}$ ($\approx 23.7\text{ trades/bulan}$) | **$850 - 1,100\text{ trades}$** ($\approx 28 - 36\text{ trades/bulan}$) | $\ge 800\text{ trades}$ |
| **Tingkat Kemenangan (*Win Rate*)** | $74.68\%$ | **$\ge 72.0\%$** | $\ge 70.0\%$ |
| **Faktor Keuntungan (*Profit Factor*)** | $2.15$ | **$\ge 2.05$** | $\ge 1.90$ |
| **Laba Bersih Kumulatif** | $+\$17,260.75\text{ USDT}$ | **$\ge +\$18,500.00\text{ USDT}$** | $\ge +\$16,500.00\text{ USDT}$ |
| **Drawdown Akun Maksimum (*Max DD*)** | $8.87\%$ | **$\le 9.50\%$** | $\le 10.50\%$ |
| **Jeda Transaksi Maksimum (*Max Dormancy*)** | $14.0\text{ hari}$ ($336\text{ jam}$) | **$\le 8.0\text{ hari}$ ($192\text{ jam}$)** | $\le 9.0\text{ hari}$ |
| **Porsi Transaksi Short** | $9.8\%$ ($70\text{ trades}$) | **$18\% - 25\%$ ($150 - 250\text{ trades}$)** | $\ge 15\%$ |

---

## 2. PILAR ARSITEKTUR OPTIMASI MODEL V25

```
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                   LIMA PILAR REKAYASA MODEL APEX QUANTUM V25 ADAPTIVE                  │
└────────────────────────────────────────────────────────────────────────────────────────┘
                                           │
  ┌───────────────────┬────────────────────┼────────────────────┬────────────────────┐
  │                   │                    │                    │                    │
  ▼                   ▼                    ▼                    ▼                    ▼
PILAR 1:            PILAR 2:             PILAR 3:             PILAR 4:             PILAR 5:
LOKALISASI          STOCHASTIC REGIME    SUB-ENGINE 4B:       CHOP-ADAPTIVE        DOCKER PAPER
SENTIMEN NEWS       WINDOW (3-BAR)       BEAR CONTINUATION    VOLATILITY GATING    DUAL-RUNTIME
Isolasi blackout    Ganti crossed_above  Short breakdown      Turunkan ADX 4H      Nyalakan service
khusus aset target; 1-bar dengan zona    tanpa syarat RSI>53  ke 16-18 saat        paper trading
eliminasi veto      momentum memantul    terkonfirmasi        Carter Squeeze       dengan database
global portofolio.  SlowK < 42 naik.     taker sell volume.   aktif berkepanjangan. terpisah di VPS.
```

---

### 2.1 Pilar 1: Perbaikan Bug Global Blackout Veto & Lokalisasi Sentimen
* **Akar Masalah**: Baris 682 pada `news_sentiment_daemon.py` mengaktifkan `macro_blackout_active = True` hanya karena satu berita spesifik Bitcoin memicu kata kunci hukum (*sued*), yang kemudian mematikan seluruh entri di fungsi `confirm_trade_entry()` strategi.
* **Solusi V25**:
  1. `macro_blackout_active` dihitung secara ketat hanya jika skor rata-rata makro tertimbang jatuh di bawah $-0.45$ ($\text{macro\_score} \le -0.45$) atau rasio berita *bearish* melebihi $3\times$ lipat berita *bullish* pada sampel $\ge 15$ artikel.
  2. Status *blackout* berita pada Bitcoin diisolasi khusus untuk pasangan `BTC/USDT:USDT`. Pasangan altcoin lain (`ETH`, `SOL`, `ADA`, `DOGE`, `LINK`, `PAXG`, `HYPE`) tidak boleh dibatalkan secara membabi-buta hanya karena kabar individual pada BTC.
  3. Pada fungsi `confirm_trade_entry()` di strategi, dilakukan pemeriksaan berjenjang:
     - Jika order adalah Long/Short untuk `pair`: periksa apakah *blackout* aktif secara khusus pada `pair` tersebut.
     - Hanya batalkan secara global jika `sent.get("macro_crash_alert") == True` atau `macro_score <= -0.45`.

---

### 2.2 Pilar 2: Transformasi Stochastic 1-Bar Crossing ke Jendela Rezim (3-Bar Momentum Window)
* **Akar Masalah**: Pada Alpha 1 (Long Pullback), fungsi `qtpylib.crossed_above(dataframe["slowk"], dataframe["slowd"]) & (dataframe["slowk"] < 54.0)` hanya bernilai `True` tepat pada 1 lilin ($7.6\%$ frekuensi). Jika pada jam tersebut volume sedikit di bawah rata-rata ($0.69\times$ vs $0.70\times$), sinyal mati selamanya walaupun pembalikan harga berlanjut kuat.
* **Solusi V25**:
  Ganti syarat persilangan titik tunggal dengan jendela konfirmasi momentum memantul (*momentum inflection window*):
  $$\mathcal{S}_{\text{regime}} \equiv (\text{crossed\_above}(SlowK, SlowD) \lor (\text{SlowK} > \text{SlowD} \land \text{SlowK} > \text{SlowK}_{t-1})) \land (\text{SlowK} < 45.0)$$
  Kondisi ini memberikan jendela fleksibilitas 2 hingga 3 lilin selama Stochastic berada di zona bawah dan mempertahankan lintasan naik yang sehat.

---

### 2.3 Pilar 3: Pembangunan Sub-Engine 4B (Bearish Trend Continuation Short Alpha)
* **Akar Masalah**: Mesin Short V24 hanya mengandalkan *Resistance Exhaustion Fade* yang menuntut reli jenuh beli ($\text{RSI} \ge 53 - 56$). Pada pasar yang sedang jatuh bebas (*bear trend dump*), harga tidak pernah memantul cukup tinggi sehingga mesin Short lumpuh total.
* **Solusi V25**:
  Membangun mesin kedua untuk sisi Short: `short_breakdown_alpha`.
  Formula matematis entri:
  $$\text{short\_breakdown} = \mathcal{M}_{\text{bear}} \land (\text{Close} < \text{EMA}(21) \times 0.985) \land (\text{Close} < \text{EMA}(50)) \land (\text{RSI} \in [30.0, 48.0]) \land (\text{SlowK} < \text{SlowD}) \land (\text{Volume} > \overline{V}_{20} \times 1.15)$$
  dengan:
  $$\mathcal{M}_{\text{bear}} \equiv (\text{Close}_{4\text{h}} < \text{EMA}(50)_{4\text{h}}) \land (\text{EMA}(50)_{4\text{h}} < \text{EMA}(200)_{4\text{h}})$$
  Mesin ini menangkap momentum penembusan lantai *support* pada tren turun makro tanpa menunggu harga memantul ke area jenuh beli, dilengkapi proteksi *take profit* cepat $+3.5\%$ dan *stop loss* ketat $-1.8\%$ untuk mencegah *short squeeze trap*.

---

### 2.4 Pilar 4: Sensitivitas Kompresi Pasar & Follow-Through Squeeze (3-Bar Expansion)
* **Akar Masalah**: Carter TTM Squeeze pada V24 langsung kedaluwarsa setelah 1 lilin pertama pelepasan kompresi (`squeeze_fired`), dan ambang $\text{ADX}_{4\text{h}} > 20$ mematikan entri pada pasar berkonsolidasi panjang.
* **Solusi V25**:
  1. Perluas jendela *squeeze follow-through* hingga 3 lilin pasca lepasnya pita kompresi:
     $$\text{squeeze\_active\_3} \equiv \text{squeeze\_fired} \lor (\text{squeeze\_fired}_{t-1} \land \text{Close} > \text{Close}_{t-1}) \lor (\text{squeeze\_fired}_{t-2} \land \text{Close} > \text{Close}_{t-1})$$
  2. Modulasi dinamis ADX makro: Jika durasi kompresi Bollinger Bands di dalam Keltner Channel berlangsung lama ($\text{in\_squeeze.rolling}(6).\text{sum}() \ge 5$), ambang batas ADX 4h dilonggarkan dari $20.0$ menjadi $16.0$ untuk menangkap ledakan volatilitas awal dari fase *chop*.

---

### 2.5 Pilar 5: Aktivasi Dual-Runtime Paper Trading di Docker Compose
* **Akar Masalah**: `freqtrade-paper` tidak berjalan karena terisolasi di dalam `profiles: ["paper"]`.
* **Solusi V25**:
  1. Hapus pembatasan profil pada `docker-compose.yml` atau sediakan skrip peluncur mandiri.
  2. Pastikan `freqtrade-paper` membaca `user_data/config_futures.json` (`dry_run: true`, port 8081 internal atau tanpa clash) dan menyimpan hasil transaksi ke `user_data/tradesv3.dryrun.sqlite`.
  3. Bot Live tetap beroperasi di port 8080 dengan `user_data/config_futures.live.json` dan `user_data/tradesv3.live.sqlite`. Keduanya berjalan berdampingan secara mandiri.

---

## 3. PROTOKOL EKSEKUSI & VERIFIKASI

1. **Implementasi Kode Strategi**: Tulis `user_data/strategies/ApexDualAlpha_Omni_V25_AdaptiveFlow.py` dengan mewarisi seluruh parameter *risk management* unggulan V24 (ROI ladder, trailing stop ratchet, chandelier ATR) dan mengintegrasikan 4 pilar kuantitatif baru.
2. **Perbaikan Berkas Daemon**: Tambal `user_data/modules/news_sentiment_daemon.py` pada logika kalkulasi `macro_blackout_active`.
3. **Validasi Kompilasi & Backtest Lokal**:
   - Jalankan uji sintaksis Python (0 galat sintaksis).
   - Jalankan backtest lokal menggunakan data Bybit Linear Futures ($2024 - 2026$) untuk mengukur *delta* performa V24 vs V25.
4. **Audit Integritas Format**:
   - 0 karakter em dash di seluruh berkas.
   - Verifikasi kebersihan direktori dan penyimpanan laporan akhir.
