import os
import json
import uuid
import glob
import re
import mimetypes
import platform
import subprocess
import threading
import shutil
import zipfile
from functools import lru_cache
from pathlib import Path
from urllib.request import Request, urlopen
from flask import Flask, request, jsonify, send_file, send_from_directory

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

TEMP_DIR = BASE_DIR / "temp"
DOWNLOADS_DIR = TEMP_DIR / "downloads"
PROCESSING_DIR = TEMP_DIR / "processing"
OUTPUT_DIR = BASE_DIR / "output"
if platform.system() == "Windows":
    local_app_data = os.environ.get("LOCALAPPDATA")
    runtime_root_base = Path(local_app_data) if local_app_data else BASE_DIR
    RUNTIME_DIR = runtime_root_base / "alert-alert" / "runtime"
else:
    RUNTIME_DIR = BASE_DIR / ".runtime"
RUNTIME_BIN_DIR = RUNTIME_DIR / "bin"

# Ensure directories exist
for d in [DOWNLOADS_DIR, PROCESSING_DIR, OUTPUT_DIR, RUNTIME_BIN_DIR]:
    d.mkdir(parents=True, exist_ok=True)

NULL_DEVICE = "NUL" if platform.system() == "Windows" else "/dev/null"
AUTO_INSTALL_SUPPORTED = platform.system() == "Windows"
FFMPEG_WINDOWS_URL = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"
YTDLP_WINDOWS_URL = "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp.exe"
DENO_WINDOWS_URL = "https://github.com/denoland/deno/releases/latest/download/deno-x86_64-pc-windows-msvc.zip"

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

    return subprocess.run(cmd, **kwargs)


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

    r = subprocess.run(args, **kwargs)
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


# ── Serve frontend ──────────────────────────────────────────────

@app.route("/")
def index():
    return send_from_directory("static", "index.html")

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
    payload["ytdlp_update_available"] = bool(results.get("yt-dlp", {}).get("installed")) or AUTO_INSTALL_SUPPORTED
    return payload


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


def summarize_ytdlp_error(stderr_text):
    """Return a concise yt-dlp error message from stderr."""
    if not stderr_text:
        return "Download failed"

    lines = [ln.strip() for ln in stderr_text.splitlines() if ln.strip()]
    error_lines = [ln for ln in lines if ln.startswith("ERROR:")]
    if error_lines:
        return error_lines[-1]

    warn_lines = [ln for ln in lines if ln.startswith("WARNING:")]
    if warn_lines:
        return warn_lines[-1]

    return lines[-1] if lines else "Download failed"


def looks_like_youtube_challenge_issue(stderr_text):
    s = (stderr_text or "").lower()
    markers = [
        "sabr",
        "forbidden",
        "http error 403",
        "signature solving failed",
        "n challenge solving failed",
        "requested format is not available",
        "only images are available for download",
        "po token",
        "remote components challenge solver",
    ]
    return any(m in s for m in markers)


def looks_like_age_restricted_issue(stderr_text):
    s = (stderr_text or "").lower()
    markers = [
        "age-restricted",
        "sign in to confirm your age",
        "confirm your age",
        "this video may be inappropriate",
        "this content may be inappropriate",
    ]
    return any(m in s for m in markers)


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

    def build_profiles():
        if not youtube:
            return [
                {"name": "standard", "format": "bestaudio/b", "sort": "abr", "extra": []},
                {"name": "compatibility", "format": "b/bestaudio", "sort": None, "extra": []},
            ]

        progressive_audio = "ba[ext=m4a]/bestaudio/b"
        profiles = [
            {
                "name": "web client",
                "format": progressive_audio,
                "sort": "ext:m4a,abr",
                "extra": ["--extractor-args", "youtube:player_client=web"],
            },
            {
                "name": "android client",
                "format": progressive_audio,
                "sort": "ext:m4a,abr",
                "extra": ["--extractor-args", "youtube:player_client=android"],
            },
            {
                "name": "mweb client",
                "format": progressive_audio,
                "sort": "ext:m4a,abr",
                "extra": ["--extractor-args", "youtube:player_client=mweb"],
            },
            {
                "name": "tv embedded client",
                "format": progressive_audio,
                "sort": "ext:m4a,abr",
                "extra": ["--extractor-args", "youtube:player_client=tv_embedded,web"],
            },
        ]

        if has_deno_runtime():
            profiles.append(
                {
                    "name": "web + challenge solver",
                    "format": progressive_audio,
                    "sort": "ext:m4a,abr",
                    "extra": [
                        "--remote-components", "ejs:github",
                        "--extractor-args", "youtube:player_client=web",
                    ],
                }
            )

        for browser in ["chrome", "edge", "firefox"]:
            profiles.append(
                {
                    "name": f"{browser} cookies",
                    "format": progressive_audio,
                    "sort": "ext:m4a,abr",
                    "extra": [
                        "--cookies-from-browser", browser,
                        "--extractor-args", "youtube:player_client=web",
                    ],
                }
            )

        profiles.append(
            {"name": "compatibility format", "format": "b/bestaudio", "sort": None, "extra": []}
        )
        return profiles

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
    profiles = build_profiles()
    last_error = "Audio download failed"
    last_stderr = ""
    success = False

    for use_sections in section_modes:
        for profile in profiles:
            for old_audio in job_dir.glob("audio.*"):
                old_audio.unlink(missing_ok=True)

            r = run_subprocess(build_audio_cmd(profile, use_sections), timeout=300)
            if r.returncode == 0:
                success = True
                break

            last_stderr = r.stderr or ""
            last_error = summarize_ytdlp_error(last_stderr)

        if success:
            break

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
    return str(audio_files[0])


# ── Shared routes ──────────────────────────────────────────────

@app.route("/api/status/<job_id>")
def job_status(job_id):
    if not is_safe_job_id(job_id):
        return jsonify({"error": "Invalid job_id"}), 400
    if job_id not in jobs:
        return jsonify({"status": "unknown"}), 404
    return jsonify(jobs[job_id])


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
    filepath = OUTPUT_DIR / safe_filename
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

from reel import register_reel_routes
register_reel_routes(app)

from captions import register_caption_routes
register_caption_routes(app)


# ── Run ─────────────────────────────────────────────────────────

if __name__ == "__main__":
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
    print("  Starting server...")
    print("  App will open in your browser.")
    print("  Keep this window open while using the app.")
    print("="*65)

    # Open the browser
    webbrowser.open("http://127.0.0.1:5000")

    # Run using Waitress (Production Server)
    from waitress import serve
    serve(app, host="127.0.0.1", port=5000, threads=6)
