# Web Session Troubleshooting and Fixes

Tanggal: 18 November 2026. Branch: `web-experimental`.

## Masalah dan bukti

Safari dapat memutar film, tetapi aplikasi tetap menunggu login atau menampilkan verifikasi pemutaran. Pengujian langsung menemukan bahwa API pemutaran menolak request dengan HTTP 401 dan pesan `Access token required`. JavaScript publik situs menyertakan `Authorization: Bearer …`, sementara adapter sebelumnya hanya meneruskan cookies. Cookie Safari yang sudah diizinkan ternyata berisi `access_token` yang masih berlaku.

Status login pada halaman browser tidak dengan sendirinya membuktikan request aplikasi sudah terautentikasi. Endpoint akun Safari dapat mengembalikan HTTP 200 dengan user, sementara request pemutaran tanpa Bearer tetap ditolak. Pesan sebelumnya terlalu menyederhanakan masalah sebagai keharusan login atau memutar film lagi.

## Perbaikan

Engine web memakai cookie `access_token` yang berlaku untuk endpoint tersebut sebagai header Bearer. Header hanya ditambahkan untuk HTTPS, origin yang sama dengan halaman film, dan path `/api/`. Provider dan CDN tidak menerima Bearer. Transport membuang Authorization jika redirect berpindah origin, turun ke HTTP, berganti port, atau keluar dari path API. Header tidak menjadi default global, metadata publik, nilai DB, maupun log. Perubahan autentikasi ini khusus engine web; perilaku CLI download tidak diperluas.

Tidak ada pembacaan password, ekspor token ke disk, pemanggilan refresh yang merotasi sesi browser, atau bypass CAPTCHA/verifikasi/entitlement. Token yang sudah kedaluwarsa tetap memerlukan login browser yang sah.

Penolakan sesi eksplisit sekarang menghentikan probe dan mempertahankan kode error, bukan mengulang hingga berubah menjadi timeout umum. Timeout akibat kegagalan lain mempertahankan penyebab terakhir jika tersedia. Badge membedakan koneksi bermasalah, sesi kedaluwarsa, dan kondisi benar-benar disconnected. Banner error inspeksi lama dibersihkan setelah pemeriksaan baru berjalan atau berhasil; error lain seperti download tidak ikut dihapus.

Menjalankan instance kedua pada folder data yang sama kini memberikan pesan terminal yang menjelaskan instance lain masih aktif, sebelum menjalankan Uvicorn. Pemeriksaan tidak membuat lock pada folder baru sebelum repository diinisialisasi. Ini menghindari konflik akibat dua server dan mempertahankan startup data baru.

## Penggunaan setelah pembaruan

1. Hentikan server lama dari terminalnya dengan Ctrl+C.
2. Jalankan `.venv/bin/idlixport web` dari folder proyek. Tidak perlu membuat ulang virtual environment atau memasang ulang editable install untuk perubahan source.
3. Refresh halaman aplikasi. Tempel URL film, pilih profil Safari yang digunakan login, berikan persetujuan, lalu hubungkan sesi.
4. Tunggu status **Terhubung**, kemudian periksa tautan. Jika sesi ditolak, login pada profil yang sama dan pilih **Coba lagi**. Memutar film berulang kali bukan solusi untuk header autentikasi yang hilang.

Jika dependensi web belum dipasang, gunakan `.venv/bin/python -m pip install -e '.[web,portable]'`. `command not found` pada terminal baru dapat dihindari dengan menggunakan path `.venv/bin/idlixport` langsung.

## Verifikasi dan batas bukti

Suite backend/regresi: 224 lulus, enam tes browser opsional dilewati pada run backend. Pengujian terarah meliputi scope Bearer, guest tanpa Authorization, redirect tanpa kebocoran header, sesi ditolak berhenti dengan error asli, dan instance kedua tanpa traceback startup. Ruff, format, syntax JavaScript dan diff whitespace lulus.

Probe live Safari pada film yang diberikan pengguna berhasil: `account_verified=true`, `media_ready=true`, `max_height=1080`. UI Safari pada instance terisolasi port 8766 dengan data sementara menampilkan **Terhubung** setelah tombol koneksi ditekan. Inspeksi dari UI Safari kemudian berhasil menampilkan pilihan 360p, 720p dan 1080p. Percobaan inspeksi pertama masih ditolak sumber; retry melalui UI berhasil. Dua tes browser sesi desktop/mobile juga lulus. Server pengguna port 8765 tidak dihentikan selama pengujian.

Film penuh tidak diunduh. Bukti ini tidak menutup pengujian guest 1080p, download film live hingga selesai, browser lain, atau platform lain. Klaim live lama adalah bukti historis; kompatibilitas sumber perlu diverifikasi lagi ketika API berubah.

## Unduhan ditolak meskipun inspeksi berhasil

Audit lanjutan pada film The Intern menemukan `NETWORK_BLOCKED` saat memulai download. Sumber merujuk CDN grup g7 pada empat domain, sedangkan default sebelumnya hanya mengenali CDN g2. Ini merupakan ketidaklengkapan daftar host bawaan, bukan kegagalan login.

Empat host yang benar-benar teramati adalah `g7.aspireheightsacademy.digital`, `g7.akademivo.website`, `g7.edubambu.store` dan `g7.horizonacademy.site`. Masing-masing diverifikasi melalui resolusi IP publik dan TLS dengan verifikasi sertifikat sebelum ditambahkan secara exact. Tidak ada wildcard baru; pemeriksaan IP, redirect, port, dan perlindungan Bearer tetap berlaku. Tes menolak subdomain liar, domain tiruan, dan grup g999 yang tidak terdaftar.

Sebanyak 45 tes kontrak browser/backend, transport dan autentikasi lulus pada audit CDN ini. Server yang sudah berjalan harus direstart karena konfigurasi allowlist tersimpan saat startup. Jika operator memakai `--allow-host`, opsi tersebut mengganti seluruh daftar default dan perlu diperbarui tersendiri. CDN lain yang belum diverifikasi tetap dapat ditolak; jangan menonaktifkan guard untuk mengatasinya.

Bukti transfer terbatas: engine mencapai fase `downloading` dan melaporkan 4.464.561 byte progres transfer; total pembacaan fixture live sekitar 8 MiB sebelum probe menghentikannya dengan `CANCELLED`. Tidak ada `NETWORK_BLOCKED` setelah penambahan host. Sampel dan data sementara dibersihkan otomatis; film penuh tidak diunduh.

## Koreksi audit rotasi CDN

Probe pertama sekitar 8 MiB belum mencakup pergantian CDN berikutnya. Transfer lanjutan gagal setelah sekitar 12 MiB karena manifest merotasi segmen melewati 30 hostname g7, bukan hanya empat host awal. Audit seluruh manifest video dan audio mengidentifikasi 2.654 URI segmen/key/map. URL pada komentar manifest tidak dimasukkan ke allowlist.

Seluruh 26 host tambahan lolos resolusi IP publik dan verifikasi sertifikat TLS sebelum ditambahkan secara exact ke `WebConfig.allowed_hosts`. Tidak ada wildcard atau penerimaan host otomatis berdasarkan isi manifest. Daftar lengkap terdapat di `src/idlix_downloader/web/config.py`. Domain tiruan, subdomain tambahan, grup lain yang belum didaftarkan, IP privat dan redirect di luar kebijakan tetap ditolak.

Bukti terbaru: 60 sampel live, satu per host untuk masing-masing stream video/audio, berhasil pada seluruh 30 host. Total data sampel yang dibaca 951.978 byte. Setiap sampel dibatasi 16 KiB; film penuh tidak diunduh. Pengujian HLS sintetis benar-benar melewati 30 host dan menghasilkan artifact 1080p valid sampai selesai. Gate akhir 72 tes transport, kontrak host dan autentikasi lulus; Ruff, format dan diff whitespace lulus.

Setelah memperbarui daftar host, restart server, refresh UI dan hubungkan kembali sesi Safari. Untuk job yang terikat sesi sebelum restart, gunakan **Periksa ulang** lalu buat unduhan dengan hasil inspeksi baru. Jangan menghapus file parsial untuk mengatasi host yang ditolak; HLS tetap mulai ulang sesuai batas resume yang telah didokumentasikan. Perubahan ini mencakup rotasi g7 yang diamati, bukan klaim dukungan setiap CDN atau film penuh.
