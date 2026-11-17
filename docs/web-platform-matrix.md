# Web Platform Matrix

Tanggal keputusan: 17 November 2026. Scope: T01, keputusan target engineering dan audit dependency awal. Dokumen ini tidak menyatakan bahwa installer atau aplikasi web sudah berfungsi pada semua platform.

## Baseline yang ditetapkan

| Target | Baseline clean-machine untuk verifikasi | Toolchain yang direncanakan | Paket | Browser sumber sesi awal |
|---|---|---|---|---|
| Windows x86_64 | Windows 11 24H2 | CPython 3.12, PyInstaller 6.22.3 pada runner Windows x86_64 | Installer `.exe` berisi bundle onedir | Firefox, profil default eksplisit |
| Windows ARM64 | Windows 11 24H2 ARM64 | CPython 3.12 ARM64, PyInstaller 6.22.3 pada runner native ARM64 | Installer `.exe` berisi bundle native | Firefox ARM64, profil default eksplisit |
| macOS x86_64 | macOS 14 | CPython 3.12, PyInstaller 6.22.3 pada runner Intel dengan baseline yang ditetapkan | `.app` dalam ZIP | Firefox; Safari sebagai opsi macOS |
| macOS ARM64 | macOS 14 | CPython 3.12 ARM64, PyInstaller 6.22.3 pada runner Apple Silicon | `.app` dalam ZIP | Firefox ARM64; Safari sebagai opsi macOS |
| Linux x86_64 | Ubuntu 24.04 LTS, glibc | CPython 3.12, PyInstaller 6.22.3 pada runner Ubuntu x86_64 | `.tar.gz` dengan launcher | Firefox, profil default eksplisit |
| Linux ARM64 | Ubuntu 24.04 LTS ARM64, glibc | CPython 3.12 ARM64, PyInstaller 6.22.3 pada runner Ubuntu ARM64 | `.tar.gz` dengan launcher | Firefox ARM64, profil default eksplisit |

Versi OS di atas adalah baseline kontraktual untuk pengujian rilis, bukan hasil uji kompatibilitas. Rilis pada versi lebih baru juga memerlukan smoke test. Distro Linux lain, musl, ARM32, Windows x86 32-bit dan macOS universal2 tidak termasuk gate awal. Packaging Linux harus mencatat versi glibc aktual dari builder dan menguji binary pada baseline tersebut; tidak menebak kompatibilitas berdasarkan nama distro saja.

Python 3.12 adalah lini runtime yang dipilih agar sejalan dengan engine awal. Security patch tepatnya dikunci saat build; jangan memakai installer Python lama hanya karena lebih mudah diperoleh. Source CLI tetap mengikuti `requires-python` yang ada; keputusan ini tidak mengubah dependency proyek atau menjamin semua versi source lulus.

Browser Firefox adalah target uji lintas platform, bukan browser yang diwajibkan untuk UI. UI membuka browser default. Nama profil yang sebenarnya harus dipilih dari hasil deteksi, bukan mengasumsikan direktori bernama Default. Safari yang telah berhasil dibaca pada mesin P0 merupakan bukti terbatas macOS Intel, bukan bukti Firefox atau akses akun IDLIX. Profil Firefox Snap/Flatpak dan integrasi izin/keyring masuk pengujian T02/T23; kegagalan harus memberi petunjuk tanpa mengakses profil lain secara otomatis.

## Audit per kombinasi

Audit metadata resmi PyPI dijalankan melalui:

```bash
.venv/bin/python tools/spikes/check_platform_dependencies.py
```

- PyInstaller 6.22.3 menyediakan wheel yang cocok dengan seluruh enam target; macOS memakai wheel universal2. Wheel bukan bukti bundle berhasil dijalankan.
- `imageio-ffmpeg==0.6.0` yang dipatok proyek menyediakan wheel FFmpeg untuk Windows x86_64, macOS x86_64/ARM64 dan Linux x86_64/ARM64.
- Versi tersebut **tidak menyediakan wheel Windows ARM64**. Paket Windows ARM64 harus membundel build FFmpeg native yang terverifikasi atau menyiapkan build source native pada T23. Jangan menyebut sdist sebagai binary tersedia atau menggunakan emulasi x86_64 sebagai bukti native.
- Pemeriksaan dependency ini merupakan spike ketersediaan input build per kombinasi. Tidak ada runner Windows/Apple Silicon yang dipakai pada pekerjaan ini. Build, signing, launch, clean install serta sesi browser per kombinasi tetap gate T23/T25.

Sumber binary FFmpeg Windows ARM64, checksum dan lisensi distribusinya belum diselesaikan. Ini blocker paket platform tersebut, bukan alasan menghapus target ARM64 atau menyebut T01 belum menetapkan matriks. Jika build native tidak lolos, rilis kombinasi itu ditunda dengan status eksplisit.

## Mode container

Target OCI image adalah `linux/amd64` dan `linux/arm64`, dengan baseline image berbasis glibc, Python 3.12 dan FFmpeg native dari dependency build yang dipatok. Docker dan Podman adalah runtime sasaran; CLI terpasang tidak berarti daemon aktif. Versi minimum runtime ditetapkan pada uji packaging container sebelum publikasi image. Runtime OCI lain tidak diklaim kompatibel sebelum diuji.

Frontend dan API sama dengan mode native, tetapi kemampuan host berbeda. Backend bind pada interface container yang diperlukan, sementara publikasi port lokal dibatasi ke `127.0.0.1`; jangan memakai bind loopback di dalam container yang membuat published port tidak terjangkau. Data SQLite dan hasil memakai volume/bind mount persisten dengan izin nonroot. Bind mount menunjuk host daemon, bukan komputer browser yang membuka UI. Mode remote menggunakan endpoint pengambilan artifact, bukan membuka folder desktop klien.

Mode container awal menggunakan guest. Sesi browser host tidak otomatis tersedia di container; pembacaan profile host, keyring atau password manager tidak diatasi dengan mount seluruh home directory. Integrasi akun memerlukan bridge terpisah yang diotorisasi dan diuji, di luar gate container awal. Jangan menjanjikan login otomatis yang sama dengan native.

Rilis container diuji pada native amd64/arm64. Build/emulasi QEMU boleh membantu engineering tetapi tidak menggantikan acceptance native. Docker Desktop/Podman machine pada Windows/macOS memakai lingkungan Linux/VM; ini bukan Windows/macOS native bundle.

## Batas selesai dan task lanjutan

T01 selesai ketika baseline, format, browser target, jalur toolchain dan risiko dependency tiap kombinasi telah dicatat dengan bukti audit. T01 bukan task membangun installer. Kebutuhan spike sebelumnya ditafsirkan sebagai audit kelayakan input build; tidak mengubah kriteria installer atau menyembunyikan kekurangan runner.

T23 dipecah pelaksanaannya per enam kombinasi native dan dua arsitektur container. Satu platform hanya masuk daftar didukung setelah build, clean install, runtime FFmpeg, download sintetis/subtitle, cancel/process tree, restart, folder, izin sesi, upgrade/uninstall dan keamanan paket lulus. Container diuji dengan volume persisten, restart dan izin nonroot serta smoke test Docker/Podman. Estimasi platform tanpa runner tidak boleh disamakan dengan platform lokal yang sudah teruji.

## Referensi primer

- [PyInstaller requirements](https://pyinstaller.org/en/stable/requirements.html) dan [build toolchains](https://pyinstaller.org/en/stable/bootloader-building.html).
- [PyInstaller 6.22.3 metadata](https://pypi.org/pypi/pyinstaller/6.22.3/json) dan [imageio-ffmpeg 0.6.0 metadata](https://pypi.org/pypi/imageio-ffmpeg/0.6.0/json): daftar file yang benar-benar dipublikasikan.
- [Python 3.12.10 release file matrix](https://www.python.org/downloads/release/python-31210/): bukti adanya arsitektur installer/embeddable; bukan pilihan patch untuk rilis aplikasi.
- [Docker multi-platform builds](https://docs.docker.com/build/building/multi-platform/) dan [Podman machine](https://docs.podman.io/en/latest/markdown/podman-machine.1.html).
