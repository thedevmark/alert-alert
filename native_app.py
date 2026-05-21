"""Alert! Alert! — native PySide6 app (no Flask, no QtWebEngine, no HTML/JS).

A native Qt Widgets clip tool for streamers: load a source (URL via yt-dlp, or a
local file), preview it natively (QMediaPlayer + QVideoWidget/QGraphicsVideoItem
— H.264 decoded by the OS, no WebM proxy), set an aspect-locked crop + in/out
trim, and export a square alert clip with ffmpeg.

The heavy pipeline (yt-dlp profiles/retries, ffprobe, ffmpeg location, tool
discovery) is imported unchanged from the existing modules; only the UI and the
direct ffmpeg export are new here.

Run: python native_app.py
"""
import os
import sys
import uuid
import subprocess
import json
from pathlib import Path

from PySide6.QtCore import (
    Qt, QThread, Signal, QUrl, QRectF, QPointF, QSizeF, QTimer, QPoint, QRect,
    QPropertyAnimation, QEasingCurve, QVariantAnimation,
)
from PySide6.QtGui import (
    QColor, QPen, QBrush, QPainter, QAction, QPixmap, QIcon, QPainterPath, QRegion, QRadialGradient,
)
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLineEdit, QPushButton, QLabel, QFileDialog, QSlider, QComboBox, QCheckBox,
    QGraphicsView, QGraphicsScene, QGraphicsRectItem, QGraphicsPixmapItem, QProgressBar, QFrame,
    QListWidget, QListWidgetItem, QGraphicsDropShadowEffect, QScrollArea,
)
from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput
from PySide6.QtMultimediaWidgets import QGraphicsVideoItem

from app import (
    DOWNLOADS_DIR, YTDLP, FFMPEG, FFPROBE, FFMPEG_DIR, run_subprocess,
    probe_media_file, is_youtube_url, has_deno_runtime, get_env, get_output_dir,
    download_separate_audio, INTERNAL_DIR,
)
from ytdlp import build_ytdlp_profiles, run_ytdlp_with_retries

ACCENT = "#5E8FCB"
SAMPLE_URL = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"  # sample clip to try without your own

# label -> aspect ratio (w/h); None = free/original
RATIOS = {
    "Original": None, "1:1": 1 / 1, "16:9": 16 / 9, "9:16": 9 / 16,
    "4:3": 4 / 3, "3:4": 3 / 4, "21:9": 21 / 9,
}


def probe_dimensions(path):
    """Return (width, height, duration_seconds) via ffprobe."""
    try:
        r = run_subprocess(
            [FFPROBE, "-v", "quiet", "-print_format", "json",
             "-show_streams", "-show_format", str(path)], timeout=20)
        info = json.loads(r.stdout or "{}")
        vs = next((s for s in info.get("streams", []) if s.get("codec_type") == "video"), {})
        w = int(vs.get("width", 0) or 0)
        h = int(vs.get("height", 0) or 0)
        dur = float(info.get("format", {}).get("duration", 0) or 0)
        return w, h, dur
    except Exception:
        return 0, 0, 0.0


# ──────────────────────────────────────────────────────────────────────
# Crop overlay
# ──────────────────────────────────────────────────────────────────────
class CropItem(QGraphicsRectItem):
    """An aspect-lockable, draggable, resizable crop rectangle whose rect() is
    in source-pixel coordinates (the scene is sized to the source video)."""

    HANDLE = 9  # base handle half-size in scene px (scaled by source size)

    def __init__(self, on_change=None):
        super().__init__()
        self.setAcceptHoverEvents(True)
        self.setPen(QPen(QColor(ACCENT), 0))  # cosmetic (0-width) pen
        self.setBrush(QBrush(Qt.transparent))
        self.setZValue(10)
        self._bounds = QRectF(0, 0, 0, 0)   # source frame rect
        self._ratio = None                  # locked aspect (w/h) or None
        self._drag_mode = None              # 'move' | corner/edge id | None
        self._press_scene = QPointF()
        self._press_rect = QRectF()
        self._on_change = on_change

    # --- geometry helpers ---
    def set_bounds(self, w, h):
        self._bounds = QRectF(0, 0, w, h)

    def _handle_size(self):
        return max(self.HANDLE, self._bounds.width() * 0.012)

    def _handles(self):
        r = self.rect()
        s = self._handle_size()
        pts = {
            "tl": r.topLeft(), "tr": r.topRight(), "bl": r.bottomLeft(), "br": r.bottomRight(),
            "t": QPointF(r.center().x(), r.top()), "b": QPointF(r.center().x(), r.bottom()),
            "l": QPointF(r.left(), r.center().y()), "rt": QPointF(r.right(), r.center().y()),
        }
        return {k: QRectF(p.x() - s, p.y() - s, 2 * s, 2 * s) for k, p in pts.items()}

    def boundingRect(self):
        s = self._handle_size()
        return self.rect().adjusted(-s, -s, s, s)

    def paint(self, painter, option, widget=None):
        painter.setRenderHint(QPainter.Antialiasing, True)
        r = self.rect()
        pen = QPen(QColor(ACCENT))
        pen.setCosmetic(True)
        pen.setWidth(2)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        painter.drawRect(r)
        # rule-of-thirds guides
        thin = QPen(QColor(255, 255, 255, 60)); thin.setCosmetic(True)
        painter.setPen(thin)
        for i in (1, 2):
            x = r.left() + r.width() * i / 3
            y = r.top() + r.height() * i / 3
            painter.drawLine(QPointF(x, r.top()), QPointF(x, r.bottom()))
            painter.drawLine(QPointF(r.left(), y), QPointF(r.right(), y))
        # handles
        painter.setPen(QPen(QColor(ACCENT), 0))
        painter.setBrush(QBrush(QColor(ACCENT)))
        for hr in self._handles().values():
            painter.drawRect(hr)

    # --- interaction ---
    def _hit(self, pos):
        for name, hr in self._handles().items():
            if hr.contains(pos):
                return name
        if self.rect().contains(pos):
            return "move"
        return None

    def hoverMoveEvent(self, event):
        name = self._hit(event.pos())
        cursors = {
            "tl": Qt.SizeFDiagCursor, "br": Qt.SizeFDiagCursor,
            "tr": Qt.SizeBDiagCursor, "bl": Qt.SizeBDiagCursor,
            "t": Qt.SizeVerCursor, "b": Qt.SizeVerCursor,
            "l": Qt.SizeHorCursor, "rt": Qt.SizeHorCursor, "move": Qt.SizeAllCursor,
        }
        self.setCursor(cursors.get(name, Qt.ArrowCursor))
        super().hoverMoveEvent(event)

    def mousePressEvent(self, event):
        self._drag_mode = self._hit(event.pos())
        self._press_scene = event.scenePos()
        self._press_rect = QRectF(self.rect())
        event.accept()

    def mouseMoveEvent(self, event):
        if not self._drag_mode:
            return
        delta = event.scenePos() - self._press_scene
        r = QRectF(self._press_rect)
        if self._drag_mode == "move":
            r.translate(delta)
            r = self._clamp_move(r)
        else:
            r = self._resize(r, self._drag_mode, delta)
        self.prepareGeometryChange()
        self.setRect(r)
        if self._on_change:
            self._on_change()
        event.accept()

    def mouseReleaseEvent(self, event):
        self._drag_mode = None
        event.accept()

    def _clamp_move(self, r):
        b = self._bounds
        if r.left() < b.left():
            r.moveLeft(b.left())
        if r.top() < b.top():
            r.moveTop(b.top())
        if r.right() > b.right():
            r.moveRight(b.right())
        if r.bottom() > b.bottom():
            r.moveBottom(b.bottom())
        return r

    def _resize(self, r, mode, delta):
        minw = max(16, self._bounds.width() * 0.05)
        if "l" == mode or "tl" in mode or "bl" in mode:
            r.setLeft(min(r.left() + delta.x(), r.right() - minw))
        if mode in ("rt", "tr", "br"):
            r.setRight(max(r.right() + delta.x(), r.left() + minw))
        if "t" == mode or "tl" in mode or "tr" in mode:
            r.setTop(min(r.top() + delta.y(), r.bottom() - minw))
        if mode in ("b", "bl", "br"):
            r.setBottom(max(r.bottom() + delta.y(), r.top() + minw))
        if self._ratio:
            # lock aspect: derive height from width, anchor sensibly
            w = r.width()
            h = w / self._ratio
            if mode in ("t", "b"):
                w = r.height() * self._ratio
                r.setWidth(w) if mode == "b" else r.setLeft(r.right() - w)
                h = r.height()
            else:
                if "t" in mode:
                    r.setTop(r.bottom() - h)
                else:
                    r.setHeight(h)
        # clamp within bounds
        r = r.intersected(self._bounds)
        return r

    def apply_ratio(self, ratio, zoom=1.0):
        """Set crop to a centered rect of `ratio`, scaled by zoom (0.1..1.0)."""
        self._ratio = ratio
        b = self._bounds
        if ratio is None:
            w, h = b.width() * zoom, b.height() * zoom
        else:
            # largest rect of this ratio that fits, then * zoom
            if b.width() / b.height() > ratio:
                h = b.height(); w = h * ratio
            else:
                w = b.width(); h = w / ratio
            w *= zoom; h *= zoom
        cx, cy = b.center().x(), b.center().y()
        r = QRectF(cx - w / 2, cy - h / 2, w, h)
        self.prepareGeometryChange()
        self.setRect(r.intersected(b))
        if self._on_change:
            self._on_change()

    def crop_px(self):
        """Return integer (x, y, w, h) clamped to source bounds for ffmpeg."""
        r = self.rect().intersected(self._bounds)
        x = max(0, int(round(r.left())))
        y = max(0, int(round(r.top())))
        w = int(round(r.width()))
        h = int(round(r.height()))
        w = max(2, min(w, int(self._bounds.width()) - x))
        h = max(2, min(h, int(self._bounds.height()) - y))
        # even dimensions for yuv420p
        w -= w % 2; h -= h % 2
        return x, y, w, h


class CropView(QGraphicsView):
    """Shows the video with the crop overlay and dims the area outside the crop."""

    def __init__(self):
        super().__init__()
        self.setRenderHint(QPainter.Antialiasing, True)
        self.setStyleSheet("background:#000; border:none;")
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self.video_item = QGraphicsVideoItem()
        self._scene.addItem(self.video_item)
        self.image_item = QGraphicsPixmapItem(); self.image_item.setZValue(5); self.image_item.hide()
        self._scene.addItem(self.image_item)
        self.crop = CropItem(on_change=self.viewport().update)
        self._scene.addItem(self.crop)
        self._w = self._h = 0

    def show_image(self, path):
        """Preview a still-image override (cover-scaled), or revert to the video."""
        if path:
            pm = QPixmap(str(path))
            if not pm.isNull() and self._w:
                pm = pm.scaled(int(self._w), int(self._h), Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
                self.image_item.setPixmap(pm)
                self.image_item.setOffset((self._w - pm.width()) / 2, (self._h - pm.height()) / 2)
                self.image_item.show(); self.video_item.hide()
        else:
            self.image_item.hide(); self.video_item.show()
        self.viewport().update()

    def set_source_size(self, w, h):
        self._w, self._h = w, h
        self.video_item.setSize(QSizeF(w, h))
        self._scene.setSceneRect(0, 0, w, h)
        self.crop.set_bounds(w, h)
        self.crop.apply_ratio(None, 1.0)
        self.fit()

    def fit(self):
        if self._w and self._h:
            self.fitInView(QRectF(0, 0, self._w, self._h), Qt.KeepAspectRatio)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.fit()

    def drawForeground(self, painter, rect):
        # dim everything in the video frame except the crop hole
        if not self._w:
            return
        frame = QRectF(0, 0, self._w, self._h)
        crop = self.crop.rect().intersected(frame)
        painter.setBrush(QColor(0, 0, 0, 120))
        painter.setPen(Qt.NoPen)
        # four bands around the crop
        painter.drawRect(QRectF(frame.left(), frame.top(), frame.width(), crop.top() - frame.top()))
        painter.drawRect(QRectF(frame.left(), crop.bottom(), frame.width(), frame.bottom() - crop.bottom()))
        painter.drawRect(QRectF(frame.left(), crop.top(), crop.left() - frame.left(), crop.height()))
        painter.drawRect(QRectF(crop.right(), crop.top(), frame.right() - crop.right(), crop.height()))


# ──────────────────────────────────────────────────────────────────────
# Workers
# ──────────────────────────────────────────────────────────────────────
class DownloadWorker(QThread):
    progress = Signal(str)
    finished_ok = Signal(str, str)  # (path, title)
    failed = Signal(str)

    def __init__(self, url):
        super().__init__()
        self.url = url

    def run(self):
        try:
            job_dir = DOWNLOADS_DIR / uuid.uuid4().hex
            job_dir.mkdir(parents=True, exist_ok=True)
            template = str(job_dir / "clip.%(ext)s")
            title_file = job_dir / "title.txt"
            youtube = is_youtube_url(self.url)

            def build_cmd(profile, use_sections):
                cmd = [YTDLP, "--no-playlist", "--force-ipv4", "-f", profile["format"],
                       "--retries", "5", "--fragment-retries", "5", "-o", template,
                       "--print-to-file", "%(title)s", str(title_file)]  # capture real title
                if profile.get("sort"):
                    cmd += ["-S", profile["sort"]]
                cmd += profile.get("extra", [])
                if FFMPEG_DIR:
                    cmd += ["--ffmpeg-location", FFMPEG_DIR]
                cmd.append(self.url)
                return cmd

            profiles = build_ytdlp_profiles("video", youtube=youtube, has_deno=has_deno_runtime())

            def on_attempt(i, total, profile, _s):
                self.progress.emit(f"Downloading ({i}/{total}) [{profile['name']}]…")

            ok, _stderr, err = run_ytdlp_with_retries(
                job_dir, file_glob="clip.*", build_cmd=build_cmd, profiles=profiles,
                section_modes=[False], run_subprocess=run_subprocess, timeout=360,
                default_error="Download failed", on_attempt=on_attempt, clean_first_attempt=False)
            if not ok:
                self.failed.emit(err or "Download failed"); return
            files = list(job_dir.glob("clip.*"))
            if not files:
                self.failed.emit("No file downloaded"); return
            self.progress.emit("Verifying…")
            probe = probe_media_file(files[0])
            if not probe["ok"] or not probe["has_video"]:
                self.failed.emit(probe["error"] or "No video stream"); return
            title = ""
            try:
                title = title_file.read_text(encoding="utf-8", errors="replace").strip().splitlines()[0]
            except Exception:
                pass
            self.finished_ok.emit(str(files[0]), title)
        except Exception as e:
            self.failed.emit(str(e))


def build_export_cmd(src, out, crop, trim, out_size, crf, normalize, fade,
                     end_buffer=0, audio_src=None, image_src=None, fade_dur=0.35):
    """Construct the ffmpeg export command. Pure function (unit-testable).

    audio_src: replace the clip's audio with this file (separate-audio override).
    image_src: use this still image as the visual (cover-scaled), keeping audio
               from the clip (static-image override). Crop is ignored for images.
    """
    x, y, w, h = crop
    start, end = trim
    trim_len = max(0.05, end - start)
    total = trim_len + (end_buffer or 0)
    cmd = [FFMPEG, "-y"]

    # --- video input (input 0) ---
    if image_src:
        cmd += ["-loop", "1", "-t", f"{total:.3f}", "-i", str(image_src)]
        vf = (f"scale={out_size}:{out_size}:force_original_aspect_ratio=increase,"
              f"crop={out_size}:{out_size},setsar=1")
    else:
        cmd += ["-ss", f"{start:.3f}", "-to", f"{end:.3f}", "-i", str(src)]
        vf = (f"crop={w}:{h}:{x}:{y},"
              f"scale={out_size}:{out_size}:force_original_aspect_ratio=decrease,"
              f"pad={out_size}:{out_size}:(ow-iw)/2:(oh-ih)/2:black,setsar=1")
        if end_buffer and end_buffer > 0:
            # Freeze last frame (audio ends naturally — tpad+apad deadlocks ffmpeg).
            vf += f",tpad=stop_mode=clone:stop_duration={end_buffer}"

    # --- audio input ---
    if audio_src:
        cmd += ["-t", f"{trim_len:.3f}", "-i", str(audio_src)]
        a_idx = 1
    elif image_src:
        cmd += ["-ss", f"{start:.3f}", "-to", f"{end:.3f}", "-i", str(src)]
        a_idx = 1
    else:
        a_idx = 0

    af = []
    if normalize:
        af.append("loudnorm=I=-16:TP=-1.5:LRA=11")
    if fade and fade != "none":
        fl = float(fade_dur)
        if fade in ("start", "both"):
            af.append(f"afade=t=in:st=0:d={fl}")
        if fade in ("end", "both") and trim_len > fl:
            af.append(f"afade=t=out:st={trim_len - fl:.3f}:d={fl}")

    cmd += ["-map", "0:v:0", "-map", f"{a_idx}:a:0?", "-vf", vf]
    if af:
        cmd += ["-af", ",".join(af)]
    cmd += ["-c:v", "libx264", "-crf", str(crf), "-preset", "veryfast",
            "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k",
            "-movflags", "+faststart"]
    if image_src:
        cmd += ["-t", f"{total:.3f}"]  # bound the looped image
    cmd += [str(out)]
    return cmd


class ExportWorker(QThread):
    progress = Signal(int)
    finished_ok = Signal(str)
    failed = Signal(str)

    def __init__(self, src, out, crop, trim, out_size, crf, normalize, fade,
                 end_buffer=0, audio_src=None, image_src=None, fade_dur=0.35):
        super().__init__()
        self.args = (src, out, crop, trim, out_size, crf, normalize, fade,
                     end_buffer, audio_src, image_src, fade_dur)

    def run(self):
        (src, out, crop, trim, out_size, crf, normalize, fade,
         end_buffer, audio_src, image_src, fade_dur) = self.args
        cmd = build_export_cmd(src, out, crop, trim, out_size, crf, normalize, fade,
                               end_buffer, audio_src, image_src, fade_dur)
        cmd += ["-progress", "pipe:1", "-nostats"]
        total = max(0.001, trim[1] - trim[0]) + (end_buffer or 0)
        try:
            # Merge stderr into stdout so a single read loop drains everything —
            # avoids the classic deadlock of an undrained stderr pipe filling up.
            kwargs = {"stdout": subprocess.PIPE, "stderr": subprocess.STDOUT,
                      "text": True, "env": get_env()}
            if sys.platform == "win32":
                kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
            proc = subprocess.Popen(cmd, **kwargs)
            tail = []
            for line in proc.stdout:
                line = line.strip()
                if line.startswith("out_time_us=") or line.startswith("out_time_ms="):
                    val = line.split("=", 1)[1]
                    if val.isdigit():
                        secs = int(val) / (1_000_000 if "us" in line else 1_000)
                        self.progress.emit(min(99, int(secs / total * 100)))
                elif line and not line.startswith(("frame=", "fps=", "bitrate=", "total_size=",
                                                   "out_time=", "dup_frames=", "drop_frames=",
                                                   "speed=", "progress=", "stream_")):
                    tail.append(line)
            proc.wait()
            if proc.returncode != 0 or not Path(out).exists():
                msg = tail[-1] if tail else "ffmpeg failed"
                self.failed.emit(msg)
                return
            self.progress.emit(100)
            self.finished_ok.emit(str(out))
        except Exception as e:
            self.failed.emit(str(e))


class WaveformWorker(QThread):
    """Render the clip's audio waveform to a PNG via ffmpeg showwavespic."""
    done = Signal(str)

    def __init__(self, src, out):
        super().__init__()
        self.src, self.out = src, out

    def run(self):
        try:
            run_subprocess([FFMPEG, "-y", "-i", str(self.src), "-filter_complex",
                            "aformat=channel_layouts=mono,"
                            "showwavespic=s=1200x140:colors=#9ec0ff:scale=sqrt",
                            "-frames:v", "1", str(self.out)], timeout=60)
            if Path(self.out).exists():
                self.done.emit(str(self.out))
        except Exception:
            pass


class AudioWorker(QThread):
    """Download a separate audio source from a URL (reuses the existing helper)."""
    done = Signal(str)
    failed = Signal(str)

    def __init__(self, url):
        super().__init__()
        self.url = url

    def run(self):
        try:
            job_dir = DOWNLOADS_DIR / ("aud_" + uuid.uuid4().hex)
            job_dir.mkdir(parents=True, exist_ok=True)
            path = download_separate_audio(job_dir, self.url, 0, 0)
            if path and Path(path).exists():
                self.done.emit(str(path))
            else:
                self.failed.emit("No audio downloaded")
        except Exception as e:
            self.failed.emit(str(e))


class DepsInstallWorker(QThread):
    """Download + install the missing third-party tools, with byte-progress.

    Only runs AFTER the user has clicked "Download & Install" in the consent
    panel. Installs only the specific tools reported missing, then refreshes
    both app.py's globals and this module's stale module-globals so the other
    workers (DownloadWorker, ExportWorker, probe_dimensions, …) pick up the
    freshly installed paths.
    """
    # tool_name, downloaded_bytes, total_bytes (total 0 => indeterminate)
    progress = Signal(str, int, int)
    status = Signal(str)
    finished_ok = Signal()
    failed = Signal(str)

    def __init__(self, missing):
        super().__init__()
        # missing: set/list of canonical names from run_deps_check
        self.missing = set(missing)

    def run(self):
        import app
        try:
            need_ffmpeg = bool({"ffmpeg", "ffprobe"} & self.missing)
            need_ytdlp = "yt-dlp" in self.missing

            if need_ffmpeg:
                self.status.emit("Downloading FFmpeg…")

                def ff_cb(done, total):
                    self.progress.emit("Downloading FFmpeg…", done, total)

                app._install_ffmpeg_windows(progress_cb=ff_cb)
                self.status.emit("Installing FFmpeg…")

            if need_ytdlp:
                self.status.emit("Downloading yt-dlp…")

                def yt_cb(done, total):
                    self.progress.emit("Downloading yt-dlp…", done, total)

                app._install_ytdlp_windows(progress_cb=yt_cb)
                self.status.emit("Installing yt-dlp…")

            # Refresh app.py's globals, then this module's STALE module-globals so
            # the workers that captured FFMPEG/FFPROBE/YTDLP/FFMPEG_DIR at import
            # time use the newly installed paths.
            app.refresh_tool_paths()
            g = globals()
            g["FFMPEG"] = app.FFMPEG
            g["FFPROBE"] = app.FFPROBE
            g["YTDLP"] = app.YTDLP
            g["FFMPEG_DIR"] = app.FFMPEG_DIR

            # Verify the tools are actually present now.
            results = app.run_deps_check(force=True)
            still_missing = [n for n in ("ffmpeg", "ffprobe", "yt-dlp")
                             if not results.get(n, {}).get("installed")]
            if still_missing:
                self.failed.emit(
                    "Still missing after install: " + ", ".join(still_missing))
                return
            self.finished_ok.emit()
        except Exception as e:
            self.failed.emit(str(e))


class DepInstallWorker(QThread):
    """Download + install the requested tools (only after explicit user consent),
    reporting progress. Refreshes tool paths in BOTH app and this module."""
    progress = Signal(int, str)
    done = Signal(bool, str)

    def __init__(self, tools):
        super().__init__()
        self.tools = list(tools)  # subset of {"ffmpeg", "yt-dlp"}

    def run(self):
        import app
        try:
            n = max(1, len(self.tools))
            for i, tool in enumerate(self.tools):
                base = int(i / n * 100)
                span = max(1, int(100 / n))

                def cb(downloaded, total, base=base, span=span, tool=tool):
                    pct = base + (int(downloaded / total * span) if total else 0)
                    self.progress.emit(min(99, pct), f"Downloading {tool}…")

                if tool == "ffmpeg":
                    app._install_ffmpeg_windows(progress_cb=cb)
                elif tool == "yt-dlp":
                    app._install_ytdlp_windows(progress_cb=cb)
            app.refresh_tool_paths()
            g = globals()
            g["FFMPEG"] = app.FFMPEG; g["FFPROBE"] = app.FFPROBE
            g["YTDLP"] = app.YTDLP; g["FFMPEG_DIR"] = app.FFMPEG_DIR
            results = app.run_deps_check(force=True)
            missing = app._required_missing(results)
            self.progress.emit(100, "Done")
            if missing:
                self.done.emit(False, "Still missing: " + ", ".join(missing))
            else:
                ver = (results.get("yt-dlp") or {}).get("version") or ""
                self.done.emit(True, ("Ready." + (f" yt-dlp {ver}" if ver else "")))
        except Exception as e:
            self.done.emit(False, str(e))


class Scrubber(QWidget):
    """Combined timeline: waveform + playhead + draggable in/out trim handles
    (Premiere/TikTok style). Click or drag the body to seek; drag a handle to trim."""
    seek_to = Signal(float)              # 0..1 fraction
    trim_changed = Signal(float, float)  # in_frac, out_frac
    HANDLE = 7  # px hit tolerance

    def __init__(self):
        super().__init__()
        self.setFixedHeight(74)
        self.setObjectName("wave")
        self.setMouseTracking(True)
        self._pix = None
        self._frac = 0.0
        self._in = 0.0
        self._out = 1.0
        self._drag = None

    def set_image(self, path):
        self._pix = QPixmap(path); self.update()

    def set_position(self, frac):
        self._frac = max(0.0, min(1.0, frac)); self.update()

    def set_region(self, in_frac, out_frac):
        self._in, self._out = in_frac, out_frac; self.update()

    def _hit(self, x):
        w = self.width() or 1
        if abs(x - self._in * w) <= self.HANDLE + 2:
            return "in"
        if abs(x - self._out * w) <= self.HANDLE + 2:
            return "out"
        return "seek"

    def paintEvent(self, event):
        p = QPainter(self); p.setRenderHint(QPainter.Antialiasing, True)
        w, h = self.width(), self.height()
        track = QRectF(0, 0, w, h)
        # track background
        p.setPen(Qt.NoPen); p.setBrush(QColor("#0c0e13")); p.drawRoundedRect(track, 8, 8)
        # waveform centered on the track baseline
        if self._pix and not self._pix.isNull():
            p.drawPixmap(self.rect(), self._pix)
        # faint center baseline
        p.setPen(QPen(QColor(255, 255, 255, 22), 1)); p.drawLine(QPointF(0, h / 2), QPointF(w, h / 2))
        xi, xo = self._in * w, self._out * w
        px = max(xi, min(self._frac * w, xo))
        # dim everything outside the trim region
        p.setPen(Qt.NoPen); p.setBrush(QColor(8, 9, 12, 170))
        p.drawRect(QRectF(0, 0, xi, h)); p.drawRect(QRectF(xo, 0, w - xo, h))
        # played progress inside the trim (subtle accent wash up to the playhead)
        if px > xi:
            c = QColor(ACCENT); c.setAlpha(48); p.setBrush(c)
            p.drawRect(QRectF(xi, 0, px - xi, h))
        # trim region outline
        pen = QPen(QColor(ACCENT)); pen.setWidth(2); p.setPen(pen); p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(QRectF(xi + 1, 1, max(1.0, xo - xi - 2), h - 2), 6, 6)
        # in / out handles with a grip notch
        for x in (xi, xo):
            p.setPen(Qt.NoPen); p.setBrush(QColor(ACCENT)); p.drawRoundedRect(QRectF(x - 4, 0, 8, h), 3, 3)
            p.setPen(QPen(QColor("#0d1117"), 1))
            p.drawLine(QPointF(x, h * 0.36), QPointF(x, h * 0.64))
        # playhead: bright line + a grabber dot on top
        p.setPen(QPen(QColor("#ffffff"), 2)); p.drawLine(QPointF(px, 0), QPointF(px, h))
        p.setPen(Qt.NoPen); p.setBrush(QColor("#ffffff")); p.drawEllipse(QPointF(px, 7), 5, 5)

    def mousePressEvent(self, event):
        if not self.width():
            return
        self._drag = self._hit(event.position().x())
        if self._drag == "seek":
            self.seek_to.emit(max(0.0, min(1.0, event.position().x() / self.width())))

    def mouseMoveEvent(self, event):
        w = self.width() or 1
        frac = max(0.0, min(1.0, event.position().x() / w))
        if self._drag == "in":
            self._in = min(frac, self._out - 0.01); self.trim_changed.emit(self._in, self._out); self.update()
        elif self._drag == "out":
            self._out = max(frac, self._in + 0.01); self.trim_changed.emit(self._in, self._out); self.update()
        elif self._drag == "seek":
            self.seek_to.emit(frac)
        else:
            self.setCursor(Qt.SizeHorCursor if self._hit(event.position().x()) in ("in", "out") else Qt.PointingHandCursor)

    def mouseReleaseEvent(self, event):
        self._drag = None


# ──────────────────────────────────────────────────────────────────────
# Start screens: shared branded backdrop + first-run welcome + clean empty
# ──────────────────────────────────────────────────────────────────────
class GradientBackdrop(QWidget):
    """Branded navy→amber radial backdrop (web welcome look) filling the window."""

    def __init__(self, parent):
        super().__init__(parent)
        self.setObjectName("emptyBackdrop")

    def showEvent(self, event):
        if self.parent():
            self.setGeometry(self.parent().rect())  # always cover the whole window
        super().showEvent(event)

    def paintEvent(self, event):
        p = QPainter(self); p.setRenderHint(QPainter.Antialiasing, True)
        w, h = self.width(), self.height()
        base = QRadialGradient(w * 0.5, h * 1.15, max(w, h) * 1.25)
        base.setColorAt(0.0, QColor("#182739")); base.setColorAt(0.62, QColor("#0D1521"))
        base.setColorAt(1.0, QColor("#0D1521"))
        p.fillRect(self.rect(), QBrush(base))
        amber = QRadialGradient(w * 0.5, h * 0.27, max(w, h) * 0.62)
        amber.setColorAt(0.0, QColor(255, 181, 71, 40)); amber.setColorAt(0.6, QColor(255, 181, 71, 0))
        p.fillRect(self.rect(), QBrush(amber))

    @staticmethod
    def app_icon(size):
        lp = INTERNAL_DIR / "static" / "img" / "logo.png"
        fav = INTERNAL_DIR / "static" / "favicon.ico"
        if lp.exists():
            return QPixmap(str(lp)).scaled(size, size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        if fav.exists():
            return QIcon(str(fav)).pixmap(size, size)
        return QPixmap()


class WelcomeScreen(GradientBackdrop):
    """First-run onboarding only: glowing icon, wordmark, a led 'Start tour' + Skip."""

    def __init__(self, parent, on_start_tour, on_skip):
        super().__init__(parent)
        outer = QVBoxLayout(self); outer.setAlignment(Qt.AlignCenter); outer.setContentsMargins(24, 24, 24, 24)
        col = QVBoxLayout(); col.setAlignment(Qt.AlignCenter); col.setSpacing(0)
        icon = QLabel(); icon.setAlignment(Qt.AlignCenter); icon.setPixmap(self.app_icon(120))
        glow = QGraphicsDropShadowEffect(self); glow.setColor(QColor(255, 181, 71, 175))
        glow.setBlurRadius(56); glow.setOffset(0, 0); icon.setGraphicsEffect(glow)
        self._glow = QPropertyAnimation(glow, b"blurRadius", self)
        self._glow.setKeyValueAt(0.0, 46); self._glow.setKeyValueAt(0.5, 82); self._glow.setKeyValueAt(1.0, 46)
        self._glow.setDuration(2800); self._glow.setEasingCurve(QEasingCurve.InOutSine); self._glow.setLoopCount(-1)
        self._glow.start()
        col.addWidget(icon, alignment=Qt.AlignCenter); col.addSpacing(22)
        wordmark = QLabel('Alert<span style="color:#FFB547;">!</span> Alert<span style="color:#EEF4FA;">!</span>')
        wordmark.setTextFormat(Qt.RichText); wordmark.setObjectName("emptyWordmark"); wordmark.setAlignment(Qt.AlignCenter)
        col.addWidget(wordmark, alignment=Qt.AlignCenter); col.addSpacing(12)
        head = QLabel("Turn any video into a finished alert clip."); head.setObjectName("emptyTitle"); head.setAlignment(Qt.AlignCenter)
        col.addWidget(head, alignment=Qt.AlignCenter); col.addSpacing(14)
        sub = QLabel("Pull a clip from a URL or a file, crop and trim it, and export a clean stream "
                     "alert. We'll walk you through your first one — about a minute.")
        sub.setObjectName("emptySub"); sub.setAlignment(Qt.AlignCenter); sub.setWordWrap(True); sub.setFixedWidth(470)
        col.addWidget(sub, alignment=Qt.AlignCenter); col.addSpacing(32)
        self.start_btn = QPushButton("Get started  →"); self.start_btn.setObjectName("ctaAmber")
        self.start_btn.setFixedHeight(52); self.start_btn.setMinimumWidth(206); self.start_btn.setCursor(Qt.PointingHandCursor)
        sh = QGraphicsDropShadowEffect(self); sh.setColor(QColor(255, 181, 71, 110))
        sh.setBlurRadius(48); sh.setOffset(0, 0); self.start_btn.setGraphicsEffect(sh)  # soft halo, not a hard drop
        self.start_btn.clicked.connect(on_start_tour)
        col.addWidget(self.start_btn, alignment=Qt.AlignCenter); col.addSpacing(18)
        self.skip_btn = QPushButton("Skip"); self.skip_btn.setObjectName("skipLink")
        self.skip_btn.setCursor(Qt.PointingHandCursor); self.skip_btn.clicked.connect(on_skip)
        col.addWidget(self.skip_btn, alignment=Qt.AlignCenter)
        outer.addLayout(col)


class EmptyScreen(GradientBackdrop):
    """Clean centered add screen (no video, no sample) — shown when the queue is empty."""

    def __init__(self, parent, on_url, on_file):
        super().__init__(parent)
        outer = QVBoxLayout(self); outer.setAlignment(Qt.AlignCenter); outer.setContentsMargins(24, 24, 24, 24)
        col = QVBoxLayout(); col.setAlignment(Qt.AlignCenter); col.setSpacing(0)
        icon = QLabel(); icon.setAlignment(Qt.AlignCenter); icon.setPixmap(self.app_icon(82))
        glow = QGraphicsDropShadowEffect(self); glow.setColor(QColor(255, 181, 71, 110))
        glow.setBlurRadius(38); glow.setOffset(0, 0); icon.setGraphicsEffect(glow)
        col.addWidget(icon, alignment=Qt.AlignCenter); col.addSpacing(18)
        head = QLabel("Add a clip"); head.setObjectName("emptyTitle"); head.setAlignment(Qt.AlignCenter)
        col.addWidget(head, alignment=Qt.AlignCenter); col.addSpacing(10)
        sub = QLabel("Paste a video URL, or open a file from your computer."); sub.setObjectName("emptySub")
        sub.setAlignment(Qt.AlignCenter); sub.setWordWrap(True); sub.setFixedWidth(420)
        col.addWidget(sub, alignment=Qt.AlignCenter); col.addSpacing(26)
        self.url = QLineEdit(); self.url.setPlaceholderText("Paste a video URL…")
        self.url.setObjectName("emptyInput"); self.url.setFixedWidth(440); self.url.setFixedHeight(50)
        self.url.returnPressed.connect(lambda: on_url(self.url.text()))
        col.addWidget(self.url, alignment=Qt.AlignCenter); col.addSpacing(12)
        row = QHBoxLayout(); row.setSpacing(10); row.setAlignment(Qt.AlignCenter)
        b_url = QPushButton("Add URL"); b_url.setObjectName("ctaAmber"); b_url.setFixedHeight(48); b_url.setMinimumWidth(140)
        b_url.setCursor(Qt.PointingHandCursor); b_url.clicked.connect(lambda: on_url(self.url.text()))
        b_file = QPushButton("Add file"); b_file.setObjectName("ctaGhost"); b_file.setFixedHeight(48)
        b_file.setCursor(Qt.PointingHandCursor); b_file.clicked.connect(on_file)
        row.addWidget(b_url); row.addWidget(b_file)
        col.addLayout(row)
        outer.addLayout(col)


# ──────────────────────────────────────────────────────────────────────
# Dependency consent overlay (shown only when ffmpeg/ytdlp are missing)
# ──────────────────────────────────────────────────────────────────────
FFMPEG_SOURCE_URL = "https://www.gyan.dev/ffmpeg/builds/"
YTDLP_SOURCE_URL = "https://github.com/yt-dlp/yt-dlp"


class DepsConsentOverlay(GradientBackdrop):
    """Full-screen, non-dismissable-until-resolved consent panel — the first
    pre-onboarding step when tools are missing. Nothing downloads until the user
    explicitly clicks "Download & Install" (we fetch third-party software for them)."""

    def __init__(self, parent, results, on_install, on_continue):
        super().__init__(parent)  # GradientBackdrop: full-screen branded backdrop
        self._on_install = on_install
        self._on_continue = on_continue
        self._missing = [n for n in ("ffmpeg", "ffprobe", "yt-dlp")
                         if not results.get(n, {}).get("installed")]

        outer = QVBoxLayout(self); outer.setAlignment(Qt.AlignCenter)
        card = QFrame(); card.setObjectName("welcomeCard"); card.setFixedWidth(520)
        c = QVBoxLayout(card); c.setContentsMargins(36, 34, 36, 30); c.setSpacing(13)

        badge = QLabel("!"); badge.setObjectName("welcomeBadge")
        badge.setAlignment(Qt.AlignCenter); badge.setFixedSize(64, 64)
        title = QLabel("First-time setup")
        title.setObjectName("welcomeTitle"); title.setAlignment(Qt.AlignCenter)

        import re
        def _shortver(v):
            v = (v or "").strip()
            m = re.search(r"\d+(?:\.\d+)+", v)  # clean version number (8.0.1, 2026.03.17)
            return m.group(0) if m else ""
        rows = []
        for key, label in (("ffmpeg", "FFmpeg"), ("ffprobe", "ffprobe"), ("yt-dlp", "yt-dlp")):
            r = results.get(key, {}); ok = r.get("installed"); ver = _shortver(r.get("version"))
            rows.append(("✓  " if ok else "✗  ") + label + (f"   {ver}" if ok and ver else ("   not found" if not ok else "")))
        statuses = QLabel("\n".join(rows)); statuses.setObjectName("depsStatus"); statuses.setAlignment(Qt.AlignCenter)

        if self._missing:
            body = QLabel("These power downloading and exporting clips. We'll fetch the missing ones "
                          "from their official sources — nothing downloads until you click.")
        else:
            body = QLabel("FFmpeg and yt-dlp are installed and ready. Re-download them if something "
                          "seems off, or continue.")
        body.setObjectName("welcomeSub"); body.setWordWrap(True); body.setAlignment(Qt.AlignCenter)
        body.setFixedWidth(440)

        sources = QLabel("From their official sources — FFmpeg (gyan.dev) · yt-dlp (github.com/yt-dlp)")
        sources.setObjectName("depsSources"); sources.setWordWrap(True); sources.setAlignment(Qt.AlignCenter)
        sources.setFixedWidth(440)

        self.status_lbl = QLabel(""); self.status_lbl.setObjectName("mono")
        self.status_lbl.setAlignment(Qt.AlignCenter); self.status_lbl.hide()
        self.bar = QProgressBar(); self.bar.setRange(0, 100); self.bar.hide()

        btn_row = QHBoxLayout(); btn_row.setSpacing(10)
        if self._missing:
            self.secondary_btn = QPushButton("Skip")
            self.secondary_btn.clicked.connect(lambda: self._on_continue and self._on_continue())
            self.primary_btn = QPushButton("Download && Install")
            self.primary_btn.clicked.connect(lambda: self._on_install and self._on_install(self._missing))
        else:
            self.secondary_btn = QPushButton("Re-download")
            self.secondary_btn.clicked.connect(lambda: self._on_install and self._on_install(["ffmpeg", "yt-dlp"]))
            self.primary_btn = QPushButton("Continue  →")
            self.primary_btn.clicked.connect(lambda: self._on_continue and self._on_continue())
        self.primary_btn.setObjectName("primary")
        btn_row.addWidget(self.secondary_btn); btn_row.addWidget(self.primary_btn, 1)

        for w in (badge, title, statuses, body, sources):
            c.addWidget(w, alignment=Qt.AlignCenter)
        c.addWidget(self.status_lbl)
        c.addWidget(self.bar)
        c.addLayout(btn_row)
        outer.addWidget(card)

    def set_busy(self, busy):
        self.primary_btn.setEnabled(not busy)
        self.secondary_btn.setEnabled(not busy)
        self.status_lbl.setVisible(busy)
        self.bar.setVisible(busy)

    def set_progress(self, label, done, total):
        if total > 0:
            self.bar.setRange(0, 100)
            self.bar.setValue(min(100, int(done / total * 100)))
            mb = done / (1024 * 1024)
            tmb = total / (1024 * 1024)
            self.status_lbl.setText(f"{label}  {mb:.1f} / {tmb:.1f} MB")
        else:
            # indeterminate — server gave no Content-Length
            self.bar.setRange(0, 0)
            self.status_lbl.setText(label)

    def set_status(self, text):
        self.status_lbl.setText(text)
        if not self.bar.isVisible():
            self.bar.show()
        self.bar.setRange(0, 0)


# ──────────────────────────────────────────────────────────────────────
# Guided spotlight tour
# ──────────────────────────────────────────────────────────────────────
class TourOverlay(QWidget):
    """A guided tour: dims the window and spotlights one widget at a time with a
    floating step card (Back / Next / Skip)."""

    def __init__(self, parent, steps, on_finish=None):
        super().__init__(parent)
        self.setObjectName("tourScrim")
        self._steps = steps  # list of (widget, title, body)
        self._i = 0
        self._on_finish = on_finish
        self._hole = QRect()
        self.card = QFrame(self); self.card.setObjectName("tourCard"); self.card.setFixedWidth(330)
        c = QVBoxLayout(self.card); c.setContentsMargins(20, 18, 20, 18); c.setSpacing(9)
        self.counter = QLabel(); self.counter.setObjectName("tourCounter")
        self.title = QLabel(); self.title.setObjectName("tourTitle"); self.title.setWordWrap(True)
        self.body = QLabel(); self.body.setObjectName("tourBody"); self.body.setWordWrap(True)
        btns = QHBoxLayout(); btns.setSpacing(8)
        self.skip_btn = QPushButton("Skip"); self.skip_btn.clicked.connect(self.finish)
        self.back_btn = QPushButton("Back"); self.back_btn.clicked.connect(self._back)
        self.next_btn = QPushButton("Next"); self.next_btn.setObjectName("primary"); self.next_btn.clicked.connect(self._next)
        btns.addWidget(self.skip_btn); btns.addStretch(1); btns.addWidget(self.back_btn); btns.addWidget(self.next_btn)
        for w in (self.counter, self.title, self.body):
            c.addWidget(w)
        c.addLayout(btns)
        self._pulse = 1.0
        self._pulse_anim = QVariantAnimation(self)
        self._pulse_anim.setStartValue(0.0); self._pulse_anim.setEndValue(1.0)
        self._pulse_anim.setDuration(520); self._pulse_anim.setEasingCurve(QEasingCurve.OutCubic)
        self._pulse_anim.valueChanged.connect(self._on_pulse)

    def _on_pulse(self, v):
        self._pulse = float(v); self.update()

    def start(self):
        self._i = 0
        self.setGeometry(self.parent().rect())
        self.show(); self.raise_()
        self.relayout()

    def _target_rect(self):
        w = self._steps[self._i][0]
        if w is None or not w.isVisible():
            return QRect()
        return QRect(w.mapTo(self.parent(), QPoint(0, 0)), w.size())

    def relayout(self):
        self.setGeometry(self.parent().rect())
        w, title, body = self._steps[self._i]
        self.counter.setText(f"STEP {self._i + 1} OF {len(self._steps)}")
        self.title.setText(title); self.body.setText(body)
        self.back_btn.setVisible(self._i > 0)
        self.next_btn.setText("Done" if self._i == len(self._steps) - 1 else "Next")
        r = self._target_rect()
        self._hole = r.adjusted(-8, -8, 8, 8) if not r.isNull() else QRect()
        self.card.adjustSize()
        cw, ch = self.card.width(), self.card.height()
        W, H = self.width(), self.height()
        if self._hole.isNull():
            cx, cy = (W - cw) // 2, (H - ch) // 2
        else:
            hb = self._hole
            if hb.bottom() + ch + 16 <= H:               # below the target
                cx = min(max(hb.center().x() - cw // 2, 14), W - cw - 14); cy = hb.bottom() + 16
            elif hb.top() - ch - 16 >= 0:                 # above
                cx = min(max(hb.center().x() - cw // 2, 14), W - cw - 14); cy = hb.top() - ch - 16
            elif hb.right() + cw + 20 <= W:               # to the right (tall targets like the queue)
                cx = hb.right() + 18; cy = min(max(hb.center().y() - ch // 2, 14), H - ch - 14)
            else:                                         # to the left
                cx = max(14, hb.left() - cw - 18); cy = min(max(hb.center().y() - ch // 2, 14), H - ch - 14)
        self.card.move(cx, cy)
        # Scrim everywhere except the spotlight hole (so it's click-through), but
        # always keep the card painted — never clip it against the hole.
        full = QRegion(self.rect())
        if not self._hole.isNull():
            region = full.subtracted(QRegion(self._hole)).united(QRegion(self.card.geometry()))
        else:
            region = full
        self.setMask(region)
        self._pulse_anim.stop(); self._pulse_anim.start()  # animate the highlight in on each step
        self.update()

    def _next(self):
        if self._i >= len(self._steps) - 1:
            self.finish(); return
        self._i += 1; self.relayout()

    def _back(self):
        if self._i > 0:
            self._i -= 1; self.relayout()

    def finish(self):
        self.hide()
        if self._on_finish:
            self._on_finish()

    def paintEvent(self, event):
        p = QPainter(self); p.setRenderHint(QPainter.Antialiasing, True)
        path = QPainterPath(); path.addRect(QRectF(self.rect()))
        if not self._hole.isNull():
            hole = QPainterPath(); hole.addRoundedRect(QRectF(self._hole), 10, 10)
            path = path.subtracted(hole)
        p.fillPath(path, QColor(8, 9, 12, 140))  # lighter scrim — keep the UI readable
        if not self._hole.isNull():
            grow = (1.0 - self._pulse) * 16       # ring "focuses in" on each step change
            ring = QRectF(self._hole).adjusted(-grow, -grow, grow, grow)
            col = QColor(ACCENT); col.setAlphaF(0.45 + 0.55 * self._pulse)
            pen = QPen(col); pen.setWidthF(2 + (1 - self._pulse) * 2.5)
            p.setPen(pen); p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(ring, 12, 12)

    def mousePressEvent(self, event):
        event.accept()  # swallow clicks on the scrim during the tour


# ──────────────────────────────────────────────────────────────────────
# Main window
# ──────────────────────────────────────────────────────────────────────
class QueueItem:
    """One clip in the batch queue, with its own crop/trim/overrides."""
    def __init__(self, path, w, h, duration):
        self.path = path
        self.name = Path(path).name
        self.w, self.h, self.duration = w, h, duration
        self.crop = None            # (x, y, w, h) px, or None = full frame
        self.ratio = "Original"
        self.trim_in = 0.0
        self.trim_out = duration
        self.audio_src = None
        self.image_src = None
        self.status = ""            # "", "ok", "fail"


class Segmented(QWidget):
    """A compact row of mutually-exclusive buttons — a friendlier dropdown."""

    def __init__(self, items, default, fmt=None):
        super().__init__()
        self._items = list(items)
        self._value = self._items[default]
        lay = QHBoxLayout(self); lay.setContentsMargins(0, 0, 0, 0); lay.setSpacing(6)
        self._btns = []
        for v in self._items:
            b = QPushButton(fmt(v) if fmt else str(v)); b.setObjectName("seg"); b.setCheckable(True)
            b.setChecked(v == self._value)
            b.clicked.connect(lambda _c=False, val=v: self._select(val))
            lay.addWidget(b); self._btns.append(b)

    def _select(self, v):
        self._value = v
        for b, val in zip(self._btns, self._items):
            b.setChecked(val == v)

    def currentData(self):
        return self._value


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Alert! Alert!")
        _icon = INTERNAL_DIR / "static" / "favicon.ico"
        if _icon.exists():
            self.setWindowIcon(QIcon(str(_icon)))
        self.resize(1240, 740)
        self.clip_path = None
        self.duration = 0.0
        self.dl = None
        self.ex = None
        self.aud_worker = None
        self.audio_src = None
        self.image_src = None
        self.queue = []
        self.cur = -1
        self._exporting = False

        central = QWidget()
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # far-left: the batch queue
        self.qpanel = qpanel = QFrame(); qpanel.setObjectName("qpanel"); qpanel.setFixedWidth(210)
        qv = QVBoxLayout(qpanel); qv.setContentsMargins(12, 12, 12, 12); qv.setSpacing(10)
        qhdr = QLabel("QUEUE"); qhdr.setObjectName("section")
        qv.addWidget(qhdr)
        self.queue_list = QListWidget()
        self.queue_list.currentRowChanged.connect(self._select_row)
        qv.addWidget(self.queue_list, 1)
        qrow = QHBoxLayout()
        self.remove_btn = QPushButton("Remove"); self.remove_btn.clicked.connect(self._remove_item)
        qrow.addWidget(self.remove_btn)
        qv.addLayout(qrow)
        self.export_all_btn = QPushButton("Export All"); self.export_all_btn.setObjectName("primary")
        self.export_all_btn.clicked.connect(self._export_all)
        qv.addWidget(self.export_all_btn)
        self.qbar = QProgressBar(); self.qbar.setRange(0, 100); self.qbar.hide()
        qv.addWidget(self.qbar)
        root.addWidget(qpanel)

        # left: preview + transport
        left = QVBoxLayout()
        left.setContentsMargins(12, 12, 12, 12)
        self.src_row = QWidget()
        src = QHBoxLayout(self.src_row); src.setContentsMargins(0, 0, 0, 0); src.setSpacing(8)
        self.qtoggle_btn = QPushButton("☰"); self.qtoggle_btn.setFixedWidth(38)
        self.qtoggle_btn.setToolTip("Show / hide the queue")
        self.qtoggle_btn.clicked.connect(self._toggle_queue)
        self.url_input = QLineEdit(); self.url_input.setPlaceholderText("Paste a video URL (or drag a file in) to add…")
        self.load_btn = QPushButton("Add URL")
        self.file_btn = QPushButton("Add file")
        src.addWidget(self.qtoggle_btn); src.addWidget(self.url_input)
        src.addWidget(self.load_btn); src.addWidget(self.file_btn)
        left.addWidget(self.src_row)

        self.view = CropView()
        left.addWidget(self.view, 1)

        self.player = QMediaPlayer()
        self.player.setLoops(QMediaPlayer.Loops.Infinite)  # loop preview, no black freeze at end
        self.audio = QAudioOutput(); self.audio.setVolume(0.15)
        self.player.setAudioOutput(self.audio)
        self.player.setVideoOutput(self.view.video_item)
        # secondary player to preview a swapped-in audio override
        self.aud_player = QMediaPlayer()
        self.aud_player.setLoops(QMediaPlayer.Loops.Infinite)
        self.aud_out = QAudioOutput(); self.aud_out.setVolume(0.15)
        self.aud_player.setAudioOutput(self.aud_out)

        # transport: play, volume, trim/time readouts, with the scrubber below
        tr = QHBoxLayout(); tr.setSpacing(10)
        self.play_btn = QPushButton("▶"); self.play_btn.setFixedWidth(44); self.play_btn.setEnabled(False)
        vol_ic = QLabel("🔊"); vol_ic.setObjectName("muted")
        self.vol = QSlider(Qt.Horizontal); self.vol.setRange(0, 100); self.vol.setValue(15)
        self.vol.setFixedWidth(100); self.vol.setToolTip("Preview volume")
        self.vol.valueChanged.connect(self._set_volume)
        self.trim_lbl = QLabel("In 0:00 · Out 0:00 · 0:00"); self.trim_lbl.setObjectName("mono")
        self.time_lbl = QLabel("0:00 / 0:00"); self.time_lbl.setObjectName("mono")
        tr.addWidget(self.play_btn); tr.addWidget(vol_ic); tr.addWidget(self.vol)
        tr.addStretch(1); tr.addWidget(self.trim_lbl); tr.addSpacing(12); tr.addWidget(self.time_lbl)
        left.addLayout(tr)
        self.scrub = Scrubber()
        self.scrub.seek_to.connect(lambda f: self.player.setPosition(int(f * self.duration * 1000)))
        self.scrub.trim_changed.connect(self._on_scrub_trim)
        left.addWidget(self.scrub)
        root.addLayout(left, 1)

        # right: controls panel
        panel = QFrame(); panel.setObjectName("panel")
        p = QVBoxLayout(panel); p.setContentsMargins(20, 18, 20, 18); p.setSpacing(11)

        p.addWidget(self._h("Crop & frame", 1))
        self.crop_group = QWidget()
        cg = QVBoxLayout(self.crop_group); cg.setContentsMargins(0, 0, 0, 0); cg.setSpacing(9)
        self.ratio_box = QGridLayout(); self.ratio_box.setSpacing(7)
        self.ratio_btns = {}
        for i, name in enumerate(RATIOS):
            b = QPushButton(name); b.setCheckable(True); b.setObjectName("chip")
            b.clicked.connect(lambda _c, n=name: self.set_ratio(n))
            self.ratio_box.addWidget(b, i // 4, i % 4)
            self.ratio_btns[name] = b
        self.ratio_btns["Original"].setChecked(True)
        cg.addLayout(self.ratio_box)
        self.zoom = self._slider(cg, "Zoom", 30, 100, 100, "%")
        p.addWidget(self.crop_group)

        p.addWidget(self._h("Swap audio / visuals — optional"))
        hint = QLabel("Use a different sound, or replace the video with a still image.")
        hint.setObjectName("muted"); hint.setWordWrap(True)
        p.addWidget(hint)
        ov = QGridLayout(); ov.setSpacing(8)
        self.aud_file_btn = QPushButton("Audio: file")
        self.aud_url_btn = QPushButton("Audio: URL")
        self.img_btn = QPushButton("Visual: image")
        self.clear_ovr_btn = QPushButton("Reset to clip")
        ov.addWidget(self.aud_file_btn, 0, 0); ov.addWidget(self.aud_url_btn, 0, 1)
        ov.addWidget(self.img_btn, 1, 0); ov.addWidget(self.clear_ovr_btn, 1, 1)
        p.addLayout(ov)
        self.ovr_lbl = QLabel("Using clip's own audio + video"); self.ovr_lbl.setObjectName("muted")
        self.ovr_lbl.setWordWrap(True)
        p.addWidget(self.ovr_lbl)

        p.addWidget(self._h("Export", 2))
        self.export_group = QWidget()
        eg = QVBoxLayout(self.export_group); eg.setContentsMargins(0, 0, 0, 0); eg.setSpacing(10)
        self.res = self._segment(eg, "Resolution", ["480", "720", "1080"], 1, lambda v: v,
                                 tip="Output size of the square alert clip, in pixels.")
        self.preset = self._segment(eg, "Quality", ["18", "23", "28"], 1,
                                    {"18": "Best", "23": "Balanced", "28": "Small"}.get,
                                    tip="Lower CRF = higher quality + bigger file. Balanced (23) is a good default; Small (28) is tightest.")
        self.fade = self._segment(eg, "Audio fade", ["none", "start", "end", "both"], 0, str.capitalize,
                                  tip="Fade the audio in at the start, out at the end, both, or off.")
        self.fade_dur = self._segment(eg, "Fade length", ["0.25", "0.5", "1.0"], 1, lambda v: f"{v}s",
                                      tip="How long each audio fade lasts.")
        self.buffer = self._segment(eg, "End buffer (freeze)", ["0", "1", "2", "3", "5"], 2,
                                    lambda v: "None" if v == "0" else f"{v}s",
                                    tip="Hold the last frame for a few seconds so the alert has room to fade out on stream.")
        self.normalize = self._segment(eg, "Normalize audio", ["off", "on"], 1, str.capitalize,
                                       tip="Even out loudness to a consistent target (~-16 LUFS) so alerts aren't wildly louder than each other.")
        self.export_btn = QPushButton("Export current"); self.export_btn.setObjectName("primary")
        self.export_btn.setEnabled(False)
        eg.addWidget(self.export_btn)
        p.addWidget(self.export_group)
        self.bar = QProgressBar(); self.bar.setRange(0, 100); self.bar.hide()
        p.addWidget(self.bar)
        self.status = QLabel("Add a clip to get started."); self.status.setWordWrap(True); self.status.setObjectName("statusline")
        p.addWidget(self.status)
        p.addStretch(1)
        # wrap the controls in a scroll area so they scroll when the window is short
        pscroll = QScrollArea(); pscroll.setObjectName("panelScroll"); pscroll.setWidgetResizable(True)
        pscroll.setFixedWidth(320); pscroll.setFrameShape(QFrame.NoFrame)
        pscroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        pscroll.setWidget(panel)
        root.addWidget(pscroll)

        self.setCentralWidget(central)
        self.trim_in = 0.0
        self.trim_out = 0.0

        # wiring
        self.load_btn.clicked.connect(self.on_load_url)
        self.url_input.returnPressed.connect(self.on_load_url)
        self.file_btn.clicked.connect(self.on_open_file)
        self.play_btn.clicked.connect(self.toggle_play)
        self.zoom.valueChanged.connect(lambda _v: self.set_ratio(self._current_ratio_name()))
        self.aud_file_btn.clicked.connect(self.on_audio_file)
        self.aud_url_btn.clicked.connect(self.on_audio_url)
        self.img_btn.clicked.connect(self.on_image_file)
        self.clear_ovr_btn.clicked.connect(self.on_clear_overrides)
        self.export_btn.clicked.connect(self.on_export)
        self.player.positionChanged.connect(self.on_position)
        self.player.durationChanged.connect(self.on_player_duration)
        self.player.playbackStateChanged.connect(
            lambda s: self.play_btn.setText("⏸" if s == QMediaPlayer.PlaybackState.PlayingState else "▶"))

        self._build_menu()
        self._last_output = None
        self.welcome = WelcomeScreen(central, self._begin_onboarding, self._skip_onboarding)  # first-run only
        self.empty = EmptyScreen(central, self._submit_url, self.on_open_file)  # clean add screen
        self._onboarding = False  # True from 'Get started' until the first clip -> starts tour
        self.deps = None  # created on demand by _check_dependencies (DepsConsentOverlay)
        self.deps_worker = None
        self._missing = []
        self._missing_deps = []  # set by _check_dependencies; read by onboarding
        self.tour = TourOverlay(central, [
            (self.src_row, "Add a clip",
             "Paste a video URL and hit Add URL — or Add file. Pull from YouTube, TikTok, "
             "Instagram, Facebook, or straight off your computer."),
            (self.queue_list, "Your queue",
             "Every clip you add lands here. Each one keeps its own crop, trim, and audio — "
             "click a clip to edit it."),
            (self.crop_group, "Crop & frame",
             "Pick an aspect ratio and drag the box on the video (or use Zoom) to frame the alert."),
            (self.scrub, "Trim",
             "Drag the handles on the timeline to set where the alert starts and ends. "
             "Or press I and O while it plays to snap them to the playhead."),
            (self.export_group, "Export",
             "Set size, quality, fades and normalize. Export current for this clip, or Export All "
             "to render the whole queue at once."),
        ], on_finish=self._tour_done)
        self.tour.hide()
        self.setAcceptDrops(True)
        self.drop_hint = QLabel("Drop a video to add it to the queue", central)
        self.drop_hint.setObjectName("drophint"); self.drop_hint.setAlignment(Qt.AlignCenter)
        self.drop_hint.hide()
        self._set_steps_enabled(False)  # nothing to edit until a clip is loaded
        QTimer.singleShot(0, self._show_start)  # centered start screen until first clip
        QTimer.singleShot(0, self._check_dependencies)

    # --- menu ---
    def _build_menu(self):
        m = self.menuBar().addMenu("&App")
        a_open = QAction("Open Output Folder", self); a_open.triggered.connect(self._open_output)
        a_tour = QAction("Take the tour", self); a_tour.triggered.connect(self._menu_tour)
        a_about = QAction("About", self); a_about.triggered.connect(self._about)
        a_update = QAction("Update yt-dlp", self); a_update.triggered.connect(self._update_ytdlp)
        a_check = QAction("Check dependencies", self); a_check.triggered.connect(self._show_deps_status)
        from PySide6.QtCore import QSettings
        a_console = QAction("Show log terminal", self); a_console.setCheckable(True)
        a_console.setChecked(QSettings("deutschmark", "AlertAlert").value("show_console", False, type=bool))
        a_console.toggled.connect(self._toggle_console)
        a_quit = QAction("Quit", self); a_quit.triggered.connect(self.close)
        for a in (a_open, a_check, a_update, a_console, a_tour, a_about):
            m.addAction(a)
        m.addSeparator(); m.addAction(a_quit)

    def _menu_tour(self):
        if self.queue:
            self.tour.start()
        else:
            self._onboarding = True  # load the bundled sample, then the tour fires on add
            self.on_sample()

    def _toggle_console(self, on):
        from PySide6.QtCore import QSettings
        QSettings("deutschmark", "AlertAlert").setValue("show_console", on)
        if on:
            attach_console()
        else:
            detach_console()
        self.status.setText("Log terminal " + ("shown." if on else "hidden (also applies next launch)."))

    # --- dependency status + yt-dlp update ---
    def _show_deps_status(self):
        import app
        from PySide6.QtWidgets import QMessageBox
        results = app.run_deps_check(force=True)
        lines = []
        for k, label in (("ffmpeg", "FFmpeg"), ("ffprobe", "ffprobe"),
                         ("yt-dlp", "yt-dlp"), ("deno", "Deno (optional)")):
            r = results.get(k, {})
            mark = "✓" if r.get("installed") else "✗"
            ver = r.get("version") or ("not found" if not r.get("installed") else "")
            lines.append(f"{mark}   {label}{('  —  ' + ver) if ver else ''}")
        QMessageBox.information(self, "Dependency status", "\n".join(lines))

    def _update_ytdlp(self):
        self.bar.show(); self.bar.setValue(0)
        self.status.setText("Updating yt-dlp…")
        self._ytupd = DepInstallWorker(["yt-dlp"])
        self._ytupd.progress.connect(lambda p, l: (self.bar.setValue(p), self.status.setText(l)))
        self._ytupd.done.connect(
            lambda ok, msg: (self.bar.hide(),
                             self.status.setText(("yt-dlp updated. " + msg) if ok else ("Update failed: " + msg))))
        self._ytupd.start()

    def _open_output(self):
        d = get_output_dir(); d.mkdir(parents=True, exist_ok=True)
        os.startfile(str(d)) if hasattr(os, "startfile") else None

    def _about(self):
        from PySide6.QtWidgets import QMessageBox
        QMessageBox.about(self, "About Alert! Alert!",
                          "<b>Alert! Alert!</b><br>Native build — trim, crop & export "
                          "stream alerts fast.<br>Built with PySide6 + ffmpeg.")

    def _show_start(self):
        """Launch landing — always the welcome intro (when no clip is open)."""
        if self.queue:
            return
        self.empty.hide()
        self.welcome.setGeometry(self.centralWidget().rect())
        self.welcome.show(); self.welcome.raise_()

    def _show_add(self):
        """The paste-a-URL screen (after Get started / Skip, or when the queue empties)."""
        if self.queue:
            return
        self.welcome.hide()
        if getattr(self, "deps", None):
            self.deps.hide()
        self.empty.setGeometry(self.centralWidget().rect())
        self.empty.show(); self.empty.raise_()

    def _begin_onboarding(self):
        """Welcome → Get started. First run: show the setup step, then add; the
        first clip then starts the guided tour."""
        from PySide6.QtCore import QSettings
        onboarded = QSettings("deutschmark", "AlertAlert").value("onboarded", False, type=bool)
        self._onboarding = not onboarded  # first run → first clip starts the tour
        if self._missing_deps or not onboarded:
            self._show_setup()
        else:
            self._show_add()

    def _skip_onboarding(self):
        from PySide6.QtCore import QSettings
        QSettings("deutschmark", "AlertAlert").setValue("onboarded", True)
        self._onboarding = False
        self._show_add()

    def _submit_url(self, text):
        text = (text or "").strip()
        if text:
            self.url_input.setText(text)
            self.on_load_url()

    def _tour_done(self):
        self.status.setText("You're set — add a clip to start.")

    # --- first-time setup / dependencies ---
    def _check_dependencies(self):
        """Startup: detect tools (for the status line + setup step). Show nothing
        here — the welcome is the landing; setup is a step inside onboarding."""
        import app
        results = app.run_deps_check(force=True)
        self._missing_deps = [n for n in ("ffmpeg", "ffprobe", "yt-dlp")
                              if not results.get(n, {}).get("installed")]
        if self._missing_deps:
            self.status.setText("Some tools aren't installed yet — Get started to set them up.")
        else:
            self.status.setText("Ready  ·  ✓ FFmpeg   ✓ ffprobe   ✓ yt-dlp")
        self._show_start()  # welcome is the first screen

    def _show_setup(self):
        import app
        results = app.run_deps_check(force=True)
        self.welcome.hide()
        self.deps = DepsConsentOverlay(
            self.centralWidget(), results, self._setup_install, self._setup_continue)
        self.deps.setGeometry(self.centralWidget().rect())
        self.deps.show(); self.deps.raise_()

    def _setup_install(self, tools):
        self.deps.set_busy(True)
        self.deps_worker = DepsInstallWorker(tools)
        self.deps_worker.progress.connect(self.deps.set_progress)
        self.deps_worker.status.connect(self.deps.set_status)
        self.deps_worker.finished_ok.connect(self._setup_continue)
        self.deps_worker.failed.connect(self._on_deps_failed)
        self.deps_worker.start()

    def _setup_continue(self):
        from PySide6.QtCore import QSettings
        QSettings("deutschmark", "AlertAlert").setValue("onboarded", True)
        self._missing_deps = []
        if getattr(self, "deps", None):
            self.deps.hide(); self.deps.deleteLater(); self.deps = None
        self._show_add()  # the _onboarding flag persists → first clip starts the tour

    def _on_deps_failed(self, msg):
        from PySide6.QtWidgets import QMessageBox
        self.deps.set_busy(False)
        QMessageBox.warning(
            self, "Install failed",
            f"Could not install the required tools:\n\n{msg}\n\n"
            "You can retry, or install them manually:\n"
            f"• FFmpeg: {FFMPEG_SOURCE_URL}\n"
            f"• yt-dlp: {YTDLP_SOURCE_URL}")

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if getattr(self, "deps", None) and self.deps.isVisible():
            self.deps.setGeometry(self.centralWidget().rect())
        if getattr(self, "tour", None) and self.tour.isVisible():
            self.tour.relayout()
        for scr in ("welcome", "empty"):
            s = getattr(self, scr, None)
            if s and s.isVisible():
                s.setGeometry(self.centralWidget().rect())
        if getattr(self, "drop_hint", None):
            self.drop_hint.setGeometry(self.centralWidget().rect())

    # --- queue toggle / sample / drag-and-drop ---
    def _toggle_queue(self):
        self.qpanel.setVisible(not self.qpanel.isVisible())

    def on_sample(self):
        sample = INTERNAL_DIR / "static" / "sample.mp4"
        if sample.exists():           # bundled — loads instantly, no download
            self._add_source(str(sample), "Sample clip")
        else:                         # dev fallback
            self.url_input.setText(SAMPLE_URL); self.on_load_url()

    def dragEnterEvent(self, event):
        md = event.mimeData()
        if md.hasUrls() or md.hasText():
            event.acceptProposedAction()
            self.drop_hint.setGeometry(self.centralWidget().rect())
            self.drop_hint.show(); self.drop_hint.raise_()

    def dragLeaveEvent(self, event):
        self.drop_hint.hide()

    def dropEvent(self, event):
        self.drop_hint.hide()
        md = event.mimeData()
        added = False
        for url in md.urls():
            if url.isLocalFile():
                self._add_source(url.toLocalFile()); added = True
            elif url.toString().startswith(("http://", "https://")):
                self.url_input.setText(url.toString()); self.on_load_url(); added = True
        if not added and md.hasText() and md.text().strip().startswith(("http://", "https://")):
            self.url_input.setText(md.text().strip()); self.on_load_url()
        event.acceptProposedAction()

    # --- ui helpers ---
    def _h(self, text, num=None):
        row = QWidget()
        h = QHBoxLayout(row); h.setContentsMargins(0, 6, 0, 2); h.setSpacing(9)
        if num is not None:
            badge = QLabel(str(num)); badge.setObjectName("stepbadge")
            badge.setFixedSize(22, 22); badge.setAlignment(Qt.AlignCenter)
            h.addWidget(badge)
        lbl = QLabel(text.upper()); lbl.setObjectName("section")
        h.addWidget(lbl); h.addStretch(1)
        return row

    def _slider(self, layout, label, lo, hi, val, suffix=""):
        row = QHBoxLayout(); row.setSpacing(10)
        lab = QLabel(label); lab.setObjectName("fieldlabel"); row.addWidget(lab)
        s = QSlider(Qt.Horizontal); s.setRange(lo, hi); s.setValue(val)
        v = QLabel(f"{val}{suffix}"); v.setObjectName("mono"); v.setFixedWidth(46)
        s.valueChanged.connect(lambda x: v.setText(f"{x}{suffix}"))
        row.addWidget(s, 1); row.addWidget(v); layout.addLayout(row); return s

    def _combo(self, layout, label, items, default, fmt, tip=""):
        box = QWidget(); v = QVBoxLayout(box); v.setContentsMargins(0, 0, 0, 0); v.setSpacing(5)
        head = QHBoxLayout(); head.setSpacing(6)
        lab = QLabel(label); lab.setObjectName("fieldlabel"); head.addWidget(lab)
        if tip:
            q = QLabel("ⓘ"); q.setObjectName("infotip"); q.setToolTip(tip); head.addWidget(q)
        head.addStretch(1)
        v.addLayout(head)
        c = QComboBox()
        for it in items:
            c.addItem(fmt(it) if fmt else it, it)
        c.setCurrentIndex(default); v.addWidget(c)
        layout.addWidget(box); return c

    def _segment(self, layout, label, items, default, fmt=None, tip=""):
        box = QWidget(); v = QVBoxLayout(box); v.setContentsMargins(0, 0, 0, 0); v.setSpacing(5)
        head = QHBoxLayout(); head.setSpacing(6)
        lab = QLabel(label); lab.setObjectName("fieldlabel"); head.addWidget(lab)
        if tip:
            q = QLabel("ⓘ"); q.setObjectName("infotip"); q.setToolTip(tip); head.addWidget(q)
        head.addStretch(1)
        v.addLayout(head)
        seg = Segmented(items, default, fmt); v.addWidget(seg)
        layout.addWidget(box); return seg

    # --- source loading ---
    def _set_adding(self, busy):
        self.load_btn.setText("Adding…" if busy else "Add URL")
        for b in (self.load_btn, self.file_btn):
            b.setEnabled(not busy)

    def on_load_url(self):
        url = self.url_input.text().strip()
        if not url:
            return
        self._set_adding(True); self.status.setText("Downloading…")
        self.dl = DownloadWorker(url)
        self.dl.progress.connect(self.status.setText)
        self.dl.finished_ok.connect(self._add_source)
        self.dl.failed.connect(self._dl_failed)
        self.dl.start()

    def on_open_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Add video", str(Path.home()),
            "Videos (*.mp4 *.mov *.mkv *.webm *.avi);;All files (*.*)")
        if path:
            self._add_source(path)

    def _dl_failed(self, msg):
        self._set_adding(False); self.status.setText(f"Couldn't add that: {msg}")
        # keep _onboarding armed so a successful retry still starts the tour;
        # the add screen is still showing for the retry.

    @staticmethod
    def _short(name, n=26):
        return name if len(name) <= n else name[: n - 1] + "…"

    def _add_source(self, path, title=""):
        self._set_adding(False)
        self.url_input.clear()
        w, h, dur = probe_dimensions(path)
        if not w:
            self.status.setText("Could not read video."); return
        item = QueueItem(path, w, h, dur)
        name = (title or "").strip() or Path(path).name
        if name.lower().startswith("clip.") or not name:   # generic download filename
            name = f"Clip {len(self.queue) + 1}"
        item.name = name
        self.queue.append(item)
        li = QListWidgetItem(self._short(name)); li.setToolTip(name)  # hover to see full name
        self.queue_list.addItem(li)
        self.queue_list.setCurrentRow(len(self.queue) - 1)  # -> _select_row loads it
        self.welcome.hide(); self.empty.hide()  # leave the start screen now that there's a clip
        self.status.setText(f"Added {self._short(name)}  ·  {len(self.queue)} in queue")
        if self._onboarding:           # first clip during onboarding — run the guided editing tour
            self._onboarding = False
            QTimer.singleShot(450, self.tour.start)

    # --- queue: select / save / restore ---
    def _select_row(self, row):
        if not (0 <= row < len(self.queue)):
            return
        self._save_live()
        self.cur = row
        self._load_item(self.queue[row])

    def _save_live(self):
        if not (0 <= self.cur < len(self.queue)) or not self.clip_path:
            return
        it = self.queue[self.cur]
        it.crop = self.view.crop.crop_px()
        it.ratio = self._current_ratio_name()
        it.trim_in, it.trim_out = self.trim_in, self.trim_out
        it.audio_src, it.image_src = self.audio_src, self.image_src

    def _set_steps_enabled(self, on):
        # everything that needs a loaded clip to mean anything
        for w in (self.crop_group, self.export_group,
                  self.aud_file_btn, self.aud_url_btn, self.img_btn, self.clear_ovr_btn,
                  self.play_btn, self.vol, self.scrub):
            w.setEnabled(on)
        self.export_btn.setEnabled(on)
        self.trim_lbl.setVisible(on); self.time_lbl.setVisible(on)

    def _load_item(self, it):
        self.clip_path = it.path
        self.duration = it.duration
        self.trim_in, self.trim_out = it.trim_in, it.trim_out
        self.audio_src, self.image_src = it.audio_src, it.image_src
        self.view.set_source_size(it.w, it.h)
        for n, b in self.ratio_btns.items():
            b.setChecked(n == it.ratio)
        self.view.crop.apply_ratio(RATIOS.get(it.ratio), self.zoom.value() / 100)
        if it.crop:
            x, y, w, h = it.crop
            self.view.crop.setRect(QRectF(x, y, w, h))
            self.view.viewport().update()
        self.player.setSource(QUrl.fromLocalFile(it.path))
        self.player.play(); self.player.pause()  # show first frame but start paused (no loud autoplay loop)
        self.play_btn.setEnabled(True)
        self.scrub.set_position(0.0)
        self._set_steps_enabled(True)   # steps come alive once a clip is loaded
        self._update_trim_lbl(); self._update_ovr_lbl()
        self.status.setText(f"{self._short(it.name)} · {it.w}×{it.h}")
        self.scrub.set_image("")
        self.wf = WaveformWorker(it.path, Path(it.path).parent / "wave.png")
        self.wf.done.connect(self.scrub.set_image)
        self.wf.start()

    def _remove_item(self):
        row = self.queue_list.currentRow()
        if row < 0:
            return
        self.queue.pop(row)
        self.cur = -1
        self.queue_list.takeItem(row)
        if self.queue:
            self.queue_list.setCurrentRow(min(row, len(self.queue) - 1))
        else:
            self.clip_path = None
            self.player.setSource(QUrl())
            self.play_btn.setEnabled(False)
            self.scrub.set_image(""); self.scrub.set_position(0.0)
            self._set_steps_enabled(False)
            self.status.setText("Queue empty — add a clip.")
            self._show_add()  # mid-session: back to the add screen, not the welcome

    # --- playback ---
    def _set_volume(self, v):
        self.audio.setVolume(v / 100.0)
        self.aud_out.setVolume(v / 100.0)

    def toggle_play(self):
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
            if self.audio_src:
                self.aud_player.pause()
        else:
            self.player.play()
            if self.audio_src:
                self.aud_player.play()

    def on_position(self, ms):
        sec = ms / 1000
        # keep preview playback inside the user's trim bounds (loop within in→out)
        if (self.duration and self.trim_out > self.trim_in
                and self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState):
            if sec >= self.trim_out or sec < self.trim_in - 0.05:
                self.player.setPosition(int(self.trim_in * 1000)); sec = self.trim_in
        if self.duration:
            self.scrub.set_position(sec / self.duration)
        self.time_lbl.setText(f"{self._fmt(sec)} / {self._fmt(self.duration)}")

    def _on_scrub_trim(self, in_frac, out_frac):
        if not self.duration:
            return
        self.trim_in = in_frac * self.duration
        self.trim_out = out_frac * self.duration
        self.trim_lbl.setText(
            f"In {self._fmt(self.trim_in)} · Out {self._fmt(self.trim_out)} · {self._fmt(self.trim_out - self.trim_in)}")

    def keyPressEvent(self, event):
        if self.url_input.hasFocus():
            return super().keyPressEvent(event)
        k = event.key()
        ctrl = event.modifiers() & Qt.ControlModifier
        pos = self.player.position()
        if k == Qt.Key_Space:
            self.toggle_play()
        elif k == Qt.Key_I:
            self.set_in()
        elif k == Qt.Key_O:
            self.set_out()
        elif k == Qt.Key_Left:
            self.player.setPosition(max(0, pos - 1000))
        elif k == Qt.Key_Right:
            self.player.setPosition(pos + 1000)
        elif k == Qt.Key_Comma:
            self.player.setPosition(max(0, pos - 100))
        elif k == Qt.Key_Period:
            self.player.setPosition(pos + 100)
        elif k == Qt.Key_J:
            self.player.setPosition(max(0, pos - 5000))
        elif k == Qt.Key_L:
            self.player.setPosition(pos + 5000)
        elif k == Qt.Key_Home:
            self.player.setPosition(0)
        elif k == Qt.Key_End and self.duration:
            self.player.setPosition(max(0, int(self.duration * 1000) - 60))
        elif k == Qt.Key_Up:
            self.vol.setValue(min(100, self.vol.value() + 5))
        elif k == Qt.Key_Down:
            self.vol.setValue(max(0, self.vol.value() - 5))
        elif k in (Qt.Key_Plus, Qt.Key_Equal):
            self.zoom.setValue(min(self.zoom.maximum(), self.zoom.value() + 5))
        elif k == Qt.Key_Minus:
            self.zoom.setValue(max(self.zoom.minimum(), self.zoom.value() - 5))
        elif k == Qt.Key_R:
            self.set_ratio("Original")
        elif k == Qt.Key_E and ctrl:
            self._export_all()
        elif k == Qt.Key_PageDown and self.queue:
            self.queue_list.setCurrentRow(min(self.cur + 1, len(self.queue) - 1))
        elif k == Qt.Key_PageUp and self.queue:
            self.queue_list.setCurrentRow(max(self.cur - 1, 0))
        else:
            return super().keyPressEvent(event)

    def on_player_duration(self, ms):
        if ms > 0 and self.duration <= 0:
            self.duration = ms / 1000
            self.trim_out = self.duration
            self._update_trim_lbl()

    # --- crop ---
    def _current_ratio_name(self):
        for n, b in self.ratio_btns.items():
            if b.isChecked():
                return n
        return "Original"

    def set_ratio(self, name):
        for n, b in self.ratio_btns.items():
            b.setChecked(n == name)
        self.view.crop.apply_ratio(RATIOS[name], self.zoom.value() / 100)

    # --- trim ---
    def set_in(self):
        self.trim_in = self.player.position() / 1000
        if self.trim_in >= self.trim_out:
            self.trim_out = min(self.duration, self.trim_in + 0.1)
        self._update_trim_lbl()

    def set_out(self):
        self.trim_out = self.player.position() / 1000
        if self.trim_out <= self.trim_in:
            self.trim_in = max(0.0, self.trim_out - 0.1)
        self._update_trim_lbl()

    def _update_trim_lbl(self):
        self.trim_lbl.setText(
            f"In {self._fmt(self.trim_in)} · Out {self._fmt(self.trim_out)} · {self._fmt(self.trim_out - self.trim_in)}")
        if self.duration:
            self.scrub.set_region(self.trim_in / self.duration, self.trim_out / self.duration)

    # --- overrides ---
    def _update_ovr_lbl(self):
        a = self._short(Path(self.audio_src).name) if self.audio_src else "clip"
        v = self._short(Path(self.image_src).name) if self.image_src else "video"
        self.ovr_lbl.setText(f"Audio: {a}  ·  Visual: {v}")
        self._reflect_overrides()

    def _reflect_overrides(self):
        """Make the preview show what export will use: still image instead of
        video, and the swapped audio instead of the clip's own."""
        self.view.show_image(self.image_src)  # None reverts to the video
        if self.audio_src:
            self.audio.setMuted(True)
            self.aud_player.setSource(QUrl.fromLocalFile(self.audio_src))
            if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
                self.aud_player.play()
        else:
            self.audio.setMuted(False)
            self.aud_player.stop()

    def on_audio_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Choose audio", str(Path.home()),
            "Audio/Video (*.mp3 *.wav *.aac *.m4a *.flac *.ogg *.opus *.mp4 *.mov *.mkv);;All files (*.*)")
        if path:
            self.audio_src = path; self._update_ovr_lbl()

    def on_audio_url(self):
        from PySide6.QtWidgets import QInputDialog
        url, ok = QInputDialog.getText(self, "Audio URL", "Paste an audio/video URL:")
        if ok and url.strip():
            self.status.setText("Downloading audio…")
            self.aud_worker = AudioWorker(url.strip())
            self.aud_worker.done.connect(self._audio_ready)
            self.aud_worker.failed.connect(lambda m: self.status.setText(f"Audio failed: {m}"))
            self.aud_worker.start()

    def _audio_ready(self, path):
        self.audio_src = path; self._update_ovr_lbl()
        self.status.setText("Separate audio ready.")

    def on_image_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Choose image", str(Path.home()),
            "Images (*.png *.jpg *.jpeg *.webp *.bmp *.gif);;All files (*.*)")
        if path:
            self.image_src = path; self._update_ovr_lbl()
            self.status.setText("Static image will be used as the visual on export.")

    def on_clear_overrides(self):
        self.audio_src = None; self.image_src = None; self._update_ovr_lbl()

    # --- export ---
    def on_export(self):
        if not self.clip_path:
            return
        out_dir = get_output_dir(); out_dir.mkdir(parents=True, exist_ok=True)
        out = out_dir / f"alert_{uuid.uuid4().hex[:8]}.mp4"
        crop = self.view.crop.crop_px()
        trim = (self.trim_in, self.trim_out if self.trim_out > self.trim_in else self.duration)
        out_size = int(self.res.currentData())
        crf = int(self.preset.currentData())
        self.export_btn.setEnabled(False); self.bar.setValue(0); self.bar.show()
        self.status.setText("Exporting…")
        self.ex = ExportWorker(self.clip_path, str(out), crop, trim, out_size, crf,
                               (self.normalize.currentData() == "on"), self.fade.currentData(),
                               int(self.buffer.currentData()), self.audio_src, self.image_src,
                               float(self.fade_dur.currentData()))
        self.ex.progress.connect(self.bar.setValue)
        self.ex.finished_ok.connect(self._exported)
        self.ex.failed.connect(self._export_failed)
        self.ex.start()

    def _exported(self, path):
        self.export_btn.setEnabled(True); self.bar.hide()
        self.status.setText(f"Saved: {path}")

    def _export_failed(self, msg):
        self.export_btn.setEnabled(True); self.bar.hide()
        self.status.setText(f"Export failed: {msg}")

    # --- export all (batch) ---
    def _export_all(self):
        if self._exporting or not self.queue:
            return
        self._save_live()
        self.player.stop()  # release the current file + stop contention during batch
        self._exporting = True
        self._eq = list(range(len(self.queue)))
        self._eq_total = len(self._eq)
        self._eq_done = 0
        self._batch_workers = []  # retain refs so finishing QThreads aren't GC'd mid-run
        self.export_all_btn.setEnabled(False); self.qbar.setValue(0); self.qbar.show()
        self._export_next()

    def _export_next(self):
        if not self._eq:
            self._exporting = False
            self.export_all_btn.setEnabled(True); self.qbar.hide()
            ok = sum(1 for it in self.queue if it.status == "ok")
            out_dir = get_output_dir()
            self.status.setText(f"Batch done: {ok}/{len(self.queue)} exported to {out_dir}")
            if ok and hasattr(os, "startfile"):  # reveal the exports (Explorer focuses an existing window)
                try:
                    os.startfile(str(out_dir))
                except OSError:
                    pass
            return
        idx = self._eq.pop(0)
        it = self.queue[idx]
        self.queue_list.item(idx).setText("⏳ " + it.name)
        out_dir = get_output_dir(); out_dir.mkdir(parents=True, exist_ok=True)
        out = out_dir / f"alert_{Path(it.name).stem[:20]}_{uuid.uuid4().hex[:6]}.mp4"
        crop = it.crop if it.crop else (0, 0, it.w - it.w % 2, it.h - it.h % 2)
        trim = (it.trim_in, it.trim_out if it.trim_out > it.trim_in else it.duration)
        worker = ExportWorker(it.path, str(out), crop, trim,
                              int(self.res.currentData()), int(self.preset.currentData()),
                              (self.normalize.currentData() == "on"), self.fade.currentData(),
                              int(self.buffer.currentData()), it.audio_src, it.image_src,
                              float(self.fade_dur.currentData()))
        self._batch_workers.append(worker)  # keep a reference for the whole batch
        worker.progress.connect(self._eq_progress)
        worker.finished_ok.connect(lambda p, i=idx: self._eq_item_done(i, True))
        worker.failed.connect(lambda m, i=idx: self._eq_item_done(i, False))
        worker.start()

    def _eq_progress(self, pct):
        overall = int((self._eq_done + pct / 100) / max(1, self._eq_total) * 100)
        self.qbar.setValue(min(99, overall))

    def _eq_item_done(self, idx, ok):
        it = self.queue[idx]
        it.status = "ok" if ok else "fail"
        self.queue_list.item(idx).setText(("✓ " if ok else "✗ ") + it.name)
        self._eq_done += 1
        self._export_next()

    @staticmethod
    def _fmt(secs):
        secs = max(0, secs)
        return f"{int(secs // 60)}:{int(secs % 60):02d}"


QSS = f"""
QMainWindow {{ background: #0f1014; }}
QWidget {{ color: #e6e9ef; font-family: 'Segoe UI', sans-serif; font-size: 13px; }}
QLabel {{ background: transparent; }}
#panel {{ background: #15171d; }}
#panelScroll {{ background: #15171d; border-left: 1px solid #23262f; }}
#qpanel {{ background: #15171d; border-right: 1px solid #23262f; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: #353b48; border-radius: 5px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: {ACCENT}; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}
#preview {{ background: #000; border: none; }}
#section {{ color: #aeb4c0; font-size: 11px; font-weight: 700; letter-spacing: 1.4px; }}
#fieldlabel {{ color: #9aa0ad; font-size: 12px; }}
#infotip {{ color: #6b7280; font-size: 12px; }}
#statusline {{ color: #c7ccd6; font-size: 12px; }}
#drophint {{ background: rgba(13,14,18,0.92); border: 3px dashed {ACCENT}; border-radius: 16px; color: {ACCENT}; font-size: 22px; font-weight: 700; }}
#emptyWordmark {{ font-size: 30px; font-weight: 900; color: #EEF4FA; letter-spacing: -0.5px; }}
#emptyTitle {{ font-size: 30px; font-weight: 800; color: #ffffff; letter-spacing: -0.5px; }}
#emptySub {{ color: #9fb0c6; font-size: 15px; line-height: 1.5; }}
#emptyStatus {{ color: #FFB547; font-size: 13px; }}
#emptyInput {{ background: rgba(255,255,255,0.05); border: 1px solid rgba(255,255,255,0.16); border-radius: 12px; padding: 0 16px; font-size: 14px; color: #EEF4FA; }}
#emptyInput:focus {{ border-color: #FFB547; }}
#ctaAmber {{ background: qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 #FFC468, stop:1 #FFB547); color: #0D1521; border: none; border-radius: 26px; font-size: 16px; font-weight: 800; padding: 0 30px; }}
#ctaAmber:hover {{ background: qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 #FFD080, stop:1 #FFC153); }}
#ctaAmber:pressed {{ background: #F0A838; }}
#ctaAmber:disabled {{ background: #6b5a37; color: #b9a37a; }}
#ctaGhost {{ background: rgba(255,255,255,0.07); color: #DCE4F0; border: 1px solid rgba(255,255,255,0.20); border-radius: 24px; font-size: 14px; font-weight: 600; padding: 0 24px; }}
#ctaGhost:hover {{ border-color: rgba(255,255,255,0.40); background: rgba(255,255,255,0.12); }}
#skipLink {{ background: transparent; border: none; color: #9fb0c6; font-size: 14px; padding: 4px; }}
#skipLink:hover {{ color: #EEF4FA; }}
QToolTip {{ background: #1a1d25; color: #e6e9ef; border: 1px solid #2c313c; padding: 6px 8px; border-radius: 6px; }}
#muted {{ color: #868c98; font-size: 12px; }}
#mono {{ font-family: Consolas, monospace; color: #c7ccd6; }}
#stepbadge {{ background: {ACCENT}; color: #0f1014; font-weight: 800; font-size: 12px; border-radius: 11px; }}
QLineEdit, QComboBox {{ background: #0f1014; border: 1px solid #2a2d36; border-radius: 8px; padding: 9px 12px; color: #e6e9ef; }}
QLineEdit:focus, QComboBox:focus {{ border-color: {ACCENT}; }}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox QAbstractItemView {{ background: #1a1d25; border: 1px solid #2a2f3a; selection-background-color: {ACCENT}; selection-color: #0f1014; outline: none; }}
QPushButton {{ background: #232732; border: 1px solid #353b48; border-radius: 8px; padding: 9px 14px; min-height: 18px; color: #dfe3ea; }}
QPushButton:hover {{ border-color: {ACCENT}; background: #2a2f3c; }}
QPushButton:pressed {{ background: #1b1f27; }}
QPushButton:disabled {{ color: #5a5f6b; background: #1a1d24; border-color: #262a32; }}
QPushButton#primary:pressed {{ background: #4f7fb8; }}
QPushButton#chip {{ padding: 9px 6px; min-height: 20px; }}
QPushButton#chip:checked {{ background: {ACCENT}; color: #0f1014; border-color: {ACCENT}; font-weight: 700; }}
QPushButton#seg {{ padding: 9px 4px; min-height: 18px; font-size: 12px; }}
QPushButton#seg:checked {{ background: {ACCENT}; color: #0f1014; border-color: {ACCENT}; font-weight: 700; }}
QPushButton#primary {{ background: {ACCENT}; color: #0f1014; font-weight: 700; border: none; padding: 12px; font-size: 14px; }}
QPushButton#primary:hover {{ background: #79a6db; }}
QPushButton#primary:disabled {{ background: #2b3340; color: #6b7280; }}
QListWidget {{ background: #0f1014; border: 1px solid #2a2d36; border-radius: 8px; padding: 6px; }}
QListWidget::item {{ padding: 9px 8px; border-radius: 6px; margin-bottom: 2px; color: #dfe3ea; }}
QListWidget::item:selected {{ background: {ACCENT}; color: #0f1014; }}
QProgressBar {{ background: #0f1014; border: none; border-radius: 5px; height: 8px; text-align: center; }}
QProgressBar::chunk {{ background: {ACCENT}; border-radius: 5px; }}
QSlider::groove:horizontal {{ height: 4px; background: #2a2d36; border-radius: 2px; }}
QSlider::handle:horizontal {{ background: {ACCENT}; width: 15px; height: 15px; margin: -6px 0; border-radius: 7px; }}
QSlider::handle:horizontal:hover {{ background: #79a6db; }}
QCheckBox {{ background: transparent; color: #c7ccd6; spacing: 8px; }}
QCheckBox::indicator {{ width: 16px; height: 16px; border: 1px solid #353b48; border-radius: 4px; background: #0f1014; }}
QCheckBox::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT}; }}
QMenuBar {{ background: #15171d; color: #c7ccd6; }}
QMenuBar::item {{ padding: 6px 12px; }}
QMenuBar::item:selected {{ background: {ACCENT}; color: #0f1014; }}
QMenu {{ background: #1a1d25; border: 1px solid #2a2f3a; padding: 4px; }}
QMenu::item {{ padding: 7px 22px; border-radius: 4px; }}
QMenu::item:selected {{ background: {ACCENT}; color: #0f1014; }}
#welcomeBackdrop {{ background: rgba(8,9,12,0.90); }}
#welcomeCard {{ background: #15171d; border: 1px solid #2c313c; border-radius: 18px; }}
#welcomeBadge {{ background: {ACCENT}; color: #0f1014; font-size: 46px; font-weight: 800; border-radius: 42px; }}
#welcomeTitle {{ font-size: 27px; font-weight: 800; color: #ffffff; }}
#welcomeSub {{ color: #c7ccd6; font-size: 14px; }}
#depsStatus {{ color: #c7ccd6; font-size: 14px; font-family: Consolas, monospace; }}
#depsSources {{ color: #9aa0ab; font-size: 12px; }}
#depsSources a {{ color: {ACCENT}; }}
#wave {{ background: #0f1014; border-radius: 8px; }}
#tourScrim {{ background: transparent; }}
#tourCard {{ background: #1a1d25; border: 1px solid #2c313c; border-radius: 12px; }}
#tourCounter {{ color: {ACCENT}; font-size: 11px; font-weight: 700; letter-spacing: 1.2px; }}
#tourTitle {{ color: #ffffff; font-size: 17px; font-weight: 800; }}
#tourBody {{ color: #c7ccd6; font-size: 13px; }}
"""


def run_selftest(video_path, result_path):
    """Headless: prove this (possibly frozen) build can decode+play the clip.
    Writes a one-line result to result_path. Exit 0 = playable."""
    app = QApplication(sys.argv)
    player = QMediaPlayer()
    ao = QAudioOutput(); player.setAudioOutput(ao)
    vw = QGraphicsVideoItem()
    scene = QGraphicsScene(); scene.addItem(vw)
    gv = QGraphicsView(scene); player.setVideoOutput(vw); gv.show()
    state = {"err": None}
    player.errorOccurred.connect(lambda e, m: state.__setitem__("err", f"{e} {m}"))
    player.setSource(QUrl.fromLocalFile(video_path))
    player.play()
    code = {"v": 1}

    def check():
        ok = state["err"] is None and player.hasVideo() and player.position() > 0
        Path(result_path).write_text(
            f"error={state['err']} hasVideo={player.hasVideo()} "
            f"pos={player.position()} OK={ok}", encoding="utf-8")
        code["v"] = 0 if ok else 2
        app.quit()

    QTimer.singleShot(4500, check)
    app.exec()
    return code["v"]


BANNER = r"""
    _    _           _   _      _    _           _   _
   / \  | | ___ _ __| |_| |    / \  | | ___ _ __| |_| |
  / _ \ | |/ _ \ '__| __| |   / _ \ | |/ _ \ '__| __| |
 / ___ \| |  __/ |  | |_|_|  / ___ \| |  __/ |  | |_|_|
/_/   \_\_|\___|_|   \__(_) /_/   \_\_|\___|_|   \__(_)

  Alert! Alert! — log terminal. Close this window to hide it next launch.
"""


def attach_console():
    """Allocate a console window for this (windowed) app and route stdout/stderr
    to it, then print the banner. Windows only. Returns True on success."""
    if sys.platform != "win32":
        return False
    import ctypes
    k = ctypes.windll.kernel32
    if not k.GetConsoleWindow():
        if not k.AllocConsole():
            return False
    try:
        sys.stdout = open("CONOUT$", "w", buffering=1, encoding="utf-8", errors="replace")
        sys.stderr = open("CONOUT$", "w", buffering=1, encoding="utf-8", errors="replace")
    except OSError:
        return False
    print(BANNER, flush=True)
    return True


def detach_console():
    if sys.platform == "win32":
        import ctypes
        ctypes.windll.kernel32.FreeConsole()


def run_selftest_deps(result_path):
    """Headless: exercise the real dependency-install path (yt-dlp) in this
    (possibly frozen) build, so a progress_cb signature mismatch between
    native_app and app can't ship undetected. Exit 0 = install path works."""
    QApplication(sys.argv)
    prog = {"n": 0}
    res = {"v": None}
    w = DepInstallWorker(["yt-dlp"])
    w.progress.connect(lambda p, l: prog.__setitem__("n", prog["n"] + 1))
    w.done.connect(lambda ok, msg: res.__setitem__("v", (ok, msg)))
    w.run()
    ok, msg = res["v"] or (False, "no result")
    Path(result_path).write_text(
        f"ok={ok} progress_events={prog['n']} msg={msg}", encoding="utf-8")
    return 0 if ok else 2


def run_selftest_batch(result_path):
    """Headless: run the real _export_all over a 2-item queue (distinct crop/trim)
    in this (possibly frozen) build. Exit 0 = both items exported."""
    import tempfile
    from PySide6.QtCore import QTimer
    app = QApplication(sys.argv)
    tmp = Path(tempfile.gettempdir())
    c1, c2 = tmp / "_aa_b1.mp4", tmp / "_aa_b2.mp4"
    for c, sz, d in ((c1, "1280x720", 3), (c2, "640x480", 3)):
        subprocess.run([FFMPEG, "-y", "-v", "quiet", "-f", "lavfi", "-i",
                        f"testsrc=duration={d}:size={sz}:rate=24", "-f", "lavfi", "-i",
                        f"sine=frequency=300:duration={d}", "-c:v", "libx264", "-pix_fmt",
                        "yuv420p", "-c:a", "aac", str(c)], env=get_env())
    w = MainWindow()  # NOT shown — keeps the event loop free
    it0 = QueueItem(str(c1), 1280, 720, 3.0); it0.crop = (280, 0, 720, 720); it0.trim_in, it0.trim_out = 0.5, 2.0
    it1 = QueueItem(str(c2), 640, 480, 3.0); it1.crop = (185, 0, 270, 480); it1.trim_in, it1.trim_out = 0.0, 1.5
    w.queue = [it0, it1]
    for it in w.queue:
        w.queue_list.addItem(QListWidgetItem(it.name))
    w.cur = -1
    code = {"v": 2}

    def poll():
        if getattr(w, "_eq_total", 0) and not w._exporting:
            ok = all(it.status == "ok" for it in w.queue)
            Path(result_path).write_text(
                f"ok={ok} statuses={[it.status for it in w.queue]}", encoding="utf-8")
            code["v"] = 0 if ok else 2
            app.quit()

    QTimer.singleShot(200, w._export_all)
    t = QTimer(); t.timeout.connect(poll); t.start(300)
    QTimer.singleShot(60000, app.quit)
    app.exec()
    for c in (c1, c2):
        try: c.unlink()
        except OSError: pass
    return code["v"]


def main():
    if "--selftest" in sys.argv:
        i = sys.argv.index("--selftest")
        return run_selftest(sys.argv[i + 1], sys.argv[i + 2])
    if "--selftest-deps" in sys.argv:
        i = sys.argv.index("--selftest-deps")
        return run_selftest_deps(sys.argv[i + 1])
    if "--selftest-batch" in sys.argv:
        i = sys.argv.index("--selftest-batch")
        return run_selftest_batch(sys.argv[i + 1])
    app = QApplication(sys.argv)
    app.setApplicationName("Alert! Alert!")
    app.setStyleSheet(QSS)
    # Optional log terminal alongside the app (off by default).
    from PySide6.QtCore import QSettings
    if QSettings("deutschmark", "AlertAlert").value("show_console", False, type=bool) \
            or "--console" in sys.argv:
        attach_console()
    win = MainWindow()
    win.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
