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

from PySide6.QtCore import Qt, QThread, Signal, QUrl, QRectF, QPointF, QSizeF, QTimer
from PySide6.QtGui import QColor, QPen, QBrush, QPainter, QAction, QPixmap
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLineEdit, QPushButton, QLabel, QFileDialog, QSlider, QComboBox, QCheckBox,
    QGraphicsView, QGraphicsScene, QGraphicsRectItem, QProgressBar, QFrame,
)
from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput
from PySide6.QtMultimediaWidgets import QGraphicsVideoItem

from app import (
    DOWNLOADS_DIR, YTDLP, FFMPEG, FFPROBE, FFMPEG_DIR, run_subprocess,
    probe_media_file, is_youtube_url, has_deno_runtime, get_env, get_output_dir,
    download_separate_audio,
)
from ytdlp import build_ytdlp_profiles, run_ytdlp_with_retries

ACCENT = "#FFB547"

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
        self.crop = CropItem(on_change=self.viewport().update)
        self._scene.addItem(self.crop)
        self._w = self._h = 0

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
    finished_ok = Signal(str)
    failed = Signal(str)

    def __init__(self, url):
        super().__init__()
        self.url = url

    def run(self):
        try:
            job_dir = DOWNLOADS_DIR / uuid.uuid4().hex
            job_dir.mkdir(parents=True, exist_ok=True)
            template = str(job_dir / "clip.%(ext)s")
            youtube = is_youtube_url(self.url)

            def build_cmd(profile, use_sections):
                cmd = [YTDLP, "--no-playlist", "--force-ipv4", "-f", profile["format"],
                       "--retries", "5", "--fragment-retries", "5", "-o", template]
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
            self.finished_ok.emit(str(files[0]))
        except Exception as e:
            self.failed.emit(str(e))


def build_export_cmd(src, out, crop, trim, out_size, crf, normalize, fade,
                     end_buffer=0, audio_src=None, image_src=None):
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
        fl = 0.35
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
                 end_buffer=0, audio_src=None, image_src=None):
        super().__init__()
        self.args = (src, out, crop, trim, out_size, crf, normalize, fade,
                     end_buffer, audio_src, image_src)

    def run(self):
        (src, out, crop, trim, out_size, crf, normalize, fade,
         end_buffer, audio_src, image_src) = self.args
        cmd = build_export_cmd(src, out, crop, trim, out_size, crf, normalize, fade,
                               end_buffer, audio_src, image_src)
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
                            f"showwavespic=s=1200x80:colors={ACCENT}",
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


class WaveformBar(QWidget):
    """Waveform strip with a playhead and dimmed out-of-trim regions; click to seek."""
    seek_to = Signal(float)

    def __init__(self):
        super().__init__()
        self.setFixedHeight(64)
        self.setObjectName("wave")
        self._pix = None
        self._frac = 0.0
        self._in = 0.0
        self._out = 1.0

    def set_image(self, path):
        self._pix = QPixmap(path)
        self.update()

    def set_position(self, frac):
        self._frac = max(0.0, min(1.0, frac))
        self.update()

    def set_region(self, in_frac, out_frac):
        self._in, self._out = in_frac, out_frac
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        w, h = self.width(), self.height()
        if self._pix and not self._pix.isNull():
            p.drawPixmap(self.rect(), self._pix)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(0, 0, 0, 150))
        p.drawRect(QRectF(0, 0, self._in * w, h))
        p.drawRect(QRectF(self._out * w, 0, w - self._out * w, h))
        pen = QPen(QColor(ACCENT)); pen.setWidth(2)
        p.setPen(pen)
        x = self._frac * w
        p.drawLine(QPointF(x, 0), QPointF(x, h))

    def mousePressEvent(self, event):
        if self.width():
            self.seek_to.emit(max(0.0, min(1.0, event.position().x() / self.width())))


# ──────────────────────────────────────────────────────────────────────
# Welcome overlay (first run)
# ──────────────────────────────────────────────────────────────────────
class WelcomeOverlay(QWidget):
    def __init__(self, parent, on_start):
        super().__init__(parent)
        self.setObjectName("welcomeBackdrop")
        self._on_start = on_start
        outer = QVBoxLayout(self); outer.setAlignment(Qt.AlignCenter)
        card = QFrame(); card.setObjectName("welcomeCard"); card.setFixedWidth(460)
        c = QVBoxLayout(card); c.setContentsMargins(36, 40, 36, 40); c.setSpacing(16)
        badge = QLabel("!"); badge.setObjectName("welcomeBadge")
        badge.setAlignment(Qt.AlignCenter); badge.setFixedSize(84, 84)
        title = QLabel("Alert! Alert!"); title.setObjectName("welcomeTitle"); title.setAlignment(Qt.AlignCenter)
        sub = QLabel("Turn any clip into a stream alert — trim, crop, export. Fast.")
        sub.setObjectName("welcomeSub"); sub.setWordWrap(True); sub.setAlignment(Qt.AlignCenter)
        steps = QLabel("①  Load a URL or open a file\n②  Crop & set in/out on the video\n③  Export your alert")
        steps.setObjectName("welcomeSteps")
        btn = QPushButton("Get Started"); btn.setObjectName("primary"); btn.setFixedWidth(190)
        btn.clicked.connect(lambda: self._on_start and self._on_start())
        for w in (badge, title, sub, steps, btn):
            c.addWidget(w, alignment=Qt.AlignCenter)
        outer.addWidget(card)


# ──────────────────────────────────────────────────────────────────────
# Main window
# ──────────────────────────────────────────────────────────────────────
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Alert! Alert!")
        self.resize(1040, 720)
        self.clip_path = None
        self.duration = 0.0
        self.dl = None
        self.ex = None
        self.aud_worker = None
        self.audio_src = None
        self.image_src = None

        central = QWidget()
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # left: preview + transport
        left = QVBoxLayout()
        left.setContentsMargins(12, 12, 12, 12)
        src = QHBoxLayout()
        self.url_input = QLineEdit(); self.url_input.setPlaceholderText("Paste a video URL…")
        self.load_btn = QPushButton("Load URL")
        self.file_btn = QPushButton("Open File…")
        src.addWidget(self.url_input); src.addWidget(self.load_btn); src.addWidget(self.file_btn)
        left.addLayout(src)

        self.view = CropView()
        left.addWidget(self.view, 1)

        self.player = QMediaPlayer()
        self.audio = QAudioOutput(); self.audio.setVolume(0.15)
        self.player.setAudioOutput(self.audio)
        self.player.setVideoOutput(self.view.video_item)

        # transport
        tr = QHBoxLayout()
        self.play_btn = QPushButton("▶"); self.play_btn.setFixedWidth(44); self.play_btn.setEnabled(False)
        self.seek = QSlider(Qt.Horizontal); self.seek.setRange(0, 1000); self.seek.setEnabled(False)
        self.time_lbl = QLabel("0:00 / 0:00"); self.time_lbl.setObjectName("mono")
        tr.addWidget(self.play_btn); tr.addWidget(self.seek, 1); tr.addWidget(self.time_lbl)
        left.addLayout(tr)
        self.wave = WaveformBar()
        self.wave.seek_to.connect(lambda f: self.player.setPosition(int(f * self.duration * 1000)))
        left.addWidget(self.wave)
        root.addLayout(left, 1)

        # right: controls panel
        panel = QFrame(); panel.setObjectName("panel"); panel.setFixedWidth(300)
        p = QVBoxLayout(panel); p.setContentsMargins(16, 16, 16, 16); p.setSpacing(14)

        p.addWidget(self._h("Crop"))
        self.ratio_box = QGridLayout()
        self.ratio_btns = {}
        for i, name in enumerate(RATIOS):
            b = QPushButton(name); b.setCheckable(True); b.setObjectName("chip")
            b.clicked.connect(lambda _c, n=name: self.set_ratio(n))
            self.ratio_box.addWidget(b, i // 4, i % 4)
            self.ratio_btns[name] = b
        self.ratio_btns["Original"].setChecked(True)
        p.addLayout(self.ratio_box)
        self.zoom = self._slider(p, "Zoom", 30, 100, 100, "%")

        p.addWidget(self._h("Trim"))
        trbtns = QHBoxLayout()
        self.set_in_btn = QPushButton("Set In"); self.set_out_btn = QPushButton("Set Out")
        trbtns.addWidget(self.set_in_btn); trbtns.addWidget(self.set_out_btn)
        p.addLayout(trbtns)
        self.trim_lbl = QLabel("In 0:00 · Out 0:00 · 0:00"); self.trim_lbl.setObjectName("mono")
        p.addWidget(self.trim_lbl)

        p.addWidget(self._h("Overrides (optional)"))
        ov = QGridLayout()
        self.aud_file_btn = QPushButton("Audio file…")
        self.aud_url_btn = QPushButton("Audio URL…")
        self.img_btn = QPushButton("Image…")
        self.clear_ovr_btn = QPushButton("Clear")
        ov.addWidget(self.aud_file_btn, 0, 0); ov.addWidget(self.aud_url_btn, 0, 1)
        ov.addWidget(self.img_btn, 1, 0); ov.addWidget(self.clear_ovr_btn, 1, 1)
        p.addLayout(ov)
        self.ovr_lbl = QLabel("Audio: clip · Visual: video"); self.ovr_lbl.setObjectName("muted")
        self.ovr_lbl.setWordWrap(True)
        p.addWidget(self.ovr_lbl)

        p.addWidget(self._h("Export"))
        self.res = self._combo(p, "Resolution", ["480", "720", "1080"], 1, lambda v: f"{v}×{v}")
        self.preset = self._combo(p, "Quality (CRF)", ["18", "23", "28"], 1,
                                  {"18": "Max (18)", "23": "Balanced (23)", "28": "Small (28)"}.get)
        self.fade = self._combo(p, "Audio fade", ["none", "start", "end", "both"], 0, str.capitalize)
        self.buffer = self._combo(p, "End buffer (freeze)", ["0", "1", "2", "3", "5"], 2,
                                  lambda v: "None" if v == "0" else f"{v}s freeze")
        self.normalize = QCheckBox("Normalize audio"); self.normalize.setChecked(True)
        p.addWidget(self.normalize)

        p.addStretch(1)
        self.export_btn = QPushButton("Export Alert"); self.export_btn.setObjectName("primary")
        self.export_btn.setEnabled(False)
        p.addWidget(self.export_btn)
        self.bar = QProgressBar(); self.bar.setRange(0, 100); self.bar.hide()
        p.addWidget(self.bar)
        self.status = QLabel("Load a clip to begin."); self.status.setWordWrap(True); self.status.setObjectName("muted")
        p.addWidget(self.status)
        root.addWidget(panel)

        self.setCentralWidget(central)
        self.trim_in = 0.0
        self.trim_out = 0.0

        # wiring
        self.load_btn.clicked.connect(self.on_load_url)
        self.url_input.returnPressed.connect(self.on_load_url)
        self.file_btn.clicked.connect(self.on_open_file)
        self.play_btn.clicked.connect(self.toggle_play)
        self.seek.sliderMoved.connect(self.on_seek)
        self.set_in_btn.clicked.connect(self.set_in)
        self.set_out_btn.clicked.connect(self.set_out)
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
        self.welcome = WelcomeOverlay(central, self._dismiss_welcome)
        self.welcome.hide()
        QTimer.singleShot(0, self._maybe_welcome)

    # --- menu / welcome ---
    def _build_menu(self):
        m = self.menuBar().addMenu("&App")
        a_open = QAction("Open Output Folder", self); a_open.triggered.connect(self._open_output)
        a_welcome = QAction("Replay Welcome", self); a_welcome.triggered.connect(self._show_welcome)
        a_about = QAction("About", self); a_about.triggered.connect(self._about)
        a_quit = QAction("Quit", self); a_quit.triggered.connect(self.close)
        for a in (a_open, a_welcome, a_about):
            m.addAction(a)
        m.addSeparator(); m.addAction(a_quit)

    def _open_output(self):
        d = get_output_dir(); d.mkdir(parents=True, exist_ok=True)
        os.startfile(str(d)) if hasattr(os, "startfile") else None

    def _about(self):
        from PySide6.QtWidgets import QMessageBox
        QMessageBox.about(self, "About Alert! Alert!",
                          "<b>Alert! Alert!</b><br>Native build — trim, crop & export "
                          "stream alerts fast.<br>Built with PySide6 + ffmpeg.")

    def _maybe_welcome(self):
        from PySide6.QtCore import QSettings
        if not QSettings("deutschmark", "AlertAlert").value("welcomed", False, type=bool):
            self._show_welcome()

    def _show_welcome(self):
        self.welcome.setGeometry(self.centralWidget().rect())
        self.welcome.show(); self.welcome.raise_()

    def _dismiss_welcome(self):
        from PySide6.QtCore import QSettings
        QSettings("deutschmark", "AlertAlert").setValue("welcomed", True)
        self.welcome.hide()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if getattr(self, "welcome", None) and self.welcome.isVisible():
            self.welcome.setGeometry(self.centralWidget().rect())

    # --- ui helpers ---
    def _h(self, text):
        lbl = QLabel(text.upper()); lbl.setObjectName("section"); return lbl

    def _slider(self, layout, label, lo, hi, val, suffix=""):
        row = QHBoxLayout(); row.addWidget(QLabel(label))
        s = QSlider(Qt.Horizontal); s.setRange(lo, hi); s.setValue(val)
        v = QLabel(f"{val}{suffix}"); v.setObjectName("mono"); v.setFixedWidth(44)
        s.valueChanged.connect(lambda x: v.setText(f"{x}{suffix}"))
        row.addWidget(s, 1); row.addWidget(v); layout.addLayout(row); return s

    def _combo(self, layout, label, items, default, fmt):
        layout.addWidget(QLabel(label))
        c = QComboBox()
        for it in items:
            c.addItem(fmt(it) if fmt else it, it)
        c.setCurrentIndex(default); layout.addWidget(c); return c

    # --- source loading ---
    def on_load_url(self):
        url = self.url_input.text().strip()
        if not url:
            return
        self.load_btn.setEnabled(False); self.status.setText("Starting download…")
        self.dl = DownloadWorker(url)
        self.dl.progress.connect(self.status.setText)
        self.dl.finished_ok.connect(self._loaded)
        self.dl.failed.connect(self._dl_failed)
        self.dl.start()

    def on_open_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Open video", str(Path.home()),
            "Videos (*.mp4 *.mov *.mkv *.webm *.avi);;All files (*.*)")
        if path:
            self._loaded(path)

    def _dl_failed(self, msg):
        self.load_btn.setEnabled(True); self.status.setText(f"Failed: {msg}")

    def _loaded(self, path):
        self.load_btn.setEnabled(True)
        self.clip_path = path
        w, h, dur = probe_dimensions(path)
        if not w:
            self.status.setText("Could not read video."); return
        self.duration = dur
        self.trim_in, self.trim_out = 0.0, dur
        self.view.set_source_size(w, h)
        self.player.setSource(QUrl.fromLocalFile(path))
        self.player.play()
        self.seek.setEnabled(True); self.play_btn.setEnabled(True); self.export_btn.setEnabled(True)
        self.set_ratio("Original")
        self._update_trim_lbl()
        self.status.setText(f"{Path(path).name} · {w}×{h} · native preview (no proxy)")
        # render the waveform strip in the background
        self.wave.set_image("")
        self.wf = WaveformWorker(path, Path(path).parent / "wave.png")
        self.wf.done.connect(self.wave.set_image)
        self.wf.start()

    # --- playback ---
    def toggle_play(self):
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
        else:
            self.player.play()

    def on_seek(self, v):
        if self.duration:
            self.player.setPosition(int(v / 1000 * self.duration * 1000))

    def on_position(self, ms):
        if self.duration and not self.seek.isSliderDown():
            self.seek.setValue(int(ms / 1000 / self.duration * 1000))
        if self.duration:
            self.wave.set_position(ms / 1000 / self.duration)
        self.time_lbl.setText(f"{self._fmt(ms / 1000)} / {self._fmt(self.duration)}")

    def keyPressEvent(self, event):
        if self.url_input.hasFocus():
            return super().keyPressEvent(event)
        k = event.key()
        if k == Qt.Key_Space:
            self.toggle_play()
        elif k == Qt.Key_I:
            self.set_in()
        elif k == Qt.Key_O:
            self.set_out()
        elif k == Qt.Key_Left:
            self.player.setPosition(max(0, self.player.position() - 1000))
        elif k == Qt.Key_Right:
            self.player.setPosition(self.player.position() + 1000)
        elif k == Qt.Key_Home:
            self.player.setPosition(0)
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
            self.wave.set_region(self.trim_in / self.duration, self.trim_out / self.duration)

    # --- overrides ---
    def _update_ovr_lbl(self):
        a = Path(self.audio_src).name if self.audio_src else "clip"
        v = Path(self.image_src).name if self.image_src else "video"
        self.ovr_lbl.setText(f"Audio: {a} · Visual: {v}")

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
                               self.normalize.isChecked(), self.fade.currentData(),
                               int(self.buffer.currentData()), self.audio_src, self.image_src)
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

    @staticmethod
    def _fmt(secs):
        secs = max(0, secs)
        return f"{int(secs // 60)}:{int(secs % 60):02d}"


QSS = f"""
* {{ font-family: 'Segoe UI', sans-serif; font-size: 13px; color: #e8e8ea; }}
QMainWindow, QWidget {{ background: #14151a; }}
#panel {{ background: #1b1d24; border-left: 1px solid #2a2d36; }}
#section {{ color: {ACCENT}; font-size: 11px; font-weight: 700; letter-spacing: 1px; }}
#muted {{ color: #8a8f9a; font-size: 12px; }}
#mono {{ font-family: Consolas, monospace; color: #c7ccd6; }}
QLineEdit, QComboBox {{ background: #0f1014; border: 1px solid #2a2d36; border-radius: 6px; padding: 7px 10px; }}
QLineEdit:focus, QComboBox:focus {{ border-color: {ACCENT}; }}
QPushButton {{ background: #262932; border: 1px solid #333742; border-radius: 6px; padding: 7px 12px; }}
QPushButton:hover {{ border-color: {ACCENT}; }}
QPushButton:disabled {{ color: #5a5e68; }}
QPushButton#chip {{ padding: 6px 4px; }}
QPushButton#chip:checked {{ background: {ACCENT}; color: #14151a; border-color: {ACCENT}; font-weight: 700; }}
QPushButton#primary {{ background: {ACCENT}; color: #14151a; font-weight: 700; border: none; padding: 11px; font-size: 14px; }}
QPushButton#primary:hover {{ background: #ffc46b; }}
QPushButton#primary:disabled {{ background: #4a4536; color: #8a8270; }}
QProgressBar {{ background: #0f1014; border: none; border-radius: 6px; height: 8px; text-align: center; }}
QProgressBar::chunk {{ background: {ACCENT}; border-radius: 6px; }}
QSlider::groove:horizontal {{ height: 4px; background: #2a2d36; border-radius: 2px; }}
QSlider::handle:horizontal {{ background: {ACCENT}; width: 14px; height: 14px; margin: -6px 0; border-radius: 7px; }}
QCheckBox {{ color: #c7ccd6; }}
QCheckBox::indicator:checked {{ background: {ACCENT}; border-radius: 3px; }}
QMenuBar {{ background: #1b1d24; }}
QMenuBar::item:selected {{ background: {ACCENT}; color: #14151a; }}
QMenu {{ background: #1b1d24; border: 1px solid #2a2d36; }}
QMenu::item:selected {{ background: {ACCENT}; color: #14151a; }}
#welcomeBackdrop {{ background: rgba(10,11,14,0.88); }}
#welcomeCard {{ background: #1b1d24; border: 1px solid #2f333d; border-radius: 16px; }}
#welcomeBadge {{ background: {ACCENT}; color: #14151a; font-size: 46px; font-weight: 800; border-radius: 42px; }}
#welcomeTitle {{ font-size: 27px; font-weight: 800; color: #ffffff; }}
#welcomeSub {{ color: #c7ccd6; font-size: 14px; }}
#welcomeSteps {{ color: #9aa0ab; font-size: 14px; }}
#wave {{ background: #0f1014; border-radius: 6px; }}
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


def main():
    if "--selftest" in sys.argv:
        i = sys.argv.index("--selftest")
        return run_selftest(sys.argv[i + 1], sys.argv[i + 2])
    app = QApplication(sys.argv)
    app.setApplicationName("Alert! Alert!")
    app.setStyleSheet(QSS)
    win = MainWindow()
    win.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
