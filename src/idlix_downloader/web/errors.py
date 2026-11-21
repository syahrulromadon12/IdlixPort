"""Public errors never contain raw provider messages or credentials."""

import re

ERRORS = {
    "SUBTITLE_FAILED": (
        "Subtitle atau penggabungannya gagal; video tetap tersedia.",
        True,
        "Coba subtitle lagi atau lanjutkan tanpa subtitle.",
    ),
    "SUBTITLE_UNAVAILABLE": (
        "Bahasa subtitle pilihan tidak tersedia; video tetap tersedia.",
        False,
        "Periksa ulang bahasa atau lanjutkan tanpa subtitle.",
    ),
    "BROWSER_UNAVAILABLE": (
        "Browser/profil tidak tersedia.",
        False,
        "Pilih profil lain atau buka login secara manual.",
    ),
    "CONSENT_REQUIRED": (
        "Persetujuan membaca sesi browser diperlukan.",
        False,
        "Aktifkan persetujuan untuk profil pilihan.",
    ),
    "COOKIE_PERMISSION": (
        "Akses cookies browser ditolak oleh sistem.",
        False,
        "Periksa izin OS, lalu coba lagi.",
    ),
    "COOKIE_UNAVAILABLE": (
        "Sesi browser tidak dapat dibaca.",
        False,
        "Periksa profil dan izin browser, lalu coba lagi.",
    ),
    "SESSION_NOT_VERIFIED": (
        "Akun belum dapat diverifikasi.",
        True,
        "Login di profil browser pilihan, lalu kembali ke aplikasi.",
    ),
    "SESSION_TIMEOUT": (
        "Waktu pemeriksaan sesi habis.",
        True,
        "Periksa login lalu pilih Coba lagi.",
    ),
    "RETRY_LIMIT": (
        "Batas tiga attempt tercapai.",
        False,
        "Periksa ulang sumber untuk pekerjaan baru.",
    ),
    "SESSION_EXPIRED": (
        "Sesi sumber kedaluwarsa atau ditolak.",
        False,
        "Login pada profil browser yang dipilih, lalu pilih Coba lagi. "
        "Memutar film saja belum menjamin sesi terbaca.",
    ),
    "INVALID_INPUT": ("Input tidak valid.", False, "Periksa tautan dan pilihan Anda."),
    "INSPECTION_EXPIRED": ("Hasil pemeriksaan sudah kedaluwarsa.", True, "Periksa ulang tautan."),
    "PROBE_LIMIT": ("Sumber melebihi batas pemeriksaan.", False, "Coba sumber media lain."),
    "FOLDER_UNAVAILABLE": ("Folder pilihan tidak tersedia.", False, "Pilih folder lain."),
    "OUTPUT_UNWRITABLE": (
        "Folder tidak dapat ditulis.",
        False,
        "Pilih folder lain atau periksa izin.",
    ),
    "ARTIFACT_UNAVAILABLE": (
        "File sudah dipindahkan atau tidak tersedia.",
        False,
        "Periksa folder hasil.",
    ),
    "OPEN_UNAVAILABLE": (
        "File atau folder tidak dapat dibuka otomatis.",
        False,
        "Gunakan simpan salinan lewat browser.",
    ),
    "QUALITY_CHANGED": (
        "Kualitas pilihan berubah atau tidak tersedia.",
        False,
        "Periksa ulang sumber.",
    ),
    "UNKNOWN_RESOLUTION": ("Resolusi sumber belum dapat dibuktikan.", False, "Pilih sumber lain."),
    "SOURCE_UNAVAILABLE": ("Sumber tidak dapat diproses.", True, "Periksa URL dan coba lagi."),
    "VERIFICATION_REQUIRED": (
        "IDLIX meminta pemeriksaan tambahan untuk video ini. "
        "IdlixPort belum mendukung pemeriksaan itu, sehingga unduhan tidak bisa dimulai.",
        False,
        "",
    ),
    "NETWORK_BLOCKED": (
        "Aturan jaringan IdlixPort menolak koneksi yang diperlukan untuk unduhan ini.",
        False,
        "",
    ),
    "NETWORK_BLOCKED_DESTINATION": (
        "Server tujuan berada di luar daftar izin IdlixPort.",
        False,
        "",
    ),
    "NETWORK_BLOCKED_PORT": ("IdlixPort hanya mengizinkan koneksi ke port 80 dan 443.", False, ""),
    "NETWORK_BLOCKED_ADDRESS": (
        "Server unduhan mengarah ke alamat IP yang tidak diizinkan.",
        False,
        "",
    ),
    "NETWORK_BLOCKED_EXTERNAL": (
        "Format stream ini memerlukan pengunduh eksternal. IdlixPort hanya mengizinkan pengunduh internal.",
        False,
        "",
    ),
    "NETWORK_CAPACITY_TIMEOUT": (
        "Semua slot koneksi IdlixPort sedang terpakai.",
        True,
        "Tunggu sebentar, lalu coba unduh lagi.",
    ),
    "DISK_FULL": ("Ruang penyimpanan tidak cukup.", False, "Kosongkan ruang disk."),
    "FFMPEG_MISSING": ("FFmpeg belum tersedia.", False, "Periksa instalasi aplikasi."),
    "WORKER_FAILED": (
        "Proses pekerjaan berhenti atau menghasilkan data tidak valid.",
        True,
        "Coba lagi.",
    ),
    "QUEUE_FULL": ("Antrean penuh.", True, "Tunggu pekerjaan selesai."),
    "CONFLICT": ("Status atau permintaan bertentangan.", False, "Muat ulang status."),
    "CANCELLED": ("Pekerjaan dibatalkan.", False, "Mulai pekerjaan baru bila diperlukan."),
    "STORAGE_UNAVAILABLE": (
        "Penyimpanan aplikasi belum siap.",
        False,
        "Periksa izin dan database.",
    ),
}


class AppError(Exception):
    def __init__(self, code):
        self.code = code if code in ERRORS else "WORKER_FAILED"
        super().__init__(self.code)

    def public(self):
        message, retryable, action = ERRORS[self.code]
        return dict(code=self.code, message=message, retryable=retryable, action=action)


def safe_blocked_host(value):
    if isinstance(value, str) and re.fullmatch(r"[a-zA-Z0-9.-]{1,253}", value):
        return value.lower()
    return None


def public_error(error):
    if isinstance(error, AppError):
        return error.public()
    text = str(error).lower()
    code = "SOURCE_UNAVAILABLE"
    if "probe_limit" in text:
        code = "PROBE_LIMIT"
    elif "disk_full" in text or "no space left" in text:
        code = "DISK_FULL"
    elif isinstance(error, PermissionError):
        code = "OUTPUT_UNWRITABLE"
    elif "network_capacity_timeout" in text:
        code = "NETWORK_CAPACITY_TIMEOUT"
    elif "network_blocked_destination" in text:
        code = "NETWORK_BLOCKED_DESTINATION"
    elif "network_blocked_port" in text:
        code = "NETWORK_BLOCKED_PORT"
    elif "network_blocked_address" in text:
        code = "NETWORK_BLOCKED_ADDRESS"
    elif "network_blocked" in text or "proxy_blocked" in text:
        code = "NETWORK_BLOCKED"
    elif "http error 401" in text or "http error 403" in text or "api idlix menolak sesi" in text:
        code = "SESSION_EXPIRED"
    elif "verifikasi pemutaran" in text:
        code = "VERIFICATION_REQUIRED"
    elif isinstance(error, OSError) and error.errno == 28:
        code = "DISK_FULL"
    result = AppError(code).public()
    if code == "NETWORK_BLOCKED_DESTINATION":
        match = re.search(r"network_blocked_destination \[([a-z0-9.-]{1,253})\]", text)
        if match:
            result["blocked_host"] = match[1]
    return result
