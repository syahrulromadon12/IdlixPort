# Local Web Usage

Tanggal: 18 November 2026. Status: P1–P5 selesai pada macOS Intel; alur satu unduhan, sesi browser opt-in, cancel, retry dan riwayat tersedia. Subtitle dan batch tersedia; installer masih tahap berikutnya. Bukti ini tidak menyatakan dukungan seluruh OS/CPU sudah diuji.

## Menjalankan dari source

```bash
python -m pip install -e '.[web,portable]'
idlixport web
```

Buka `http://127.0.0.1:8765/`. Jika server versi P1 masih berjalan, hentikan dengan Ctrl+C lalu jalankan kembali. Tidak diperlukan build Node atau command per film. Backend harus tetap hidup selama pekerjaan berjalan; tab boleh ditutup.

1. Tempel URL halaman film atau media yang didukung, lalu pilih **Periksa tautan**.
2. Pilih kartu kualitas yang tersedia. Default memilih kualitas tertinggi sampai 1080p dengan audio; dimensi frame ditampilkan terpisah. Menu lanjutan menyediakan kebijakan maksimum 1080p atau terbaik.
3. Pilih folder yang tersedia, lalu **Unduh film**. Pilih subtitle: Indonesia dalam MKV menjadi default jika tersedia; pilihan tanpa subtitle dan file terpisah juga tersedia.
4. Pantau antrean, fase dan progres. Gunakan **Batalkan** untuk menghentikan pekerjaan aktif dan **Coba lagi** untuk attempt gagal/terhenti. Setelah selesai, pilih **Buka file** atau **Buka folder**. Hasil sudah tersimpan lokal; **Simpan salinan lewat browser** bersifat opsional.

Jika inspeksi kedaluwarsa setelah sepuluh menit, pilih **Periksa ulang**. Pekerjaan yang sudah diterima tetap diproses meskipun inspeksinya kemudian kedaluwarsa. Format diekstrak ulang saat mulai dan pilihan konkret gagal dengan `QUALITY_CHANGED` jika berubah; tidak turun resolusi diam-diam.

## Folder dan host yang disetujui

Default hasil berada di subfolder `downloads` pada direktori data aplikasi: macOS `~/Library/Application Support/IdlixPort`, Windows `%LOCALAPPDATA%/IdlixPort`, Linux `$XDG_DATA_HOME/idlixport` atau `~/.local/share/idlixport`. Setiap pekerjaan memiliki direktori tersendiri. Folder lain dapat didaftarkan operator saat startup:

```bash
idlixport web --download-dir "$HOME/Movies/IdlixPort"
```

Opsi dapat diulang. Browser hanya menerima label/ID folder, bukan path bebas. Folder yang sama perlu didaftarkan kembali setelah restart agar hasilnya dapat dibuka melalui aplikasi. File tidak dipindahkan ketika konfigurasi berubah. Opsi `--data-dir` khusus mengatur penyimpanan aplikasi, bukan folder hasil; jangan arahkan ke direktori yang sudah berisi file lain.

Default host adalah `z3.idlixku.com`, `majorplay.net`, subdomain provider tersebut, CDN persis `g2.akademivo.website` yang diverifikasi pada P4, serta `g7.aspireheightsacademy.digital`, `g7.akademivo.website`, `g7.edubambu.store` dan `g7.horizonacademy.site` beserta 26 host rotasi g7 lain yang diverifikasi pada audit unduhan The Intern. Daftar exact-host lengkap berada di `src/idlix_downloader/web/config.py`; lihat [audit rotasi CDN](web-session-troubleshooting.md#koreksi-audit-rotasi-cdn). `--allow-host` mengganti seluruh daftar default; operator harus menyebut semua host sumber/CDN yang sudah diverifikasi. Host lain, redirect ke alamat privat, proxy serta downloader jaringan FFmpeg ditolak. Direct MP4/HLS/DASH harus berada pada host yang disetujui. Jangan memakai opsi ini sebagai cara menonaktifkan kontrol jaringan.

Buka file/folder memakai aplikasi OS tanpa shell. Linux memerlukan `xdg-open` serta sesi desktop; lingkungan headless dapat memakai salinan browser. Mode container belum diuji sebagai distribusi aplikasi; tombol buka lokal akan berlaku pada mesin backend, bukan mesin browser.

## Batas dan pemulihan

Inspeksi dibatasi 120 detik, 12 varian, sampel media 256 KiB per varian, 2 MiB per respons dan 6 MiB total selama ekstraksi. Transfer yang telah diterima tidak memakai batas byte inspeksi. Jika dimensi tidak terbaca dari sampel, aplikasi tidak mengarang label resolusi. Ukuran adalah perkiraan dari sumber jika tersedia, bukan jaminan ukuran akhir.

Aplikasi memeriksa ruang kosong minimal 1 GiB ditambah 2,5 kali estimasi ukuran untuk transfer dan merge. Folder readonly atau ruang tidak cukup ditolak; ruang juga diperiksa selama proses. File hasil didaftarkan setelah transfer, merge dan validasi stream selesai. File yang dihapus/dipindahkan di luar aplikasi ditandai tidak tersedia. File parsial tidak menjadi artifact. **Hapus hasil** membersihkan output attempt terminal setelah konfirmasi; ditolak selama hasil sedang dikirim atau dipakai retry aktif. Riwayat tetap disimpan. Retry job dan penghapusan tidak berjalan otomatis. Lihat [Web Reliability and Queue](web-reliability.md) untuk batas tiga attempt, resume MP4 dan recovery.

UI polling paling cepat sekali per detik saat aktif, lima detik saat idle, lima belas detik saat tersembunyi dan backoff sampai tiga puluh detik setelah gangguan API. Preferensi browser menyimpan ID inspeksi/folder, bukan URL sumber. Respons publik tidak menyertakan payload privat, cookies atau URL media bertoken. URL input tetap diperlukan pada payload privat SQLite untuk ekstraksi ulang; database lokal harus diperlakukan sebagai data privat dan tidak masuk Git.

Sesi browser opt-in tersedia melalui panel **Sesi IDLIX opsional**. Pilih profil, beri persetujuan, lalu **Login di IDLIX**; pemeriksaan setelah kembali ke tab berjalan otomatis. Safari live telah terhubung dan menampilkan kelas 1080p dengan sampel frame 1920×804. Guest masih meminta verifikasi dengan batas 720p; T02 belum lengkap untuk format guest. Lihat [Browser Sessions](web-browser-sessions.md) untuk panduan, privasi dan batas bukti. Film live penuh belum diuji sampai selesai.

## API yang tersedia

Semua mutasi memerlukan Origin lokal dan token `X-CSRF-Token` dari `GET /api/session`. `Idempotency-Key` tersedia pada create/refresh untuk mencegah duplikasi; pemakaian kunci yang sama dengan payload berbeda ditolak.

| Endpoint | Fungsi |
| --- | --- |
| `GET /`, `/static/*` | UI lokal |
| `GET /health/live`, `/health/ready` | Status proses/dependensi |
| `POST /api/inspections` | Terima URL, kembalikan ID dengan 202 |
| `GET /api/inspections/{id}` | Status, pilihan, estimasi dan expiry |
| `POST /api/inspections/{id}/refresh` | Inspeksi baru dengan URL privat yang tersimpan |
| `POST /api/jobs` | Buat download dari inspection, choice/policy dan folder ID |
| `GET /api/jobs`, `/api/jobs/{id}` | Snapshot semua pekerjaan aktif dan dua puluh terbaru/detail |
| `GET /api/history` | Riwayat download berpaginasi |
| `POST /api/jobs/{id}/cancel` | Batalkan queued/aktif |
| `POST /api/jobs/{id}/retry` | Attempt baru dengan batas retry |
| `DELETE /api/jobs/{id}/files` | Hapus hasil terminal, pertahankan riwayat |
| `GET /api/folders` | Daftar folder yang disetujui |
| `POST /api/jobs/{id}/open-folder` | Buka folder hasil lokal |
| `POST /api/artifacts/{id}/open` | Buka hasil dengan aplikasi OS |
| `GET /api/artifacts/{id}/file` | Salinan browser, mendukung satu Range |
| `DELETE /api/artifacts/{id}` | Hapus artifact terminal yang tidak sedang dikirim |

## Bukti pengujian

Suite CLI, fondasi web, alur P2–P3 dan spike jaringan: **195 passed, 4 skipped**. Empat tes yang dilewati adalah browser opt-in; semuanya dijalankan terpisah dan **4 passed** pada Chrome headless, desktop 1440×1000 dan mobile 390×844. Pengujian mobile memakai viewport Chrome, bukan perangkat ponsel fisik. Screenshot diperiksa untuk kedua ukuran.

Tes browser P3 juga membuktikan cancel, retry, pagination dan konfirmasi hapus; lihat [bukti ketahanan](web-reliability.md). Tes browser membuktikan pilihan 720p/1080p, transfer HLS 1080p dengan audio, pemulihan setelah refresh, buka hasil melalui ID, salinan browser dengan hash sama, API terputus/pulih, expiry dan judul sumber berisi HTML yang tetap ditampilkan sebagai teks. Pembukaan aplikasi OS dimock pada tes; pengiriman argumen tanpa shell diuji terpisah.

Tes backend mencakup expiry sebelum enqueue dan setelah antre, replay idempotency, konflik payload, sumber gagal/timeout, metadata tanpa dimensi, batas byte/varian, disk penuh, folder readonly, symlink/traversal, hapus saat aktif/dikirim serta transfer dan validasi stream. Bukti HLS/DASH sintetis dan perubahan kualitas P1 tetap tercakup. Ada satu warning deprecation Starlette TestClient/httpx yang tidak menggagalkan tes.

```bash
python -m pip install -e '.[web,dev,portable]'
python -m pytest -q -p no:cacheprovider tests tools/spikes/test_network_spike.py
ruff check --no-cache src tests tools
ruff format --check --no-cache src tests tools
node --check src/idlix_downloader/web/static/app.js
```

Uji browser opsional memerlukan Node 22+ dan Chrome. Di luar macOS, isi `CHROME_BIN` dengan path executable Chrome. Profil sementara terpisah digunakan; tidak ada pembacaan Keychain atau cookies browser utama.

```bash
IDLIXPORT_BROWSER_TESTS=1 python -m pytest -q -p no:cacheprovider tests/test_web_flow.py tests/test_web_session_browser.py -k browser
```

Aset HTML/CSS/JS disertakan sebagai package data. Tidak diperlukan Node pada runtime aplikasi. Lihat [Web Foundation](web-foundation.md) untuk kontrak backend P1 dan [PRD](web-prd.md) untuk scope rilis selanjutnya.

Panduan subtitle, hasil parsial dan batch tersedia di [Web Subtitles and Batch](web-subtitles-and-batch.md).

Jika Safari sudah login tetapi aplikasi tetap menunggu atau menolak sesi, lihat [troubleshooting sesi](web-session-troubleshooting.md). Setelah memperbarui source, restart backend agar worker dan sesi memakai versi yang sama.
