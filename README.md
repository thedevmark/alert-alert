```text
    _    _           _   _      _    _           _   _ 
   / \  | | ___ _ __| |_| |    / \  | | ___ _ __| |_| |
  / _ \ | |/ _ \ '__| __| |   / _ \ | |/ _ \ '__| __| |
 / ___ \| |  __/ |  | |_|_|  / ___ \| |  __/ |  | |_|_|
/_/   \_\_|\___|_|   \__(_) /_/   \_\_|\___|_|   \__(_)
```

**The production-ready desktop tool for creating stream alerts from YouTube, Instagram, TikTok, and local files.**

Download clips or load local media, trim and crop with precision, apply audio options, and export polished alert videos quickly with a workflow built for one-click reliability.

![Python](https://img.shields.io/badge/Python-3.9+-blue?logo=python&logoColor=white)
![Flask](https://img.shields.io/badge/Flask-Backend-green?logo=flask&logoColor=white)
![FFmpeg](https://img.shields.io/badge/FFmpeg-Powered-orange?logo=ffmpeg&logoColor=white)
![License](https://img.shields.io/badge/License-MIT-yellow)

---

## Features

### Video Processing
- **Multi-Platform Support** — Download from YouTube, Instagram, and TikTok
- **Smart URL Cleaning** — Automatically strips playlist/radio parameters from YouTube URLs
- **Live Video Preview** — Real-time video playback with audio in the crop preview
- **Auto-Populated Timestamps** — Start defaults to 0:00, end defaults to full video duration
- **Precision Trimming** — Fine-tune start and end times with dual sliders after download
- **Multiple Aspect Ratios** — Export in 1:1 (square), 16:9 (widescreen), 9:16 (vertical/TikTok), or 4:3
- **Resolution Options** — Export at 480p, 720p, or 1080p
- **Interactive Crop** — Drag to position your crop area and zoom as needed

### Audio
- **Audio Normalization** — Automatic loudness normalization (EBU R128, -16 LUFS), toggleable
- **Separate Audio Source** — Use audio from a different URL or local file
- **Fade Control** — Configure fade mode per clip and fade duration globally
- **High-Quality Output** — Lossless audio processing pipeline with single-encode AAC at 192kbps

### User Experience
- **Muted Slate UI** — Modern, low-glare interface optimized for long sessions
- **Helpful Error Messages** — Clear instructions when dependencies or downloads fail
- **Dependency Status + Auto Install** — App auto-installs missing FFmpeg/yt-dlp on first run (with manual fallback)
- **Workflow-Native Settings** — Output, audio, dependency, and app controls live in relevant workflow steps
- **End Buffer** — Configurable still frame buffer at the end (0-5 seconds)
- **Smart Timestamps** — Type `90` and it auto-formats to `1:30`
- **Persistent Settings** — Preferences are saved locally
- **Standalone EXE** — Single executable file, no installer required

---

## Requirements

### System Dependencies
You need **Python 3.10 or newer** to run or build this project.

### EXE Dependency Behavior (One-Click)
- On Windows, `alert-alert.exe` auto-checks dependencies at launch.
- If `ffmpeg`, `ffprobe`, or `yt-dlp` are missing, the app downloads them into a user-local runtime folder (no admin required).
- Manual fallback remains available in **Step 1 > Dependency Setup & Troubleshooting**.
- Auto-install requires outbound internet access to GitHub and FFmpeg mirrors.
- `deno` is **optional** and only helps `yt-dlp` handle certain advanced YouTube challenge/protection scenarios.
- Without `deno`, local files and most URLs still work normally. Installing `deno` mainly improves success rate on some harder YouTube cases.

### Step-by-Step Installation Guide (For Beginners)

If you are new to installing developer tools, follow these steps exactly:

#### 1. Install Python
1. Download Python from [python.org](https://www.python.org/downloads/).
2. Run the installer.
3. **IMPORTANT:** Check **"Add Python to PATH"** before clicking "Install Now".
4. Open Command Prompt as Administrator.
5. Run `python --version` and confirm Python 3.10+.

#### 2. Install FFmpeg
> If you are running the EXE, this is optional because the app can auto-install it.
1. Open Command Prompt as Administrator.
2. Run:
   ```cmd
   winget install Gyan.FFmpeg
   ```
3. Wait for completion.

#### 3. Install yt-dlp
> If you are running the EXE, this is optional because the app can auto-install it.
1. In Command Prompt, run:
   ```cmd
   pip install -U yt-dlp
   ```
2. Restart your computer, then launch `alert-alert.exe` again.

#### 4. Troubleshooting
If you see errors like `'pip' is not recognized` or `'winget' is not recognized`:
- **For pip:** Reinstall Python and ensure **Add Python to PATH** is checked.
- **For winget:** Update Windows and install [App Installer](https://apps.microsoft.com/store/detail/app-installer/9NBLGGH4NNS1) from Microsoft Store.

> **Tip:** Step 1 in the app shows dependency health and direct troubleshooting actions.

### Python Dependencies (for running from source)

```bash
pip install -r requirements.txt
```

---

## Quick Start

### Option 1: Download the EXE (Recommended)

1. Download `alert-alert.exe` from [Releases](https://github.com/thedeutschmark/alert-alert/releases)
2. Double-click to run
3. On first launch, the app may briefly auto-install missing dependencies
4. Browser opens automatically to the app interface
5. Keep the console window open while using the app

### Option 2: Run from Source

1. **Clone the repository**
   ```bash
   git clone https://github.com/thedeutschmark/alert-alert.git
   cd alert-alert
   ```

2. **Install Python dependencies**
   ```bash
   pip install -r requirements.txt
   ```

3. **Run the app**
   ```bash
   python app.py
   ```

---

## How to Use

### Step 1: Enter Video URL
- Paste a video URL (YouTube, Instagram, TikTok)
- Click **Load Video** to validate and download
- Use **Dependency Setup & Troubleshooting** in this step if dependencies are missing

### Step 2: Download & Preview
- After video load, configure optional audio and image features
- Choose separate audio source (URL or local file) if needed
- Set audio start/end for sync and timing

### Step 3: Crop & Adjust
- Drag video to position crop
- Adjust zoom and trim start/end
- Choose aspect ratio and optional audio fade mode

### Step 4: Process & Export
- Open **Output Settings** to choose resolution and end buffer
- Click **Process Video**
- Download the finished alert once processing completes

---

## Settings

Settings are integrated directly into workflow steps and can also be opened from the top-right quick buttons.

| Setting | Options | Description |
|---------|---------|-------------|
| **Resolution** | 480p, 720p, 1080p | Output video resolution (Step 4) |
| **End Buffer** | 0-5 seconds | Adds still frame buffer at clip end (Step 4) |
| **Normalize Audio** | On/Off | EBU R128 loudness normalization (Step 2) |
| **Audio Fade Length** | 0.20s, 0.35s, 0.50s | Global fade duration used by clip fade mode (Step 2/3) |
| **Dependencies** | Auto/manual | Runtime install status and repair actions (Step 1) |

---

## Building the EXE

To build your own executable:

```bash
pip install pyinstaller
python -m PyInstaller --name "alert-alert" --add-data "static;static" --icon=static/favicon.ico --clean --onefile app.py
```

The output is generated in the `dist/` folder.

---

## Troubleshooting

### "FFmpeg not found"
**Solution:** In **Step 1 > Dependency Setup & Troubleshooting**, click **Auto Install Missing** first. If needed, install manually:
```bash
winget install Gyan.FFmpeg
```
Or download from [ffmpeg.org](https://ffmpeg.org/download.html) and add to PATH.

### "yt-dlp not found"
**Solution:** In **Step 1 > Dependency Setup & Troubleshooting**, click **Auto Install Missing** first. If needed, install manually:
```bash
pip install -U yt-dlp
```

### Video URL not working
- Ensure the URL is a direct video link (not a playlist)
- The app auto-cleans YouTube playlist/radio params
- If YouTube protection blocks formats, update yt-dlp and retry
- If protected YouTube URLs keep failing, install optional `deno` (`winget install DenoLand.Deno`) and retry

### Port 5000 already in use
Another application is already using port 5000. Close it and restart Alert! Alert!.

### Auto-install fails repeatedly
- Verify internet connectivity and retry from Step 1
- Check firewall/proxy/enterprise policies for GitHub and FFmpeg hosts
- Check antivirus quarantine history and allow the app runtime directory
- Install dependencies manually, then restart the app

---

## License

MIT License — see [LICENSE](LICENSE) for details.

Third-party runtime tool notices are documented in [THIRD_PARTY_NOTICES.txt](THIRD_PARTY_NOTICES.txt).

---

## Credits

Created by **deutschmark**

Built with:
- [Flask](https://flask.palletsprojects.com/) — Web framework
- [FFmpeg](https://ffmpeg.org/) — Video processing
- [yt-dlp](https://github.com/yt-dlp/yt-dlp) — Video download engine
- [Waitress](https://docs.pylonsproject.org/projects/waitress/) — Production WSGI server

---

**Made for streamers**
