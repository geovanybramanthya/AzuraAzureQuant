# Runbook Deployment V26 - AzuraAzureQuant (Freqtrade Futures, Bybit)

Dokumen ini adalah satu-satunya prosedur deployment resmi mulai era V26.
Semua *script* deployment lama dinyatakan DEPRECATED di bagian akhir dokumen ini.

Prinsip wajib:

- Nol rahasia di dalam *script*, *config* hasil salin, maupun dokumen ini. Semua kredensial diambil dari *vault*.
- Akses VPS memakai kunci SSH, bukan *password*.
- Tidak ada perubahan di VPS di luar langkah yang tertulis di sini.

## 0. Kredensial dan Akses

- *Vault* (sumber kebenaran tunggal): `C:\Users\geova\.azura-vault\credentials.txt`
  Folder ini terproteksi ACL khusus user `geova`. Jangan menyalin isinya ke tempat lain.
- Sebelum menjalankan *script* lama yang masih dipertahankan (mis. verifikasi), muat variabel berikut dari *vault* ke *environment* sesi PowerShell:

| Variabel *environment* | Isi | Sumber di *vault* |
|---|---|---|
| `VPS_SSH_PASSWORD` | *Password* SSH VPS (cadangan; jalur utama tetap kunci SSH) | Bagian `[VPS SSH]` |
| `DASHBOARD_PASSCODE` | *Passcode* login *dashboard* | Bagian `[Dashboard ...]` |
| `FREQTRADE_REST_PASSWORD` | *Password* REST API *live* | Bagian `[Freqtrade REST API - LIVE ...]` |
| `FREQTRADE_REST_PASSWORD_DRYRUN` | *Password* REST API *dryrun/paper* (berbeda dari *live*) | Bagian `[Freqtrade REST API - DRYRUN/PAPER ...]` |

- Akses VPS utama: kunci SSH `~/.ssh/vps_sandbox` (dari *sandbox*) atau helper `ops\vps_conn.py` (PC, *environment-first*, mendukung `VPS_KEY_PATH`).
- Target VPS: host dan user sesuai *vault* bagian `[VPS SSH]`; direktori proyek `/home/ubuntu/freqtrade`; *stack* Docker Compose dengan 6 *service*: `freqtrade-core`, `freqtrade-paper`, `freqtrade-supervisor`, `freqtrade-derivatives`, `freqtrade-sentiment`, `freqtrade-dashboard`.

## 1. Prasyarat (Preconditions)

1. Akun dalam keadaan *flat*: tidak ada posisi terbuka dan tidak ada *open order* di Bybit.
   Cara cek: *dashboard* `:5050` (login dengan *passcode* dari *vault*) menampilkan 0 posisi, atau panggil REST API status dari VPS: `GET http://127.0.0.1:8080/api/v1/status` dengan *basic auth* user REST + `FREQTRADE_REST_PASSWORD` dari *vault*. Bila masih ada posisi/order: STOP, jangan lanjut.
2. Catat versi yang sedang *live* (nama strategi aktif di `docker-compose.yml` VPS) dan *commit*/salinan file PC yang akan dikirim.
3. Pastikan PC dan VPS tidak sedang menjalankan proses deployment lain.
4. Pastikan ruang disk VPS cukup (`df -h`) dan semua *container* berstatus `Up` (`docker ps`).

## 2. Backup

Di VPS, sebelum menyentuh file apa pun:

1. Buat folder backup bertanggal, contoh: `/home/ubuntu/freqtrade/backups/deploy-YYYYMMDD-HHMM/`.
2. Salin ke folder itu: `docker-compose.yml`, `dashboard_server.py`, `ai_supervisor_daemon.py`, `ai_supervisor_state.json`, `user_data/config_futures.live.json`, `user_data/config_futures.json`, dan file strategi yang sedang aktif di `user_data/strategies/`.
3. Simpan juga *snapshot* daftar *container* dan log terakhir: `docker ps` dan `docker logs --tail 200 freqtrade-core`.
4. Catatan: folder backup di VPS berisi file yang memuat kredensial (config dan `dashboard_server.py`). Jangan mengunduhnya ke luar VPS/PC dan jangan membagikannya.

Di PC: file yang mengandung rahasia hanya boleh dibackup ke dalam *vault* (`C:\Users\geova\.azura-vault\`), bukan ke folder proyek.

## 3. Sinkronisasi File PC ke VPS

Kirim hanya file yang berubah, memakai SCP/SFTP berbasis kunci SSH (bukan *script* lama):

1. Strategi V26: `user_data\strategies\ApexDualAlpha_Omni_V26_Optimized.py` ke `/home/ubuntu/freqtrade/user_data/strategies/`.
2. Bila berubah: `dashboard_server.py`, `ai_supervisor_daemon.py`, `docker-compose.yml` ke `/home/ubuntu/freqtrade/`.
3. Bila berubah: `user_data\config_futures.live.json` dan/atau `user_data\config_futures.json` ke `/home/ubuntu/freqtrade/user_data/`.
   Perhatian: kedua config memuat kredensial (kunci Bybit dan REST API). Perlakukan transfernya seperti memindahkan rahasia: hanya lewat kanal SSH terenkripsi, jangan lewat kanal lain.
4. Perbarui nama strategi pada `command` *service* `freqtrade-core` (dan `freqtrade-paper` bila dipakai) di `docker-compose.yml` VPS agar menunjuk ke strategi V26.
5. JANGAN menimpa `ai_supervisor_daemon.py` di VPS sebelum selisihnya dengan versi PC ditinjau (lihat langkah 4 butir 3).

## 4. Pemeriksaan Paritas Config (Config Parity Check)

Untuk setiap file berikut, hitung SHA-256 di PC dan di VPS dan pastikan sama persis sebelum *restart*:

- `user_data/config_futures.live.json`
- `user_data/config_futures.json`
- `dashboard_server.py`
- `ai_supervisor_daemon.py`
- `docker-compose.yml`

Aturan keputusan:

1. Beda *hash* pada config: hentikan deployment; samakan dulu sumbernya (PC adalah sumber kebenaran untuk config, kecuali ada *hotfix* VPS yang belum diadopsi - tinjau selisihnya baris per baris).
2. Beda *hash* pada `dashboard_server.py` atau `docker-compose.yml`: sama seperti config, jangan lanjut sebelum selisih dipahami.
3. Khusus `ai_supervisor_daemon.py`: per 2026-10-21 versi VPS dan PC TERBUKTI berbeda (jumlah baris dan *hash* tidak sama). File ini tidak boleh ditimpa membabi buta dari PC; lakukan rekonsiliasi manual terlebih dahulu dan dokumentasikan hasilnya.
4. Verifikasi isi kritis config *live* di VPS: `api_server.enabled = true`, `listen_ip_address = 127.0.0.1`, `listen_port = 8080` (config *dryrun*: port 8081), nama *exchange* `bybit`, dan kunci *exchange* terisi (cek keberadaan dan panjang saja, jangan mencetak nilainya).

## 5. Urutan Restart

Urutan ini mencegah *supervisor* menimpa *state* saat *core* berganti:

1. Hentikan *supervisor* lebih dulu: `docker compose stop freqtrade-supervisor`.
2. Hentikan *dashboard*: `docker compose stop freqtrade-dashboard`.
3. Bangun ulang *image* bila `Dockerfile`/dependensi berubah: `docker compose build freqtrade-core`.
4. Naikkan *core*: `docker compose up -d freqtrade-core`. Tunggu hingga log menyatakan *bot* berjalan dan REST API aktif.
5. Naikkan *sentiment* dan *derivatives* (bila ikut berubah): `docker compose up -d freqtrade-sentiment freqtrade-derivatives`.
6. Naikkan *supervisor*: `docker compose up -d freqtrade-supervisor`.
7. Naikkan *dashboard* terakhir: `docker compose up -d freqtrade-dashboard`.
8. *Service* `freqtrade-paper` hanya dinaikkan bila sesi *paper trading* memang direncanakan.

## 6. Verifikasi Kesehatan (Health Verification)

Semua butir harus lulus sebelum deployment dinyatakan selesai:

1. `docker ps`: semua *service* yang direncanakan berstatus `Up` dan tidak *restart loop*.
2. Log *core*: `docker logs --tail 100 freqtrade-core` - tidak ada *exception* berulang; strategi V26 termuat; pasangan (pairs) sesuai *pairlist*.
3. REST API *live*: dari VPS, `GET http://127.0.0.1:8080/api/v1/ping` merespons, lalu `GET /api/v1/status` dan `GET /api/v1/balance` dengan *basic auth* (user REST dan *password* dari *vault*) mengembalikan data akun yang benar.
4. *Dashboard*: `http://<VPS>:5050` menampilkan halaman login; login dengan *passcode* dari *vault* berhasil; halaman utama menampilkan saldo dan status *core* yang mutakhir.
5. *Heartbeat*: `ai_supervisor_state.json` di VPS terbarui beberapa menit terakhir, dan `ai_supervisor.log` tidak menunjukkan galat autentikasi REST (401) setelah *restart*.
6. *Sentiment*: file *state* sentimen terbarui dan *daemon* `freqtrade-sentiment` tidak *error* di log.
7. Posisi: status akun masih *flat* atau sesuai rencana sesi; tidak ada order nyangkut dari proses *restart*.

## 7. Rollback

Bila verifikasi gagal dan tidak teratasi dalam 15 menit:

1. Hentikan *service* dengan urutan terbalik: *dashboard*, *supervisor*, lalu *core*.
2. Kembalikan file dari folder backup langkah 2 (config, `docker-compose.yml`, `dashboard_server.py`, `ai_supervisor_daemon.py`, strategi versi sebelumnya, `ai_supervisor_state.json`).
3. Naikkan kembali mengikuti urutan langkah 5.
4. Ulangi verifikasi langkah 6 butir 1-5 untuk versi lama.
5. Catat penyebab kegagalan dan file yang dikembalikan; jangan mencoba deployment ulang di sesi yang sama tanpa analisis penyebab.

## 8. Catatan Rotasi Kredensial (Ditunda, Bukan Bagian Deployment Ini)

Rotasi *password* REST API TIDAK dilakukan bersamaan dengan deployment biasa. Rotasi memerlukan jendela *restart* `freqtrade-core` tersendiri karena kredensial REST tersebar di: config *live* dan *dryrun* (pembangkit: `make_config.py` di PC), `dashboard_server.py` (kredensial tertanam untuk eksekusi order), dan `ai_supervisor_daemon.py` (membaca dari config saat *start*). Nilai hasil rotasi disimpan di *vault* terlebih dahulu, lalu disebarkan dalam satu jendela *restart*: *core*, *supervisor*, *dashboard*.

## 9. Script DEPRECATED

*Script* berikut adalah peninggalan era V12-V15 dan/atau pola lama (kredensial tertanam, satu kali pakai). Seluruhnya DINYATAKAN DEPRECATED dan digantikan oleh runbook ini. Sebagian sudah dibersihkan dari kredensial tertanam pada 2026-10-21 dan membaca nilai dari *environment*, tetapi tetap tidak boleh dipakai sebagai jalur deployment resmi:

- `deploy_v12_maker_optimized_vps.py` (era V12)
- `deploy_news_layer_vps.py` (era V15)
- `sync_v15_to_vps.py` (era V15)
- `sync_to_vps.py`
- `deploy_and_verify_live.py`
- `deploy_and_verify_dashboard_vps.py`
- `deploy_security_to_vps.py`
- `execute_remediation.py`
- `sync_config_live.py`
- `sync_full_cfg.py`
- `update_vps_compose.py`
- `harden_vps.py`
- `start_services.py`

Dipertahankan sebagai alat verifikasi (bukan deployment): `verify_vps_security_gate.py` - sudah dibersihkan, membaca kredensial dari *environment* (`VPS_SSH_PASSWORD`, `DASHBOARD_PASSCODE`, nilai dari *vault*).

Jalur koneksi yang dipertahankan: `ops\vps_conn.py` (helper *environment-first* berbasis kunci SSH).
