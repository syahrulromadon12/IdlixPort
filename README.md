# IdlixPort

**Film pilihan, siap disimpan.**

IdlixPort adalah aplikasi CLI Python untuk memeriksa format dan mengunduh satu atau banyak film dari halaman IDLIX, player yang didukung yt-dlp, atau URL MP4/HLS/DASH langsung. URL film pada contoh adalah bahan pengujian; aplikasi menerima URL film lain dengan struktur situs yang didukung.

**Status:** MVP mendukung alur Next.js/Pentos pada URL target `https://z2.idlixku.com/movie/the-last-10-years-2022`. Verifikasi manifest pada 22 Oktober 2026 menemukan 360p dan 720p dengan subtitle Indonesia; sesi tamu dibatasi 720p. Berdasarkan informasi pengguna, 1080p memerlukan login dan hanya tersedia pada sebagian film. Adapter Dooplay tambahan diuji menggunakan fixture.

Transfer live dibatasi 10.240 byte untuk pemeriksaan. FFmpeg mengenali H.264 1280×536 dan audio AAC Jepang pada sampel dari varian berlabel 720p. Unduhan film penuh dan akses akun pengguna belum diuji.

## Instalasi cepat

Gunakan Python 3.10 atau lebih baru. Jalankan dari direktori proyek:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[portable]'
idlixport doctor
```

Command utama adalah `idlixport`. Command lama `idlix` tetap tersedia sebagai alias kompatibilitas. Setelah memperbarui source, jalankan ulang instalasi di atas agar command `idlixport` tersedia.

Extra `portable` memasang binary FFmpeg melalui `imageio-ffmpeg`, sehingga tidak perlu memasang FFmpeg secara global pada platform yang menyediakan wheel. Ukuran instalasi bertambah sekitar puluhan MB. Jika FFmpeg sudah tersedia pada `PATH`, cukup `python -m pip install -e .`. Instalasi dependency membutuhkan internet.

Windows PowerShell menggunakan `.venv\Scripts\Activate.ps1` dan `python` sebagai pengganti `python3`. FFmpeg juga dapat dipilih melalui `--ffmpeg-location /path/ke/ffmpeg` atau direktori yang berisi binary tersebut. FFprobe opsional untuk MVP; instalasi FFmpeg lengkap berguna untuk diagnosis lanjutan.

## Penggunaan

Periksa format sebelum mengunduh:

```bash
idlixport info 'https://z2.idlixku.com/movie/the-last-10-years-2022'
```

Unduh dengan batas maksimum 1080p:

```bash
idlixport download 'https://z2.idlixku.com/movie/the-last-10-years-2022'
```

Pilih resolusi tertinggi yang tersedia pada server terpilih, termasuk di atas 1080p jika tersedia:

```bash
idlixport download 'https://z2.idlixku.com/movie/the-last-10-years-2022' --quality best
```

Jika sudah login di Safari, jalankan sendiri opsi berikut untuk menggunakan sesi akun lokal:

```bash
idlixport info 'https://z2.idlixku.com/movie/the-last-10-years-2022' --cookies-from-browser safari
idlixport download 'https://z2.idlixku.com/movie/the-last-10-years-2022' \
  --cookies-from-browser safari --quality best --subtitles
```

Login diperlukan untuk mengakses 1080p, tetapi tidak semua film menyediakan varian tersebut. Periksa `idlixport info` menggunakan sesi login yang sama dengan perintah download. `--quality best` memilih kualitas tertinggi yang tersedia untuk film dan sesi tersebut; jika maksimum sumber 720p, hasilnya tetap 720p. Gunakan `--quality 1080` jika ingin membatasi hasil maksimal 1080p.

Pilihan browser lain: `chrome`, `firefox`, `edge`, dan `brave`, menggunakan profil default. Pembacaan cookies bergantung pada izin OS dan dukungan browser. Untuk Safari, terminal mungkin memerlukan izin Full Disk Access dari macOS. Aplikasi tidak membaca cookies browser sampai opsi tersebut dipilih. Dukungan sesi akun pengguna belum diuji langsung. Jangan kirim isi cookies atau token ke chat.

### Banyak film dalam satu perintah

Buat `films.txt` berisi satu URL film per baris. Ganti slug contoh berikut dengan URL film pilihan Anda:

```text
# Film yang ingin diunduh
https://z2.idlixku.com/movie/slug-film-pertama
https://z2.idlixku.com/movie/slug-film-kedua
https://z2.idlixku.com/movie/slug-film-ketiga
```

Jalankan batch menggunakan sesi browser yang sudah login:

```bash
idlixport download --batch-file films.txt \
  --cookies-from-browser safari --quality best --subtitles
```

Beberapa URL juga dapat diberikan langsung:

```bash
idlixport download 'URL_FILM_PERTAMA' 'URL_FILM_KEDUA' \
  --cookies-from-browser safari --quality best
```

URL dari argumen diproses lebih dahulu, kemudian isi file. Baris kosong dan komentar yang diawali `#` diabaikan; URL duplikat persis hanya diproses sekali. Seluruh URL divalidasi sebelum akses jaringan.

Film diunduh berurutan dengan satu sesi downloader dan cookies yang digunakan bersama. Setiap film mendapat sesi media ketika mulai diproses, sehingga URL bertoken tidak disiapkan jauh sebelum giliran unduh. Kualitas dipilih per film: satu film bisa 1080p, film berikutnya 720p sesuai sumber dan akses akun. Fragmen di dalam setiap film tetap dapat diunduh paralel dengan `-N`.

Kegagalan satu film tidak menghentikan film berikutnya. Terminal menampilkan hasil per film dan ringkasan; exit code `1` jika ada yang gagal, `0` jika semuanya selesai atau sudah tersedia. Ctrl+C menghentikan batch dengan exit code `130`. Jalankan ulang daftar yang sama untuk mencoba film gagal dan melanjutkan file parsial; hasil lengkap dengan nama yang sama tidak ditimpa. Metadata tetap diperiksa ulang untuk memilih kualitas sumber saat ini. Pengujian batch menggunakan media sintetis lokal; batch banyak film live belum diuji.

### Server dan sumber langsung

Ganti server Dooplay, folder hasil, dan jumlah segmen paralel:

```bash
idlixport download 'URL_FILM' --server 2 --quality best -o downloads -N 8 --subtitles
```

Jika halaman tidak dapat diekstrak, gunakan URL player yang didukung atau URL stream langsung yang dapat diakses:

```bash
idlixport info 'https://cdn.example/master.m3u8' --referer 'https://situs.example/movie/judul'
idlixport download 'https://cdn.example/master.m3u8' --quality best \
  --referer 'https://situs.example/movie/judul'
```

URL contoh `example` adalah placeholder. URL stream bisa ditemukan melalui tab Network pada browser saat memutar video: cari `m3u8`, `mpd`, atau `mp4`, lalu salin URL lengkap beserta query string. Gunakan URL master playlist agar semua varian resolusi tetap tersedia. URL variant playlist hanya menyediakan resolusi variant tersebut. URL dengan token dapat kedaluwarsa.

Pada mirror Dooplay yang tidak mengandung `idlix` di hostname, `--page-adapter` memaksa adapter Dooplay. Opsi tersebut tidak ditujukan untuk halaman Next.js. File cookies Netscape milik pengguna dapat diberikan dengan `--cookies /path/cookies.txt` sebagai alternatif `--cookies-from-browser`. Cookies tidak otomatis menyelesaikan HTTP 403.

Jalankan kembali perintah yang sama untuk melanjutkan unduhan yang terputus. Resume bergantung pada dukungan HTTP Range untuk file langsung atau status fragmen HLS/DASH. Jangan hapus file `.part` dan `.ytdl` jika ingin melanjutkan. Ctrl+C menghentikan proses; exit code `130`.

## Perilaku penting

- `--quality 1080` memilih kualitas tertinggi dengan tinggi video **maksimum 1080**, termasuk fallback ke 720p jika itu yang tersedia. Pilihan lain: `480`, `720`, `1440`, `2160`, `best`.
- Metadata tinggi yang tidak diketahui tidak dianggap memenuhi batas angka. Gunakan `info`, kemudian `--quality best` jika ingin menerima sumber tanpa metadata tinggi; mode tersebut tidak membatasi resolusi.
- `best` memilih kualitas tertinggi yang diiklankan sumber pada **server terpilih**. Aplikasi tidak membandingkan seluruh server dan tidak melakukan upscale.
- Pada IDLIX Next.js, aplikasi memakai API metadata dan sesi player normal, menunggu countdown server, lalu membaca manifest dari provider. Batas `maxHeight` sesi tetap diterapkan, termasuk ketika manifest mengiklankan format lebih tinggi. Nomor `--server` hanya berlaku untuk Dooplay.
- Manifest provider target saat diperiksa hanya memberi label `360p`/`720p` tanpa dimensi frame. Adapter membaca tinggi dari label tersebut. Label sumber bukan hasil pengukuran kualitas visual.
- Jika manifest tidak menyediakan dimensi maupun label resolusi, adapter memeriksa maksimal 256 KiB dari satu segmen atau init per varian menggunakan FFmpeg. `info` dapat membaca sampel kecil ini. Dimensi film sinema yang terpotong vertikal ditampilkan apa adanya, misalnya 1280×534; angka tersebut tidak sama dengan kelas kualitas 720p. Varian yang tidak dapat diperiksa gagal dengan pesan yang jelas.
- URL media sesi target berlaku terbatas; pemeriksaan tamu menunjukkan TTL 3600 detik. Pembaruan token otomatis selama transfer belum tersedia. Jika token kedaluwarsa, jalankan kembali perintah yang sama untuk meminta sesi baru dan mencoba resume.
- Video dan audio terpisah digabungkan ke MKV tanpa encoding ulang. Sumber yang sudah berisi video dan audio mempertahankan container asal, misalnya MP4.
- Delapan koneksi paralel berlaku untuk fragmen native HLS/DASH, bukan delapan koneksi untuk satu file MP4 biasa. Batas CLI: 1–16. Gunakan `-N 4` jika server sering mengembalikan HTTP 429.
- Retry terbatas: lima kali untuk file/fragmen, dua kali untuk extraction. Fragmen yang tetap gagal menyebabkan proses gagal; aplikasi tidak sengaja melewatkan fragmen rusak.
- File hasil yang sudah ada tidak ditimpa. Tidak ada arsip global yang menghalangi pengunduhan kualitas lebih tinggi.
- Subtitle `id`/`en` disimpan sebagai file terpisah jika extractor menyediakannya. Subtitle yang sudah tertanam di gambar tidak dapat dipisahkan.
- Playlist dan live stream ditolak. Halaman dengan beberapa embed yang dikembalikan sebagai playlist perlu diarahkan ke URL satu player.
- Aplikasi tidak menjalankan JavaScript browser dan tidak menyediakan bypass DRM, CAPTCHA, atau kontrol akses. Gunakan untuk konten yang boleh diunduh.

## Perintah zsh terputus

Jika muncul `zsh: command not found: --cookies-from-browser`, periksa akhir baris sebelumnya. Karakter `\` harus menjadi karakter terakhir, tanpa spasi setelahnya. Gunakan perintah satu baris untuk menghindari masalah salin-tempel:

```bash
idlixport download 'https://z2.idlixku.com/movie/500-days-of-summer-2009' --cookies-from-browser safari --quality best --subtitles
```

## Pengembangan dan pemeriksaan

```bash
python -m pip install -e '.[dev,portable]'
python -m pytest -q
ruff check src tests
ruff format --check src tests
```

Tes unit menguji pemilihan format melalui engine yt-dlp asli dan adapter melalui respons fixture. Tes integrasi memakai server HTTP loopback dan video sintetis yang dibuat FFmpeg; tidak mengunduh film pihak ketiga. Tes integrasi dilewati jika FFmpeg tidak tersedia.

Analisis kebutuhan, keputusan teknis, acceptance criteria, dan batas pengembangan tersedia di [docs/RANCANGAN.md](docs/RANCANGAN.md). Bukti pemeriksaan tersedia di [docs/VALIDASI.md](docs/VALIDASI.md).
