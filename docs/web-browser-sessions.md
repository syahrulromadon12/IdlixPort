# Browser Sessions

Tanggal: 18 November 2026. P4 (T17–T19) diimplementasikan dan diverifikasi pada macOS Intel. Safari telah diterima sumber live; pengujian UI memakai Chrome headless dengan profil/data sintetis. Dukungan seluruh kombinasi OS/CPU/browser masih mengikuti gate distribusi, bukan otomatis selesai.

## Menghubungkan sesi

1. Jalankan `idlixport web`, lalu buka `http://127.0.0.1:8765/`. Restart server versi lama dan refresh tab setelah memperbarui source.
2. Tempel URL film IDLIX, lalu buka panel **Sesi IDLIX opsional**. Pilih browser/profil yang dipakai login; untuk Safari standar pilih **Safari · Standar**.
3. Centang persetujuan membaca cookies IDLIX, kemudian pilih **Login di IDLIX**. Password hanya dimasukkan pada situs sumber.
4. Chrome/Edge/Brave mencoba membuka URL login pada profil pilihan. Untuk Safari/Firefox atau saat profil tab tidak dapat dipastikan, panel memberi tautan dan petunjuk manual. Jika UI berada pada browser berbeda, salin alamat lalu buka sendiri pada browser/profil pilihan.
5. Kembali ke tab aplikasi. Tidak ada tombol **Sudah login**. Pemeriksaan berjalan otomatis selama menghubungkan, termasuk setelah focus/visible. Jika sudah login, langkah login di situs tidak perlu diulang.
6. Setelah akun diterima dan format tersedia, status menjadi **Terhubung**; inspeksi lama diperbarui otomatis. Pilih kualitas lalu unduh melalui alur P2. **Putuskan sesi** menghentikan pekerjaan terkait sebelum membuang salinan cookies aplikasi; sesi browser tetap login.

Mode tanpa akun tetap tersedia tanpa membaca browser. Subtitle masih nonaktif sampai T20. Pengguna tidak perlu menjalankan command per film.

## Status dan pemulihan

| Status | Arti |
| --- | --- |
| `disconnected` | Mode tanpa akun; belum ada izin baca browser aktif |
| `connecting` | Memeriksa login/profil dan kesiapan sumber, maksimal dua menit |
| `connected` | Sumber memberikan bukti tier akun yang dikenal dan format dengan audio tersedia |
| `expired` | Sumber menolak konteks akun; pekerjaan terkait dihentikan |
| `error` | Izin, browser, timeout, verifikasi pemutaran atau sumber belum siap |
| `disconnecting` | Menunggu probe/worker terkait berhenti sebelum membersihkan sesi |

Cookies yang terbaca atau `maxHeight=1080` saja tidak menghasilkan status connected. Akun dikenali tetapi media belum siap ditampilkan terpisah. Tier guest/unknown tidak dianggap akun terverifikasi. Verifikasi pemutaran harus diselesaikan pada situs; aplikasi tidak melewatinya.

**Coba lagi** memakai persetujuan aktif tanpa menanyakan ulang, tetapi membuat generasi konteks baru. Izin OS tetap bisa muncul kembali. Jika akses ditolak, periksa izin aplikasi/Terminal yang menjalankan backend pada pengaturan privasi OS, pilih profil yang benar, lalu coba lagi. Tidak diperlukan pengiriman password, cookies, atau key dekripsi ke pengembang.

Setelah konteks berubah, attempt lama yang memerlukan sesi tersebut menawarkan **Periksa ulang**. Ini memakai URL privat yang sudah tersimpan untuk membuat inspeksi baru pada konteks saat ini; tidak menghidupkan kembali konteks yang dicabut. File hasil yang sudah selesai tetap dapat dibuka setelah sesi diputus.

## Batas browser

Katalog memakai lokasi profil standar Chrome, Edge, Brave, Firefox serta penyimpanan cookies Safari standar. UI menerima ID katalog, bukan path bebas. Safari dengan profil terpisah/custom dan semua kombinasi platform lain belum diverifikasi. Profil yang tidak dapat diakses tidak mematikan mode guest.

Browser untuk UI boleh berbeda dari sumber cookies. Pembukaan tab tidak menjamin akun aktif pada profil lain. Pada container/headless, profil host tidak otomatis tersedia dan fitur ini tidak menjadi bridge browser host; distribusi/bridge tetap P6. Chrome Windows dengan enkripsi yang tidak didukung atau keyring Linux yang tidak tersedia dilaporkan sebagai kegagalan baca, tanpa menyimpan password/key untuk mengakalinya.

## Penyimpanan dan penjadwalan

Persetujuan berlaku selama fitur sesi diaktifkan dalam proses aplikasi. Preferensi disk hanya berisi ID profil. Restart selalu kembali disconnected dan meminta opt-in baru, bukan menganggap sesi valid dari preferensi.

Cookies hanya berada pada memori aplikasi/worker. Pembaca SQLite Chrome/Firefox menggunakan snapshot `:memory:` dan memfilter domain sumber sebelum dekripsi; tidak memakai salinan database sementara di disk bawaan yt-dlp. Safari dibaca dari penyimpanan browser, kemudian hanya cookies yang sesuai domain/masa berlaku yang diteruskan ke konteks aplikasi. Cookie host-only tidak diekspor untuk subdomain lain. Database browser asli tidak diubah.

Cookies dikirim ke worker melalui pipe privat yang dibatasi 64 KiB, bukan argumen proses, database job, browser UI atau log. Referensi payload dan hasil probe dibersihkan agar scheduler tidak mempertahankan salinan tersembunyi setelah pemutusan. Job menyimpan ID konteks; setiap mulai/retry tetap melakukan ekstraksi sumber baru dan mematuhi entitlement saat itu. Cookies tidak direfresh tanpa batas setelah terhubung; jika sumber menolak saat mulai/transfer, sesi menjadi expired dan pekerjaan terkait dihentikan.

Hanya satu probe sesi berjalan. Jarak awal minimal lima detik, backoff sampai tiga puluh detik, dan tidak memulai probe baru setelah dua menit. Focus/visibility tidak melewati debounce. Setelah berhasil, error terminal, pemutusan atau timeout, pembacaan/probe login berhenti; polling status UI tidak membaca cookies. Pembacaan ulang hanya ketika data browser berubah atau percobaan eksplisit memerlukannya. Untuk Chromium dengan schema yang dikenali, fingerprint hanya memeriksa baris domain IDLIX tanpa dekripsi; aktivitas situs lain tidak memicu Keychain ulang. Fallback metadata file tetap dapat memerlukan izin OS lagi.

Pemutusan menutup akses konteks sebelum mencari/membatalkan job, menghentikan probe/process tree, menunggu job terminal, lalu menghapus cookies. Hasil probe lama tidak dapat menyambung kembali sesi yang sudah diputus. Riwayat, file selesai dan cookies browser tidak dihapus.

## Perbaikan integrasi sumber

Source saat ini mengembalikan `redeemUrl=/watch/session/redeem`, yaitu path relatif terhadap basis API. Adapter sebelumnya memanggil root halaman dan memperoleh HTTP 404. Normalisasi khusus mengarahkannya ke `/api/watch/session/redeem`; URL `/api/...` dan provider absolut yang sudah didukung tetap mengikuti validasi origin sebelumnya. Query dipertahankan; origin asing, kredensial dalam URL, HTTP provider dan port lain tetap ditolak.

Manifest live merujuk CDN `g2.akademivo.website`. Host persis tersebut diverifikasi dengan transport DNS/IP/TLS terbatasi dan ditambahkan ke default allowlist; subdomain lain tidak otomatis diizinkan. Alamat privat/rebinding/redirect tetap ditolak. `--allow-host` tetap mengganti seluruh default, bukan menambahkannya otomatis.

Fragmen HTTP 401/403 pada transfer akun menghasilkan `SESSION_EXPIRED`. Fragmen yang gagal tidak boleh dilewati lalu menghasilkan completed palsu. Shutdown juga merekonsiliasi state interrupted setelah seluruh thread/process dan lock worker selesai jika commit terminal sebelumnya gagal.

## Bukti live Safari

Sampel pengguna: `https://z3.idlixku.com/movie/forget-me-not-2015?play=1` pada 18 November 2026.

- Akun Safari diterima sumber. Probe produksi mencapai media-ready dengan batas sesi 1080p dan pilihan kelas 1080p.
- Sampel media maksimal 256 KiB menghasilkan frame **1920×804**. Ini kelas 1080p dari sumber dengan crop sinema, bukan klaim tinggi frame 1080 piksel atau upscale aplikasi.
- Alur API produksi diuji: consent Safari, connected, inspeksi worker selesai dengan pilihan 1080p, kemudian disconnect dan cookies aplikasi kosong.
- Guest melaporkan batas 720p dan meminta verifikasi pemutaran. Daftar format/frame guest aktual belum terbaca; T02 tetap parsial untuk bagian itu. Ini bukan bukti 1080p tanpa login.
- Tidak ada film pihak ketiga penuh yang diunduh. Transfer lengkap, merge dan validasi audio/video tetap dibuktikan melalui media sintetis; ketahanan film live sampai selesai masih gate berikutnya.

## API tambahan

| Endpoint | Fungsi |
| --- | --- |
| `GET /api/browser-session` | Status, katalog profil, batas sesi dan petunjuk; tanpa cookies |
| `POST /api/browser-session/connect` | Terima profile_id, URL film dan consent eksplisit |
| `POST /api/browser-session/probe` | Wake/debounce setelah kembali ke tab; tidak memaksa probe paralel |
| `POST /api/browser-session/retry` | Ulangi pemeriksaan dengan grant aktif dan generasi baru |
| `DELETE /api/browser-session` | Revoke konteks, hentikan job/probe, bersihkan salinan sesi |

`GET /api/session` tetap khusus bootstrap CSRF. Mutasi sesi browser memakai Origin/CSRF yang sama; path cookies, password, tokens atau konfigurasi command tidak diterima dari UI.

## Pengujian

Gate backend/regresi: **195 passed, 4 skipped**; empat tes browser opt-in dijalankan terpisah dan **4 passed**. Dua alur P2/P3 serta dua alur sesi P4 mencakup desktop 1440×1000 dan viewport mobile 390×844 pada Chrome headless; ini tidak menggantikan uji rendering Safari atau ponsel fisik. Screenshot diperiksa.

Bukti mencakup opt-in/CSRF, profil salah, izin ditolak, timeout, focus berulang, pembacaan hanya saat berubah, fingerprint domain Chromium, snapshot SQLite RAM tanpa salinan disk, scope cookie, generasi baru, restart tanpa auto-consent, invalidasi inspeksi, perubahan entitlement, revoke saat antre/transfer, pemutusan bersamaan job berjalan, pembersihan cookies serta pesan verifikasi yang jujur. Regresi URL API-relative, host CDN persis dan DNS privat juga tersedia.

```bash
python -m pytest -q -p no:cacheprovider tests tools/spikes/test_network_spike.py
IDLIXPORT_BROWSER_TESTS=1 python -m pytest -q -p no:cacheprovider tests/test_web_flow.py tests/test_web_session_browser.py -k browser
ruff check --no-cache src tests tools
ruff format --check --no-cache src tests tools
```

Harness browser membutuhkan Node 22+ dan Chrome; `CHROME_BIN` dapat menunjuk executable lain. Profil/data browser sintetis dipisahkan dari pengguna. Ada satu warning deprecation Starlette TestClient/httpx yang tidak menggagalkan tes. Integrasi memakai API internal yt-dlp yang dipatok; upgrade harus menjalankan ulang tes, tanpa fallback diam-diam ke salinan cookies disk.

## Pembaruan kompatibilitas autentikasi

Audit 18 November 2026 menemukan API pemutaran meminta Bearer dari cookie access token. Perbaikan scope header, redirect, penanganan sesi ditolak, startup instance ganda dan bukti live terbaru tersedia di [Web Session Troubleshooting](web-session-troubleshooting.md). Login atau playback browser saja tidak membuktikan adapter telah mengirim autentikasi yang benar.
