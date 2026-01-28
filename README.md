```text
    _    _           _   _      _    _           _   _ 
   / \  | | ___ _ __| |_| |    / \  | | ___ _ __| |_| |
  / _ \ | |/ _ \ '__| __| |   / _ \ | |/ _ \ '__| __| |
 / ___ \| |  __/ |  | |_|_|  / ___ \| |  __/ |  | |_|_|
/_/   \_\_|\___|_|   \__(_) /_/   \_\_|\___|_|   \__(_)
```

**The ultimate desktop tool for creating stream alerts from YouTube, Instagram, and TikTok clips.**

Download any video segment from popular platforms, crop it to your desired aspect ratio, normalize audio, add end buffers, and export perfectly formatted alert videos — all in one streamlined workflow.

![Python](https://img.shields.io/badge/Python-3.9+-blue?logo=python&logoColor=white)
![Flask](https://img.shields.io/badge/Flask-Backend-green?logo=flask&logoColor=white)
![FFmpeg](https://img.shields.io/badge/FFmpeg-Powered-orange?logo=ffmpeg&logoColor=white)
![License](https://img.shields.io/badge/License-MIT-yellow)

---

## ✨ Features

### 🎬 Video Processing
- **Multi-Platform Support** — Download from YouTube, Instagram, and TikTok
- **Smart URL Cleaning** — Automatically strips playlist/radio parameters from YouTube URLs
- **Live Video Preview** — Real-time video playback with audio in the crop preview
- **Auto-Populated Timestamps** — Start defaults to 0:00, end defaults to full video duration
- **Precision Trimming** — Fine-tune start and end times with dual sliders after download
- **Multiple Aspect Ratios** — Export in 1:1 (square), 16:9 (widescreen), 9:16 (vertical/TikTok), or 4:3
- **Resolution Options** — Export at 480p, 720p, or 1080p
- **Interactive Crop** — Drag to position your crop area, zoom slider to adjust size

### 🔊 Audio
- **Audio Normalization** — Automatic loudness normalization (EBU R128, -16 LUFS) — toggleable
- **Separate Audio Source** — Use audio from a completely different video
- **High-Quality Output** — Lossless audio processing pipeline with single-encode AAC at 192kbps

### 🎨 User Experience
- **Dark Mode Interface** — Easy on the eyes during late-night editing sessions
- **Helpful Error Messages** — Clear instructions on how to fix issues
- **Dependency Status** — Settings panel shows installed/missing dependencies with download links
- **End Buffer** — Configurable still frame buffer at the end (0-5 seconds)
- **Smart Timestamps** — Type `90` and it auto-formats to `1:30`
- **Persistent Settings** — Your preferences are saved locally
- **Standalone EXE** — Single executable file, no installation required

---

## 📋 Requirements

### System Dependencies
You need **Python 3.10 or newer** to run or build this project.

### 🔰 Step-by-Step Installation Guide (For Beginners)

If you are new to installing developer tools, follow these steps exactly:

#### 1. Install Python
1. Download Python from [python.org](https://www.python.org/downloads/).
2. Run the installer.
3. **IMPORTANT:** Check the box that says **"Add Python to PATH"** before clicking "Install Now".
4. Once finished, open Command Prompt (search for `cmd` in Windows).
5. Type `python --version` and hit Enter. You should see Python 3.10 or higher.

#### 2. Install FFmpeg
1. Open Command Prompt as Administrator (Right-click Command Prompt > Run as Administrator).
2. Type the following command and hit Enter:
   ```cmd
   winget install Gyan.FFmpeg
   ```
3. Wait for it to finish. You may need to restart your computer after this.

#### 3. Install yt-dlp
1. In the same Command Prompt, type:
   ```cmd
   pip install yt-dlp
   ```
2. If that works, you are all set!

#### 4. Troubleshooting
If you see errors like `'pip' is not recognized` or `'winget' is not recognized`:
- **For pip:** You likely didn't check "Add Python to PATH" during installation. Reinstall Python and make sure to check that box.
- **For winget:** Ensure you are on a recent version of Windows 10 or 11. You can also download the [App Installer](https://apps.microsoft.com/store/detail/app-installer/9NBLGGH4NNS1) from the Microsoft Store.

> **Tip:** The app will show you which dependencies are missing and provide individual download links if needed.

### Python Dependencies (for running from source)

```bash
pip install -r requirements.txt
```

---

## 🚀 Quick Start

### Option 1: Download the EXE (Recommended)

1. Download `AlertAlert.exe` from [Releases](https://github.com/thedeutschmark/alert-alert/releases)
2. Double-click to run
3. Your browser will open automatically to the app interface
4. Keep the console window open while using the app

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

## 📖 How to Use

### Step 1: Enter Video URL
- Paste any video URL (YouTube, Instagram, TikTok)
- Timestamps auto-populate to full video duration
- Click **Validate** to check the URL

### Step 2: Download & Preview
- Click **Download Clip** to fetch the video segment
- Use the **Trim Sliders** for precise start/end adjustments
- Play/pause the preview with audio to verify your selection

### Step 3: Crop & Adjust
- **Drag** the video to position the crop area
- Use the **Zoom Slider** to adjust crop size
- Select your **Aspect Ratio** (1:1, 16:9, 9:16, 4:3)

### Step 4: Process & Export
- Choose your **Resolution** (480p, 720p, 1080p)
- Set **End Buffer** duration (0-5 seconds)
- Toggle **Audio Normalization** on/off
- Click **Process** and wait for the magic
- **Download** your finished alert video!

---

## ⚙️ Settings

Access settings via the gear icon in the top-right corner:

| Setting | Options | Description |
|---------|---------|-------------|
| **Resolution** | 480p, 720p, 1080p | Output video resolution |
| **End Buffer** | 0-5 seconds | Still frame at end of video |
| **Normalize Audio** | On/Off | EBU R128 loudness normalization |
| **Dependencies** | — | Shows install status with download links |

---

## 🏗️ Building the EXE

To build your own executable:

```bash
pip install pyinstaller
python -m PyInstaller --name "AlertAlert" --add-data "static;static" --icon=static/favicon.ico --clean --onefile app.py
```

The output will be in the `dist/` folder.

---

## � Troubleshooting

### "FFmpeg not found"
**Solution:** Install FFmpeg using one of these methods:
```bash
winget install Gyan.FFmpeg
```
Or download from [ffmpeg.org](https://ffmpeg.org/download.html) and add to PATH.

### "yt-dlp not found"
**Solution:** Install yt-dlp:
```bash
pip install yt-dlp
```

### Video URL not working
- Make sure the URL is a direct video link (not a playlist)
- The app automatically cleans YouTube playlist parameters
- Instagram and TikTok links should work directly

### Port 5000 already in use
Another application is using port 5000. Close it and restart the app.

---

## 📜 License

MIT License — see [LICENSE](LICENSE) for details.

---

## 🙏 Credits

Created by **deutschmark**

Built with:
- [Flask](https://flask.palletsprojects.com/) — Web framework
- [FFmpeg](https://ffmpeg.org/) — Video processing
- [yt-dlp](https://github.com/yt-dlp/yt-dlp) — Video downloads
- [Waitress](https://docs.pylonsproject.org/projects/waitress/) — Production WSGI server

---

**Made with ❤️ for streamers**
