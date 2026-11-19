# Web Subtitles and Batch

Tanggal: 18 November 2026. Status: implementasi P5 tersedia pada branch `web-experimental`. Bukti lokal memakai macOS Intel, FFmpeg dan Chrome dengan profil pengujian terpisah. Installer dan platform lain belum diverifikasi.

## Subtitle satu film

Periksa tautan film terlebih dahulu. Pilihan bahasa berasal dari metadata sumber; URL subtitle tidak dikirim ke UI. Jika tersedia bahasa Indonesia, default adalah soft subtitle Indonesia dalam MKV. Varian bahasa Indonesia juga dikenali. Jika tidak tersedia, default tanpa subtitle; pengguna dapat memilih bahasa lain secara eksplisit.

- **Tanpa subtitle:** simpan video dan audio saja.
- **File terpisah:** simpan video beserta SRT dengan basename yang sama, misalnya `media.mp4` dan `media.id.srt`.
- **Gabungkan dalam MKV:** FFmpeg menyalin stream video/audio tanpa encoding ulang dan menambahkan track subtitle dengan label bahasa serta disposition default. Subtitle bukan burn-in; player yang mendukung MKV dapat menonaktifkan track tersebut.

Sumber teks SRT, VTT dan ASS didukung dan dinormalisasi menjadi SRT. Setiap unduhan subtitle dibatasi 2 MiB dan melewati transport jaringan yang sama dengan media. FFmpeg hanya membaca input lokal dengan protokol/demuxer terbatas. Bahasa yang diminta tetapi hilang saat download menjadi kegagalan subtitle, bukan diganti diam-diam.

## Video tersimpan, subtitle gagal

Status **Hasil parsial** berarti video valid tersedia tetapi permintaan subtitle belum terpenuhi. Pengguna dapat mengambil video yang tersimpan, **Coba lagi**, atau **Lanjut tanpa subtitle**. Aplikasi tidak otomatis menandainya selesai.

Retry membuat attempt baru, memeriksa sumber/sesi kembali, menyalin video yang sudah divalidasi, kemudian mengulang subtitle tanpa mengunduh ulang stream video. Identitas sumber dan kualitas video harus sesuai; video asal tetap tersedia. Batas tiga attempt tetap berlaku. Pembersihan tidak menghapus video yang sedang atau akan dipakai recovery. Jika subtitle bertautan sementara sudah kedaluwarsa, retry mengambil metadata sumber yang baru.

Lanjut tanpa subtitle adalah persetujuan eksplisit atas video lokal. Tidak membutuhkan login atau request jaringan baru. Keputusan tercatat dan aman diulang.

## Batch

Buka **Unduh banyak film**, tempel satu URL per baris, pilih folder, kualitas dan subtitle, kemudian **Mulai batch**.

- Maksimum 20 baris URL sebelum deduplikasi; URL identik hanya menjadi satu film.
- Semua URL dan kebijakan host divalidasi sebelum penerimaan. Jika invalid atau kapasitas tidak cukup, seluruh batch ditolak tanpa enqueue sebagian.
- Kapasitas antrean 20 menghitung child jobs aktif. Parent batch disimpan terpisah dan tidak memakai slot unduhan. Penerimaan berlangsung dalam transaksi SQLite agar request bersamaan tidak melampaui kapasitas.
- **Maksimum 1080p** dan **Terbaik tersedia** dihitung ulang per film ketika gilirannya dimulai. Media URL dan sesi tidak digunakan ulang dari film sebelumnya.
- Default subtitle batch adalah Indonesia dalam MKV jika tersedia, selain itu tanpa subtitle. Pilihan bahasa eksplisit yang tidak tersedia menghasilkan video parsial.
- Satu film gagal tidak menghentikan film berikutnya. Ringkasan menampilkan jumlah antre, berjalan, selesai, parsial, gagal dan batal. Detail film menyediakan retry; tombol **Muat ulang film** menyegarkan detail yang terbuka.
- Cancel batch membatalkan child jobs yang belum selesai. Retry per film memperbarui attempt aktif pada posisi film yang sama. Ringkasan dan pemetaan tetap tersedia setelah restart.

Sesi browser tetap opt-in. Untuk memulai login dengan form batch saja, aplikasi memakai URL pertama yang tidak kosong. Sesi hanya dipakai untuk host sumber yang sesuai; film dari host lain tidak menerima cookie tersebut.

## Kontrak dan penyimpanan

`POST /api/batches` menerima `urls`, `policy`, `folder_id`, `subtitle_mode` dan opsional `subtitle_language`. Pilihan subtitle download satu film memakai field yang sama. Mode adalah `auto`, `off`, `separate` atau `embed`. `GET /api/batches` menyediakan ringkasan; detail dan cancel tersedia pada `/api/batches/{id}`. `POST /api/jobs/{id}/continue-without-subtitles` menerima video parsial. Mutasi tetap memakai CSRF dan idempotency yang sesuai.

Batas body batch 192 KiB; endpoint lain tetap 16 KiB. Tabel batch dan pemetaan attempt ditambahkan secara additive pada schema versi 1. Job lama tanpa konfigurasi subtitle mempertahankan perilaku tanpa subtitle. Payload privat dan URL media/subtitle tidak dipublikasikan oleh snapshot job.

## Bukti dan batas verifikasi

Tes integrasi menggunakan media sintetis dan server fixture lokal: remux MKV, bahasa/default track, ekstraksi subtitle, kesamaan hash packet video/audio sebelum-sesudah remux, SRT terpisah, mode off, batas ukuran, URL privat ditolak, subtitle invalid, retry tanpa request segmen video, persetujuan tanpa subtitle tanpa jaringan, enqueue atomik, kapasitas bersamaan, deduplikasi, idempotency, cancel, retry dan persistensi parent/child.

Uji browser desktop/mobile menjalankan pilihan subtitle, hasil parsial, retry, persetujuan tanpa subtitle, batch dengan satu film gagal, cancel dan refresh. Bukti soft track menggunakan pemeriksaan/demux FFmpeg dan decoding video/audio dengan subtitle dinonaktifkan (`-sn`); tampilan menu subtitle pada aplikasi player desktop belum diuji secara manual.

Probe Safari live pada URL yang diberikan pengguna menerima akun dan mengembalikan bahasa Indonesia. Subtitle tersebut berhasil diunduh melalui transport terjaga dan dipasang ke video sintetis satu detik; FFmpeg mengenali track `subrip (ind)`. Film penuh tidak diunduh. Hasil ini tidak membuktikan batch film live penuh, guest 1080p, atau dukungan Windows/Linux.

Jalankan verifikasi developer:

```bash
python -m pytest -q tests tools/spikes/test_network_spike.py
IDLIXPORT_BROWSER_TESTS=1 python -m pytest -q tests/test_web_flow.py tests/test_web_session_browser.py tests/test_web_p5_browser.py -k browser
ruff check src tests tools
ruff format --check src tests tools
node --check src/idlix_downloader/web/static/app.js
```

Tes browser membutuhkan Chrome dan Node.js; tes media membutuhkan FFmpeg. Setelah memperbarui source, restart `idlixport web` dan refresh halaman. P6 launcher/distribusi tetap terpisah.

Hasil verifikasi 18 November 2026: suite regresi 210 lulus dan enam tes browser opsional dilewati pada run backend; satu tes tambahan isolasi sesi batch lulus terpisah (211 tes backend unik). Enam skenario browser desktop/mobile lulus, termasuk pengulangan dua skenario setelah perbaikan sinkronisasi harness. Ruff, format, syntax JavaScript, diff whitespace, build wheel serta pencocokan modul/aset wheel lulus. Warning deprecation Starlette/httpx pada test client tetap ada; tidak ada kegagalan tes terkait. Belum di-commit.
