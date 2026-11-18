# Web Reliability and Queue

Tanggal: 18 November 2026. P3 (T14–T16) selesai dan diverifikasi pada macOS Intel. Dukungan Windows/Linux dan CPU lain masih memerlukan pengujian platform. Tidak ada perubahan pada dukungan sesi browser atau akses IDLIX live.

## Perilaku pengguna

- **Batalkan** tersedia untuk pekerjaan antre/berjalan, termasuk pemeriksaan sumber. Pekerjaan aktif menampilkan **Menghentikan** sampai worker berhenti. Antrean yang belum mulai langsung menjadi **Dibatalkan**.
- **Coba lagi** tersedia untuk attempt gagal, dibatalkan atau terhenti. Setiap klik yang diterima membuat job baru dan mempertahankan riwayat sebelumnya. Maksimum tiga attempt total per rangkaian, termasuk attempt pertama. Tidak ada retry job otomatis; setelah batas tercapai pengguna harus memeriksa sumber kembali untuk pekerjaan baru.
- **Detail unduhan** memuat pilihan awal, kualitas efektif bila selesai, nomor attempt dan ukuran parsial. Pesan `SESSION_EXPIRED`, `DISK_FULL` dan `QUALITY_CHANGED` memberi tindakan pemulihan tanpa menampilkan URL/token provider.
- **Riwayat unduhan** menampilkan sepuluh attempt per halaman, tombol sebelumnya/berikutnya serta muat ulang. Semua pekerjaan aktif tetap terlihat pada daftar utama meskipun ada lebih dari dua puluh pekerjaan terbaru.
- **Hapus hasil** meminta konfirmasi browser sebelum menghapus file selesai/parsial milik attempt tersebut. Riwayat tetap disimpan. File attempt lain dengan nama yang sama tidak tersentuh. Tidak ada pembersihan otomatis.

Command startup tetap `idlixport web`; lihat [Local Web Usage](web-usage.md). Restart server versi lama untuk memuat API/UI baru. Jangan hentikan server selama pekerjaan berjalan kecuali memang ingin menghentikan proses; menutup tab browser tidak menghentikan backend.

## Cancel, shutdown dan recovery

Niat pembatalan dipersist sebelum signal process tree. Pada POSIX, runner mengirim SIGTERM, menunggu maksimum lima detik, kemudian SIGKILL bila leader belum berhenti; proses turunan juga dihentikan. Status cancelled baru ditulis setelah penghentian dan penutupan worker. Windows memakai Job Objects dari fondasi P1; jalur tersebut belum diverifikasi di Windows.

Transaksi SQLite menentukan pemenang race cancel/completed. Jika cancel lebih dahulu diterima, tidak ada artifact yang didaftarkan. Jika commit completed sudah menang, cancel tidak mengubah hasil selesai. Cancel berulang tidak membuat pekerjaan baru. Shutdown mempertahankan file parsial dan menandai pekerjaan yang terputus sebagai interrupted; niat cancel yang sudah menang tetap menghasilkan cancelled.

Startup mengambil instance lock dan menunggu slot worker lama dilepas sebelum recovery. Running/cancelling yang tertinggal menjadi interrupted; queued tetap antre dan diproses setelah startup. Interrupted tidak diunduh ulang tanpa aksi pengguna. Tes parent-death P1 tetap mencakup penghentian descendant saat broker mati.

## Retry dan resume

Retry selalu mengekstrak ulang URL input privat untuk mendapatkan URL media saat ini. Expiry inspeksi tidak menghalangi retry job yang sebelumnya sudah diterima. Pilihan kualitas konkret tetap dicocokkan secara semantik; perubahan codec/dimensi atau pilihan ambigu menghasilkan `QUALITY_CHANGED` tanpa fallback tersembunyi.

MP4 progresif melalui HTTP(S) dapat memakai parsial attempt langsung sebelumnya bila seluruh bukti berikut cocok:

1. Satu stream muxed MP4, layout/kualitas semantik sama.
2. Scheme, host/port dan path media sama; query token boleh berubah.
3. Respons Range 206 menyediakan total byte dan ETag kuat yang sama.
4. Panjang file lama masih parsial dan prefiks byte cocok dengan sampel sumber saat ini.

Sidecar `.resume.json` menyimpan fingerprint layout, hash identitas URL tanpa query, hash ETag dan ukuran. Tidak menyimpan URL media, cookies atau token. Header If-Range memakai ETag respons terbaru agar perubahan representasi sesudah probe meminta transfer penuh. Parsial disalin ke folder attempt baru; kegagalan copy membersihkan kandidat baru dan tidak mengubah file lama. Resume tetap best effort dan bergantung pada perilaku server.

Tanpa bukti yang cukup, unduhan dimulai dari awal. HLS/DASH selalu memakai direktori attempt baru dan tidak menggunakan ulang fragmen lama; URL bertoken diekstrak ulang. Ini menghindari pencampuran urutan fragmen, key atau media init yang berubah. Parsial sebelum P3 yang tidak memiliki sidecar juga mulai dari awal. Penyimpanan memerlukan ruang untuk parsial lama dan attempt baru; pengguna dapat menghapus hasil lama setelah tidak dipakai retry aktif.

Retry dan idempotency dijalankan dalam transaksi yang sama dengan pemeriksaan state/kapasitas. Satu attempt hanya memiliki satu penerus; klik ganda dengan Idempotency-Key sama mengembalikan ID yang sama. Retry attempt lama setelah penerusnya dibuat ditolak. Batas antrean tetap dua puluh, satu slot transfer dan satu slot inspeksi.

## Riwayat dan lifecycle file

`GET /api/history` menerima offset dan limit (1–50, default 20); UI memakai sepuluh. Pagination memuat pekerjaan download, sedangkan `/api/jobs` menyatukan seluruh pekerjaan aktif dan dua puluh pekerjaan terbaru. Detail menggunakan snapshot publik; payload sumber tetap privat. Riwayat dipertahankan dalam SQLite setelah restart.

`DELETE /api/jobs/{id}/files` hanya menghapus output dari direktori UUID attempt terminal. Operasi ditolak jika masih ada lease pengiriman browser, retry aktif yang menggunakan parsial itu, atau entri symlink/direktori tak terduga. Retry acceptance dan penghapusan diserialkan dengan lock artifact. Penghapusan individual artifact P2 tetap tersedia dan menolak file yang sedang dikirim. Pemeriksaan aplikasi mencakup worker dan lease HTTP; aplikasi tidak melacak lifecycle player eksternal setelah OS membukanya.

Folder tidak terdaftar atau output hilang ditampilkan sebagai tidak tersedia. Metadata/kualitas riwayat tetap ada setelah file dihapus. SQLite memakai tabel additive `attempts` di schema 1 untuk lineage dan nomor attempt; kolom/tabel P1–P2 tidak diubah. Checkout lama dapat membaca database itu, tetapi tidak memahami pembatasan retry baru. Gunakan backup SQLite API atau shutdown bersih sebelum rollback; file hasil tidak otomatis dihapus.

## Endpoint P3

| Endpoint | Fungsi |
| --- | --- |
| `POST /api/jobs/{id}/cancel` | Persist cancel dan kembalikan snapshot |
| `POST /api/jobs/{id}/retry` | Buat attempt baru, 202; mendukung Idempotency-Key |
| `GET /api/history?offset=0&limit=10` | Riwayat download, total dan next_offset |
| `DELETE /api/jobs/{id}/files` | Hapus hasil/parsial terminal, pertahankan riwayat |

Seluruh mutasi mengikuti guard Origin/CSRF P1; tidak menerima URL pengganti, selector, command atau path output bebas. Retry juga memakai allowlist dan transport jaringan yang sama.

## Bukti dan batas

Regresi gabungan: **166 passed, 2 skipped**. Dua tes browser opt-in dijalankan terpisah dan **2 passed**, Chrome headless desktop 1440×1000 serta viewport mobile 390×844. Lint, format dan syntax JavaScript lulus. Setelah penguatan hash ETag/pembersihan copy, lima tes resume juga dijalankan kembali dan lulus.

Bukti backend meliputi race cancel/completed berulang, cancel saat downloading/merging dengan descendant, worker yang mengabaikan SIGTERM dan dibunuh setelah grace lima detik, restart runner dengan parsial/antrean, retry concurrent/idempotent, batas tiga attempt, expiry inspeksi, penghapusan saat lease/retry aktif, isolasi nama file, MP4 Range dengan checksum sama, ETag berubah, HLS token baru, kualitas berubah dan sumber hilang.

Bukti browser mencakup cancel queued, retry sampai selesai, pagination maju/mundur, konfirmasi hapus yang dibatalkan lalu disetujui, hasil attempt lain tetap tersedia dan refresh tab. Skenario P2 (resolusi, progres, buka lokal, salinan browser, offline/recovery, expiry dan XSS) tetap lulus. Screenshot desktop/mobile diperiksa. Uji ini memakai media sintetis dan profil Chrome sementara; tidak membaca Keychain atau cookies pengguna.

Ada satu warning deprecation Starlette TestClient/httpx yang tidak menggagalkan tes. Catatan ini mencatat gate P3. Sesi browser P4 dan bukti Safari live terbaru tersedia di [Browser Sessions](web-browser-sessions.md); subtitle, batch, installer dan penerimaan pengguna umum masih P5–P6. Format guest dan film live penuh tetap belum selesai.
