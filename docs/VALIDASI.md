# Bukti validasi

Pemeriksaan dilakukan pada 22 Oktober 2026 dengan Python 3.12.13, yt-dlp 2026.10.21, dan binary FFmpeg 7.1 dari extra portable. Aplikasi dipasang secara editable pada `.venv` proyek; tidak ada pemasangan FFmpeg global.

## Pemeriksaan lokal

Perintah `python -m pytest -q --tb=short` menghasilkan **62 passed** setelah penambahan batch dan perbaikan manifest tanpa resolusi. `ruff check src tests`, `ruff format --check src tests`, dan kompilasi modul Python juga berhasil. `idlix doctor` menemukan Python, yt-dlp, dan FFmpeg portable.

Tes mencakup seleksi resolusi 720p/1080p/2160p melalui engine yt-dlp asli, prioritas resolusi dibanding bitrate, penggabungan audio terpisah, batas tinggi yang tidak diketahui, validasi input, dan penolakan playlist/live stream. Tes adapter mencakup pemilihan server Dooplay, Referer, countdown sesi Next.js, batas hak akses 720p/1080p, dan kegagalan API.

Tes integrasi membuat video sintetis menggunakan FFmpeg dan menyajikannya melalui HTTP loopback. Transfer HLS, penggabungan video/audio, Referer pada player, HTTP Range untuk resume file MP4, preservasi file selesai, FFmpeg portable, dan penerusan token ke playlist/segmen berhasil diperiksa. Tidak ada film pihak ketiga yang digunakan oleh suite tes.

Tes batch mencakup beberapa URL, file UTF-8 dengan BOM/komentar/baris kosong, penghilangan duplikat persis, penolakan URL tidak valid sebelum akses jaringan, penggunaan satu sesi bersama, ringkasan kegagalan parsial, Ctrl+C, dan kompatibilitas perintah satu URL. Tes transfer batch lokal berhasil mengunduh MP4, menerima kegagalan URL 404, kemudian tetap mengunduh dan menggabungkan HLS film berikutnya.

## Pemeriksaan URL pengguna

Target: `https://z2.idlixku.com/movie/the-last-10-years-2022`.

1. curl awal menerima HTTP 403. Pembacaan memakai sesi HTTP yt-dlp berhasil.
2. HTML dan aset JavaScript publik menunjukkan aplikasi Next.js, endpoint metadata film, dan alur sesi Pentos.
3. API metadata mengembalikan judul **The Last 10 Years** dan `hasVideo: true`.
4. API sesi tamu mengembalikan `maxHeight: 720` dan countdown 15 detik. Adapter menunggu countdown sebelum meminta claim.
5. Provider memberikan URL manifest dengan TTL 3600 detik serta subtitle Indonesia.
6. `idlix info` berhasil menampilkan varian berlabel **360p** dan **720p** dengan batas sesi **720p (guest)**.
7. Uji cuplikan awal menggunakan FFmpeg mengungkap HTTP 401 pada playlist turunan karena query otorisasi master tidak diteruskan. Adapter diperbaiki dan dilengkapi tes regresi lokal.
8. Uji akhir membaca satu segmen dengan `Range: bytes=0-10239` dan batas baca eksplisit `read(10240)`. Transfer berhasil menghasilkan **10.240 byte**. FFmpeg mengenali **H.264 1280×536, 23,98 fps** dan **AAC Jepang, 44.100 Hz, stereo**.

Satu percobaan mode test downloader ditolak pemeriksaan persetujuan otomatis karena batas transfer dinilai belum terbukti. Percobaan itu tidak dijalankan. Verifikasi akhir menggunakan alternatif dengan batas byte eksplisit dan disetujui. Tidak ada unduhan film penuh yang dilakukan.

## Batas bukti

Pemeriksaan tambahan `(500) Days of Summer` menemukan manifest yang hanya menyediakan `BANDWIDTH`, tanpa `NAME` atau `RESOLUTION`. Sebelum perbaikan, semua varian terbuang oleh filter. Setelah pemeriksaan sampel dibatasi 256 KiB per varian, perintah `info` pada sesi tamu berhasil menampilkan dimensi 640×266 dan 1280×534. Cookies Safari tidak dibaca dalam pemeriksaan tambahan ini. Tes regresi membuktikan varian berbasis bitrate tetap diproses, batas baca diterapkan, parameter otorisasi diteruskan, dan lebar film yang terpotong vertikal diperhitungkan untuk batas sesi.

Sesi akun Safari pengguna tidak dibaca atau diuji. Screenshot membuktikan film dapat diputar pada sesi browser pengguna, tetapi tidak membuktikan bahwa akun memiliki akses 1080p. Dukungan pembacaan cookies disediakan sebagai opsi yang dijalankan pengguna sendiri.

Belum diuji: penyelesaian film penuh, batch banyak film live, kecepatan CDN selama transfer panjang, subtitle live sampai selesai, resume lintas token sesi, dan pembaruan token otomatis. Keberhasilan 60 tes lokal serta transfer 10.240 byte tidak berarti seluruh kondisi jaringan/provider sudah terverifikasi. Hasil live dapat berubah jika situs memperbarui API atau hak akses akun.
