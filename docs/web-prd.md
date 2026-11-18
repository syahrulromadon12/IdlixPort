# PRD IdlixPort Web

Tanggal rancangan: 16 November 2026. Status 18 November: P1–P4 diimplementasikan, termasuk UI satu unduhan, sesi browser otomatis, cancel, retry/recovery dan riwayat hasil. Lihat [bukti dan panduan P2](web-usage.md); subtitle dan installer masih roadmap. Bukti Safari live tersedia di [Browser Sessions](web-browser-sessions.md).

## 1. Keputusan produk

IdlixPort menjadi aplikasi web lokal/self-hosted untuk memeriksa sumber video, memilih kualitas yang tersedia, mengantrekan unduhan, dan mengambil hasilnya melalui browser. Engine Python yang ada dipertahankan; antarmuka utama berpindah dari terminal ke browser. CLI tetap berfungsi.

Target pengalaman: **buka IdlixPort → tempel URL → periksa → pilih resolusi → unduh → simpan file**. Pengguna tidak perlu membuka atau login ke situs IDLIX untuk alur guest. Mode guest tidak membaca cookies. Pengguna dapat mengaktifkan sesi browser lokal dengan persetujuan eksplisit; aplikasi tidak meminta password IDLIX atau menggunakan akun bersama.

**Target 1080p tanpa login masih bergantung pada sumber.** Implementasi web dapat mengunduh 1080p jika sumber menyediakan stream 1080p yang dapat diakses tanpa autentikasi. Mengubah UI atau memindahkan pengunduhan ke server tidak menambah hak akses sesi. Jika IDLIX hanya memberikan maksimum 720p kepada guest, 1080p dari sumber itu tidak bisa dijadikan janji rilis.

Default deployment pertama: satu pengguna pada komputer sendiri, terikat ke `127.0.0.1`. Repository public tidak berarti server unduh publik. Hosting multiuser di internet adalah scope lanjutan.

## 2. Bukti dan hal yang belum diketahui

| Temuan | Dasar | Implikasi |
|---|---|---|
| Nama paket distribusi dan command sudah `idlixport` | `pyproject.toml` | Branding baru dipakai web |
| Engine memiliki inspect, download, batch, retry, resume, dan FFmpeg | `src/idlix_downloader/downloader.py` | Tidak perlu menulis ulang engine |
| Adapter Next.js mengikuti metadata → play-info → gate → claim → redeem → manifest | `src/idlix_downloader/extractor.py` | Bisa dipakai dari worker web |
| Format difilter sesuai `maxHeight` sesi | `extractor.py`, `tests/test_next_session.py` | Batas akses tetap dipertahankan |
| Pemeriksaan terdokumentasi 22 Oktober 2026 menemukan guest maksimal 720p | `docs/validation-report.md` | Ini bukti historis, bukan hasil live hari ini |
| Selector CLI `1080` berarti maksimum 1080, sehingga bisa menghasilkan 720 | `format_selector()` | Web perlu kontrak pilihan kualitas yang lebih eksplisit |
| Probe dimensi dapat membaca hingga 256 KiB per varian | `_probe_dimensions()` | Tombol periksa tidak berarti nol transfer media |
| Token sesi terbatas; renewal otomatis belum ada | `docs/technical-design.md` | Ekstraksi ulang saat mulai/retry, tidak menyimpan URL sesi untuk antrean |
| Belum ada web/API, job database, cancel terkontrol, atau file endpoint | Inventaris source | Ini pekerjaan baru yang diperlukan |

Belum terbukti: akses IDLIX guest 1080p saat ini, film IDLIX penuh sampai selesai, stabilitas transfer melampaui TTL, dan resume lintas token live. Pembacaan halaman melalui web pada 16 November hanya menampilkan shell/error pemuatan, bukan bukti manifest atau resolusi. Jangan menganggap halaman tersedia berarti 1080p tersedia.

## 3. Sasaran dan ukuran keberhasilan

1. Pengguna umum dapat memasang paket aplikasi dan menjalankan launcher tanpa Python, pip, atau terminal; instalasi source tetap tersedia untuk developer.
2. Pengguna menyelesaikan alur periksa sampai file akhir melalui browser tanpa terminal untuk setiap film.
3. Pilihan kualitas berasal dari hasil inspeksi sumber/sesi, bukan daftar resolusi statis yang seolah tersedia.
4. Sumber uji tanpa login yang memiliki 1080p menghasilkan file 1080p berisi video dan audio, lolos pemeriksaan decode.
5. Sumber guest maksimal 720p tidak menawarkan 1080p sebagai pilihan yang dapat diunduh.
6. Refresh/menutup tab tidak menghentikan unduhan. Restart service mempertahankan riwayat dan file parsial; pengguna dapat retry.
7. CLI dan semua tes lama tetap lulus setelah refactor.

Pisahkan status rilis: **web berfungsi**, **1080p tanpa login pada sumber uji terbukti**, dan **1080p tanpa login pada IDLIX terbukti**. Status terakhir memerlukan pengujian tersendiri pada sesi guest dan film konkret. Dua status pertama tidak membuktikan status ketiga.

## 4. Scope MVP

### Masuk

- Halaman input URL film IDLIX yang didukung; dukungan URL MP4/HLS/DASH langsung dengan kebijakan jaringan yang sama.
- Inspeksi asynchronous: judul, durasi jika tersedia, kualitas, dimensi, audio, subtitle, batas sesi, dan estimasi ukuran jika ada.
- Pilihan satu kualitas yang benar-benar tersedia; `Terbaik tersedia` sebagai opsi eksplisit. Default kualitas tertinggi yang tersedia hingga 1080p, ditampilkan sebagai nilai konkret sebelum submit.
- Satu unduhan aktif; antrean maksimum 20 pekerjaan termasuk probe. Batch maksimal 20 URL, dengan hasil independen per film.
- Progres byte, kecepatan, ETA jika diketahui, serta tahap menunggu sesi, mengunduh, menggabungkan, dan selesai.
- Cancel, retry/resume best effort, riwayat persisten, subtitle id/en, unduh file akhir melalui browser, dan hapus hasil secara eksplisit.
- Pemeriksaan readiness FFmpeg, folder writable, database, dan ruang disk.
- Layout desktop/mobile sederhana, pesan berbahasa Indonesia.

### Ditunda

Katalog/pencarian seluruh IDLIX, player streaming, akun aplikasi, server multiuser publik, Redis/Celery, distributed workers, transcoding/upscale, auto-scan semua mirror, subtitle yang dibakar ke video, dan token renewal otomatis. Engine CLI generic tetap ada; web tidak harus membuka semua extractor generic pada MVP.

## 5. Alur pengguna dan perilaku kualitas

1. Pengguna menempel URL dan memilih **Periksa**. UI langsung menerima ID pekerjaan probe dan menampilkan tahap, bukan request HTTP panjang yang membeku.
2. Setelah probe selesai, UI memperlihatkan informasi sumber dan kartu kualitas. Resolusi yang tidak diketahui tidak diberi label 1080p.
3. UI membedakan **kelas kualitas** (contoh 1080p), **dimensi frame** (contoh 1920×800 karena crop sinema), dan **asal informasi** (label manifest/dimensi/probe). Lebar 1920 saja bukan bukti mutu visual asli atau native 1080p.
4. Jika guest dibatasi 720p, tampilkan “Sesi tanpa login tersedia sampai 720p”. Jika server secara eksplisit menyatakan login diperlukan, jelaskan; jangan menebak penyebab hanya karena 1080p tidak ada.
5. Pengguna memilih kualitas dan subtitle lalu klik **Unduh**. UI menunjukkan pilihan efektif; tidak ada label “1080p” bila yang akan diproses 720p.
6. Worker meminta sesi baru ketika job mulai, lalu memvalidasi ulang kualitas. Jika pilihan tidak lagi tersedia, job gagal dengan `QUALITY_CHANGED` dan tindakan **Periksa ulang**. Tidak ada fallback diam-diam untuk pilihan konkret.
7. Pada `Terbaik tersedia`, kualitas ditentukan kembali saat mulai dan pilihan efektif ditampilkan. Perubahan menjadi kualitas lebih rendah ditandai di riwayat.
8. Selesai berarti transfer, merge, validasi file, dan pencatatan artifact berhasil. Progres transfer 100% belum berarti seluruh job selesai.
9. Dalam mode lokal, hasil disimpan langsung ke folder tujuan dan tersedia aksi **Buka file/Buka folder**, tanpa transfer ulang melalui browser. Endpoint simpan melalui browser disediakan untuk penggunaan jarak jauh di tahap lanjutan.

Batch: semua URL divalidasi sebelum membuat batch; setelah validasi tiap film diperiksa dan memilih kualitasnya sendiri. Kebijakan batch adalah `Terbaik tersedia` atau `Maksimum 1080p` dengan hasil efektif yang jelas. Pilihan konkret 1080p per film menggunakan alur inspeksi; jika tidak tersedia film itu gagal dan film berikutnya tetap diproses.

## 6. Kebutuhan nonfungsional

- UI polling maksimal sekali per detik saat ada pekerjaan aktif; berhenti/backoff ketika idle atau tab tersembunyi. Tidak perlu WebSocket pada MVP.
- Endpoint pembuatan job hanya validasi dan persist, target respons lokal <1 detik di luar kegagalan storage; proses jaringan dilakukan worker.
- RAM tidak mengikuti ukuran film. Streaming file dan downloader tidak memuat seluruh file ke RAM.
- Default satu transfer, delapan fragmen seperti engine saat ini; batas worker/queue tetap diterapkan pada backend.
- Estimasi disk: sediakan sekitar 2,5× ukuran akhir bila diketahui plus cadangan 1 GiB; bila ukuran tidak diketahui periksa ruang awal dan selama transfer. Angka ini kebijakan awal, bukan jaminan semua format.
- Data lokal disimpan di direktori data yang ditentukan operator; jangan hapus hasil otomatis. Tampilkan kapasitas/aksi hapus; file yang sedang dikirim tidak boleh dihapus.
- URL sesi bertoken, cookies, claim, headers rahasia, dan stack trace tidak dikirim ke UI atau log biasa. Input URL bertoken juga diperlakukan sebagai rahasia.
- Karena web menerima URL, validasi skema saja tidak cukup: pencegahan SSRF harus mencakup redirect, DNS, request manifest, segmen, key, subtitle, dan provider.
- Server loopback tetap membutuhkan trusted Host, pemeriksaan Origin/CSRF untuk mutasi, tanpa CORS wildcard, batas request, dan output path yang tidak dikendalikan input pengguna.

## 7. Acceptance criteria

| ID | Skenario | Bukti lulus |
|---|---|---|
| A1 | Install dan start | `/health/ready` sukses; browser membuka halaman |
| A2 | Sumber guest 360/720 | Dua kualitas ditampilkan; 1080 tidak dapat dipilih |
| A3 | Sumber tanpa login 720/1080 | Pilih 1080, hasil video/audio sesuai sumber dan selesai |
| A4 | Format berubah antara inspect/start | `QUALITY_CHANGED`, tidak menjadi 720 diam-diam |
| A5 | `Terbaik tersedia` | Memilih kualitas yang tersedia saat mulai; hasil aktual tercatat |
| A6 | Refresh dan tutup tab | Transfer berlanjut; snapshot job dapat diambil lagi |
| A7 | Cancel ketika download/merge | Seluruh process group berhenti, tidak ada status selesai palsu |
| A8 | Restart service saat transfer | Job menjadi interrupted, file parsial tersimpan, retry meminta sesi baru |
| A9 | Batch satu gagal | Film berikutnya berjalan; ringkasan sesuai hasil per film |
| A10 | Pengambilan hasil | Content-Disposition benar; byte/hash sama; Range diuji bila didukung stack terpilih |
| A11 | URL berbahaya | Privat/loopback/metadata, redirect ke privat, IPv6, dan DNS rebinding ditolak di jalur produksi |
| A12 | Rahasia/path | Token tidak bocor; artifact tidak bisa keluar root/symlink; mutasi lintas origin ditolak |
| A13 | Disk penuh/token kedaluwarsa | Error spesifik; tidak completed; tidak retry tanpa batas |
| A14 | Regresi CLI | Tes lama lulus dan command lama tetap bekerja |
| A15 | Klaim IDLIX guest 1080 | Sesi guest aktual mengizinkan 1080 dan unduhan lengkap diuji; selain itu status belum terbukti/terbatas |

A3 memakai video sintetis atau media yang diizinkan; A15 dicatat terpisah. Tidak melakukan unduhan film penuh sebagai efek samping tes otomatis.

## 8. Gate kelayakan 1080p

Sebelum UI besar dibangun, periksa sampel kecil film target tanpa cookies melalui alur sesi normal. Catat tanggal, URL halaman, tier, batas sesi, varian yang dapat diakses, dimensi, dan hasil probe terbatas tanpa menyimpan token.

- Jika guest diberi 1080p: lanjutkan pengujian transfer pada sumber yang boleh diunduh; jangan menggeneralisasi satu film ke seluruh katalog.
- Jika guest dibatasi 720p: web tetap dapat dibangun, tetapi kebutuhan “IDLIX 1080p tanpa login” berstatus **terblokir oleh sumber**, bukan selesai.
- Jika metadata gagal/berubah: perbaiki kompatibilitas alur publik dahulu; status 1080p tetap tidak diketahui.
- Jika target 1080p wajib sementara IDLIX membatasi guest: perlu keputusan produk untuk memakai sumber 1080p yang tersedia tanpa login atau mengubah syarat autentikasi. Rencana ini tidak mengasumsikan persetujuan perubahan itu.

## 9. Hasil analisis

Layak dibangun sekarang: web tanpa login, pemilihan resolusi, antrean, progres, dan pengambilan file dengan engine yang ada. Belum layak dijanjikan: 1080p IDLIX untuk sesi guest. Implementasi dimulai dari pembuktian sumber dan satu jalur end-to-end, lalu batch dan deployment. Detail tersedia di [rencana implementasi](web-implementation-plan.md).


## 10. Revisi pengalaman pengguna — 17 November 2026

Bagian ini memperjelas dan menggantikan ketentuan sebelumnya yang bertentangan, terutama penundaan sesi browser, instalasi melalui terminal, dan pengambilan ulang hasil lokal.

### Aplikasi dan distribusi

Antarmuka utama adalah web lokal dengan engine Python yang dipakai bersama CLI. Launcher menyalakan backend, membuka browser default setelah readiness berhasil, menangani port terpakai dan instance yang sudah berjalan. Pilih OS rilis pertama sebelum menentukan paket installer; OS lain hanya dinyatakan didukung setelah diuji. Instalasi source untuk developer tidak menjadi ukuran kemudahan pengguna umum. Update dan uninstall harus menjelaskan preservasi pengaturan dan hasil unduhan.

### Sesi browser otomatis

Guest menjadi default. Jika pengguna membutuhkan akses akun, aplikasi menawarkan **Login di IDLIX** dan membuka tab baru. Pembacaan sesi memerlukan persetujuan eksplisit sekali saat fitur diaktifkan serta pemilihan browser/profil; izin OS tetap berlaku. Browser untuk UI dapat berbeda dari browser sumber sesi, sehingga aplikasi harus memastikan tab login dibuka pada browser/profil yang dipilih atau memberikan petunjuk yang jelas.

Tidak ada tombol wajib **Sudah login**. Setelah tab login dibuka, UI menunjukkan **Menunggu login**. Ketika tab IdlixPort kembali aktif, backend lokal membaca ulang cookies dan memverifikasi sesi melalui sumber menggunakan URL film. Selama proses menghubungkan saja, pemeriksaan berkala boleh berjalan dengan jeda minimal 5 detik, maksimal 2 menit; satu pemeriksaan aktif, debounce event focus dan backoff saat gagal. Berhenti setelah berhasil, dibatalkan, timeout, atau fitur dinonaktifkan. Timeout menampilkan tindakan **Coba lagi**, bukan membaca cookies terus-menerus.

Status **Terhubung** memerlukan bukti bahwa sumber menerima sesi akun; keberadaan cookies atau klik tombol login tidak cukup. Jika sumber tidak memberikan bukti yang memadai, status adalah **Sesi belum dapat diverifikasi**. Setelah verifikasi, perbarui pilihan kualitas. Sesi kedaluwarsa menawarkan hubungkan ulang; pembacaan ulang saat memulai job hanya dilakukan dalam cakupan persetujuan aktif. Pengguna dapat memutuskan koneksi; hentikan pemeriksaan dan hapus salinan sesi milik aplikasi, tanpa logout browser. Job aktif memakai sesi yang sedang berjalan; hentikan job tersebut sebelum pemutusan dinyatakan selesai.

Cookies dan token tidak masuk API response, log, repository, atau server pihak lain. Simpan preferensi browser/profil, bukan password. Gunakan sesi dalam memori sejauh memungkinkan. Website remote tidak dapat memakai mekanisme pembacaan cookies lokal ini tanpa komponen penghubung tambahan, di luar MVP.

### Subtitle dan hasil lokal

Default subtitle Indonesia digabung sebagai track yang bisa dinyalakan/dimatikan dalam MKV jika tersedia. Pilihan lain adalah file subtitle terpisah atau tanpa subtitle. Jika Indonesia tidak tersedia, tampilkan pilihan bahasa lain; jangan mengganti bahasa diam-diam. Jika subtitle gagal, jangan menandai permintaan lengkap berhasil: pertahankan video dan tampilkan status hasil parsial serta opsi lanjut tanpa subtitle atau coba ulang.

Penggabungan menggunakan soft subtitle dan remux tanpa encoding ulang video, bukan burn-in. Subtitle yang sudah dibakar pada gambar tidak dapat dimatikan. Kompatibilitas player dijelaskan. Folder tujuan dipilih melalui mekanisme lokal terkontrol, disimpan sebagai preferensi; frontend tidak boleh mengirim path bebas.

### Acceptance tambahan

- Pengguna non-IT pada instalasi bersih dapat memasang, membuka, mengunduh, dan menemukan hasil tanpa terminal; uji dengan pengguna dan catat bantuan yang diperlukan.
- Login di tab lain terdeteksi saat kembali tanpa tombol konfirmasi; browser/profil salah, izin ditolak, timeout, dan sesi kedaluwarsa memiliki tindakan pemulihan.
- Status terhubung tidak muncul untuk cookies invalid atau sesi guest; 1080p hanya ditawarkan jika sumber mengizinkan dan menyediakan formatnya.
- Pemutusan koneksi menghentikan pembacaan sesi dan tidak menghapus cookies browser.
- Satu unduhan aktif tidak menghalangi pemeriksaan film berikutnya: satu slot inspeksi terpisah dengan batas jaringan bersama.
- Subtitle dapat diaktifkan/dimatikan di player uji; pilihan terpisah/tanpa subtitle berfungsi; kegagalan subtitle tidak menjadi keberhasilan palsu.
- Hasil lokal dapat dibuka tanpa mengunduh salinan kedua; pembukaan file/folder hanya memakai artifact atau folder terdaftar.


## 11. Keputusan P0 — 17 November 2026

Target produk adalah Windows, macOS, dan Linux, masing-masing x86_64 dan ARM64 (aarch64). Keenam kombinasi merupakan target engineering, bukan dukungan yang sudah terbukti. Rilis dapat bertahap per kombinasi; paket yang belum lulus gate tidak boleh dilabeli didukung. Python tetap menjadi engine bersama, dengan lapisan platform untuk lifecycle proses, folder, keyring/cookies, dan launcher.

Matriks target distribusi:

| OS | CPU target | Bentuk paket yang direncanakan | Status bukti saat ini |
|---|---|---|---|
| Windows | x86_64 | Installer `.exe` | Belum diuji |
| Windows | ARM64 | Installer native bila toolchain/dependensi lolos spike | Belum diuji; emulasi bukan bukti native |
| macOS | x86_64 (Intel) | `.app` dalam ZIP | Tes Python lokal lulus; bundle belum diuji |
| macOS | ARM64 (Apple Silicon) | `.app` dalam ZIP | Belum diuji; paket terpisah sebelum mempertimbangkan universal binary |
| Linux | x86_64 | Paket portabel `.tar.gz` dengan launcher | Belum diuji |
| Linux | ARM64 (aarch64) | Paket portabel `.tar.gz` dengan launcher | Belum diuji |

Minimum versi OS, baseline distro/glibc Linux, toolchain dan tersedianya binary FFmpeg/runtime untuk setiap kombinasi harus ditetapkan melalui spike packaging sebelum rilis kombinasi itu. Linux tidak berarti semua distro; CPU ARM 32-bit, Windows x86 32-bit, musl dan platform lain di luar matriks belum menjadi target. Tidak mengasumsikan satu build macOS menghasilkan semua paket.

Browser UI mengikuti browser default OS. Sumber sesi dipilih dari browser/profil yang tersedia dan terverifikasi per OS/CPU. Firefox menjadi kandidat baseline lintas platform; Chrome/Chromium/Edge dipertimbangkan sesuai platform, Safari khusus macOS. Ini daftar kandidat pengujian, bukan janji semua browser bekerja di semua kombinasi. Bukti P0 yang sudah ada hanya pembacaan Safari pada macOS Intel; penerimaan sesi oleh IDLIX tetap belum terbukti.

Gate setiap kombinasi: build pada runner yang sesuai, runtime dan FFmpeg yang cocok arsitektur, clean install, launch, download sintetis, subtitle, cancel/process-tree, restart, akses folder, izin sesi browser, upgrade/uninstall dan pemeriksaan keamanan paket. Signing/notarization macOS serta signing/peringatan keamanan Windows harus diuji dan didokumentasikan; paket belum ditandatangani tidak boleh dipromosikan seolah bebas peringatan.

Validasi stream memakai FFmpeg yang dibundel; FFprobe bukan prasyarat terpisah. Scope global ini menggantikan pembatasan macOS Intel sebelumnya.


Bukti dan keterbatasan tercatat di [Web Readiness Report](web-readiness-report.md). Pembacaan cookies Safari berhasil, tetapi verifikasi akun/varian live terblokir koneksi sumber. Keputusan scope tidak berarti dukungan akun atau akses 1080p telah terbukti.


## 12. Matriks platform dan mode container

Baseline engineering final mengikuti [Web Platform Matrix](web-platform-matrix.md): Windows 11 24H2, macOS 14 dan Ubuntu 24.04 LTS pada x86_64/ARM64. Paket container menargetkan linux/amd64 dan linux/arm64. Keputusan ini menggantikan baseline yang sebelumnya belum ditetapkan, tetapi dukungan aktual hanya diumumkan setelah gate tiap paket lulus.

Mode native menargetkan integrasi sesi browser lokal. Container awal menyediakan guest dan volume persisten; tidak menjanjikan pembacaan cookies Safari/Chrome di host. Integrasi akun container memerlukan bridge terpisah. Target guest maupun container tidak menjamin akses 1080p.


## 13. Temuan live z3/Chrome dan verifikasi pemutaran

Pada 17 November 2026, URL pengguna pada z3 mengenali guest dengan batas sesi 720p dan konteks Chrome non-guest dengan batas 1080p. Daftar format aktual belum terbukti. Safari tidak dijadikan syarat karena pengguna melaporkan akses situs diblokir di Safari; bukti ini terbatas lingkungan pengguna, bukan semua instalasi Safari.

Status koneksi produk harus membedakan izin browser, sesi akun dikenali, verifikasi pemutaran diperlukan, dan format siap dipilih. Jangan menawarkan unduh 1080p berdasarkan maxHeight saja. Login di tab baru dan pemeriksaan otomatis tetap alur UX, tetapi tidak menjamin akses media jika situs meminta verifikasi pemutaran terpisah. UI harus memberi status dan tindakan yang jujur, bukan mencoba bypass CAPTCHA atau menampilkan Terhubung penuh sebelum manifest terbaca.

Akses Keychain macOS dapat muncul kembali pada proses baru. Jangan menjanjikan satu klik persetujuan meniadakan izin OS. T17/T18 perlu membuktikan pengelolaan sesi dalam memori selama koneksi aktif agar tidak memulai pembacaan Keychain berulang tanpa kebutuhan; tidak menyimpan password/key dekripsi untuk menghindari dialog. Lihat [hasil live](web-readiness-report.md).
