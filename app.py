import os
import re
import json
import uuid
import glob
import platform
import subprocess
import threading
from functools import lru_cache
from pathlib import Path
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

# Ensure directories exist
for d in [DOWNLOADS_DIR, PROCESSING_DIR, OUTPUT_DIR]:
    d.mkdir(parents=True, exist_ok=True)

NULL_DEVICE = "NUL" if platform.system() == "Windows" else "/dev/null"

# In-memory job status tracking
jobs = {}


# ── Tool discovery ──────────────────────────────────────────────
# Find ffmpeg, ffprobe, yt-dlp even if not on current PATH

def find_tool(name):
    """Find a CLI tool, checking PATH first then common Windows install locations."""
    # Try PATH first
    import shutil
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


FFMPEG = find_tool("ffmpeg")
FFPROBE = find_tool("ffprobe")
YTDLP = find_tool("yt-dlp")
DENO = find_tool("deno")

# Directory containing ffmpeg, needed by yt-dlp's --ffmpeg-location
FFMPEG_DIR = str(Path(FFMPEG).parent) if FFMPEG != "ffmpeg" else None

# Build an env dict that includes ffmpeg and deno dirs on PATH
@lru_cache(maxsize=1)
def get_env():
    """Return an env dict with ffmpeg and deno directories added to PATH."""
    env = os.environ.copy()
    extra_dirs = []
    if FFMPEG_DIR:
        extra_dirs.append(FFMPEG_DIR)
    deno_dir = str(Path(DENO).parent) if DENO != "deno" else None
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


# ── Dependency check ────────────────────────────────────────────


# Global cache for dependency results
DEPS_CACHE = {}

@app.route("/api/check-deps")
def check_deps():
    # If the cache is already populated (should be from startup), return it
    if DEPS_CACHE:
        return jsonify(DEPS_CACHE)
        
    # Fallback if not populated
    return jsonify(run_deps_check())

def run_deps_check():
    """Run dependency checks and return the results dict."""
    print("Checking system dependencies...")
    results = {}
    tools = [
        ("ffmpeg", FFMPEG, ["-version"]),
        ("ffprobe", FFPROBE, ["-version"]),
        ("yt-dlp", YTDLP, ["--version"]),
    ]
    for name, path, args in tools:
        try:
            r = run_subprocess([path] + args, timeout=10)
            output = (r.stdout + r.stderr).strip()
            has_output = bool(output)
            first_line = output.split("\n")[0].strip() if has_output else None
            results[name] = {
                "installed": has_output,
                "version": first_line,
            }
            if has_output:
                print(f"  [OK] {name} found: {first_line}")
            else:
                print(f"  [MISSING] {name} not found.")
        except FileNotFoundError:
            results[name] = {"installed": False, "version": None}
            print(f"  [MISSING] {name} not found (FileNotFound).")
        except subprocess.TimeoutExpired:
            results[name] = {"installed": False, "version": None}
            print(f"  [TIMEOUT] {name} timed out.")
    
    # Update global cache
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
    r = run_subprocess([YTDLP, "--dump-json", "--no-download", url], timeout=30)

    if r.returncode != 0:
        # Raise exception with the error message
        raise RuntimeError(r.stderr.strip() or "Invalid URL")

    return json.loads(r.stdout)


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


def run_download_pipeline(job_id, url, start_sec, end_sec, use_separate_audio=False,
                         audio_url="", audio_start_sec=0, audio_end_sec=0):
    """Run the download pipeline in a background thread."""
    try:
        job_dir = DOWNLOADS_DIR / job_id
        job_dir.mkdir(parents=True, exist_ok=True)

        output_template = str(job_dir / "clip.%(ext)s")
        section = f"*{start_sec}-{end_sec}"

        # Build yt-dlp command with --ffmpeg-location so it can find ffmpeg
        ytdlp_cmd = [
            YTDLP,
            "-f", "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
            "--download-sections", section,
            "--merge-output-format", "mp4",
            "-o", output_template,
        ]
        if FFMPEG_DIR:
            ytdlp_cmd.extend(["--ffmpeg-location", FFMPEG_DIR])
        ytdlp_cmd.append(url)

        r = run_subprocess(ytdlp_cmd, timeout=300)
        
        if r.returncode != 0:
            jobs[job_id] = {"status": "error", "error": r.stderr.strip() or "Download failed"}
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
            audio_output = str(job_dir / "audio.%(ext)s")
            audio_section = f"*{audio_start_sec}-{audio_end_sec}"
            
            audio_cmd = [
                YTDLP,
                "-f", "bestaudio[ext=m4a]/bestaudio",
                "--download-sections", audio_section,
                "-x",  # Extract audio
                "--audio-format", "wav",  # Use WAV to avoid lossy re-encoding
                "-o", audio_output,
            ]
            if FFMPEG_DIR:
                audio_cmd.extend(["--ffmpeg-location", FFMPEG_DIR])
            audio_cmd.append(audio_url)
            
            r = run_subprocess(audio_cmd, timeout=300)
            
            if r.returncode != 0:
                jobs[job_id] = {"status": "error", "error": f"Audio download failed: {r.stderr.strip()}"}
                return
            
            # Find the audio file
            audio_files = list(job_dir.glob("audio.*"))
            if not audio_files:
                jobs[job_id] = {"status": "error", "error": "No audio file downloaded"}
                return
            
            jobs[job_id] = {
                "status": "downloaded", 
                "filename": filename,
                "audio_filename": audio_files[0].name,
                "use_separate_audio": True
            }
            return
        
        jobs[job_id] = {"status": "downloaded", "filename": filename, "use_separate_audio": False}
        print(f"Download job {job_id} complete: {filename}")

    except subprocess.TimeoutExpired:
        jobs[job_id] = {"status": "error", "error": "Download timed out"}
    except Exception as e:
        jobs[job_id] = {"status": "error", "error": str(e)}


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


def run_processing_pipeline(job_id, crop_x, crop_y, crop_width, crop_height, trim_start=0, trim_end=0, 
                            use_separate_audio=False, use_static_image=False, resolution=720, buffer_duration=2, normalize_audio=True):
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
        audio_files = list(job_dir.glob("audio.*"))
        separate_audio_file = str(audio_files[0]) if audio_files and use_separate_audio else None

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
        fps_str = video_stream.get("r_frame_rate", "30/1")
        if "/" in fps_str:
            num, den = fps_str.split("/")
            fps = float(num) / float(den) if float(den) != 0 else 30.0
        else:
            fps = float(fps_str)

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
        crop_width = min(crop_width, src_width)
        crop_height = min(crop_height, src_height)
        crop_x = max(0, min(crop_x, src_width - crop_width))
        crop_y = max(0, min(crop_y, src_height - crop_height))

        duration = trim_end - trim_start
        audio_source = separate_audio_file if separate_audio_file else input_file
        
        # ── Stage 1: Audio Analysis (if needed) ──
        measured_loudnorm_filter = ""
        
        if normalize_audio:
            jobs[job_id] = {"status": "processing", "progress": 15, "stage": "Analyzing audio levels..."}

            # Analyze source audio directly with trim applied
            # Note: We duplicate the input args here for the analysis pass
            analysis_args = [FFMPEG]
            if trim_start > 0:
                analysis_args.extend(["-ss", str(trim_start)])

            analysis_args.extend(["-i", audio_source])

            if trim_end > 0 and trim_end > trim_start:
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
             vf = f"scale={output_width}:{output_height}:force_original_aspect_ratio=decrease,pad={output_width}:{output_height}:(ow-iw)/2:(oh-ih)/2,setsar=1"
        else:
            if trim_start > 0:
                cmd.extend(["-ss", str(trim_start)])
            cmd.extend(["-i", input_file])
            # Video filter for video clip
            vf = f"crop={crop_width}:{crop_height}:{crop_x}:{crop_y},scale={output_width}:{output_height},setsar=1"

        # Input 1 (or 0): Audio Source
        # If separate audio, it's a second input. If separate_audio is false but we used static image, we need audio from input_file.
        # If normal video, audio is from Input 0.

        separate_audio_input_idx = None

        if use_separate_audio:
            # Separate audio file is Input 1
            if trim_start > 0:
                cmd.extend(["-ss", str(trim_start)])
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
        if trim_end > 0 and trim_end > trim_start:
             cmd.extend(["-t", str(duration)])

        # Audio Filter Chain
        # Always resample to PCM s16le compatible and stereo
        af = f"aresample={sample_rate},aformat=channel_layouts=stereo"
        if measured_loudnorm_filter:
            af += measured_loudnorm_filter

        # Filter Complex Construction
        filter_complex = f"[0:v]{vf}[v];[{separate_audio_input_idx}:a]{af}[a]"

        cmd.extend([
            "-filter_complex", filter_complex,
            "-map", "[v]", "-map", "[a]",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", "30", # Ensure video format matches previous intermediate
            "-c:a", "pcm_s16le", # PCM audio
            cropped
        ])

        # Run combined command
        run_ffmpeg(cmd, env=env)

        # ── Stage 4: Extract last frame + create still buffer ──
        if buffer_duration > 0:
            jobs[job_id] = {"status": "processing", "progress": 65, "stage": "Creating end buffer..."}
            last_frame = str(proc_dir / "last_frame.jpg")
            run_ffmpeg([
                FFMPEG, "-sseof", "-0.1", "-i", cropped,
                "-frames:v", "1", "-q:v", "2", "-y", last_frame,
            ], env=env, timeout=30)

            # Create still buffer with silence (will encode audio once at final stage)
            still_buffer = str(proc_dir / "still_buffer.mp4")
            run_ffmpeg([
                FFMPEG, "-loop", "1", "-i", last_frame,
                "-f", "lavfi", "-i", f"anullsrc=channel_layout=stereo:sample_rate={sample_rate}",
                "-c:v", "libx264", "-t", str(buffer_duration), "-pix_fmt", "yuv420p",
                "-vf", f"scale={output_width}:{output_height}",
                "-r", str(round(fps)), "-c:a", "pcm_s16le",
                "-shortest", "-y", still_buffer,
            ], env=env, timeout=30)

        # ── Stage 5: Concatenate or use cropped directly ──
        if buffer_duration > 0:
            jobs[job_id] = {"status": "processing", "progress": 75, "stage": "Joining clips..."}
            concatenated = str(proc_dir / "concatenated.mp4")
            run_ffmpeg([
                FFMPEG,
                "-i", cropped,
                "-i", still_buffer,
                "-filter_complex", "[0:v][0:a][1:v][1:a]concat=n=2:v=1:a=1[outv][outa]",
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
    use_static_image = data.get("use_static_image", False)
    
    # Settings
    settings = data.get("settings", {})
    resolution = int(settings.get("resolution", "720"))
    buffer_duration = int(settings.get("bufferDuration", "2"))
    normalize_audio = settings.get("normalizeAudio", True)

    if not job_id or not is_safe_job_id(job_id):
        return jsonify({"error": "Invalid job_id"}), 400
        
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
        kwargs={"resolution": resolution, "buffer_duration": buffer_duration, "normalize_audio": normalize_audio},
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
    filename = f"alert_{job_id}.mp4"
    filepath = OUTPUT_DIR / filename
    if not filepath.exists():
        return jsonify({"error": "File not found"}), 404
    return send_file(str(filepath), as_attachment=True, download_name=filename)


# ── Serve source clip (for preview) ─────────────────────────────

@app.route("/api/serve-clip/<job_id>")
def serve_clip(job_id):
    if not is_safe_job_id(job_id):
        return jsonify({"error": "Invalid job_id"}), 400
    job_dir = DOWNLOADS_DIR / job_id
    files = list(job_dir.glob("clip.*"))
    if not files:
        return jsonify({"error": "File not found"}), 404
    # Ensure range requests work (Flask send_file supports this by default)
    return send_file(str(files[0]), mimetype="video/mp4")


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
