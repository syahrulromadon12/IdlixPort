# Web Readiness Report

Tanggal pemeriksaan: 17 November 2026. Branch: `web-experimental`.

Laporan ini mencatat keputusan dan bukti fase P0. Implementasi API, UI, transport produksi, dan installer belum dibuat. Status keseluruhan P0 adalah **belum selesai**, karena akses manifest dan kualitas aktual T02 belum terbukti; temuan terbaru z3/Chrome ada di bagian pembaruan di akhir laporan.

## T01 — Matrik rilis lintas platform: selesai sebagai keputusan engineering

Baseline versi OS, CPU, browser/profil, format paket, toolchain dan mode container telah ditetapkan di [Web Platform Matrix](web-platform-matrix.md). Matriks ini menggantikan scope awal macOS Intel. Targetnya Windows 11 24H2, macOS 14 dan Ubuntu 24.04 LTS, masing-masing x86_64/ARM64; container menargetkan linux/amd64 dan linux/arm64. Semua masih merupakan target uji, bukan installer yang sudah terbukti.

Audit metadata PyPI per enam kombinasi berhasil dijalankan melalui `tools/spikes/check_platform_dependencies.py`. PyInstaller 6.22.3 memiliki wheel untuk seluruh target. imageio-ffmpeg 0.6.0 tidak memiliki wheel Windows ARM64; build FFmpeg native menjadi blocker rilis platform tersebut di T23. Tidak menyebut emulasi sebagai native. Uji build/clean install tetap wajib pada setiap kombinasi di T23/T25; T01 mencatat keputusan dan spike ketersediaan input build, bukan membangun semua paket.

Firefox/profil yang dipilih eksplisit menjadi baseline pengujian sesi lintas platform, Safari opsi macOS. UI mengikuti browser default. Bukti pembacaan sesi yang tersedia masih terbatas Safari pada macOS Intel. Guest tetap default; 1080p memerlukan bukti sumber. Validasi media memakai FFmpeg paket, tanpa prasyarat FFprobe terpisah. Runtime Python direncanakan lini 3.12 dengan security patch dikunci saat build.

Podman lokal aktif pada amd64. Container Node Alpine yang sudah tersedia berhasil menjalankan request HEAD ke situs kontrol (HTTP 200), tetapi sumber IDLIX menolak koneksi (ECONNREFUSED). Ini smoke test runtime/koneksi, bukan pengujian aplikasi IdlixPort dalam container. CLI Docker terpasang tetapi daemon/socket tidak aktif; tidak mengubah daemon atau container lain. Container awal guest-only dengan storage persisten; sesi browser host membutuhkan integrasi terpisah.

## T02 — Sesi browser dan sumber: terblokir sebagian

Pembacaan cookies Safari melalui API yt-dlp berhasil pada mesin ini. Hanya status dan jumlah cookies terkait domain yang dihitung dalam memori; nilai cookies, headers, token dan respons sesi mentah tidak dicetak atau disimpan sebagai laporan. Salinan cookie jar dibersihkan setelah pemeriksaan terpisah. Tidak membaca profil Chrome secara otomatis.

Inspeksi guest dan Safari pada halaman `https://z2.idlixku.com/movie/the-last-10-years-2022` sama-sama gagal pada akses sumber. Lookup DNS domain sumber dan domain kontrol berhasil. Pemeriksaan terpisah dengan curl HEAD, timeout 15 detik, menghasilkan exit code 7 dan HTTP status 000. Ini membuktikan kegagalan koneksi dari lingkungan pengujian saat itu; bukan bukti akun ditolak, tier guest, film tidak tersedia, atau domain mati secara global.

Karena sumber belum dapat diakses, belum ada bukti terkini bahwa Safari diterima sebagai akun oleh IDLIX, belum ada maxHeight live, dan belum ada daftar kualitas live. Tidak mengulang ke film kedua melalui host yang sama saat koneksi dasarnya gagal. Bukti lama 720p guest tetap historis. T02 tidak boleh ditandai selesai berdasarkan keberhasilan membaca cookies saja.

Langkah lanjut ketika sumber dapat dijangkau:

1. Pastikan pengguna login pada Safari/profil yang diuji dan membuka film yang sama. Jika domain target berubah, validasi domain baru melalui sumber pengguna sebelum menyesuaikan adapter.
2. Jalankan inspeksi guest dan akun dengan logging tersanitasi, tanpa menulis cookie jar atau metadata sesi mentah ke disk.
3. Catat tanggal, tier yang dinyatakan sumber, batas resolusi, varian dan bukti dimensi. Batasi probe sesuai kebijakan; jangan mengunduh film penuh untuk pemeriksaan ini.
4. Verifikasi paling sedikit dua film jika tersedia agar satu hasil tidak dianggap mewakili katalog. Catat gagal/tidak diketahui apa adanya.
5. Pastikan status akun dapat dibedakan dari guest berdasarkan respons sumber; jika tidak, jangan menyatakan Terhubung.

Pemulihan yang harus disediakan implementasi: izin cookies ditolak menjelaskan izin OS yang diperlukan; profil salah menawarkan pilihan browser/profil; sesi kedaluwarsa membuka login ulang dan memeriksa otomatis; koneksi gagal menawarkan coba lagi tanpa menghapus sesi browser; API berubah dilaporkan sebagai kompatibilitas sumber, bukan langsung dianggap perlu login. Jangan menonaktifkan TLS verification untuk mengatasi kegagalan koneksi.

## T03 — Spike transport: selesai pada batas pembuktian awal

Prototype terisolasi tersedia di `tools/spikes/test_network_spike.py`. Ia tidak diimpor oleh source produksi. Semua 23 tes spike lulus melalui perintah:

```bash
.venv/bin/python -m pytest -q -p no:cacheprovider tools/spikes/test_network_spike.py
```

Bukti yang tersedia:

- Alamat privat, loopback, link-local/metadata, multicast, IPv6 lokal dan mapped IPv6 yang diuji ditolak sebelum socket dibuat.
- Jawaban DNS campuran public/private ditolak. Socket terhubung ke literal IP tervalidasi tanpa lookup kedua sebelum connect.
- Koneksi HTTPS mempertahankan hostname asal untuk SNI/TLS; tes memakai SSL context mock, bukan server TLS nyata.
- Request `YoutubeDL.urlopen` untuk bentuk endpoint metadata, redeem, master/variant playlist, segmen, key dan subtitle melewati guard saat RequestDirector hanya berisi handler prototype.
- Redirect HTTP nyata pada fixture loopback ke alamat privat ditolak. Pemetaan IP public ke fixture loopback hanya terjadi melalui monkeypatch tes.

Temuan implementasi penting: `urllib.request.build_opener` dapat memasukkan HTTP handler bawaan jika handler custom tidak dikenali sebagai penggantinya. Percobaan awal menunjukkan koneksi melewati guard. Prototype diperbaiki memakai OpenerDirector dan daftar handler eksplisit. Factory koneksi yt-dlp juga dapat menimpa `_create_connection`; prototype memasang factory sendiri. Jangan memperbaiki ini dengan monkeypatch socket global di aplikasi produksi.

Peta request pada kode saat ini: metadata film, play-info, claim dan redeem memakai API extractor; master/nested manifests dan subtitle lewat fasilitas yt-dlp; probe sampel memakai `downloader.urlopen`; downloader native HTTP/HLS/DASH juga perlu diikat ke RequestDirector yang sama ketika diintegrasikan. Generic extractor dapat menyerahkan pekerjaan ke jalur lain, sedangkan FFmpeg external downloader tidak dijamin melalui handler Python.

Keputusan integrasi T08: satu RequestDirector dengan handler yang dijaga, skema hanya HTTP(S), proxy ditolak, allowlist domain/operator, dan native HTTP/HLS/DASH saja. FFmpeg dibatasi ke probe stdin dan merge file lokal; pemilihan external downloader atau extractor yang dapat melewati jalur ini harus ditolak sebelum transfer. CLI lama tidak berubah. Ini adalah kebijakan integrasi yang masih harus diimplementasikan, bukan kontrol yang sudah aktif sekarang.

Batas bukti: belum menguji rangkaian transfer HLS/DASH penuh di dalam prototype, sertifikat TLS nyata, fallback multi-address, policy allowlist, atau semua kemungkinan backend downloader. Tes URL berbentuk segmen tidak sama dengan membuktikan seluruh transfer native. T08 dan tes integrasi web wajib membuktikan cakupan tersebut sebelum membuka endpoint pengambil URL. Estimasi implementasi guard awal 2–4 hari engineering setelah spike; verifikasi TLS/download dan perubahan provider dapat mengubahnya. Jangan menganggap angka ini sebagai janji rilis.

## T04 — Baseline dan fixture: selesai

Suite lama berisi 62 tes; setelah penambahan fixture P0 terdapat 66 tes aplikasi dan 23 tes spike. Perintah gabungan menghasilkan **89 passed in 7.99s**:

```bash
.venv/bin/python -m pytest -q -p no:cacheprovider tests tools/spikes/test_network_spike.py --tb=short
.venv/bin/ruff check --no-cache src tests tools/spikes
.venv/bin/ruff format --check --no-cache src tests tools/spikes
.venv/bin/idlixport doctor
```

Lint dan pemeriksaan format lulus pada 11 file Python. Doctor menemukan Python 3.12.13, yt-dlp 2026.10.21, dan FFmpeg sistem; FFprobe tidak ditemukan. Tes HTTP lokal dijalankan dengan izin bind loopback. Tidak ada pengunduhan film penuh.

Fixture integrasi menyediakan HLS sintetis 360/720/1080/2160 dengan audio terpisah; tes batas maksimum 480 memilih 360 dan menghasilkan video/audio. MP4 sintetis 1920×800 diperiksa langsung melalui FFmpeg. Extractor generic untuk URL MP4 tidak selalu mengisi dimensi; jangan menganggap metadata inspect direct MP4 selalu lengkap. Aturan probe/crop adapter tetap dibuktikan oleh tes unit yang sudah ada.

Token bernama expired-token dan invalid-token menghasilkan HTTP 401 pada server fixture, tanpa artifact hasil. Ini simulasi deterministik penolakan token, bukan pembuktian renewal otomatis atau expiry berdasarkan jam server. Sumber 404, resume Range, token propagasi HLS, batas entitlement, dan error sumber juga tercakup oleh suite yang sudah ada.

## Keputusan melanjutkan

T01 selesai sebagai keputusan matriks dengan audit input build per kombinasi; packaging runtime tetap gate T23/T25. T03 (spike) dan T04 tetap selesai pada lingkungan yang sudah diuji. T02 masih terblokir pada verifikasi live. Pekerjaan engine berbasis fixture T05/T06 serta fondasi storage dapat dilanjutkan sesuai dependensi; UI akun dan klaim resolusi live tidak boleh dianggap siap. Tidak ada API/launcher yang dijalankan sebagai hasil P0 dan tidak ada commit atau publikasi otomatis.

## Referensi primer

- [yt-dlp authentication options](https://github.com/yt-dlp/yt-dlp#authentication-options): browser/profil dan cookies.
- [Python HTTP client](https://docs.python.org/3.12/library/http.client.html): koneksi HTTPS dan verifikasi hostname.
- [PyInstaller macOS application bundles](https://pyinstaller.org/en/stable/usage.html#building-mac-os-x-app-bundles): arah packaging, bukan bukti installer proyek telah berjalan.

Titik integrasi transport diperiksa terhadap source yt-dlp yang terpasang dan dipatok; memakai API internal memerlukan tes regresi ketika versi dinaikkan.


## Pemeriksaan ulang T02 — koneksi sumber masih gagal

Pemeriksaan lanjutan pada 17 November 2026 menunjukkan koneksi kontrol `https://www.python.org` menerima HTTP 200, sedangkan target IDLIX lama menghasilkan curl exit 7 dan HTTP 000. Percobaan IPv4 eksplisit juga gagal. Podman Linux amd64 memberikan hasil yang sama: kontrol 200, sumber ECONNREFUSED. Dengan demikian masalah tidak dapat dianggap sebagai kegagalan cookies atau kegagalan jaringan umum saja. Penyebab infrastruktur/ISP/provider belum dipastikan; tidak mengganti DNS, menonaktifkan TLS, atau menebak domain login lain.

Domain cookies IDLIX Safari sesuai domain target lama, tetapi kecocokan domain tidak membuktikan login atau penerimaan sesi. Pengguna diminta URL halaman film yang saat ini berhasil dibuka setelah login, tanpa cookies/token. T02 tetap terblokir sampai sumber dapat diakses dan tier/kualitas guest serta akun terverifikasi.

Probe ulang tersedia di `tools/spikes/check_source_session.py`. Secara default hanya guest; pembacaan browser memerlukan dua argumen eksplisit, `--browser safari --consent-browser-read`. Maksimal tiga URL, dua belas varian manifest sebelum probing sampel, timeout socket sepuluh detik, retry dimatikan, logger diam dan output berupa ringkasan tanpa URL media atau token. Pembacaan browser tanpa flag persetujuan diuji ditolak dengan exit 2. Ini script spike single-threaded, bukan endpoint web produksi.

Contoh command dengan URL halaman yang telah diverifikasi pengguna:

```bash
.venv/bin/python tools/spikes/check_source_session.py 'URL_HALAMAN_FILM' --browser safari --consent-browser-read
```

Jangan menjalankan placeholder tersebut sebagai URL aktual. Bukti metadata diterima tidak otomatis menjadi bukti akun; tier guest/tidak dikenal tetap dilabeli belum terbukti. Tidak ada film penuh diunduh, dan belum ada bukti tambahan resolusi IDLIX live.


## Pembaruan T02 — z3 dan Chrome, 17 November 2026

Pengguna memberikan `https://z3.idlixku.com/movie/forget-me-not-2015` dan memilih Chrome; menurut pengguna Safari memblokir akses IDLIX. Hasil z2/Safari sebelumnya tetap historis dan tidak menjadi kesimpulan untuk host baru. Permintaan akses Keychain Chrome muncul dan disetujui pengguna di macOS; tidak ada password atau nilai cookies dimasukkan ke laporan.

Hasil terbaru:

- Host z3 dapat dijangkau. HEAD curl menerima HTTP 403, sementara sesi HTTP extractor dapat membaca metadata dan play-info; hasil HEAD tidak boleh dianggap bukti seluruh alur gagal.
- Tanpa cookies, sumber menyatakan tier guest, `maxHeight=720`, kind gate. Pemeriksaan metadata berikutnya membuktikan field verification aktif. Daftar format guest belum diperoleh karena claim meminta verifikasi pemutaran.
- Dengan Chrome, salah satu probe berhasil menerima sesi pentos dengan `maxHeight=1080` dan viewerTier bukan guest. Ini bukti sumber membedakan konteks sesi Chrome dari kontrol guest; tidak membuktikan manifest 1080p atau transfer penuh.
- Endpoint redeem dari sumber berupa URL relatif. Adapter sebelumnya hanya mengizinkan provider majorplay.net dan menolaknya. Adapter kini menerima HTTPS pada origin halaman yang persis sama, selain provider lama. HTTP, origin asing, port berbeda pada origin halaman, dan kredensial dalam URL tetap ditolak serta diuji.
- Probe Chrome setelah perbaikan kembali ditolak API pada play-info sebelum manifest. Tidak ada kesimpulan bahwa seluruh alur sudah berhasil.

Kontrak player publik diperiksa pada aset JavaScript host yang sama: `196vz2-i834f1.js` dan `1qfhjk592kyr3.js`. Player dapat meminta verifikasi browser, lalu mengirim verificationToken pada claim selain gateToken. Ini konsisten dengan field verification aktif pada probe guest; tidak membuktikan bahwa setiap akun selalu membutuhkan challenge yang sama. Tidak mencoba menyelesaikan CAPTCHA secara otomatis atau menggunakan token hasil pemalsuan.

Adapter sekarang berhenti dengan pesan khusus bila play-info meminta verifikasi browser, sebelum sleep/claim. Cookies saja belum cukup untuk alur tersebut. Pemulihan produk harus membedakan login, izin Keychain, verifikasi pemutaran, perubahan API dan sumber yang tidak dapat dijangkau. Menyelesaikan challenge di situs tidak boleh dijanjikan otomatis membuat backend terhubung sampai hal itu dibuktikan. Bridge browser atau jalur resmi provider memerlukan spike tersendiri dan otorisasi pengguna; tidak mengirim challenge/session token ke server pihak lain.

Hasil regresi setelah dua perbaikan adapter: **96 passed in 10.38s** melalui suite aplikasi dan spike jaringan; lint serta format 13 file Python lulus. Tes tambahan membuktikan redeem relatif diterima, provider/HTTP/kredensial/origin-port asing ditolak, serta verifikasi browser tidak diikuti claim tanpa penanganan yang didukung. Tidak ada film penuh diunduh.

Status T02 tetap parsial: bukti tier dan batas sesi guest/Chrome tersedia, tetapi daftar format, dimensi dan akses manifest aktual belum terverifikasi. Tidak menandai T02 atau P0 selesai berdasarkan batas 1080p saja.

Referensi kontrak yang diperiksa: [API client publik](https://z3.idlixku.com/_next/static/chunks/196vz2-i834f1.js), [player publik](https://z3.idlixku.com/_next/static/chunks/1qfhjk592kyr3.js). Aset ber-hash dapat berubah dan bukan API resmi stabil.
