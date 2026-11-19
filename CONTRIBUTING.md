# Contributing

Gunakan Python 3.10 atau lebih baru dan FFmpeg. Jalankan dari root proyek.

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev,web,portable]'
.venv/bin/idlixport doctor
.venv/bin/ruff check src tests tools
.venv/bin/ruff format --check src tests tools
.venv/bin/python -m pytest -q tests tools/spikes/test_network_spike.py
```

Untuk tes browser opsional, siapkan Chrome dan Node.js, lalu jalankan
`IDLIXPORT_BROWSER_TESTS=1 .venv/bin/python -m pytest -q tests -k browser`.
Gunakan fixture sintetis untuk reproduksi bug. Dalam PR, jelaskan dampak perubahan,
hasil pengujian, dan OS/arsitektur yang diuji. Jangan lampirkan cookies, token,
URL media bertanda tangan, database runtime, atau film hasil unduhan.
