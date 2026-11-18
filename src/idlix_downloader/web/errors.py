"""Public errors never contain raw provider messages or credentials."""

ERRORS = {
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
        "Periksa ulang akses di situs sumber, lalu coba lagi.",
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
        "Sumber meminta verifikasi pemutaran browser.",
        False,
        "Buka situs sumber.",
    ),
    "NETWORK_BLOCKED": (
        "Permintaan jaringan tidak diizinkan.",
        False,
        "Periksa konfigurasi host operator.",
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
    elif "network_blocked" in text or "proxy_blocked" in text:
        code = "NETWORK_BLOCKED"
    elif "http error 401" in text or "http error 403" in text or "api idlix menolak sesi" in text:
        code = "SESSION_EXPIRED"
    elif "verifikasi pemutaran" in text:
        code = "VERIFICATION_REQUIRED"
    elif isinstance(error, OSError) and error.errno == 28:
        code = "DISK_FULL"
    return AppError(code).public()
