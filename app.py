import os
import json
import uuid
import glob
import re
import mimetypes
import platform
import socket
import subprocess
import threading
import shutil
import zipfile
import importlib
from functools import lru_cache
from pathlib import Path
from urllib.request import Request, urlopen
from flask import Flask, request, jsonify, send_file, send_from_directory

from ytdlp import (
    build_ytdlp_profiles,
    looks_like_age_restricted_issue,
    looks_like_youtube_challenge_issue,
    run_ytdlp_with_retries,
    summarize_ytdlp_error,
)

import sys
import webbrowser
import functools


# Handle PyInstaller paths
if getattr(sys, 'frozen', False):
    # If running as EXE, use sys._MEIPASS for internal assets (static)
    # and sys.executable parent for user files (output/temp)
    INTERNAL_DIR = Path(sys._MEIPASS)
    BASE_DIR = Path(sys.executable).parent.resolve()
else:
    # Running from source
    INTERNAL_DIR = Path(__file__).parent.resolve()
    BASE_DIR = INTERNAL_DIR

app = Flask(__name__, static_folder=str(INTERNAL_DIR / "static"))
# Don't let browsers / QtWebEngine hold onto stale frontend assets across builds.
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0

TEMP_DIR = BASE_DIR / "temp"
DOWNLOADS_DIR = TEMP_DIR / "downloads"
PROCESSING_DIR = TEMP_DIR / "processing"
if platform.system() == "Windows":
    local_app_data = os.environ.get("LOCALAPPDATA")
    runtime_root_base = Path(local_app_data) if local_app_data else BASE_DIR
    RUNTIME_DIR = runtime_root_base / "alert-alert" / "runtime"
    APP_STATE_DIR = runtime_root_base / "alert-alert"
else:
    RUNTIME_DIR = BASE_DIR / ".runtime"
    APP_STATE_DIR = BASE_DIR / ".appstate"
RUNTIME_BIN_DIR = RUNTIME_DIR / "bin"
APP_STATE_DIR.mkdir(parents=True, exist_ok=True)
SETTINGS_FILE = APP_STATE_DIR / "settings.json"
DEFAULT_OUTPUT_DIR = BASE_DIR / "output"

# Ensure directories exist
for d in [DOWNLOADS_DIR, PROCESSING_DIR, RUNTIME_BIN_DIR]:
    d.mkdir(parents=True, exist_ok=True)

NULL_DEVICE = "NUL" if platform.system() == "Windows" else "/dev/null"
AUTO_INSTALL_SUPPORTED = platform.system() == "Windows"
FFMPEG_WINDOWS_URL = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"
YTDLP_WINDOWS_URL = "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp.exe"
DENO_WINDOWS_URL = "https://github.com/denoland/deno/releases/latest/download/deno-x86_64-pc-windows-msvc.zip"
DEFAULT_APP_HOST = str(os.environ.get("ALERT_ALERT_HOST", "localhost") or "localhost").strip() or "localhost"
try:
    DEFAULT_APP_PORT = int(str(os.environ.get("ALERT_ALERT_PORT", "3000") or "3000").strip())
except ValueError:
    DEFAULT_APP_PORT = 3000


def _load_app_settings():
    if not SETTINGS_FILE.exists():
        return {}
    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_app_settings(settings):
    with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(settings, f, indent=2)


APP_SETTINGS = _load_app_settings()


def get_output_dir():
    configured = str(APP_SETTINGS.get("output_dir", "")).strip()
    path = Path(configured).expanduser() if configured else DEFAULT_OUTPUT_DIR
    path.mkdir(parents=True, exist_ok=True)
    return path


OUTPUT_DIR = get_output_dir()


def set_output_dir(path_value):
    global OUTPUT_DIR
    raw = str(path_value or "").strip()
    path = DEFAULT_OUTPUT_DIR if not raw else Path(raw).expanduser()
    path.mkdir(parents=True, exist_ok=True)
    APP_SETTINGS["output_dir"] = str(path.resolve())
    _save_app_settings(APP_SETTINGS)
    OUTPUT_DIR = path.resolve()
    return OUTPUT_DIR


def reset_output_dir():
    global OUTPUT_DIR
    APP_SETTINGS.pop("output_dir", None)
    _save_app_settings(APP_SETTINGS)
    OUTPUT_DIR = DEFAULT_OUTPUT_DIR.resolve()
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    return OUTPUT_DIR


# In-memory job status tracking
jobs = {}


# ── Tool discovery ──────────────────────────────────────────────
# Find ffmpeg, ffprobe, yt-dlp even if not on current PATH

def _is_explicit_tool_path(path, tool_name):
    p = Path(str(path))
    if p.exists():
        return True
    normalized = p.name.lower()
    bare = {tool_name.lower()}
    if platform.system() == "Windows":
        bare.add(f"{tool_name.lower()}.exe")
    return normalized not in bare


def find_tool(name):
    """Find a CLI tool, checking PATH first then common Windows install locations."""
    runtime_candidate = RUNTIME_BIN_DIR / (f"{name}.exe" if platform.system() == "Windows" else name)
    if runtime_candidate.exists():
        return str(runtime_candidate)

    # Try PATH first
    path = shutil.which(name)
    if path:
        return path

    if platform.system() != "Windows":
        return name  # On non-Windows, just return the name and hope for the best

    # Common Windows locations
    home = Path.home()
    search_patterns = [
        # Winget installs
        str(home / "AppData/Local/Microsoft/WinGet/Packages" / "**" / f"{name}.exe"),
        # Python scripts (yt-dlp)
        str(home / "AppData/Roaming/Python" / "**" / f"{name}.exe"),
        str(home / "AppData/Local/Programs/Python" / "**" / f"{name}.exe"),
        # Chocolatey
        f"C:/ProgramData/chocolatey/bin/{name}.exe",
        # Scoop
        str(home / f"scoop/shims/{name}.exe"),
        # Common manual installs
        f"C:/{name}/bin/{name}.exe",
        f"C:/Program Files/{name}/bin/{name}.exe",
    ]

    for pattern in search_patterns:
        matches = glob.glob(pattern, recursive=True)
        if matches:
            return matches[0]

    return name  # Fallback: let subprocess try the bare name


FFMPEG = "ffmpeg"
FFPROBE = "ffprobe"
YTDLP = "yt-dlp"
DENO = "deno"

# Directory containing ffmpeg, needed by yt-dlp's --ffmpeg-location
FFMPEG_DIR = None


def refresh_tool_paths():
    """Refresh global tool paths from runtime dir + PATH."""
    global FFMPEG, FFPROBE, YTDLP, DENO, FFMPEG_DIR
    FFMPEG = find_tool("ffmpeg")
    FFPROBE = find_tool("ffprobe")
    YTDLP = find_tool("yt-dlp")
    DENO = find_tool("deno")
    FFMPEG_DIR = str(Path(FFMPEG).parent) if _is_explicit_tool_path(FFMPEG, "ffmpeg") else None
    get_env.cache_clear()


# Build an env dict that includes ffmpeg and deno dirs on PATH
@lru_cache(maxsize=1)
def get_env():
    """Return an env dict with ffmpeg and deno directories added to PATH."""
    env = os.environ.copy()
    extra_dirs = []
    if RUNTIME_BIN_DIR.exists():
        extra_dirs.append(str(RUNTIME_BIN_DIR))
    if FFMPEG_DIR:
        extra_dirs.append(FFMPEG_DIR)
    deno_dir = str(Path(DENO).parent) if _is_explicit_tool_path(DENO, "deno") else None
    if deno_dir:
        extra_dirs.append(deno_dir)
    if extra_dirs:
        env["PATH"] = os.pathsep.join(extra_dirs) + os.pathsep + env.get("PATH", "")
    return env


def is_safe_job_id(job_id):
    """Validate that job_id is safe for path construction (prevents path traversal)."""
    if not job_id or not isinstance(job_id, str):
        return False
    # Only allow alphanumeric, underscore, and hyphen characters
    return bool(re.match(r'^[a-zA-Z0-9_\-]+$', job_id))

refresh_tool_paths()


def _missing_tool_error(cmd):
    """Turn a FileNotFoundError from a subprocess launch into an actionable message."""
    tool = Path(str(cmd[0] if isinstance(cmd, (list, tuple)) else cmd)).name
    tool = tool[:-4] if tool.lower().endswith(".exe") else tool
    return RuntimeError(
        f"{tool} was not found. Open Dependency Setup and click "
        f"'Auto Install Missing', or install {tool} and add it to your PATH."
    )


def run_subprocess(cmd, timeout=30, text=True):
    """Run a subprocess with proper flags to avoid popping up console windows on Windows."""
    kwargs = {
        "capture_output": True,
        "text": text,
        "timeout": timeout,
        "env": get_env()
    }
    if platform.system() == "Windows":
        # Prevent console window from appearing
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = subprocess.SW_HIDE
        kwargs["startupinfo"] = startupinfo
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW

    try:
        return subprocess.run(cmd, **kwargs)
    except FileNotFoundError:
        raise _missing_tool_error(cmd)


def run_ffmpeg(args, env, timeout=120):
    """Run an ffmpeg/ffprobe command, return the result. Raises on failure with clean error."""
    kwargs = {
        "capture_output": True,
        "text": True,
        "timeout": timeout,
        "env": env
    }
    if platform.system() == "Windows":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW

    try:
        r = subprocess.run(args, **kwargs)
    except FileNotFoundError:
        raise _missing_tool_error(args)
    if r.returncode != 0:
        # Extract meaningful error lines (skip the version banner)
        lines = r.stderr.strip().split("\n")
        error_lines = [l for l in lines if any(
            kw in l.lower() for kw in ["error", "invalid", "no such", "fail", "cannot", "not found"]
        )]
        error_msg = "\n".join(error_lines) if error_lines else lines[-1] if lines else "Unknown error"
        raise RuntimeError(f"ffmpeg error: {error_msg}")
    return r


def probe_media_duration(input_path):
    """Best-effort duration probe in seconds."""
    try:
        r = run_subprocess(
            [FFPROBE, "-v", "quiet", "-print_format", "json", "-show_format", str(input_path)],
            timeout=20
        )
        if r.returncode != 0:
            return 0.0
        info = json.loads(r.stdout or "{}")
        return float(info.get("format", {}).get("duration", 0) or 0)
    except Exception:
        return 0.0


# Codecs the embedded QtWebEngine build can actually decode (royalty-free only).
# Anything else (h264/aac/etc.) loads fine in ffmpeg but fails the in-app <video>
# preview with DEMUXER_ERROR_NO_SUPPORTED_STREAMS.
PREVIEW_PLAYABLE_VCODECS = {"vp8", "vp9", "av01", "av1", "theora"}


def probe_media_file(input_path):
    """Verify a downloaded media file is real and parseable.

    Returns a dict::

        {ok, error, vcodec, acodec, duration, has_video, has_audio, preview_playable}

    ``ok`` is True only when ffprobe could parse the file and it has a non-zero
    duration. ``error`` is a user-actionable message when ``ok`` is False.
    """
    result = {
        "ok": False,
        "error": "",
        "vcodec": "",
        "acodec": "",
        "duration": 0.0,
        "has_video": False,
        "has_audio": False,
        "preview_playable": False,
    }

    path = Path(input_path)
    if not path.exists():
        result["error"] = "downloaded file is missing"
        return result
    if path.stat().st_size < 1024:
        result["error"] = "downloaded file is empty or truncated"
        return result

    try:
        r = run_subprocess(
            [
                FFPROBE, "-v", "quiet", "-print_format", "json",
                "-show_streams", "-show_format", str(path),
            ],
            timeout=20,
        )
    except RuntimeError as e:
        result["error"] = str(e)
        return result
    if r.returncode != 0:
        result["error"] = "ffprobe could not read the downloaded file (corrupt or unsupported)"
        return result

    try:
        info = json.loads(r.stdout or "{}")
    except json.JSONDecodeError:
        result["error"] = "ffprobe returned unreadable output for the downloaded file"
        return result

    for stream in info.get("streams", []):
        codec_type = stream.get("codec_type")
        if codec_type == "video" and not result["has_video"]:
            result["has_video"] = True
            result["vcodec"] = (stream.get("codec_name") or "").lower()
        elif codec_type == "audio" and not result["has_audio"]:
            result["has_audio"] = True
            result["acodec"] = (stream.get("codec_name") or "").lower()

    result["duration"] = float(info.get("format", {}).get("duration", 0) or 0)
    result["preview_playable"] = result["vcodec"] in PREVIEW_PLAYABLE_VCODECS
    result["ok"] = True
    return result


PREVIEW_PROXY_NAME = "preview.webm"


def ensure_preview_proxy(source_path):
    """Make a clip the in-app <video> can actually play.

    The embedded QtWebEngine build can't decode H.264/AAC (royalty-free codecs
    only). For such clips we transcode a sibling ``preview.webm`` (VP9/Opus) used
    *only* for the live preview — the original file is left untouched so trimming
    and export keep using it. Returns the proxy ``Path`` on success, or ``None``
    if transcoding failed (caller should fall back to the honest "no preview"
    message). An existing, up-to-date proxy is reused.
    """
    source = Path(source_path)
    proxy = source.parent / PREVIEW_PROXY_NAME
    try:
        if (proxy.exists()
                and proxy.stat().st_mtime >= source.stat().st_mtime
                and proxy.stat().st_size > 1024):
            return proxy
    except OSError:
        pass

    tmp = source.parent / (PREVIEW_PROXY_NAME + ".tmp")
    args = [
        FFMPEG, "-y", "-i", str(source),
        # Downscale large sources so the realtime VP9 encode stays quick; alert
        # clips are short, so this is cheap.
        "-vf", "scale='min(1280,iw)':-2",
        "-c:v", "libvpx-vp9", "-deadline", "realtime", "-cpu-used", "8", "-row-mt", "1",
        "-b:v", "2M",
        "-c:a", "libopus", "-b:a", "128k",
        # Force the muxer: the ".tmp" output name hides the .webm extension.
        "-f", "webm", str(tmp),
    ]
    try:
        r = run_subprocess(args, timeout=300)
    except RuntimeError:
        return None
    if r.returncode != 0 or not tmp.exists() or tmp.stat().st_size < 1024:
        try:
            tmp.unlink()
        except OSError:
            pass
        return None
    try:
        os.replace(str(tmp), str(proxy))
    except OSError:
        return None
    return proxy


# ── Serve frontend ──────────────────────────────────────────────

@app.after_request
def _no_store_static(response):
    # Local single-user app: never let QtWebEngine cache our own JS/CSS/assets,
    # so edited or rebuilt files always win (this app has repeatedly hit stale
    # QtWebEngine cache on CSS/JS).
    if request.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response


@app.route("/")
def index():
    response = send_from_directory("static", "index.html")
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response

@app.route("/favicon.ico")
def favicon():
    return send_from_directory(app.static_folder, "favicon.ico")


# ── Dependency check ────────────────────────────────────────────


# Global cache for dependency results
DEPS_CACHE = {}
DEPS_BOOTSTRAP_STATE = {
    "status": "idle",  # idle | installing | ready | failed
    "message": "",
    "last_error": None,
}
DEPS_BOOTSTRAP_LOCK = threading.Lock()
YTDLP_UPDATE_STATE = {
    "status": "idle",  # idle | updating | ready | failed
    "message": "",
    "last_error": None,
}
YTDLP_UPDATE_LOCK = threading.Lock()


def _set_bootstrap_state(status, message="", error=None):
    DEPS_BOOTSTRAP_STATE["status"] = status
    DEPS_BOOTSTRAP_STATE["message"] = message
    DEPS_BOOTSTRAP_STATE["last_error"] = error


def _set_ytdlp_update_state(status, message="", error=None):
    YTDLP_UPDATE_STATE["status"] = status
    YTDLP_UPDATE_STATE["message"] = message
    YTDLP_UPDATE_STATE["last_error"] = error




def _required_missing(results):
    required = ("ffmpeg", "ffprobe", "yt-dlp")
    return [name for name in required if not results.get(name, {}).get("installed")]




def _build_deps_payload(results):
    payload = dict(results)
    payload["required_missing"] = _required_missing(results)
    payload["auto_install_available"] = AUTO_INSTALL_SUPPORTED
    payload["download_disclosure"] = {
        "runtime_path": str(RUNTIME_BIN_DIR),
        "required_tools": ["ffmpeg", "ffprobe", "yt-dlp"],
        "optional_tools": ["deno"],
        "sources": {
            "ffmpeg": FFMPEG_WINDOWS_URL,
            "yt-dlp": YTDLP_WINDOWS_URL,
            "deno": DENO_WINDOWS_URL,
        },
    }
    payload["bootstrap"] = dict(DEPS_BOOTSTRAP_STATE)
    payload["ytdlp_update"] = dict(YTDLP_UPDATE_STATE)
    payload["ytdlp_update_available"] = bool(results.get("yt-dlp", {}).get("installed"))
    return payload


def _build_storage_payload():
    output_dir = get_output_dir()
    return {
        "output_dir": str(output_dir),
        "default_output_dir": str(DEFAULT_OUTPUT_DIR.resolve()),
        "custom_output_dir": str(APP_SETTINGS.get("output_dir", "")).strip() or None,
    }


def _download_file(url, dest_path, timeout=180):
    req = Request(url, headers={"User-Agent": "alert-alert/1.0"})
    with urlopen(req, timeout=timeout) as response, open(dest_path, "wb") as out_file:
        shutil.copyfileobj(response, out_file)


def _install_ffmpeg_windows():
    """Download and extract ffmpeg/ffprobe into runtime bin."""
    archive_path = RUNTIME_DIR / "ffmpeg-release-essentials.zip"
    _download_file(FFMPEG_WINDOWS_URL, archive_path)

    ffmpeg_member = None
    ffprobe_member = None
    with zipfile.ZipFile(archive_path, "r") as zf:
        for member in zf.namelist():
            lower = member.lower()
            if lower.endswith("/bin/ffmpeg.exe"):
                ffmpeg_member = member
            elif lower.endswith("/bin/ffprobe.exe"):
                ffprobe_member = member

        if not ffmpeg_member or not ffprobe_member:
            raise RuntimeError("Downloaded FFmpeg archive is missing ffmpeg.exe or ffprobe.exe.")

        with zf.open(ffmpeg_member) as src, open(RUNTIME_BIN_DIR / "ffmpeg.exe", "wb") as dst:
            shutil.copyfileobj(src, dst)
        with zf.open(ffprobe_member) as src, open(RUNTIME_BIN_DIR / "ffprobe.exe", "wb") as dst:
            shutil.copyfileobj(src, dst)

    archive_path.unlink(missing_ok=True)


def _install_ytdlp_windows():
    """Download yt-dlp.exe into runtime bin."""
    dest = RUNTIME_BIN_DIR / "yt-dlp.exe"
    _download_file(YTDLP_WINDOWS_URL, dest)


def _install_deno_windows():
    """Download and extract deno.exe into runtime bin."""
    archive_path = RUNTIME_DIR / "deno-windows.zip"
    _download_file(DENO_WINDOWS_URL, archive_path)

    deno_member = None
    with zipfile.ZipFile(archive_path, "r") as zf:
        for member in zf.namelist():
            if member.lower().endswith("deno.exe"):
                deno_member = member
                break

        if not deno_member:
            raise RuntimeError("Downloaded Deno archive is missing deno.exe.")

        with zf.open(deno_member) as src, open(RUNTIME_BIN_DIR / "deno.exe", "wb") as dst:
            shutil.copyfileobj(src, dst)

    archive_path.unlink(missing_ok=True)


def _optional_missing(results):
    optional = ("deno",)
    return [name for name in optional if not results.get(name, {}).get("installed")]


def ensure_runtime_dependencies(auto_install=False):
    """Check dependencies and optionally auto-install required + optional tools."""
    refresh_tool_paths()
    results = run_deps_check(force=True)
    missing_required = _required_missing(results)
    missing_optional = _optional_missing(results)

    if not missing_required and (not auto_install or not AUTO_INSTALL_SUPPORTED or not missing_optional):
        _set_bootstrap_state("ready", "All required dependencies are installed.")
        return results

    if not auto_install or not AUTO_INSTALL_SUPPORTED:
        return results

    with DEPS_BOOTSTRAP_LOCK:
        # Re-check inside lock to avoid duplicate installers.
        refresh_tool_paths()
        results = run_deps_check(force=True)
        missing_required = _required_missing(results)
        missing_optional = _optional_missing(results)
        if not missing_required and not missing_optional:
            _set_bootstrap_state("ready", "All required dependencies are installed.")
            return results

        try:
            _set_bootstrap_state("installing", "Installing runtime dependencies...")
            missing_report = missing_required + missing_optional
            print("Auto-install: missing dependencies detected:", ", ".join(missing_report))
            if "ffmpeg" in missing_required or "ffprobe" in missing_required:
                print("Auto-install: downloading FFmpeg runtime...")
                _install_ffmpeg_windows()
            if "yt-dlp" in missing_required:
                print("Auto-install: downloading yt-dlp runtime...")
                _install_ytdlp_windows()

            deno_warning = None
            if "deno" in missing_optional:
                print("Auto-install: downloading Deno runtime (optional)...")
                try:
                    _install_deno_windows()
                except Exception as e:
                    deno_warning = str(e)
                    print(f"Auto-install warning (optional deno): {deno_warning}")

            refresh_tool_paths()
            results = run_deps_check(force=True)
            missing_after = _required_missing(results)
            if missing_after:
                msg = f"Still missing: {', '.join(missing_after)}"
                _set_bootstrap_state("failed", msg, msg)
            else:
                if deno_warning and not results.get("deno", {}).get("installed"):
                    _set_bootstrap_state(
                        "ready",
                        "Required dependencies installed. Optional Deno install failed.",
                        deno_warning,
                    )
                else:
                    _set_bootstrap_state("ready", "Dependencies installed successfully.")
            return results
        except Exception as e:
            err = str(e)
            print(f"Auto-install failed: {err}")
            _set_bootstrap_state("failed", "Dependency auto-install failed.", err)
            return results


def update_ytdlp_one_click():
    """Update yt-dlp with a single in-app action."""
    refresh_tool_paths()
    results = run_deps_check(force=True)

    with DEPS_BOOTSTRAP_LOCK:
        with YTDLP_UPDATE_LOCK:
            refresh_tool_paths()
            results = run_deps_check(force=True)
            ytdlp_installed = bool(results.get("yt-dlp", {}).get("installed"))

            if platform.system() != "Windows" and not ytdlp_installed:
                msg = "yt-dlp is not installed. Install it first, then retry update."
                _set_ytdlp_update_state("failed", "yt-dlp update failed.", msg)
                return results

            try:
                _set_ytdlp_update_state("updating", "Updating yt-dlp...")
                update_note = ""

                if platform.system() == "Windows":
                    _install_ytdlp_windows()
                    update_note = "Downloaded latest yt-dlp.exe to runtime folder."
                else:
                    r = run_subprocess([YTDLP, "-U"], timeout=180)
                    if r.returncode != 0:
                        detail = summarize_ytdlp_error(r.stderr or r.stdout)
                        raise RuntimeError(detail)
                    update_note = "Ran yt-dlp self-update."

                refresh_tool_paths()
                results = run_deps_check(force=True)
                if results.get("yt-dlp", {}).get("installed"):
                    version = results["yt-dlp"].get("version") or "version unknown"
                    _set_ytdlp_update_state("ready", f"{update_note} Current version: {version}")
                else:
                    msg = "yt-dlp is still missing after update."
                    _set_ytdlp_update_state("failed", "yt-dlp update failed.", msg)
                return results
            except Exception as e:
                err = str(e)
                print(f"yt-dlp update failed: {err}")
                _set_ytdlp_update_state("failed", "yt-dlp update failed.", err)
                refresh_tool_paths()
                return run_deps_check(force=True)


def _get_python_for_pip():
    """Return a Python executable suitable for pip installs.

    When running as a frozen EXE, sys.executable is the EXE itself — using it
    with -m pip would just launch another copy of the app.  Fall back to the
    first real Python found on PATH.
    """
    if not getattr(sys, "frozen", False):
        return sys.executable
    # Frozen: look for a real Python on PATH
    for candidate in ("python", "python3", "py"):
        path = shutil.which(candidate)
        if path:
            return path
    return None




























@app.route("/api/check-deps")
def check_deps():
    # If the cache is already populated (should be from startup), return it.
    if DEPS_CACHE:
        return jsonify(_build_deps_payload(DEPS_CACHE))
    return jsonify(_build_deps_payload(run_deps_check()))


@app.route("/api/bootstrap-deps", methods=["POST"])
def bootstrap_deps():
    results = ensure_runtime_dependencies(auto_install=True)
    return jsonify(_build_deps_payload(results))


@app.route("/api/update-ytdlp", methods=["POST"])
def update_ytdlp():
    results = update_ytdlp_one_click()
    return jsonify(_build_deps_payload(results))


@app.route("/api/storage-config")
def storage_config():
    return jsonify(_build_storage_payload())


@app.route("/api/storage-config", methods=["PUT"])
def update_storage_config():
    data = request.get_json(silent=True) or {}
    output_dir = data.get("output_dir", "")
    try:
        path = set_output_dir(output_dir)
        return jsonify({
            "status": "saved",
            "output_dir": str(path),
            "default_output_dir": str(DEFAULT_OUTPUT_DIR.resolve()),
            "custom_output_dir": str(APP_SETTINGS.get("output_dir", "")).strip() or None,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 400


@app.route("/api/storage-config/reset", methods=["POST"])
def reset_storage_config():
    path = reset_output_dir()
    return jsonify({
        "status": "reset",
        "output_dir": str(path),
        "default_output_dir": str(DEFAULT_OUTPUT_DIR.resolve()),
        "custom_output_dir": None,
    })


@app.route("/api/storage-config/choose", methods=["POST"])
def choose_storage_config():
    try:
        import tkinter as tk
        from tkinter import filedialog

        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        selected = filedialog.askdirectory(initialdir=str(get_output_dir()), title="Choose export save folder")
        root.destroy()
    except Exception as e:
        return jsonify({"error": f"Folder picker unavailable: {e}"}), 400

    if not selected:
        return jsonify({"status": "cancelled"})

    path = set_output_dir(selected)
    return jsonify({
        "status": "saved",
        "output_dir": str(path),
        "default_output_dir": str(DEFAULT_OUTPUT_DIR.resolve()),
        "custom_output_dir": str(APP_SETTINGS.get("output_dir", "")).strip() or None,
    })


def run_deps_check(force=False):
    """Run dependency checks and return the results dict."""
    if DEPS_CACHE and not force:
        return dict(DEPS_CACHE)

    print("Checking system dependencies...")
    refresh_tool_paths()
    results = {}
    tools = [
        ("ffmpeg", FFMPEG, ["-version"]),
        ("ffprobe", FFPROBE, ["-version"]),
        ("yt-dlp", YTDLP, ["--version"]),
        ("deno", DENO, ["--version"]),
    ]
    for name, path, args in tools:
        try:
            r = run_subprocess([path] + args, timeout=10)
            output = (r.stdout + r.stderr).strip()
            has_output = (r.returncode == 0) and bool(output)
            first_line = output.split("\n")[0].strip() if has_output else None
            results[name] = {
                "installed": has_output,
                "version": first_line,
                "path": path if has_output else None,
            }
            if has_output:
                print(f"  [OK] {name} found: {first_line}")
            else:
                print(f"  [MISSING] {name} not found.")
        except FileNotFoundError:
            results[name] = {"installed": False, "version": None, "path": None}
            print(f"  [MISSING] {name} not found (FileNotFound).")
        except subprocess.TimeoutExpired:
            results[name] = {"installed": False, "version": None, "path": None}
            print(f"  [TIMEOUT] {name} timed out.")

    # Update global cache
    DEPS_CACHE.clear()
    DEPS_CACHE.update(results)
    print("Dependency check complete.")
    return results


# ── Shared URL utilities ───────────────────────────────────────

def clean_video_url(url):
    """
    Clean a video URL for supported platforms.
    - YouTube: Remove playlist, radio, and other extra parameters
    - Instagram/TikTok: Pass through as-is (yt-dlp handles them natively)
    """
    from urllib.parse import urlparse, parse_qs

    if not url:
        return url

    try:
        parsed = urlparse(url)
        netloc = parsed.netloc.lower()

        # Instagram URLs - pass through as-is
        if 'instagram.com' in netloc or 'instagr.am' in netloc:
            return url

        # TikTok URLs - pass through as-is
        if 'tiktok.com' in netloc or 'vm.tiktok.com' in netloc:
            return url

        # Handle youtu.be short URLs
        if 'youtu.be' in netloc:
            video_id = parsed.path.strip('/')
            return f"https://www.youtube.com/watch?v={video_id}"

        # Handle standard youtube.com URLs
        if 'youtube.com' in netloc:
            query_params = parse_qs(parsed.query)

            # Extract just the video ID
            video_id = query_params.get('v', [None])[0]

            if video_id:
                # Return clean URL with only the video ID
                return f"https://www.youtube.com/watch?v={video_id}"

        # For any other URL (Twitter, etc.), pass through as-is
        return url
    except Exception:
        return url


def is_youtube_url(url):
    try:
        from urllib.parse import urlparse
        netloc = urlparse(url).netloc.lower()
        return ("youtube.com" in netloc) or ("youtu.be" in netloc)
    except Exception:
        return False


def has_deno_runtime():
    return DENO != "deno"


def parse_timestamp_to_seconds(ts):
    """Convert HH:MM:SS.hh / MM:SS.hh / SS to seconds.

    Also supports compact centiseconds like:
    - 0:012  -> 0.12 seconds
    - 0:1425 -> 14.25 seconds
    """
    raw = str(ts or "").strip()
    if not raw:
        return 0.0

    def parse_seconds_part(part):
        part = str(part or "").strip()
        if not part:
            return 0.0
        if "." in part:
            return float(part)
        if re.fullmatch(r"\d{3,4}", part):
            whole = int(part[:-2] or "0")
            hundredths = int(part[-2:])
            return whole + (hundredths / 100.0)
        return float(part)

    parts = raw.split(":")
    if len(parts) == 3:
        hours = float(parts[0] or 0)
        mins = float(parts[1] or 0)
        secs = parse_seconds_part(parts[2])
        return hours * 3600 + mins * 60 + secs
    if len(parts) == 2:
        mins = float(parts[0] or 0)
        secs = parse_seconds_part(parts[1])
        return mins * 60 + secs
    return parse_seconds_part(parts[0])


@functools.lru_cache(maxsize=32)
def _get_video_info(url):
    """
    Fetch video info from yt-dlp.
    Raises exception on failure so lru_cache only caches success.
    """
    youtube = is_youtube_url(url)
    probes = []

    if youtube:
        probes = [
            [
                YTDLP, "--dump-single-json", "--no-download", "--no-playlist",
                "--force-ipv4",
                "--extractor-args", "youtube:player_client=web",
                url,
            ],
            [
                YTDLP, "--dump-single-json", "--no-download", "--no-playlist",
                "--force-ipv4",
                "--extractor-args", "youtube:player_client=android",
                url,
            ],
            [
                YTDLP, "--dump-single-json", "--no-download", "--no-playlist",
                "--force-ipv4",
                "--extractor-args", "youtube:player_client=mweb",
                url,
            ],
            [
                YTDLP, "--dump-single-json", "--no-download", "--no-playlist",
                "--force-ipv4",
                "--extractor-args", "youtube:player_client=tv_embedded,web",
                url,
            ],
        ]
        if has_deno_runtime():
            probes.insert(
                0,
                [
                    YTDLP, "--dump-single-json", "--no-download", "--no-playlist",
                    "--force-ipv4",
                    "--remote-components", "ejs:github",
                    "--extractor-args", "youtube:player_client=web",
                    url,
                ],
            )
    else:
        probes = [[YTDLP, "--dump-single-json", "--no-download", "--no-playlist", url]]

    last_err = "Invalid URL"
    for probe_cmd in probes:
        r = run_subprocess(probe_cmd, timeout=30)
        if r.returncode == 0:
            return json.loads(r.stdout)
        last_err = summarize_ytdlp_error(r.stderr)

    if looks_like_age_restricted_issue(last_err):
        last_err = (
            f"{last_err}. Age-restricted videos require a logged-in, age-verified "
            "YouTube account in Chrome/Edge."
        )
    raise RuntimeError(last_err)


@app.route("/api/validate-url", methods=["POST"])
def validate_url():
    data = request.get_json()
    url = data.get("url", "").strip()

    # Clean the URL to remove playlist/radio parameters
    url = clean_video_url(url)
    print(f"Validating URL: {url}...")


    if not url:
        return jsonify({"valid": False, "error": "No URL provided"}), 400

    try:
        info = _get_video_info(url)

        title = info.get("title", "Unknown")
        print(f"  Validation success: {title}")
        return jsonify({
            "valid": True,
            "title": title,
            "duration": info.get("duration", 0),
            "thumbnail": info.get("thumbnail", ""),
        })
    except subprocess.TimeoutExpired:
        return jsonify({"valid": False, "error": "Request timed out"}), 504
    except json.JSONDecodeError:
        return jsonify({"valid": False, "error": "Failed to parse video info"}), 500
    except RuntimeError as e:
        print(f"  Validation failed: {str(e)[:100]}...")
        return jsonify({"valid": False, "error": str(e)})
    except Exception as e:
        return jsonify({"valid": False, "error": str(e)}), 500


# ── Shared audio download utility ──────────────────────────────

def download_separate_audio(job_dir, audio_url, audio_start_sec=None, audio_end_sec=None):
    """Download separate audio to job_dir as audio.* and return its path."""
    audio_output = str(job_dir / "audio.%(ext)s")
    youtube = is_youtube_url(audio_url)
    section_requested = (
        audio_start_sec is not None
        and audio_end_sec is not None
        and audio_end_sec > audio_start_sec
    )

    def build_audio_cmd(profile, use_sections):
        cmd = [
            YTDLP,
            "--no-playlist",
            "--force-ipv4",
            "-f", profile["format"],
            "-x",
            "--audio-format", "wav",
            "--retries", "5",
            "--fragment-retries", "5",
            "-o", audio_output,
        ]
        if profile.get("sort"):
            cmd.extend(["-S", profile["sort"]])
        cmd.extend(profile.get("extra", []))
        if use_sections and section_requested:
            cmd.extend(["--download-sections", f"*{audio_start_sec}-{audio_end_sec}"])
        if FFMPEG_DIR:
            cmd.extend(["--ffmpeg-location", FFMPEG_DIR])
        cmd.append(audio_url)
        return cmd

    section_modes = [True, False] if section_requested else [False]
    profiles = build_ytdlp_profiles("audio", youtube=youtube, has_deno=has_deno_runtime())
    success, last_stderr, last_error = run_ytdlp_with_retries(
        job_dir,
        file_glob="audio.*",
        build_cmd=build_audio_cmd,
        profiles=profiles,
        section_modes=section_modes,
        run_subprocess=run_subprocess,
        timeout=300,
        default_error="Audio download failed",
        clean_first_attempt=True,
    )

    if not success:
        if youtube:
            if looks_like_age_restricted_issue(last_stderr):
                last_error = (
                    f"{last_error}. Age-restricted videos require a logged-in, age-verified "
                    "YouTube account in Chrome/Edge. Sign in there and retry."
                )
            elif looks_like_youtube_challenge_issue(last_stderr):
                hint = "Update yt-dlp (`pip install -U yt-dlp`) and retry."
                if not has_deno_runtime():
                    hint += " Install Deno (`winget install DenoLand.Deno`) for challenge solving."
                last_error = f"{last_error}. {hint}"
        raise RuntimeError(last_error)

    audio_files = list(job_dir.glob("audio.*"))
    if not audio_files:
        raise RuntimeError("No audio file downloaded")

    # Verify the file is real audio, not a 0-byte stub or video-only stream.
    probe = probe_media_file(audio_files[0])
    if not probe["ok"]:
        raise RuntimeError(f"Audio download could not be verified: {probe['error']}")
    if not probe["has_audio"]:
        raise RuntimeError("Audio download has no audio stream")
    if probe["duration"] <= 0:
        raise RuntimeError("Audio download has zero duration")

    return str(audio_files[0])


# ── Shared routes ──────────────────────────────────────────────

@app.route("/api/status/<job_id>")
def job_status(job_id):
    if not is_safe_job_id(job_id):
        return jsonify({"error": "Invalid job_id"}), 400
    if job_id not in jobs:
        return jsonify({"status": "unknown"}), 404
    return jsonify(jobs[job_id])


@app.route("/api/waveform/<job_id>")
def get_waveform(job_id):
    if not is_safe_job_id(job_id):
        return jsonify({"error": "Invalid job_id"}), 400
    job_dir = DOWNLOADS_DIR / job_id
    files = list(job_dir.glob("clip.*"))
    if not files:
        return jsonify({"error": "No clip found"}), 404
    input_file = str(files[0])
    waveform_path = PROCESSING_DIR / f"{job_id}_waveform.png"
    if not waveform_path.exists():
        PROCESSING_DIR.mkdir(parents=True, exist_ok=True)
        cmd = [
            FFMPEG, "-y", "-i", input_file,
            "-filter_complex", "showwavespic=s=1200x80:colors=#56a3ff|#3a7acc",
            "-frames:v", "1", str(waveform_path),
        ]
        extra = {}
        if platform.system() == "Windows":
            extra["creationflags"] = subprocess.CREATE_NO_WINDOW
        result = subprocess.run(cmd, capture_output=True, timeout=30, **extra)
        if result.returncode != 0 or not waveform_path.exists():
            return jsonify({"error": "Waveform generation failed"}), 500
    return send_file(str(waveform_path), mimetype="image/png")


@app.route("/api/download-result/<job_id>")
def download_result(job_id):
    if not is_safe_job_id(job_id):
        return jsonify({"error": "Invalid job_id"}), 400
    job = jobs.get(job_id)
    if not job:
        return jsonify({"error": "Unknown job id"}), 404
    if job.get("status") != "complete":
        return jsonify({"error": "Result is not ready yet"}), 409

    filename = str(job.get("filename") or f"alert_{job_id}.mp4")
    safe_filename = Path(filename).name
    filepath = get_output_dir() / safe_filename
    if not filepath.exists():
        return jsonify({"error": "File not found"}), 404
    return send_file(str(filepath), as_attachment=True, download_name=safe_filename)


@app.route("/api/cleanup/<job_id>", methods=["POST"])
def cleanup(job_id):
    if not is_safe_job_id(job_id):
        return jsonify({"error": "Invalid job_id"}), 400
    import shutil
    for d in [DOWNLOADS_DIR / job_id, PROCESSING_DIR / job_id]:
        if d.exists():
            shutil.rmtree(d, ignore_errors=True)
    return jsonify({"status": "cleaned"})


@app.route("/api/health")
def health():
    return jsonify({"status": "ok"})


@app.route("/api/shutdown", methods=["POST"])
def shutdown():
    func = request.environ.get("werkzeug.server.shutdown")
    if func is None:
        # Fallback for non-Werkzeug servers or threaded mode if needed
        print("Shutting down (forced exit)...")
        os._exit(0)
    print("Shutting down server...")
    func()
    return jsonify({"status": "shutting down"})


# ── Register feature modules ──────────────────────────────────

from alert import register_alert_routes
register_alert_routes(app)


# ── Run ─────────────────────────────────────────────────────────

def bootstrap_runtime():
    if platform.system() == "Windows" and not getattr(sys, 'frozen', False):
        os.system("title deutschmark's Alert! Alert!")
    print("Checking runtime dependencies...")
    ensure_runtime_dependencies(auto_install=False)
    refresh_tool_paths()

    print(r"""
    _    _           _   _      _    _           _   _
   / \  | | ___ _ __| |_| |    / \  | | ___ _ __| |_| |
  / _ \ | |/ _ \ '__| __| |   / _ \ | |/ _ \ '__| __| |
 / ___ \| |  __/ |  | |_|_|  / ___ \| |  __/ |  | |_|_|
/_/   \_\_|\___|_|   \__(_) /_/   \_\_|\___|_|   \__(_)
""")
    print("="*65)
    print("  deutschmark's Alert! Alert!")
    print("="*65)
    print(f"  ffmpeg:  {FFMPEG}")
    print(f"  ffprobe: {FFPROBE}")
    print(f"  yt-dlp:  {YTDLP}")
    if DEPS_BOOTSTRAP_STATE["status"] == "failed":
        print(f"  dependency install warning: {DEPS_BOOTSTRAP_STATE['last_error']}")
    elif DEPS_BOOTSTRAP_STATE["status"] == "ready":
        print("  dependency status: ready")
    print("")


def is_port_available(host, port):
    # NB: do NOT set SO_REUSEADDR here. On Windows it lets bind() succeed even
    # when another process is already listening on the port, so this probe would
    # falsely report an occupied port (e.g. another app on :3000) as free — the
    # app would then load whatever is really serving there. A plain bind is the
    # accurate availability test.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind((host, port))
            return True
        except OSError:
            return False


def find_available_port(host=DEFAULT_APP_HOST, preferred_port=DEFAULT_APP_PORT, max_tries=25):
    if is_port_available(host, preferred_port):
        return preferred_port

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((host, 0))
        dynamic_port = sock.getsockname()[1]
    if dynamic_port:
        return dynamic_port

    for offset in range(1, max_tries + 1):
        port = preferred_port + offset
        if is_port_available(host, port):
            return port
    raise RuntimeError("Could not find an open localhost port for the app server.")


def start_server(host=DEFAULT_APP_HOST, port=DEFAULT_APP_PORT, open_browser=False):
    bootstrap_runtime()
    app_url = f"http://{host}:{port}"
    print("  Starting server...")
    if open_browser:
        print("  App will open in your browser.")
        print("  Keep this window open while using the app.")
    else:
        print("  App is running in desktop mode.")
    print("="*65)

    if open_browser:
        webbrowser.open(app_url)

    from waitress import serve
    serve(app, host=host, port=port, threads=6)


if __name__ == "__main__":
    start_server(host=DEFAULT_APP_HOST, port=DEFAULT_APP_PORT, open_browser=True)
