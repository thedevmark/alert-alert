# Handover Brief: Should Clipline go fully native (drop the web stack)?

You are a software-architecture agent. Produce a **decision-ready, evidence-grounded** analysis of whether **Clipline** should be rebuilt as a fully native PySide6 **Qt Widgets** app (no Flask, no HTTP, no HTML/CSS/JS, no QtWebEngine). **Do not write production code.** You may write *throwaway* probe scripts to prove/measure claims. **Measure and prove — do not assert.** Take a clear position at the end; do not hedge into a non-answer.

## Background (why this question exists)

Clipline's sibling app, **Alert! Alert!** (a yt-dlp + ffmpeg clip trimmer), was just analyzed for the same question and the answer was a proven "go native." The decisive findings there were:

1. **The web engine is essentially the entire binary.** `Qt6WebEngineCore.dll` was **196 MB** and the WebEngine `resources/` folder another **102 MB** — ~298 MB of Chromium producing a 262 MB onefile exe that renders a handful of panels. Dropping QtWebEngine is projected to take the exe from ~262 MB to ~100 MB.
2. **The embedded QtWebEngine (open-source Qt wheel) ships NO proprietary codecs**, so its `<video>` element cannot decode H.264/AAC. Alert! Alert! had to add a whole subsystem (`ensure_preview_proxy`) that transcodes H.264 → VP9/WebM just so the preview could play, and even biased its yt-dlp format selection toward VP9 to avoid H.264.
3. **Proven fix:** a ~70-line native `QMediaPlayer` + `QVideoWidget` PoC played the *identical* H.264 clip start-to-finish with zero error (Windows Media Foundation decodes H.264 natively). `Qt6Multimedia.dll` ships in the *same* PySide6 wheel, so native video adds ~0 MB. Going native deleted the entire proxy subsystem.
4. **The valuable code survived.** The ffmpeg/yt-dlp pipeline was reused almost unchanged; only the UI + HTTP transport were rewritten.

The owner's instinct ("it doesn't feel like it should be a web app") was validated by hard numbers, not aesthetics.

## What's different about Clipline

Clipline is a **video editor** (extracted from a shared toolkit), not a simple trimmer. That changes the calculus in BOTH directions, and you must weigh it:
- **More upside:** live preview + frame-accurate scrubbing is the *core* of an editor, so native playback quality and exact-frame seeking matter more than in a trimmer. If Clipline previews H.264 in a `<video>`, it has the same codec/proxy pain — and it hurts more.
- **More cost:** an editor's UI (timeline, multiple tracks, effects, drag interactions) is far heavier to re-implement in Qt Widgets than a 4-step trimmer. The reusable-pipeline-to-rewrite-UI ratio is likely worse.

## Investigation steps (do these, grounded in the actual repo)

You will be pointed at the Clipline repo. Confirm the path, then:

1. **Confirm the stack.** Is it Flask + QtWebEngine + `static/` HTML/JS, like Alert! Alert!? Or Electron, Tauri, native Qt already, something else? Read the entry point, the desktop shell, and the UI directory. If it is NOT a QtWebEngine web app, say so immediately — the analysis changes.
2. **Measure the exe/bundle weight of the web engine.** Find the build spec (PyInstaller `.spec`, or Electron/other packager config). Locate and measure `Qt6WebEngineCore.dll` and the WebEngine `resources/` (or Electron's bundled Chromium). Report sizes and the total artifact size. Quantify what fraction is the web engine. (On Windows, the PySide6 wheel path is typically `…/site-packages/PySide6/`.)
3. **Find the codec/preview workaround, if any.** Grep for: a curated "playable codecs" set, any transcode-to-WebM/VP9 "proxy" logic, `DEMUXER_ERROR`, MIME/`serve-clip`-style routes, or format-selection biased toward VP9/AV1. Their presence is direct evidence of the QtWebEngine codec limitation. Read `<video>`/preview code.
4. **Prove the native playback claim for Clipline's real media.** Write a throwaway PoC (model it on the pattern below) that generates or uses a representative H.264 (and any other codec Clipline handles) clip and plays it via `QMediaPlayer` + `QVideoWidget`. Confirm it decodes natively (no error, `hasVideo()` true, position advances). Also assess **frame-accurate seeking**: `QMediaPlayer.setPosition` is approximate; editors usually need exact frames — verify whether Clipline already extracts exact frames via ffmpeg (that approach carries over) or relies on `<video>.currentTime`.
5. **Inventory the UI** and split it into REUSABLE (Python pipeline: ffmpeg/yt-dlp/effects logic — quantify lines) vs. REWRITE (HTML/JS/CSS frontend + HTTP route layer — quantify lines). Identify the genuinely hard native ports (timeline, multi-track view, scrubbing, any canvas/WebGL effects, onboarding) vs. trivial widget swaps.
6. **Assess editor-specific native feasibility.** A timeline is the crux. Evaluate `QGraphicsView`/`QGraphicsScene` for the timeline + clips + playhead, `QGraphicsVideoItem` for preview-with-overlays composition (note: `QVideoWidget` does NOT compose cleanly with child widgets — the scene-graph approach is the clean route), and whether any effects rely on WebGL/Canvas/CSS that have no easy Qt equivalent (these are the real risks).

## Throwaway native-playback PoC pattern (adapt as needed)

```python
import sys, subprocess
from pathlib import Path
from PySide6.QtCore import QUrl, QTimer
from PySide6.QtWidgets import QApplication, QMainWindow
from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput
from PySide6.QtMultimediaWidgets import QVideoWidget

clip = Path("_poc.mp4").resolve()
subprocess.run(["ffmpeg","-y","-v","quiet","-f","lavfi","-i","testsrc=duration=2:size=640x360:rate=24",
  "-f","lavfi","-i","sine=frequency=440:duration=2","-c:v","libx264","-pix_fmt","yuv420p","-c:a","aac",str(clip)],check=True)
app = QApplication(sys.argv); win = QMainWindow(); vw = QVideoWidget(); win.setCentralWidget(vw)
p = QMediaPlayer(); ao = QAudioOutput(); p.setAudioOutput(ao); p.setVideoOutput(vw)
err = {"e": None}; p.errorOccurred.connect(lambda e,m: err.__setitem__("e", f"{e} {m}"))
p.setSource(QUrl.fromLocalFile(str(clip))); p.play(); win.show()
def chk():
    ok = err["e"] is None and p.hasVideo() and p.position() > 0
    print("error:", err["e"] or "NONE", "hasVideo:", p.hasVideo(), "pos:", p.position(), "->", "PLAYS NATIVELY" if ok else "FAILED")
    clip.unlink(missing_ok=True); app.quit()
QTimer.singleShot(3500, chk); app.exec()
```

## Deliverable (your final message, markdown)

1. **TL;DR recommendation** (go native / stay web / middle-path), one paragraph, with the single most decisive fact.
2. **Stack confirmation** — what Clipline is actually built on.
3. **Web-engine weight** — measured DLL/resource sizes and projected size after dropping it.
4. **Codec/preview situation** — workarounds found (or absence), and the native-playback PoC result.
5. **Reusable vs. rewrite** — line-count split; the hard ports called out (esp. timeline + scrubbing + any effects).
6. **Pros/cons** of (A) full native rewrite, (B) keep web stack, (C) lock-down middle path — covering effort/risk, bundle size, startup, codec correctness, native feel, frame-accuracy, and fit for a video editor.
7. **Effort estimate** (relative: quick/reusable vs. slow/risky) + de-risking sequence (e.g., prove native preview + one timeline track first; defer effects/onboarding).
8. **Clear recommendation** with the honest catch stated.

## Rules
- Cite real files as `path:line`. Report measured numbers, not guesses.
- If the premise is wrong (e.g., Clipline isn't a web app, or its preview already plays H.264), say so up front and pivot the analysis.
- Take a position. The owner wants proof, not vibes — they have been burned by confident-but-wrong before.
