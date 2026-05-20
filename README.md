<p align="center">
  <img src="static/img/logo.png" alt="Alert! Alert! icon" width="220">
</p>

```text
    _    _           _   _      _    _           _   _
   / \  | | ___ _ __| |_| |    / \  | | ___ _ __| |_| |
  / _ \ | |/ _ \ '__| __| |   / _ \ | |/ _ \ '__| __| |
 / ___ \| |  __/ |  | |_|_|  / ___ \| |  __/ |  | |_|_|
/_/   \_\_|\___|_|   \__(_) /_/   \_\_|\___|_|   \__(_)
```

**Desktop app for stream alerts. Quick alert clips, trims, crops, audio treatment, and exports.**

![Python](https://img.shields.io/badge/Python-3.10+-blue?logo=python&logoColor=white)
![Flask](https://img.shields.io/badge/Flask-Backend-green?logo=flask&logoColor=white)
![FFmpeg](https://img.shields.io/badge/FFmpeg-Powered-orange?logo=ffmpeg&logoColor=white)
![License](https://img.shields.io/badge/License-AGPL_v3-blue)

> **Heads up:** Alert! Alert! used to bundle a Video Editor and a Film Lab. Those have moved to their own repos:
> - Video Editor → [`clipline`](../clipline)
> - Film Lab → private repo

---

## What It Does

- Load a remote video URL or local media file.
- Trim, crop, zoom, choose aspect ratios, and export alerts fast.
- Use separate audio, normalization, fades, and image override modes.
- Run Windows one-click runtime install for `ffmpeg`, `ffprobe`, and `yt-dlp`.

---

## Requirements

- Windows is the primary desktop target.
- Python `3.10+` is recommended when running from source.
- Internet access is required for one-click runtime install and remote URL loading.

---

## Quick Start

### Option 1: Download the EXE

1. Download `alert-alert.exe` from [Releases](https://github.com/thedeutschmark/alert-alert/releases).
2. Launch it.
3. Allow runtime install if prompted.

### Option 2: Run From Source

```bash
git clone https://github.com/thedeutschmark/alert-alert.git
cd alert-alert
pip install -r requirements.txt
python desktop.py
```

Browser mode for development:

```bash
python app.py
```

The local app runs on `http://localhost:3000` by default.

---

## How To Test

1. Load a URL or local file.
2. Trim and crop it.
3. Process the alert.
4. Download the export.

---

## Building The EXE

### Windows

```bat
build.bat
```

This builds the desktop executable to `dist\alert-alert.exe`.

### Manual PyInstaller Build

```bash
pip install pyinstaller PySide6
python -m PyInstaller --clean --noconfirm AlertCreator.spec
```

---

## Troubleshooting

### Port conflict
The app defaults to `localhost:3000` and searches for another open port automatically. If startup still fails, close the conflicting process and relaunch.

### YouTube or remote URL issues
- Update `yt-dlp`.
- Retry after runtime auto-install/update.
- Install optional `deno` if YouTube challenge handling still fails.

---

## License

AGPL-3.0 — see [LICENSE](LICENSE).

Third-party runtime notices are in [THIRD_PARTY_NOTICES.txt](THIRD_PARTY_NOTICES.txt).

---

## Credits

Created by **deutschmark**

Built with:
- [Flask](https://flask.palletsprojects.com/)
- [FFmpeg](https://ffmpeg.org/)
- [yt-dlp](https://github.com/yt-dlp/yt-dlp)
- [PySide6](https://doc.qt.io/qtforpython-6/)
