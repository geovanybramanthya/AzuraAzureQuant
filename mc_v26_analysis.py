# -*- coding: utf-8 -*-
"""
Monte Carlo analysis for V26 v2 backtest export (v26_v2_export.zip).
Read-only terhadap strategi/config. Hanya menulis v26_montecarlo.md.
Metode sesuai brief: r_i = profit_abs/stake_amount; model ekuitas fraksi 1/K, K=4.
Seed tetap 42 untuk semua generator.
"""
import zipfile
import json
import numpy as np
import pandas as pd

ZIP_PATH = r"C:\Users\geova\Downloads\FREQTRADE\user_data\backtest_results\v26_v2_export.zip"
OUT_PATH = r"C:\Users\geova\Downloads\FREQTRADE\user_data\backtest_results\v26_montecarlo.md"
SEED = 42
N_ITER = 10_000
K = 4
W0 = 1000.0
CHUNK = 2000

PCTS = [1, 5, 25, 50, 75, 95, 99]


def load_trades(zip_path):
    with zipfile.ZipFile(zip_path) as z:
        for name in z.namelist():
            if name.endswith(".json") and "config" not in name.lower():
                with z.open(name) as f:
                    try:
                        data = json.loads(f.read().decode("utf-8"))
                    except Exception:
                        continue
                if isinstance(data, dict) and "strategy" in data:
                    strat = data["strategy"]
                    sname = list(strat.keys())[0]
                    return strat[sname]["trades"], name, sname
    raise RuntimeError("Trades JSON tidak ditemukan di zip")


def equity_stats(factors):
    """factors: (m, n) faktor multiplikatif per langkah. Return (max_dd_pct, final_multiple)."""
    eq = np.cumprod(factors, axis=1)
    eq_full = np.concatenate([np.ones((eq.shape[0], 1)), eq], axis=1)
    peak = np.maximum.accumulate(eq_full, axis=1)
    dd = (peak - eq_full) / peak
    return dd.max(axis=1) * 100.0, eq[:, -1].copy()


def pct_rows(x, worst):
    vals = {f"p{p}": float(np.percentile(x, p)) for p in PCTS}
    vals["terburuk"] = float(worst)
    return vals


def main():
    trades, json_name, strat_name = load_trades(ZIP_PATH)
    df = pd.DataFrame(trades)
    if "is_open" in df.columns:
        df = df[~df["is_open"].astype(bool)].copy()
    df["close_date"] = pd.to_datetime(df["close_date"], utc=True)
    df = df.sort_values("close_date").reset_index(drop=True)

    n = len(df)
    profit = df["profit_abs"].to_numpy(dtype=float)
    stake = df["stake_amount"].to_numpy(dtype=float)
    assert (stake > 0).all(), "Ada stake_amount <= 0"
    total_profit = float(profit.sum())
    wins = int((profit > 0).sum())
    losses = int((profit <= 0).sum())
    wr = wins / n * 100.0
    gp = float(profit[profit > 0].sum())
    gl = float(abs(profit[profit < 0].sum()))
    pf = gp / gl

    anchor_ok = (
        n == 1043
        and abs(total_profit - 134505.64) < 1.0
        and abs(wr - 73.92) < 0.05
        and abs(pf - 1.9280) < 0.005
    )
    print(f"ANCHOR n={n} total={total_profit:.2f} wins={wins} losses={losses} "
          f"WR={wr:.4f}% PF={pf:.4f} OK={anchor_ok} json={json_name} strat={strat_name}")
    if not anchor_ok:
        print("ANCHOR GAGAL - berhenti, zip salah.")
        return

    r = profit / stake
    is_loss = profit <= 0
    orig_streak = 0
    _cur = 0
    for _x in is_loss:
        _cur = _cur + 1 if _x else 0
        orig_streak = max(orig_streak, _cur)

    # --- Sanity: urutan asli (berdasarkan close_date) ---
    dd_orig_arr, fin_orig_arr = equity_stats((1.0 + r / K).reshape(1, -1))
    dd_orig = float(dd_orig_arr[0])
    fin_orig = float(fin_orig_arr[0])
    real_final_mult = (W0 + 134505.64) / W0
    real_dd = 9.7360
    print(f"SANITY model-1/K urutan asli: final={fin_orig:.2f}x DD={dd_orig:.2f}% | "
          f"riil: final={real_final_mult:.2f}x DD={real_dd:.2f}%")

    rng = np.random.default_rng(SEED)

    # --- Indeks permutasi (dipakai A dan D) ---
    perm_idx = np.empty((N_ITER, n), dtype=np.int32)
    for s in range(0, N_ITER, CHUNK):
        m = min(CHUNK, N_ITER - s)
        perm_idx[s:s + m] = np.argsort(rng.random((m, n)), axis=1).astype(np.int32)

    # --- Indeks bootstrap (B) ---
    boot_idx = np.empty((N_ITER, n), dtype=np.int32)
    for s in range(0, N_ITER, CHUNK):
        m = min(CHUNK, N_ITER - s)
        boot_idx[s:s + m] = rng.integers(0, n, size=(m, n)).astype(np.int32)

    def simulate(idx):
        max_dd = np.empty(N_ITER)
        final = np.empty(N_ITER)
        for s in range(0, N_ITER, CHUNK):
            m = min(CHUNK, N_ITER - s)
            rs = r[idx[s:s + m]]
            max_dd[s:s + m], final[s:s + m] = equity_stats(1.0 + rs / K)
        return max_dd, final

    dd_a, fin_a = simulate(perm_idx)
    dd_b, fin_b = simulate(boot_idx)
    print("Sim A & B selesai")

    # --- D: losing streak dari permutasi yang sama ---
    streak = np.empty(N_ITER, dtype=int)
    for s in range(0, N_ITER, CHUNK):
        m = min(CHUNK, N_ITER - s)
        lm = is_loss[perm_idx[s:s + m]].astype(np.int8)
        for i in range(m):
            row = lm[i]
            d = np.diff(np.concatenate(([0], row, [0])))
            starts = np.flatnonzero(d == 1)
            ends = np.flatnonzero(d == -1)
            streak[s + i] = int((ends - starts).max()) if len(starts) else 0
    print("Sim D selesai")

    # --- C: risiko bulanan ---
    df["month"] = df["close_date"].dt.tz_convert("UTC").dt.to_period("M")
    monthly = df.groupby("month")["profit_abs"].sum()
    months_arr = monthly.to_numpy(dtype=float)
    n_months = len(months_arr)
    neg_months_actual = int((months_arr < 0).sum())
    worst_actual = float(months_arr.min())
    best_actual = float(months_arr.max())
    boot_m = rng.integers(0, n_months, size=(N_ITER, n_months))
    samples = months_arr[boot_m]
    worst_month = samples.min(axis=1)
    any_neg = (samples < 0).any(axis=1)
    year_sum = samples[:, :12].sum(axis=1)
    print(f"Sim C selesai: n_months={n_months} neg_actual={neg_months_actual} "
          f"worst_actual={worst_actual:.2f} best_actual={best_actual:.2f}")

    # --- Probabilitas kunci ---
    probs = {
        "P_fin_lt_awal_A": float((fin_a < 1.0).mean() * 100),
        "P_fin_lt_awal_B": float((fin_b < 1.0).mean() * 100),
        "P_dd15_A": float((dd_a > 15).mean() * 100),
        "P_dd15_B": float((dd_b > 15).mean() * 100),
        "P_dd20_A": float((dd_a > 20).mean() * 100),
        "P_dd20_B": float((dd_b > 20).mean() * 100),
        "P_dd30_A": float((dd_a > 30).mean() * 100),
        "P_dd30_B": float((dd_b > 30).mean() * 100),
        "P_any_neg_month": float(any_neg.mean() * 100),
        "P_neg_year": float((year_sum < 0).mean() * 100),
    }

    t_dd_a = pct_rows(dd_a, dd_a.max())
    t_dd_b = pct_rows(dd_b, dd_b.max())
    t_fin_a = pct_rows(fin_a, fin_a.min())
    t_fin_b = pct_rows(fin_b, fin_b.min())
    t_wm = pct_rows(worst_month, worst_month.min())
    t_streak = pct_rows(streak.astype(float), streak.max())

    def fmt(x, dec=2):
        return f"{x:,.{dec}f}"

    lines = []
    lines.append("# Simulasi Monte Carlo - V26 v2 (ApexDualAlpha_Omni_V26_Optimized)")
    lines.append("")
    lines.append("Tanggal: 2026-10-20. Komputasi dari file ekspor backtest yang sudah ada (tanpa backtest ulang).")
    lines.append("")
    lines.append("## Sumber data dan anchor check")
    lines.append("")
    lines.append(f"- File zip sumber: `v26_v2_export.zip` (JSON di dalamnya: `{json_name}`, strategi `{strat_name}`) - zip yang sama yang dipakai auditor final di `v26_audit_final.md`.")
    lines.append(f"- Jumlah trade tertutup: {n} (anchor 1043) - {'COCOK' if n == 1043 else 'TIDAK COCOK'}")
    lines.append(f"- Total profit_abs: {fmt(total_profit)} USDT (anchor +134.505,64) - {'COCOK' if abs(total_profit - 134505.64) < 1.0 else 'TIDAK COCOK'}")
    lines.append(f"- Menang/kalah: {wins}/{losses}; win rate {wr:.2f}% (anchor 73,92%) - {'COCOK' if abs(wr - 73.92) < 0.05 else 'TIDAK COCOK'}")
    lines.append(f"- Profit factor: {pf:.4f} (gross profit {fmt(gp)} / gross loss {fmt(gl)}; anchor 1,9280) - {'COCOK' if abs(pf - 1.9280) < 0.005 else 'TIDAK COCOK'}")
    lines.append(f"- **Anchor check keseluruhan: {'LOLOS' if anchor_ok else 'GAGAL'}**")
    lines.append("")
    lines.append("## Metode dan asumsi")
    lines.append("")
    lines.append("- Return per trade: $r_i = profit\\_abs_i / stake\\_amount_i$ (bebas skala terhadap compounding backtest).")
    lines.append(f"- Model ekuitas: wallet awal {fmt(W0, 0)} USDT; setiap trade memasang fraksi $1/K$ dari ekuitas berjalan dengan $K = max\\_open\\_trades = 4$; ekuitas diperbarui $E \\leftarrow E \\times (1 + r_i/K)$. **Ini aproksimasi** terhadap perilaku stake unlimited freqtrade (di backtest riil, beberapa trade bisa tumpang tindih dan ukuran stake mengikuti saldo saat order dibuat, bukan urutan close_date); lihat bagian sanity check.")
    lines.append("- Urutan acuan (termasuk sanity check): trade diurutkan berdasarkan close_date.")
    lines.append(f"- Iterasi: {N_ITER:,} per metode. Seed acak tetap: {SEED} (satu aliran generator numpy default_rng({SEED}) untuk permutasi, bootstrap, dan bootstrap bulanan; indeks permutasi yang sama dipakai ulang untuk metode D).")
    lines.append("- Metode A (permutasi): urutan 1.043 trade diacak tanpa pengembalian - menguji sensitivitas terhadap urutan realisasi saja; himpunan trade identik dengan sejarah, jadi ekuitas akhir hanya berubah karena jalur compounding fraksi tetap.")
    lines.append("- Metode B (bootstrap): 1.043 trade disampling dengan pengembalian - memuat urutan dan komposisi yang lebih buruk (dan lebih baik) dari sejarah, termasuk trade yang sama muncul berkali-kali atau tidak sama sekali.")
    lines.append("- Metode C (bulanan): PnL bulanan aktual dari close_date (USDT absolut hasil compounding), lalu 32 bulan disampling dengan pengembalian. **Catatan asumsi**: bulan diperlakukan saling bebas dan dapat dipertukarkan; karena PnL absolut membesar seiring compounding, bulan-bulan akhir berbobot jauh lebih besar - distribusi ini membaca risiko pada skala absolut campuran, bukan bulan yang setara.")
    lines.append("- Metode D (streak): dari 10.000 permutasi yang sama dengan metode A, hanya urutan menang/kalah yang dipakai (kalah = profit_abs <= 0).")
    lines.append("- Max drawdown dihitung dari kurva ekuitas simulasi (puncak berjalan vs nilai berjalan), dalam persen.")
    lines.append("- Keterbatasan umum: resampling hanya mengacak trade historis yang sama; tidak memuat rezim pasar baru, slippage di luar fee backtest, kegagalan eksekusi, atau korelasi antar-trade yang tumpang tindih di dunia nyata (pengacakan justru memutus klaster volatilitas historis). Angka-angka ini indikator distribusi risiko dari sampel historis, bukan jaminan batas risiko live.")
    lines.append("")
    lines.append("## Sanity check (urutan asli, model 1/K)")
    lines.append("")
    lines.append(f"- Model 1/K urutan asli: ekuitas akhir {fin_orig:.2f}x lipat, max DD {dd_orig:.2f}%.")
    lines.append(f"- Backtest riil: ekuitas akhir {real_final_mult:.2f}x lipat (1000 menjadi {fmt(W0 + 134505.64)}), max DD akun {real_dd:.2f}%.")
    lines.append(f"- Selisih: kelipatan akhir model {fin_orig / real_final_mult:.2f}x dari riil; DD model {dd_orig - real_dd:+.2f} poin persen dari riil. Sumber selisih utama: (1) di backtest riil stake unlimited dihitung dari saldo tersedia saat trade DIBUKA (dan trade tumpang tindih berbagi saldo yang sama), sedangkan model memasang tepat 1/4 ekuitas pada urutan close; (2) profit_abs historis sendiri sudah merupakan hasil jalur compounding riil, sehingga r_i mengandung pertumbuhan stake historis - menerapkannya pada ekuitas fraksi-tetap mencampur dua rezim ukuran posisi. Selisih dilaporkan apa adanya; metode tidak disesuaikan agar cocok.")
    lines.append("")
    lines.append("## Hasil A & B - Max drawdown (%)")
    lines.append("")
    lines.append("| Persentil | A: Permutasi | B: Bootstrap |")
    lines.append("|---|---:|---:|")
    for p in PCTS:
        lines.append(f"| p{p} | {t_dd_a[f'p{p}']:.2f} | {t_dd_b[f'p{p}']:.2f} |")
    lines.append(f"| Terburuk teramati | {t_dd_a['terburuk']:.2f} | {t_dd_b['terburuk']:.2f} |")
    lines.append("")
    lines.append("## Hasil A & B - Ekuitas akhir (kelipatan dari wallet awal)")
    lines.append("")
    lines.append("| Persentil | A: Permutasi | B: Bootstrap |")
    lines.append("|---|---:|---:|")
    for p in PCTS:
        lines.append(f"| p{p} | {t_fin_a[f'p{p}']:.2f}x | {t_fin_b[f'p{p}']:.2f}x |")
    lines.append(f"| Terburuk teramati (minimum) | {t_fin_a['terburuk']:.2f}x | {t_fin_b['terburuk']:.2f}x |")
    lines.append("")
    lines.append("Catatan metode A: kolom ekuitas akhir A konstan di semua persentil BUKAN kesalahan hitung - pada model fraksi-tetap multiplikatif, perkalian bersifat komutatif sehingga urutan trade tidak mengubah ekuitas akhir sama sekali; yang berubah hanya jalurnya (drawdown). Seluruh informasi metode A ada di distribusi drawdown; variasi ekuitas akhir hanya muncul di metode B yang mengubah komposisi trade.")
    lines.append("")
    lines.append("## Hasil C - Risiko bulanan (bootstrap 32 bulan)")
    lines.append("")
    lines.append(f"- Bulan dengan trade tertutup: {n_months} ({monthly.index[0]} s.d. {monthly.index[-1]}); bulan negatif aktual: {neg_months_actual}; bulan terburuk aktual: {fmt(worst_actual)} USDT (2026-01 menurut audit: -4.779,28); bulan terbaik aktual: {fmt(best_actual)} USDT.")
    lines.append("")
    lines.append("| Persentil | Bulan terburuk dalam sampel 32 bulan (USDT) |")
    lines.append("|---|---:|")
    for p in PCTS:
        lines.append(f"| p{p} | {fmt(t_wm[f'p{p}'])} |")
    lines.append(f"| Terburuk teramati | {fmt(t_wm['terburuk'])} |")
    lines.append("")
    lines.append("## Hasil D - Streak kalah terpanjang (permutasi)")
    lines.append("")
    lines.append("| Persentil | Streak kalah terpanjang (trade) |")
    lines.append("|---|---:|")
    for p in PCTS:
        lines.append(f"| p{p} | {t_streak[f'p{p}']:.0f} |")
    lines.append(f"| Terburuk teramati | {t_streak['terburuk']:.0f} |")
    lines.append(f"| Streak kalah terpanjang pada urutan asli (close_date) | {orig_streak} |")
    lines.append("")
    lines.append("## Probabilitas kunci")
    lines.append("")
    lines.append(f"- P(ekuitas akhir < wallet awal): A {probs['P_fin_lt_awal_A']:.2f}% | B {probs['P_fin_lt_awal_B']:.2f}%")
    lines.append(f"- P(max DD > 15%): A {probs['P_dd15_A']:.2f}% | B {probs['P_dd15_B']:.2f}%")
    lines.append(f"- P(max DD > 20%): A {probs['P_dd20_A']:.2f}% | B {probs['P_dd20_B']:.2f}%")
    lines.append(f"- P(max DD > 30%): A {probs['P_dd30_A']:.2f}% | B {probs['P_dd30_B']:.2f}%")
    lines.append(f"- P(minimal satu bulan negatif dalam 32 bulan): {probs['P_any_neg_month']:.2f}%")
    lines.append(f"- P(tahun negatif, jumlah 12 bulan sampling): {probs['P_neg_year']:.2f}%")
    lines.append("")

    text = "\n".join(lines)
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        f.write(text + "\n")
    print("WROTE", OUT_PATH)
    print(json.dumps({
        "dd_orig": dd_orig, "fin_orig": fin_orig,
        "t_dd_a": t_dd_a, "t_dd_b": t_dd_b,
        "t_fin_a": t_fin_a, "t_fin_b": t_fin_b,
        "t_wm": t_wm, "t_streak": t_streak,
        "probs": probs, "n_months": n_months,
        "neg_months_actual": neg_months_actual,
        "worst_actual": worst_actual, "best_actual": best_actual,
    }, indent=1))


if __name__ == "__main__":
    main()
