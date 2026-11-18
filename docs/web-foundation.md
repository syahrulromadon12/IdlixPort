# Web Foundation

Tanggal: 17 November 2026. Status: P1 selesai pada lingkungan pengujian macOS Intel. Dokumen ini mencatat hasil historis P1; UI dan alur P2 terbaru tersedia di [Local Web Usage](web-usage.md).

## Menjalankan

```bash
python -m pip install -e '.[web,portable]'
idlixport web
```

API berjalan di `http://127.0.0.1:8765`. Gunakan `/health/ready` untuk readiness. Jika menjalankan pengujian otomatis:

```bash
python -m pip install -e '.[web,dev,portable]'
python -m pytest -q -p no:cacheprovider tests tools/spikes/test_network_spike.py
```

Command web memuat dependency secara opsional; instalasi CLI tanpa extra web tetap berlaku. Direktori default adalah direktori data aplikasi sesuai OS, bukan current working directory. Untuk pengujian, `--data-dir` boleh menunjuk direktori khusus; direktori yang berisi file lain ditolak agar tidak diambil alih. Jangan menunjuk home atau folder source.

Pada akhir P1, endpoint yang tersedia adalah `/`, `/health/live`, `/health/ready` dan `/api/session`. Pada tahap P1 belum ada endpoint create job atau UI; kontrak request dan runner internal tersedia untuk P2. Respons `/api/session` menyediakan token CSRF per proses yang tidak boleh dimasukkan ke log. Server hanya bind loopback. Rencana deployment container tetap pekerjaan distribusi, bukan sudah tersedia pada P1.

## Kontrak kualitas

`web/quality.py` membuat pilihan berdasarkan kelas resolusi, dimensi, asal metadata, codec dan ketersediaan audio. Pergantian format ID tidak mengubah pilihan semantik; pergantian resolusi/codec menghasilkan `QUALITY_CHANGED`. Pilihan ambigu gagal. `best` dan `max_1080` adalah kebijakan terpisah, sedangkan CLI lama tetap maksimum 1080p secara default.

Metadata tinggi tidak diketahui tidak diiklankan sebagai pilihan konkret. Audio rendition eksplisit tetap tersedia walaupun codec belum diiklankan. Pemilihan web dilakukan ulang saat worker mulai; proses transfer menggunakan callable selector agar ID provider tidak ditafsirkan sebagai ekspresi atau path. Dimensi hasil juga diperiksa melalui FFmpeg; frame crop dapat memakai label sumber jika asalnya ditandai.

## Progres dan error

Callback engine membatasi progres menjadi sekitar sekali per detik. Output hanya berisi fase dan angka yang valid, tidak menyalin dictionary yt-dlp. Fase transfer selesai bukan status job selesai. Logger web tidak mengirim string provider dan error dikembalikan sebagai kode/pesan tetap. CLI tetap memakai perilaku default.

## Penyimpanan dan runner

SQLite schema versi 1 menyimpan jobs, inspections, artifacts dan idempotency keys. Antrean dibatasi 20, klaim atomik per jenis dan backup memakai SQLite API. Metadata inspeksi memiliki expiry sepuluh menit. URL disimpan hanya pada payload privat; API publik belum mengekspos payload. Riwayat, retention dan endpoint pengambilan hasil merupakan pekerjaan berikutnya.

Ada satu slot inspeksi dan satu slot download. Worker berjalan sebagai subprocess tanpa shell, sedangkan komunikasi JSON dibatasi 64 KiB. Crash atau output tidak valid gagal, bukan completed. Saat shutdown pekerjaan aktif menjadi interrupted; startup memulihkan state setelah memastikan worker lama tidak aktif. Instance kedua ditolak. Job Objects Windows diimplementasikan tetapi belum diuji di Windows; tidak dianggap sebagai dukungan platform yang sudah lulus.

## Guard jaringan

Allowlist diatur operator dan berlaku juga pada redirect, varian, fragmen, key serta subtitle. DNS diputuskan sekali lalu koneksi memakai literal yang sudah divalidasi. Alamat privat, metadata, multicast dan mapped IPv6 ditolak. Empat slot request bersama mengikat kapasitas sampai response ditutup dan dilepas ketika worker mati. Tidak tersedia flag bypass untuk localhost dalam produksi; fixture jaringan hanya menggunakan monkeypatch pada tes.

Default allowlist mencakup z3 dan provider lama; CDN lain akan ditolak. Opsi `--allow-host` mengganti daftar default, sehingga operator harus menyebut seluruh host yang telah diverifikasi. Transport menggunakan internal API yt-dlp yang dipatok; upgrade memerlukan pengulangan tes.

FFmpeg downloader diblokir, termasuk fallback internal HLS. Probe stdin dan merge dibatasi protokol lokal serta demuxer media, sehingga playlist yang disamarkan tidak dapat dipakai untuk membuat request jaringan. Mode web hanya memakai native HTTP/HLS/DASH. Proteksi ini tidak diaktifkan pada CLI, sesuai kontrak mempertahankan perilaku CLI.

## Bukti

Suite gabungan menghasilkan **137 passed**, termasuk seluruh 96 tes sebelumnya dan 41 tes baru. Lint dan format lulus. Instalasi editable dengan extra web berhasil dan `pip check` lulus. Smoke test CLI memverifikasi readiness, instance lock serta shutdown. Uvicorn mengembalikan SIGTERM setelah cleanup; exit -15 diterima sesuai perilaku server, bukan dianggap startup gagal.

Tes meliputi transfer HLS dan DASH nyata memakai video sintetis, TLS valid dan hostname salah, redirect, response capacity, process crash/malformed output, parent death, concurrency, serta pengujian bahwa FFmpeg tidak mengambil URL tersembunyi. Semua pengujian jaringan dilakukan lokal, tidak membaca sesi browser dan tidak mengunduh film. Ada satu warning deprecation dari kombinasi Starlette TestClient/httpx; tidak memengaruhi hasil tes.

Versi dependency yang diuji: FastAPI 0.163.1, Uvicorn 0.54.0 dan yt-dlp 2026.10.21. Bukti tidak menyatakan seluruh matriks platform sudah diuji. Akses IDLIX live 1080p tetap belum terbukti dan task T02 belum selesai.
