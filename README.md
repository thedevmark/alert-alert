<p align="center">
  <img src="docs/hero.png" alt="Alert! Alert! — create consistent stream alerts from any source" width="100%">
</p>

**Create consistent stream alerts from any source. From idea to done, fast.**

Alert! Alert! is a small native desktop app for streamers: pull in a clip from a URL or a local file, crop and trim it, and export a clean alert — without opening a full editor.

![Python](https://img.shields.io/badge/Python-3.10+-blue?logo=python&logoColor=white)
![PySide6](https://img.shields.io/badge/PySide6-Native-41cd52?logo=qt&logoColor=white)
![FFmpeg](https://img.shields.io/badge/FFmpeg-Powered-orange?logo=ffmpeg&logoColor=white)
![License](https://img.shields.io/badge/License-Apache_2.0-blue)

---

## Supported platforms

<p align="center">
  <img src="docs/platforms.png" alt="Sources: local files, YouTube, Instagram, TikTok, Facebook — more coming soon" width="100%">
</p>

---

## What it does

- **Load** from a video URL (via yt-dlp) or a local file.
- **Preview** natively — H.264 plays directly, no browser engine, no transcode workaround.
- **Crop** with aspect-ratio presets (1:1, 16:9, 9:16, 4:3, 3:4, 21:9), zoom, and a draggable box with rule-of-thirds guides.
- **Trim** right on the timeline — drag the in/out handles on the waveform scrubber, or use keyboard shortcuts.
- **Export** a square alert: resolution + quality presets, audio normalize, fades, and an optional end-buffer freeze.
- **Overrides** — swap in a separate audio track, or use a still image as the visual with the clip's audio.
- **Batch queue** — line up multiple clips, each with its own crop/trim/overrides, and Export All in one go.

It's a native PySide6 (Qt Widgets) app — no web server, no embedded browser. The packaged exe is ~70 MB and launches instantly.

---

## Quick start

### Download the app

1. Grab `alert-alert.exe` from the [latest release](https://github.com/thedeutschmark/alert-alert/releases/latest).
2. Run it. Standalone — no Python needed.
3. On first launch, if **FFmpeg** or **yt-dlp** aren't found, the app offers a one-click setup that downloads them for you from their official sources (nothing downloads until you click).

### Run from source

```bash
git clone https://github.com/thedeutschmark/alert-alert.git
cd alert-alert
pip install -r requirements.txt
python native_app.py
```

Requirements: Windows (primary target), Python 3.10+, and internet access for URL loading and runtime setup.

---

## Keyboard shortcuts

| Key | Action |
| --- | --- |
| `Space` | Play / pause |
| `I` / `O` | Set trim in / out at the playhead |
| `←` / `→` | Seek ∓1s |
| `,` / `.` | Nudge ∓0.1s |
| `J` / `L` | Seek ∓5s |
| `Home` / `End` | Jump to start / end |
| `↑` / `↓` | Volume up / down |
| `+` / `−` | Zoom in / out |
| `R` | Reset crop to Original |
| `PgUp` / `PgDn` | Previous / next clip in queue |
| `Ctrl`+`E` | Export All |

---

## Menu

- **Take the tour** — replay the guided walkthrough (loads a sample clip if your queue is empty).
- **Check dependencies** — see FFmpeg / ffprobe / yt-dlp status anytime.
- **Update yt-dlp** — pull the latest yt-dlp so YouTube changes don't break downloads.
- **Show log terminal** — open a console alongside the app for live logs (off by default; also `--console` on launch).
- **Open Output Folder**, **About**.

---

## Building the exe

```bash
pip install -r requirements.txt pyinstaller
python -m PyInstaller --clean --noconfirm AlertNative.spec
```

Produces `dist/alert-alert.exe` (native build — `AlertNative.spec` excludes QtWebEngine). Pushing a `v*` tag also builds and attaches the exe via GitHub Actions.

---

## Troubleshooting

- **Downloads/exports fail on a fresh machine** → FFmpeg or yt-dlp isn't installed. Use the first-run setup prompt, or install them yourself.
- **YouTube URL won't download** → run **Update yt-dlp** from the App menu; install optional `deno` if challenge handling still fails.

---

## deutschmark's other apps

<table>
<tr><td align="center" width="56"><img src=".github/apps/pathos.svg" width="44" alt=""></td><td><a href="https://yourpathos.app"><b>Pathos</b></a><br>Worker-side job search with source-linked roles, evidence-checked resumes, and application tracking.</td></tr>
<tr><td align="center" width="56"><img src=".github/apps/markskill.svg" width="44" alt=""></td><td><a href="https://github.com/thedevmark/markskill"><b>Markskill</b></a><br>A product-engineering skill for AI agents: trace behavior to its owner, fix root causes, shape interfaces around real tasks, and verify claims with evidence.</td></tr>
<tr><td align="center" width="56"><img src=".github/apps/auto-iphone-uploader.svg" width="39" alt=""></td><td><a href="https://github.com/thedevmark/auto-iphone-uploader"><b>Auto iPhone Uploader</b></a><br>Write a video's title and captions once on your PC, then post it from the real apps on your iPhone. Early preview.</td></tr>
<tr><td align="center" width="56"><img src=".github/apps/streamer-online.svg" width="44" alt=""></td><td><a href="https://streamer.deutschmark.online"><b>Streamer Online</b></a><br>Build OBS scenes and browser-source overlays with connected streamer tools.</td></tr>
<tr><td align="center" width="56"><img src=".github/apps/forgetmenot.png" width="32" alt=""></td><td><a href="https://github.com/thedevmark/forgetmenot"><b>ForgetMeNot</b></a><br>A local-first Twitch bot that remembers regulars, callbacks, and stream lore.</td></tr>
</table>

<sub>All projects → <a href="https://github.com/thedevmark">github.com/thedevmark</a></sub>

---

## Acceptable use & disclaimer

Alert! Alert! is a general-purpose video utility. It does not download, host, or distribute any copyrighted content. URL loading is delegated to **yt-dlp** — an independent open-source project — and media decoding/encoding to **FFmpeg**. Alert! Alert! does not contain any video-downloading code itself; see [THIRD_PARTY_NOTICES.txt](THIRD_PARTY_NOTICES.txt).

**You are solely responsible** for ensuring you have the legal right to download, modify, or redistribute any content you process with this tool. Use it on video you own, video you have permission to use, or content available under licenses that permit reuse (Creative Commons, public domain, fair use under your local law). Respect the terms of service of any platform you interact with and the copyright of the original creators.

The authors of Alert! Alert! accept no liability for misuse. The software is provided "as is" without warranty of any kind, as set out in the LICENSE file. The first launch of the app requires you to acknowledge this disclaimer before use.

## License

Apache License 2.0 — see [LICENSE](LICENSE). Third-party runtime notices are in [THIRD_PARTY_NOTICES.txt](THIRD_PARTY_NOTICES.txt).

## Credits

Created by **deutschmark**. Built with [FFmpeg](https://ffmpeg.org/), [yt-dlp](https://github.com/yt-dlp/yt-dlp), and [PySide6](https://doc.qt.io/qtforpython-6/).
