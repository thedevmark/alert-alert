import os
import json
import uuid
import glob
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


def _set_bootstrap_state(status, message="", error=None):
    DEPS_BOOTSTRAP_STATE["status"] = status
    DEPS_BOOTSTRAP_STATE["message"] = message
    DEPS_BOOTSTRAP_STATE["last_error"] = error


def _required_missing(results):
    required = ("ffmpeg", "ffprobe", "yt-dlp")
    return [name for name in required if not results.get(name, {}).get("installed")]


def _build_deps_payload(results):
    payload = dict(results)
    payload["required_missing"] = _required_missing(results)
    payload["auto_install_available"] = AUTO_INSTALL_SUPPORTED
    payload["bootstrap"] = dict(DEPS_BOOTSTRAP_STATE)
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


def ensure_runtime_dependencies(auto_install=False):
    """Check dependencies and optionally auto-install missing required tools."""
    refresh_tool_paths()
    results = run_deps_check(force=True)
    missing = _required_missing(results)

    if not missing:
        _set_bootstrap_state("ready", "All required dependencies are installed.")
        return results

    if not auto_install or not AUTO_INSTALL_SUPPORTED:
        return results

    with DEPS_BOOTSTRAP_LOCK:
        # Re-check inside lock to avoid duplicate installers.
        refresh_tool_paths()
        results = run_deps_check(force=True)
        missing = _required_missing(results)
        if not missing:
            _set_bootstrap_state("ready", "All required dependencies are installed.")
            return results

        try:
            _set_bootstrap_state("installing", "Installing required dependencies...")
            print("Auto-install: missing dependencies detected:", ", ".join(missing))
            if "ffmpeg" in missing or "ffprobe" in missing:
                print("Auto-install: downloading FFmpeg runtime...")
                _install_ffmpeg_windows()
            if "yt-dlp" in missing:
                print("Auto-install: downloading yt-dlp runtime...")
                _install_ytdlp_windows()

            refresh_tool_paths()
            results = run_deps_check(force=True)
            missing_after = _required_missing(results)
            if missing_after:
                msg = f"Still missing: {', '.join(missing_after)}"
                _set_bootstrap_state("failed", msg, msg)
            else:
                _set_bootstrap_state("ready", "Dependencies installed successfully.")
            return results
        except Exception as e:
            err = str(e)
            print(f"Auto-install failed: {err}")
            _set_bootstrap_state("failed", "Dependency auto-install failed.", err)
            return results

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


# ── Validate Video URL ──────────────────────────────────────────

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


# ── Download clip ───────────────────────────────────────────────

def parse_timestamp_to_seconds(ts):
    """Convert HH:MM:SS or MM:SS or SS to seconds."""
    parts = ts.strip().split(":")
    parts = [float(p) for p in parts]
    if len(parts) == 3:
        return parts[0] * 3600 + parts[1] * 60 + parts[2]
    elif len(parts) == 2:
        return parts[0] * 60 + parts[1]
    else:
        return parts[0]


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


def run_download_pipeline(job_id, url, start_sec, end_sec, use_separate_audio=False,
                         audio_url="", audio_start_sec=0, audio_end_sec=0):
    """Run the download pipeline in a background thread."""
    try:
        job_dir = DOWNLOADS_DIR / job_id
        job_dir.mkdir(parents=True, exist_ok=True)

        output_template = str(job_dir / "clip.%(ext)s")
        youtube = is_youtube_url(url)

        def build_profiles():
            if not youtube:
                return [
                    {"name": "standard", "format": "bv*+ba/b", "sort": "res", "extra": []},
                    {"name": "compatibility", "format": "b/bv*+ba", "sort": None, "extra": []},
                ]

            progressive_format = "b[ext=mp4]/b[ext=webm]/b"
            merged_format = "bv*[ext=mp4]+ba[ext=m4a]/bv*+ba/b"
            profiles = [
                {
                    "name": "web progressive",
                    "format": progressive_format,
                    "sort": "res,ext:mp4:m4a",
                    "extra": ["--extractor-args", "youtube:player_client=web"],
                },
                {
                    "name": "android progressive",
                    "format": progressive_format,
                    "sort": "res,ext:mp4:m4a",
                    "extra": ["--extractor-args", "youtube:player_client=android"],
                },
                {
                    "name": "mweb progressive",
                    "format": progressive_format,
                    "sort": "res,ext:mp4:m4a",
                    "extra": ["--extractor-args", "youtube:player_client=mweb"],
                },
                {
                    "name": "tv embedded progressive",
                    "format": progressive_format,
                    "sort": "res,ext:mp4:m4a",
                    "extra": ["--extractor-args", "youtube:player_client=tv_embedded,web"],
                },
                {
                    "name": "web adaptive merge",
                    "format": merged_format,
                    "sort": "res,ext:mp4:m4a",
                    "extra": ["--extractor-args", "youtube:player_client=web"],
                },
            ]

            if has_deno_runtime():
                profiles.append(
                    {
                        "name": "web progressive + challenge solver",
                        "format": progressive_format,
                        "sort": "res,ext:mp4:m4a",
                        "extra": [
                            "--remote-components", "ejs:github",
                            "--extractor-args", "youtube:player_client=web",
                        ],
                    }
                )

            for browser in ["chrome", "edge", "firefox"]:
                profiles.append(
                    {
                        "name": f"{browser} cookies progressive",
                        "format": progressive_format,
                        "sort": "res,ext:mp4:m4a",
                        "extra": [
                            "--cookies-from-browser", browser,
                            "--extractor-args", "youtube:player_client=web",
                        ],
                    }
                )

            profiles.append(
                {"name": "compatibility adaptive", "format": merged_format, "sort": None, "extra": []}
            )
            return profiles

        def build_video_download_cmd(profile, use_sections):
            cmd = [
                YTDLP,
                "--no-playlist",
                "--force-ipv4",
                "-f", profile["format"],
                "--retries", "5",
                "--fragment-retries", "5",
                "-o", output_template,
            ]
            if profile.get("sort"):
                cmd.extend(["-S", profile["sort"]])
            cmd.extend(profile.get("extra", []))
            if use_sections:
                cmd.extend(["--download-sections", f"*{start_sec}-{end_sec}"])
            if FFMPEG_DIR:
                cmd.extend(["--ffmpeg-location", FFMPEG_DIR])
            cmd.append(url)
            return cmd

        # For full downloads (start at 0), avoid section-based ffmpeg URL reads.
        section_modes = [True, False] if start_sec > 0 else [False]
        profiles = build_profiles()
        total_attempts = len(section_modes) * len(profiles)
        attempt_idx = 0

        last_error = "Download failed"
        last_stderr = ""
        success = False

        for use_sections in section_modes:
            for profile in profiles:
                attempt_idx += 1
                if attempt_idx > 1:
                    for old_clip in job_dir.glob("clip.*"):
                        old_clip.unlink(missing_ok=True)

                mode_label = "sectioned" if use_sections else "full"
                jobs[job_id] = {
                    "status": "downloading",
                    "progress": min(5 + int((attempt_idx - 1) * 40 / max(1, total_attempts - 1)), 50),
                    "stage": f"Downloading video ({attempt_idx}/{total_attempts}) [{mode_label}, {profile['name']}]...",
                }

                r = run_subprocess(build_video_download_cmd(profile, use_sections), timeout=360)
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
                    hint = (
                        "Age-restricted videos require a logged-in, age-verified YouTube account. "
                        "Sign in to YouTube in Chrome or Edge with that account, then retry."
                    )
                    last_error = f"{last_error}. {hint}"
                elif looks_like_youtube_challenge_issue(last_stderr):
                    hint = (
                        "YouTube challenge/protection blocked usable formats. "
                        "Update yt-dlp (`pip install -U yt-dlp`). "
                    )
                    if not has_deno_runtime():
                        hint += "Install Deno (`winget install DenoLand.Deno`) for challenge solving. "
                    hint += "If this video is restricted, sign in to YouTube in Chrome/Edge and retry."
                    last_error = f"{last_error}. {hint}"

            jobs[job_id] = {"status": "error", "error": last_error}
            return

        # Find the downloaded file
        files = list(job_dir.glob("clip.*"))
        if not files:
            jobs[job_id] = {"status": "error", "error": "No file downloaded"}
            return

        filename = files[0].name
        
        # If using separate audio, download audio-only from second URL
        if use_separate_audio:
            jobs[job_id] = {"status": "downloading", "progress": 50, "stage": "Downloading audio clip..."}
            try:
                separate_audio_file = download_separate_audio(
                    job_dir,
                    audio_url,
                    audio_start_sec,
                    audio_end_sec,
                )
            except Exception as e:
                jobs[job_id] = {"status": "error", "error": f"Audio download failed: {str(e)}"}
                return

            jobs[job_id] = {
                "status": "downloaded", 
                "filename": filename,
                "audio_filename": Path(separate_audio_file).name,
                "use_separate_audio": True
            }
            return
        
        jobs[job_id] = {"status": "downloaded", "filename": filename, "use_separate_audio": False}
        print(f"Download job {job_id} complete: {filename}")

    except subprocess.TimeoutExpired:
        jobs[job_id] = {"status": "error", "error": "Download timed out"}
    except Exception as e:
        jobs[job_id] = {"status": "error", "error": str(e)}


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


@app.route("/api/download", methods=["POST"])
def download_clip():
    data = request.get_json()
    url = data.get("url", "").strip()
    start = data.get("start", "").strip()
    end = data.get("end", "").strip()

    # Clean URLs to remove playlist/radio parameters
    url = clean_video_url(url)

    # Optional separate audio source
    audio_url = data.get("audio_url", "").strip()
    audio_url = clean_video_url(audio_url) if audio_url else ""
    audio_start = data.get("audio_start", "").strip()
    audio_end = data.get("audio_end", "").strip()
    use_separate_audio = bool(audio_url and audio_start and audio_end)

    if not url or not start or not end:
        return jsonify({"error": "Missing url, start, or end"}), 400

    start_sec = parse_timestamp_to_seconds(start)
    end_sec = parse_timestamp_to_seconds(end)
    if end_sec <= start_sec:
        return jsonify({"error": "End time must be after start time"}), 400

    audio_start_sec = 0
    audio_end_sec = 0
    if use_separate_audio:
        audio_start_sec = parse_timestamp_to_seconds(audio_start)
        audio_end_sec = parse_timestamp_to_seconds(audio_end)
        if audio_end_sec <= audio_start_sec:
            return jsonify({"error": "Audio end time must be after start time"}), 400

    job_id = uuid.uuid4().hex[:8]

    jobs[job_id] = {"status": "downloading", "progress": 0, "stage": "Downloading video clip..."}
    print(f"Starting download job {job_id} for {url} ({start}-{end})")

    thread = threading.Thread(
        target=run_download_pipeline,
        args=(job_id, url, start_sec, end_sec, use_separate_audio, audio_url, audio_start_sec, audio_end_sec),
        daemon=True
    )
    thread.start()

    return jsonify({"job_id": job_id, "status": "downloading"})


# ── Upload local video ───────────────────────────────────────────

@app.route("/api/upload-video", methods=["POST"])
def upload_video():
    """Handle local video file upload."""
    if 'video' not in request.files:
        return jsonify({"error": "No video file provided"}), 400

    file = request.files['video']
    if not file.filename:
        return jsonify({"error": "No file selected"}), 400

    # Check file extension
    allowed_extensions = {'mp4', 'mov', 'avi', 'mkv', 'webm', 'wmv', 'm4v'}
    ext = file.filename.rsplit('.', 1)[1].lower() if '.' in file.filename else ''
    if ext not in allowed_extensions:
        return jsonify({"error": f"Unsupported file format. Allowed: {', '.join(allowed_extensions)}"}), 400

    job_id = uuid.uuid4().hex[:8]
    job_dir = DOWNLOADS_DIR / job_id
    job_dir.mkdir(parents=True, exist_ok=True)

    # Save the uploaded file
    save_path = job_dir / f"clip.{ext}"
    file.save(str(save_path))

    jobs[job_id] = {"status": "uploading", "progress": 50, "stage": "Processing upload..."}
    print(f"Upload job {job_id}: saved {file.filename}")

    # Get video duration using ffprobe
    try:
        kwargs = {
            "capture_output": True,
            "text": True,
            "timeout": 15,
            "env": get_env()
        }
        if platform.system() == "Windows":
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW

        r = subprocess.run(
            [FFPROBE, "-v", "quiet", "-print_format", "json",
             "-show_format", str(save_path)],
            **kwargs
        )
        info = json.loads(r.stdout)
        duration = float(info.get("format", {}).get("duration", 0))
    except Exception as e:
        print(f"Error getting video info: {e}")
        duration = 0

    jobs[job_id] = {"status": "downloaded", "filename": f"clip.{ext}"}
    print(f"Upload job {job_id} complete: clip.{ext}, duration: {duration}s")

    return jsonify({
        "job_id": job_id,
        "filename": f"clip.{ext}",
        "duration": duration,
        "status": "downloaded"
    })


# ── Video info ──────────────────────────────────────────────────

@app.route("/api/video-info/<job_id>")
def video_info(job_id):
    if not is_safe_job_id(job_id):
        return jsonify({"error": "Invalid job_id"}), 400
    job_dir = DOWNLOADS_DIR / job_id
    files = list(job_dir.glob("clip.*"))
    if not files:
        return jsonify({"error": "No clip found"}), 404

    input_file = str(files[0])
    try:
        # Use CREATE_NO_WINDOW on Windows to prevent popups
        kwargs = {
            "capture_output": True,
            "text": True,
            "timeout": 15,
            "env": get_env()
        }
        if platform.system() == "Windows":
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW

        r = subprocess.run(
            [
                FFPROBE, "-v", "quiet", "-print_format", "json",
                "-show_streams", "-show_format", input_file,
            ],
            **kwargs
        )
        info = json.loads(r.stdout)
        video_stream = next(
            (s for s in info.get("streams", []) if s.get("codec_type") == "video"), None
        )
        if not video_stream:
            return jsonify({"error": "No video stream found"}), 500

        width = int(video_stream.get("width", 0))
        height = int(video_stream.get("height", 0))

        # Parse frame rate
        fps_str = video_stream.get("r_frame_rate", "30/1")
        if "/" in fps_str:
            num, den = fps_str.split("/")
            fps = float(num) / float(den) if float(den) != 0 else 30.0
        else:
            fps = float(fps_str)

        duration = float(info.get("format", {}).get("duration", 0))

        return jsonify({
            "width": width,
            "height": height,
            "fps": round(fps, 2),
            "duration": round(duration, 2),
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── Preview frame ───────────────────────────────────────────────

@app.route("/api/preview-frame", methods=["POST"])
def preview_frame():
    data = request.get_json()
    job_id = data.get("job_id", "")
    if not is_safe_job_id(job_id):
        return jsonify({"error": "Invalid job_id"}), 400
    timestamp = float(data.get("timestamp", 0))

    job_dir = DOWNLOADS_DIR / job_id
    files = list(job_dir.glob("clip.*"))
    if not files:
        return jsonify({"error": "No clip found"}), 404

    input_file = str(files[0])
    output_frame = str(PROCESSING_DIR / f"{job_id}_preview.jpg")

    try:
        # Note: text=False for binary output capture if needed, but here we just need execution
        # We don't read stdout for the image, we read the written file. But capturing avoids console spam.
        run_subprocess(
            [
                FFMPEG, "-ss", str(timestamp), "-i", input_file,
                "-frames:v", "1", "-q:v", "2", "-y", output_frame,
            ],
            timeout=15
        )
        return send_file(output_frame, mimetype="image/jpeg")
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── Processing pipeline ────────────────────────────────────────

def run_ffmpeg(args, env, timeout=120):
    """Run an ffmpeg/ffprobe command, return the result. Raises on failure with clean error."""
    # We pass env explicitly to run_ffmpeg, but run_subprocess gets it from get_env()
    # To support the existing pipeline logic which constructs env, we'll respect the passed env
    # but still use our wrapper logic for flags
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


def run_processing_pipeline(
    job_id,
    crop_x,
    crop_y,
    crop_width,
    crop_height,
    trim_start=0,
    trim_end=0,
    use_separate_audio=False,
    use_static_image=False,
    resolution=720,
    buffer_duration=2,
    normalize_audio=True,
    audio_fade_mode="none",
    audio_fade_duration=0.35,
    separate_audio_source_type="url",
    separate_audio_url="",
    separate_audio_start=None,
    separate_audio_end=None,
):
    """Run the full ffmpeg pipeline in a background thread.
    
    Audio Quality Strategy:
    - Extract audio to lossless PCM WAV for all processing stages
    - Apply loudnorm filter on PCM (no generation loss) if normalize_audio is True
    - Only encode to AAC once at the very end at 192kbps
    - If using separate audio source, replace video audio before processing
    

    Args:
    - crop_x, crop_y: top-left corner of crop region in source pixels
    - crop_width, crop_height: dimensions of crop region in source pixels
    - trim_start: start time in seconds (0-based)
    - trim_end: end time in seconds (0-based)
    
    Settings:
    - resolution: base output size (width for wide, height for tall)
    - buffer_duration: seconds of still frame buffer at end
    - normalize_audio: whether to apply loudness normalization
    """
    # Calculate output dimensions based on crop aspect ratio
    crop_aspect = 1.0
    if crop_width and crop_height:
        crop_aspect = crop_width / crop_height
        
    if crop_aspect >= 1:
        # Wide or square: resolution is the width
        output_width = int(resolution)
        output_height = int(resolution / crop_aspect)
    else:
        # Tall: resolution is the height
        output_height = int(resolution)
        output_width = int(resolution * crop_aspect)
    env = get_env()
    try:
        job_dir = DOWNLOADS_DIR / job_id
        proc_dir = PROCESSING_DIR / job_id
        proc_dir.mkdir(parents=True, exist_ok=True)

        files = list(job_dir.glob("clip.*"))
        if not files:
            jobs[job_id] = {"status": "error", "error": "No clip found"}
            return

        input_file = str(files[0])
        
        # Check for separate audio source
        separate_audio_file = None
        if use_separate_audio:
            audio_files = list(job_dir.glob("audio.*"))
            separate_audio_file = str(audio_files[0]) if audio_files else None

            if (
                not separate_audio_file
                and separate_audio_source_type == "url"
                and separate_audio_url
            ):
                jobs[job_id] = {"status": "processing", "progress": 10, "stage": "Downloading separate audio..."}
                separate_audio_file = download_separate_audio(
                    job_dir,
                    separate_audio_url,
                    None,
                    None,
                )

            if not separate_audio_file:
                raise RuntimeError("Separate audio source is enabled, but no audio file was provided.")

        # Check for static image
        static_image_files = list(job_dir.glob("image.*"))
        static_image_file = str(static_image_files[0]) if static_image_files and use_static_image else None


        # Get video info for matching parameters
        probe = run_ffmpeg(
            [FFPROBE, "-v", "quiet", "-print_format", "json",
             "-show_streams", "-show_format", input_file],
            env=env, timeout=15
        )
        info = json.loads(probe.stdout)
        video_stream = next(
            s for s in info["streams"] if s["codec_type"] == "video"
        )
        src_width = int(video_stream.get("width", 0))
        src_height = int(video_stream.get("height", 0))
        crop_src_width = src_width
        crop_src_height = src_height
        fps_str = video_stream.get("r_frame_rate", "30/1")
        if "/" in fps_str:
            num, den = fps_str.split("/")
            fps = float(num) / float(den) if float(den) != 0 else 30.0
        else:
            fps = float(fps_str)

        if use_static_image and static_image_file:
            image_probe = run_ffmpeg(
                [FFPROBE, "-v", "quiet", "-print_format", "json", "-show_streams", static_image_file],
                env=env, timeout=15
            )
            image_info = json.loads(image_probe.stdout)
            image_stream = next(
                (s for s in image_info.get("streams", []) if s.get("codec_type") == "video"),
                None
            )
            if image_stream:
                crop_src_width = int(image_stream.get("width", crop_src_width))
                crop_src_height = int(image_stream.get("height", crop_src_height))

        # Default sample rate
        sample_rate = "48000"
        
        # Get audio info - prefer separate audio if available
        if separate_audio_file:
            audio_probe = run_ffmpeg(
                [FFPROBE, "-v", "quiet", "-print_format", "json",
                 "-show_streams", separate_audio_file],
                env=env, timeout=15
            )
            audio_info = json.loads(audio_probe.stdout)
            audio_stream = next(
                (s for s in audio_info["streams"] if s["codec_type"] == "audio"), None
            )
            if audio_stream:
                sample_rate = audio_stream.get("sample_rate", "48000")
        else:
            audio_stream = next(
                (s for s in info["streams"] if s["codec_type"] == "audio"), None
            )
            if audio_stream:
                sample_rate = audio_stream.get("sample_rate", "48000")

        # Clamp crop parameters to valid range
        if crop_width <= 0:
            crop_width = crop_src_width
        if crop_height <= 0:
            crop_height = crop_src_height
        crop_width = min(crop_width, crop_src_width)
        crop_height = min(crop_height, crop_src_height)
        crop_x = max(0, min(crop_x, crop_src_width - crop_width))
        crop_y = max(0, min(crop_y, crop_src_height - crop_height))

        duration = max(0.0, trim_end - trim_start)
        audio_seek_start = 0.0
        requested_audio_duration = 0.0
        if use_separate_audio:
            if separate_audio_start is not None:
                audio_seek_start = max(0.0, float(separate_audio_start))
            if (
                separate_audio_start is not None
                and separate_audio_end is not None
                and separate_audio_end > separate_audio_start
            ):
                requested_audio_duration = float(separate_audio_end - separate_audio_start)
                if duration > 0:
                    duration = min(duration, requested_audio_duration)
                else:
                    duration = requested_audio_duration
        source_duration = float(info.get("format", {}).get("duration", 0) or 0)
        clip_duration_for_fade = duration if duration > 0 else max(0.0, source_duration - max(0.0, trim_start))
        audio_source = separate_audio_file if separate_audio_file else input_file
        
        # ── Stage 1: Audio Analysis (if needed) ──
        measured_loudnorm_filter = ""
        
        if normalize_audio:
            jobs[job_id] = {"status": "processing", "progress": 15, "stage": "Analyzing audio levels..."}

            # Analyze source audio directly with trim applied
            # Note: We duplicate the input args here for the analysis pass
            analysis_args = [FFMPEG]
            if use_separate_audio and audio_seek_start > 0:
                analysis_args.extend(["-ss", str(audio_seek_start)])
            elif trim_start > 0:
                analysis_args.extend(["-ss", str(trim_start)])

            analysis_args.extend(["-i", audio_source])

            if duration > 0:
                 analysis_args.extend(["-t", str(duration)])

            # Use resample to ensure consistent analysis
            analysis_args.extend([
                "-af", f"aresample={sample_rate},loudnorm=I=-16:TP=-1.5:LRA=11:print_format=json",
                "-f", "null", NULL_DEVICE
            ])

            measure_result = subprocess.run(
                analysis_args, capture_output=True, text=True, timeout=120, env=env
            )

            # Parse the loudnorm JSON from stderr
            stderr_text = measure_result.stderr
            marker = '"input_i"'
            marker_pos = stderr_text.find(marker)
            if marker_pos != -1:
                json_start = stderr_text.rfind("{", 0, marker_pos)
                json_end = stderr_text.find("}", marker_pos) + 1
                try:
                    measured = json.loads(stderr_text[json_start:json_end])
                    measured_loudnorm_filter = (
                        f",loudnorm=I=-16:TP=-1.5:LRA=11"
                        f":measured_I={measured['input_i']}"
                        f":measured_TP={measured['input_tp']}"
                        f":measured_LRA={measured['input_lra']}"
                        f":measured_thresh={measured['input_thresh']}"
                        f":offset={measured['target_offset']}"
                        f":linear=true"
                    )
                except json.JSONDecodeError:
                    print("Failed to parse loudnorm JSON, skipping normalization pass 2")

        # ── Stage 2: Processing (Combined Video + Audio) ──
        jobs[job_id] = {"status": "processing", "progress": 40, "stage": "Processing video and audio..."}
        cropped = str(proc_dir / "cropped.mp4")
        
        # Build Filter Complex
        # Video: [0:v]...[v]
        # Audio: [1:a]...[a] (or [0:a] if same input)
        
        cmd = [FFMPEG, "-y"]

        # Input 0: Video Source
        if use_static_image and static_image_file:
             cmd.extend(["-loop", "1", "-i", static_image_file])
             # Video filter for static image
             vf = f"crop={crop_width}:{crop_height}:{crop_x}:{crop_y}:exact=1,scale={output_width}:{output_height},setsar=1"
        else:
            if trim_start > 0:
                cmd.extend(["-ss", str(trim_start)])
            cmd.extend(["-i", input_file])
            # Video filter for video clip
            vf = f"crop={crop_width}:{crop_height}:{crop_x}:{crop_y}:exact=1,scale={output_width}:{output_height},setsar=1"

        # Input 1 (or 0): Audio Source
        # If separate audio, it's a second input. If separate_audio is false but we used static image, we need audio from input_file.
        # If normal video, audio is from Input 0.

        separate_audio_input_idx = None

        if use_separate_audio:
            # Separate audio file is Input 1
            if audio_seek_start > 0:
                cmd.extend(["-ss", str(audio_seek_start)])
            cmd.extend(["-i", separate_audio_file])
            separate_audio_input_idx = 1
        elif use_static_image:
            # Audio comes from original video file, which must be Input 1 because Input 0 is image
            if trim_start > 0:
                cmd.extend(["-ss", str(trim_start)])
            cmd.extend(["-i", input_file])
            separate_audio_input_idx = 1
        else:
            # Audio comes from Input 0 (video file)
            separate_audio_input_idx = 0

        # Common Output Duration
        if duration > 0:
             cmd.extend(["-t", str(duration)])

        # Audio Filter Chain
        # Always resample to PCM s16le compatible and stereo
        af = f"aresample={sample_rate},aformat=channel_layouts=stereo"
        if measured_loudnorm_filter:
            af += measured_loudnorm_filter

        fade_mode = str(audio_fade_mode or "none").strip().lower()
        if fade_mode not in {"none", "start", "end", "both"}:
            fade_mode = "none"
        if fade_mode != "none":
            fade_seconds = min(1.0, max(0.05, float(audio_fade_duration or 0.35)))
            if clip_duration_for_fade > 0:
                fade_seconds = min(fade_seconds, max(0.05, clip_duration_for_fade / 4.0))
            if fade_mode in {"start", "both"}:
                af += f",afade=t=in:st=0:d={fade_seconds:.3f}"
            if fade_mode in {"end", "both"} and clip_duration_for_fade > 0.05:
                fade_out_start = max(0.0, clip_duration_for_fade - fade_seconds)
                af += f",afade=t=out:st={fade_out_start:.3f}:d={fade_seconds:.3f}"

        # Filter Complex Construction
        filter_complex = f"[0:v]{vf}[v];[{separate_audio_input_idx}:a]{af}[a]"

        output_video_fps = 30.0
        cmd.extend([
            "-filter_complex", filter_complex,
            "-map", "[v]", "-map", "[a]",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", str(int(output_video_fps)), # Ensure video format matches previous intermediate
            "-c:a", "pcm_s16le", # PCM audio
            cropped
        ])

        # Run combined command
        run_ffmpeg(cmd, env=env)

        # ── Stage 4: Extract last frame + create still buffer ──
        if buffer_duration > 0:
            jobs[job_id] = {"status": "processing", "progress": 65, "stage": "Creating end buffer..."}
            if use_static_image and static_image_file:
                # Static-image mode does not need last-frame extraction; re-use the selected image.
                still_image_input = static_image_file
            else:
                last_frame_path = proc_dir / "last_frame.png"
                last_frame = str(last_frame_path)
                last_frame_path.unlink(missing_ok=True)
                frame_step = 1.0 / output_video_fps
                last_frame_ts = 0.0
                extracted_last_frame = False
                try:
                    cropped_probe = run_ffmpeg(
                        [FFPROBE, "-v", "quiet", "-print_format", "json", "-show_format", cropped],
                        env=env, timeout=15
                    )
                    cropped_info = json.loads(cropped_probe.stdout or "{}")
                    cropped_duration = float(cropped_info.get("format", {}).get("duration", 0) or 0)
                    if cropped_duration > 0:
                        last_frame_ts = max(0.0, cropped_duration - frame_step)

                    # Use accurate seek (after input) to capture the actual final frame.
                    run_ffmpeg([
                        FFMPEG, "-i", cropped,
                        "-ss", f"{last_frame_ts:.6f}",
                        "-frames:v", "1", "-y", last_frame,
                    ], env=env, timeout=30)
                    extracted_last_frame = last_frame_path.exists() and last_frame_path.stat().st_size > 0
                except Exception:
                    extracted_last_frame = False

                if not extracted_last_frame:
                    # Fallback paths for odd sources/timelines; some inputs return 0 but write no frame.
                    for seek_tail in ["-0.01", "-0.05", "-0.1", "-1"]:
                        try:
                            last_frame_path.unlink(missing_ok=True)
                            run_ffmpeg([
                                FFMPEG, "-sseof", seek_tail, "-i", cropped,
                                "-frames:v", "1", "-y", last_frame,
                            ], env=env, timeout=30)
                            if last_frame_path.exists() and last_frame_path.stat().st_size > 0:
                                extracted_last_frame = True
                                break
                        except Exception:
                            continue

                if not extracted_last_frame:
                    raise RuntimeError("Failed to extract final frame for end buffer.")
                still_image_input = last_frame

            # Create still buffer with silence (will encode audio once at final stage)
            still_buffer = str(proc_dir / "still_buffer.mp4")
            run_ffmpeg([
                FFMPEG, "-loop", "1", "-framerate", str(int(output_video_fps)), "-i", still_image_input,
                "-f", "lavfi", "-i", f"anullsrc=channel_layout=stereo:sample_rate={sample_rate}",
                "-c:v", "libx264", "-t", str(buffer_duration), "-pix_fmt", "yuv420p",
                "-vf", f"fps={int(output_video_fps)},scale={output_width}:{output_height}:flags=lanczos,setsar=1,format=yuv420p",
                "-r", str(int(output_video_fps)), "-c:a", "pcm_s16le", "-ar", str(sample_rate), "-ac", "2",
                "-shortest", "-y", still_buffer,
            ], env=env, timeout=30)

        # ── Stage 5: Concatenate or use cropped directly ──
        if buffer_duration > 0:
            jobs[job_id] = {"status": "processing", "progress": 75, "stage": "Joining clips..."}
            concatenated = str(proc_dir / "concatenated.mp4")
            vnorm = f"fps={int(output_video_fps)},scale={output_width}:{output_height}:flags=lanczos,setsar=1,format=yuv420p"
            anorm = f"aformat=sample_fmts=s16:sample_rates={sample_rate}:channel_layouts=stereo"
            concat_filter = (
                f"[0:v]{vnorm}[v0];"
                f"[1:v]{vnorm}[v1];"
                f"[0:a]{anorm}[a0];"
                f"[1:a]{anorm}[a1];"
                f"[v0][a0][v1][a1]concat=n=2:v=1:a=1[outv][outa]"
            )
            run_ffmpeg([
                FFMPEG,
                "-i", cropped,
                "-i", still_buffer,
                "-filter_complex", concat_filter,
                "-map", "[outv]", "-map", "[outa]",
                "-c:v", "libx264", "-crf", "18", "-preset", "fast",
                "-c:a", "pcm_s16le",  # Keep audio lossless for now
                "-y", concatenated,
            ], env=env, timeout=60)
            final_input = concatenated
        else:
            final_input = cropped

        # ── Stage 6: Final compression - ONLY AAC encode happens here ──
        jobs[job_id] = {"status": "processing", "progress": 90, "stage": "Final encoding..."}
        output_file = str(OUTPUT_DIR / f"alert_{job_id}.mp4")
        run_ffmpeg([
            FFMPEG, "-i", final_input,
            "-c:v", "libx264", "-crf", "23", "-preset", "medium",
            "-c:a", "aac", "-b:a", "192k",
            "-movflags", "+faststart",
            "-y", output_file,
        ], env=env)

        jobs[job_id] = {
            "status": "complete",
            "progress": 100,
            "stage": "Done!",
            "filename": f"alert_{job_id}.mp4",
        }

    except Exception as e:
        jobs[job_id] = {"status": "error", "error": str(e)}


@app.route("/api/process", methods=["POST"])
def process_video():
    # Handle both JSON (legacy) and Multipart/Form-Data (file upload)
    
    if request.is_json:
        data = request.get_json()
    else:
        # Form data parsing
        raw_crop = request.form.get("crop")
        raw_settings = request.form.get("settings")
        data = {
            "job_id": request.form.get("job_id"),
            "crop": json.loads(raw_crop) if raw_crop else {},
            "trim_start": request.form.get("trim_start"),
            "trim_end": request.form.get("trim_end"),
            "use_separate_audio": request.form.get("use_separate_audio") == "true",
            "audio_fade_mode": request.form.get("audio_fade_mode", "none"),
            "audio_fade_duration": request.form.get("audio_fade_duration", "0.35"),
            "audio_source_type": request.form.get("audio_source_type", "url"),
            "audio_url": request.form.get("audio_url", ""),
            "audio_start": request.form.get("audio_start", ""),
            "audio_end": request.form.get("audio_end", ""),
            "use_static_image": request.form.get("use_static_image") == "true",
            "settings": json.loads(raw_settings) if raw_settings else {}
        }

    job_id = data.get("job_id", "")
    crop = data.get("crop", {})
    crop_x = int(crop.get("x", 0))
    crop_y = int(crop.get("y", 0))
    # Support both 'size' (legacy square) and 'width'/'height' (new aspect ratios)
    crop_width = int(crop.get("width", crop.get("size", 720)))
    crop_height = int(crop.get("height", crop.get("size", 720)))
    trim_start = float(data.get("trim_start", 0))
    trim_end = float(data.get("trim_end", 0))
    use_separate_audio = data.get("use_separate_audio", False)
    audio_source_type = data.get("audio_source_type", "url")
    audio_url = data.get("audio_url", "").strip()
    audio_start_raw = data.get("audio_start", "")
    audio_end_raw = data.get("audio_end", "")
    use_static_image = data.get("use_static_image", False)
    
    # Settings
    settings = data.get("settings", {})
    resolution = int(settings.get("resolution", "720"))
    buffer_duration = int(settings.get("bufferDuration", "2"))
    normalize_audio = settings.get("normalizeAudio", True)
    audio_fade_mode = str(
        data.get("audio_fade_mode", settings.get("audioFadeMode", "none"))
    ).strip().lower()
    if audio_fade_mode not in {"none", "start", "end", "both"}:
        audio_fade_mode = "none"
    raw_audio_fade_duration = data.get(
        "audio_fade_duration",
        settings.get("audioFadeDuration", "0.35")
    )
    try:
        audio_fade_duration = float(raw_audio_fade_duration)
    except (TypeError, ValueError):
        audio_fade_duration = 0.35
    # Keep UI and backend aligned to the 3 exposed durations.
    allowed_fade_durations = {0.2, 0.35, 0.5}
    if audio_fade_duration not in allowed_fade_durations:
        audio_fade_duration = 0.35

    if not job_id or not is_safe_job_id(job_id):
        return jsonify({"error": "Invalid job_id"}), 400

    audio_start_sec = None
    audio_end_sec = None
    if use_separate_audio:
        if audio_source_type not in {"url", "file"}:
            return jsonify({"error": "Invalid audio source type"}), 400

        job_dir = DOWNLOADS_DIR / job_id
        job_dir.mkdir(parents=True, exist_ok=True)

        if audio_start_raw and audio_end_raw:
            try:
                audio_start_sec = parse_timestamp_to_seconds(str(audio_start_raw))
                audio_end_sec = parse_timestamp_to_seconds(str(audio_end_raw))
            except (TypeError, ValueError):
                return jsonify({"error": "Invalid audio start/end format"}), 400
            if audio_end_sec <= audio_start_sec:
                return jsonify({"error": "Audio end time must be after audio start time"}), 400

        if audio_source_type == "file":
            if "separate_audio_file" not in request.files:
                return jsonify({"error": "Please upload a local audio file"}), 400

            audio_file = request.files["separate_audio_file"]
            if not audio_file.filename:
                return jsonify({"error": "Please upload a local audio file"}), 400

            allowed_audio_exts = {
                "mp3", "wav", "m4a", "aac", "flac", "ogg", "opus",
                "mp4", "mov", "avi", "mkv", "webm", "wmv", "m4v"
            }
            ext = audio_file.filename.rsplit(".", 1)[1].lower() if "." in audio_file.filename else ""
            if ext not in allowed_audio_exts:
                return jsonify({"error": "Unsupported audio file format"}), 400

            # Remove any previous separate audio for this job and save the new file.
            for old_audio in job_dir.glob("audio.*"):
                old_audio.unlink(missing_ok=True)
            audio_file.save(str(job_dir / f"audio.{ext}"))
        else:
            # Source is URL - remove any stale uploaded audio from previous attempts.
            for old_audio in job_dir.glob("audio.*"):
                old_audio.unlink(missing_ok=True)
            audio_url = clean_video_url(audio_url)
            if not audio_url:
                return jsonify({"error": "Please provide a separate audio URL"}), 400
        
    # Handle image upload if present
    if use_static_image and 'static_image' in request.files:
        file = request.files['static_image']
        if file.filename:
            job_dir = DOWNLOADS_DIR / job_id
            job_dir.mkdir(parents=True, exist_ok=True)
            # Save properly
            ext = file.filename.rsplit('.', 1)[1].lower() if '.' in file.filename else 'jpg'
            save_path = job_dir / f"image.{ext}"
            file.save(str(save_path))

    jobs[job_id] = {"status": "processing", "progress": 0, "stage": "Starting..."}
    print(f"Starting processing for job {job_id}...")


    thread = threading.Thread(
        target=run_processing_pipeline,
        args=(job_id, crop_x, crop_y, crop_width, crop_height, trim_start, trim_end, use_separate_audio, use_static_image),
        kwargs={
            "resolution": resolution,
            "buffer_duration": buffer_duration,
            "normalize_audio": normalize_audio,
            "audio_fade_mode": audio_fade_mode,
            "audio_fade_duration": audio_fade_duration,
            "separate_audio_source_type": audio_source_type,
            "separate_audio_url": audio_url,
            "separate_audio_start": audio_start_sec,
            "separate_audio_end": audio_end_sec,
        },
        daemon=True,
    )
    thread.start()

    return jsonify({"status": "processing", "job_id": job_id})


# ── Status polling ──────────────────────────────────────────────

@app.route("/api/status/<job_id>")
def job_status(job_id):
    if not is_safe_job_id(job_id):
        return jsonify({"error": "Invalid job_id"}), 400
    if job_id not in jobs:
        return jsonify({"status": "unknown"}), 404
    return jsonify(jobs[job_id])


# ── Download result ─────────────────────────────────────────────

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


@app.route("/api/load-separate-audio/<job_id>", methods=["POST"])
def load_separate_audio(job_id):
    job_dir = DOWNLOADS_DIR / job_id
    if not job_dir.exists():
        return jsonify({"error": "Load the main video first."}), 400

    source_type = ""
    if request.is_json:
        payload = request.get_json(silent=True) or {}
        source_type = str(payload.get("source_type", "url")).strip().lower()
    else:
        source_type = str(request.form.get("source_type", "file")).strip().lower()

    if source_type not in {"url", "file"}:
        return jsonify({"error": "Invalid audio source type."}), 400

    # Remove old preview audio before loading a new one.
    for old_audio in job_dir.glob("audio.*"):
        old_audio.unlink(missing_ok=True)

    try:
        if source_type == "url":
            payload = request.get_json(silent=True) or {}
            audio_url = clean_video_url(str(payload.get("audio_url", "")).strip())
            if not audio_url:
                return jsonify({"error": "Please provide an audio URL."}), 400
            audio_path = download_separate_audio(job_dir, audio_url, None, None)
        else:
            if "audio_file" not in request.files:
                return jsonify({"error": "Please choose a local audio file."}), 400
            audio_file = request.files["audio_file"]
            if not audio_file.filename:
                return jsonify({"error": "Please choose a local audio file."}), 400

            allowed_audio_exts = {
                "wav", "mp3", "m4a", "aac", "flac", "ogg", "opus", "webm", "mp4", "mov", "mkv"
            }
            ext = audio_file.filename.rsplit(".", 1)[1].lower() if "." in audio_file.filename else ""
            if ext not in allowed_audio_exts:
                return jsonify({"error": "Unsupported audio file format."}), 400

            save_path = job_dir / f"audio.{ext}"
            audio_file.save(str(save_path))
            audio_path = str(save_path)
    except Exception as e:
        return jsonify({"error": str(e)}), 400

    duration = probe_media_duration(audio_path)
    return jsonify({
        "status": "loaded",
        "filename": Path(audio_path).name,
        "duration": round(duration, 2),
    })


# ── Serve source clip (for preview) ─────────────────────────────

@app.route("/api/serve-clip/<job_id>")
def serve_clip(job_id):
    if not is_safe_job_id(job_id):
        return jsonify({"error": "Invalid job_id"}), 400
    job_dir = DOWNLOADS_DIR / job_id
    files = list(job_dir.glob("clip.*"))
    if not files:
        return jsonify({"error": "File not found"}), 404
    clip_path = files[0]
    mimetype, _ = mimetypes.guess_type(str(clip_path))
    # Ensure range requests work (Flask send_file supports this by default)
    return send_file(str(clip_path), mimetype=mimetype or "application/octet-stream")


@app.route("/api/serve-audio/<job_id>")
def serve_audio(job_id):
    job_dir = DOWNLOADS_DIR / job_id
    files = list(job_dir.glob("audio.*"))
    if not files:
        return jsonify({"error": "Audio file not found"}), 404
    audio_path = files[0]
    mimetype, _ = mimetypes.guess_type(str(audio_path))
    return send_file(str(audio_path), mimetype=mimetype or "application/octet-stream")


# ── Cleanup ─────────────────────────────────────────────────────

@app.route("/api/cleanup/<job_id>", methods=["POST"])
def cleanup(job_id):
    if not is_safe_job_id(job_id):
        return jsonify({"error": "Invalid job_id"}), 400
    import shutil
    for d in [DOWNLOADS_DIR / job_id, PROCESSING_DIR / job_id]:
        if d.exists():
            shutil.rmtree(d, ignore_errors=True)
    return jsonify({"status": "cleaned"})


# ── Shutdown ────────────────────────────────────────────────────

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


# ── Run ─────────────────────────────────────────────────────────

if __name__ == "__main__":
    if platform.system() == "Windows" and not getattr(sys, 'frozen', False):
        os.system("title deutschmark's Alert! Alert!")
    print("Checking required dependencies...")
    ensure_runtime_dependencies(auto_install=AUTO_INSTALL_SUPPORTED)
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
