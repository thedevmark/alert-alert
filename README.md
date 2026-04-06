```text
    _    _           _   _      _    _           _   _
   / \  | | ___ _ __| |_| |    / \  | | ___ _ __| |_| |
  / _ \ | |/ _ \ '__| __| |   / _ \ | |/ _ \ '__| __| |
 / ___ \| |  __/ |  | |_|_|  / ___ \| |  __/ |  | |_|_|
/_/   \_\_|\___|_|   \__(_) /_/   \_\_|\___|_|   \__(_)
```

**Desktop app for stream alerts and a streamer-focused Video Editor workflow.**

Alert! Alert! now has two real products in one app:

- `Alert Creator` for quick alert clips, trims, crops, audio treatment, and exports.
- `Video Editor` for turning stream VOD moments into shortform clips and then reusing those edits for longform YouTube cuts.

![Python](https://img.shields.io/badge/Python-3.10+-blue?logo=python&logoColor=white)
![Flask](https://img.shields.io/badge/Flask-Backend-green?logo=flask&logoColor=white)
![FFmpeg](https://img.shields.io/badge/FFmpeg-Powered-orange?logo=ffmpeg&logoColor=white)
![License](https://img.shields.io/badge/License-MIT-yellow)

---

## What It Does

### Alert Creator
- Load a remote video URL or local media file.
- Trim, crop, zoom, choose aspect ratios, and export alerts fast.
- Use separate audio, normalization, fades, and image override modes.
- Run Windows one-click runtime install for `ffmpeg`, `ffprobe`, and `yt-dlp`.

### Video Editor
- Create multi-clip VOD projects for stream sessions.
- Use shared Twitch auth via `auth.deutschmark.online`.
- Pull recent Twitch VODs, Twitch markers, and Twitch clips.
- Import manual timestamps from any hotkey or marker workflow.
- Surface all those moments in a `Session Inbox`.
- Prep moments as shorts with streamer-specific presets:
  - `Gameplay Focus`
  - `Facecam Top`
  - `Baked Text Punch`
- Batch-prep a whole inbox and batch-queue prepared shorts for longform.
- Stitch clips into a preview sequence, transcribe captions, and export.
- Build a horizontal longform derivative from queued prepared shorts.
- Use a saved facecam guide instead of auto-detection for recurring stream layouts.

### Captioning
- 1-click caption dependency install for `faster-whisper` + `torch`.
- Managed captioning virtualenv owned by the app.
- Optional `pyannote.audio` install for diarization.
- Editable captions with speaker colors, ASS/SRT export, and burn-in control.

---

## Streamer Beta Flow

The current intended beta loop is:

1. Connect Twitch.
2. Load a recent VOD or local stream recording.
3. Save session metadata.
4. Import Twitch markers, Twitch clips, or manual timestamps.
5. Prep the surfaced moments into shorts.
6. Stitch the chosen clips into a shortform sequence.
7. Transcribe and style captions.
8. Export shortform.
9. Queue prepared shorts for longform.
10. Build and export the longform derivative.

The Video Editor now includes a `Workflow Checklist` panel that tracks this flow per project and shows the next blocker directly in the UI.

---

## Current Beta Status

### Working Now
- Alert Creator core trim/crop/export workflow.
- Desktop shell with embedded app window.
- Shared Twitch login in the desktop app.
- Twitch VOD listing.
- Twitch marker import.
- Twitch clip import mapped back to VOD offsets when Twitch provides them.
- Manual marker import.
- Session Inbox and short prep flow.
- Batch short prep and batch longform queue actions.
- One-click caption dependency install path in the app.
- Caption editing and burned-caption export.
- Popular export formats:
  - `Shorts / Reels`
  - `4:5 Feed`
  - `Square`
  - `16:9 Landscape`
- Composition-aware short exports.
- Saved facecam guide for `Facecam Top`.
- Longform project creation from queued prepared shorts.

### Still Incomplete
- True automatic facecam detection. This is intentionally not the beta path anymore.
- Automatic “best moments” ranking beyond imported Twitch markers/clips/source moments.
- Advanced multi-session longform assembly.
- Full automated browser regression coverage.
- Live deployment verification from this repository alone. Shared auth depends on `auth.deutschmark.online`.

---

## Requirements

### Runtime
- Windows is the primary desktop target.
- Python `3.10+` is recommended when running from source.
- Internet access is required for:
  - one-click runtime install
  - one-click caption install
  - Twitch auth
  - remote VOD/clip loading

### Shared Twitch Auth
Shared Twitch auth is wired for:

- `http://localhost:<port>`
- approved `deutschmark.online` origins

The desktop shell starts the app on `localhost` and keeps `auth.deutschmark.online` plus Twitch OAuth inside the app window.

---

## One-Click Installs

### Runtime Tools
On Windows, the app can auto-install missing runtime tools into a user-local runtime folder:

- `ffmpeg`
- `ffprobe`
- `yt-dlp`
- optional `deno`

No admin install is required for that managed runtime path.

### Caption Dependencies
The app now installs caption dependencies into its own managed captioning virtualenv:

- `faster-whisper`
- `torch`
- optional `pyannote.audio`

The caption installer is exposed in the app’s dependency setup and is intended to be the normal path, not a manual `pip` workflow.

---

## Quick Start

### Option 1: Download the EXE

1. Download `alert-alert.exe` from [Releases](https://github.com/thedeutschmark/alert-alert/releases).
2. Launch it.
3. Allow runtime install if prompted.
4. Use `Dependency Setup` if you need to repair runtime or caption dependencies.

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

The local app runs on `http://localhost:3000` by default unless overridden by environment variables.

---

## How To Test The Beta

### Alert Creator
1. Load a URL or local file.
2. Trim and crop it.
3. Process the alert.
4. Download the export.

### Video Editor
1. Open `Video Editor`.
2. Log in with Twitch.
3. Confirm the app shows the connected account in the shared auth header and the Video Editor session panel.
4. Refresh VODs and load one.
5. Import Twitch markers or Twitch clips.
6. Optionally paste manual timestamps too.
7. Set the facecam guide if the stream layout has a consistent camera box.
8. Prep moments into shorts.
9. Download and stitch clips.
10. Run the caption pass.
11. Open `Build & Deliver`.
12. Render the shortform project.
13. Queue prepared shorts for longform.
14. Build the longform project.
15. Use the longform handoff card to open the derived project.
16. Render the longform project.

If you just want the fastest real test, use:

1. `Load Selected VOD`
2. `Import Twitch Markers`
3. `Prep All Inbox`
4. `Download Clips`
5. `Transcribe`
6. `Render Video`
7. `Build Longform Project`
8. `Open Longform Project`
9. `Render Video`

Expected UI checkpoints during beta:

1. The `Workflow Checklist` should move forward without hidden blockers.
2. The operation rail should report the last important success or failure.
3. `Clip Preview` should show source-side framing controls.
4. `Project Preview` should show output-aware composition context before render.
5. `Caption Pass` should expose caption runtime install state directly in that step.
6. `Build Longform Project` should not silently switch you into another project.

---

## Facecam Guide

The beta path for facecam handling is user-defined layout memory, not detection.

You can now:

- enable a facecam guide
- drag and resize it in `Clip Preview`
- apply quick corner presets
- save it with the project
- remember it per channel locally

`Facecam Top` exports use that saved guide to bias framing and caption safe area.

---

## Building The EXE

Use the checked-in spec file and build script.

### Windows

```bat
build.bat
```

This builds the desktop executable to `dist\alert-alert.exe`.

### Manual PyInstaller Build

```bash
pip install pyinstaller
pip install PySide6
python -m PyInstaller --clean --noconfirm AlertCreator.spec
```

---

## Troubleshooting

### Caption install still fails
- Check internet access.
- Check antivirus/quarantine behavior.
- Retry from `Dependency Setup`.
- Make sure a matching Python runtime is available when running from source.

### Shared Twitch auth does not work
- Run the app on `http://localhost:<port>` or an approved `deutschmark.online` origin.
- Retry the login flow inside the desktop app.
- On localhost, the app now uses a same-origin shared-auth bridge, so the callback should return to the app and populate the connected session instead of leaving the UI disconnected.
- Confirm `auth.deutschmark.online` is reachable from your machine.

### Twitch clips do not import
- Twitch clips only map back when Twitch provides usable `vod_offset` data.
- If offsets are missing or tied to a different VOD, those clips are skipped.

### Port conflict
The app defaults to `localhost:3000` and can search for another open localhost port automatically. If startup still fails, close the conflicting process and relaunch.

### YouTube or remote URL issues
- Update `yt-dlp`.
- Retry after runtime auto-install/update.
- Install optional `deno` if YouTube challenge handling still fails.

---

## License

MIT License — see [LICENSE](LICENSE).

Third-party runtime notices are in [THIRD_PARTY_NOTICES.txt](THIRD_PARTY_NOTICES.txt).

---

## Credits

Created by **deutschmark**

Built with:
- [Flask](https://flask.palletsprojects.com/)
- [FFmpeg](https://ffmpeg.org/)
- [yt-dlp](https://github.com/yt-dlp/yt-dlp)
- [PySide6](https://doc.qt.io/qtforpython-6/)
