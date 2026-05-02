"""Alert Creator pipeline — download, process, and export stream alert clips."""

import json
import uuid
import mimetypes
import platform
import subprocess
import threading
from pathlib import Path

from flask import request, jsonify, send_file


def register_alert_routes(app):
    """Register all alert-creator routes on the Flask app."""

    # Import shared state and utilities from the main app module.
    from app import (
        jobs,
        DOWNLOADS_DIR,
        PROCESSING_DIR,
        FFMPEG,
        FFPROBE,
        YTDLP,
        FFMPEG_DIR,
        DENO,
        NULL_DEVICE,
        get_env,
        run_subprocess,
        run_ffmpeg,
        is_safe_job_id,
        clean_video_url,
        is_youtube_url,
        has_deno_runtime,
        summarize_ytdlp_error,
        looks_like_youtube_challenge_issue,
        looks_like_age_restricted_issue,
        parse_timestamp_to_seconds,
        probe_media_duration,
        download_separate_audio,
        get_output_dir,
    )

    # ── Download clip ───────────────────────────────────────────────

    def run_download_pipeline(job_id, url, start_sec, end_sec=None, use_separate_audio=False,
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
                if use_sections and end_sec is not None and end_sec > start_sec:
                    cmd.extend(["--download-sections", f"*{start_sec}-{end_sec}"])
                if FFMPEG_DIR:
                    cmd.extend(["--ffmpeg-location", FFMPEG_DIR])
                cmd.append(url)
                return cmd

            # For full downloads (start at 0), avoid section-based ffmpeg URL reads.
            can_section_download = end_sec is not None and end_sec > start_sec
            section_modes = [True, False] if start_sec > 0 and can_section_download else [False]
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


    # ── Processing pipeline ────────────────────────────────────────

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
        export_preset="stream_alert",
    ):
        """Run the full ffmpeg pipeline in a background thread.

        Audio Quality Strategy:
        - Extract audio to lossless PCM WAV for all processing stages
        - Apply loudnorm filter on PCM (no generation loss) if normalize_audio is True
        - Only encode to AAC once at the very end at 192kbps
        - If using separate audio source, replace video audio before processing
        - Optional visual override can use a different image or video file

        Args:
        - crop_x, crop_y: top-left corner of crop region in source pixels
        - crop_width, crop_height: dimensions of crop region in source pixels
        - trim_start: start time in seconds (0-based)
        - trim_end: end time in seconds (0-based)

        Settings:
        - resolution: base output size (width for wide, height for tall)
        - buffer_duration: seconds of still frame buffer at end
        - normalize_audio: whether to apply loudness normalization
        - export_preset: named quality preset for final encode
        """
        _EXPORT_PRESETS = {
            "stream_alert": ("23", "medium"),
            "tiktok_reel":  ("23", "fast"),
            "discord_clip": ("28", "veryfast"),
            "quality":      ("18", "slow"),
        }
        enc_crf, enc_speed = _EXPORT_PRESETS.get(export_preset, ("23", "medium"))

        # Calculate output dimensions based on crop aspect ratio
        crop_aspect = 1.0
        if crop_width and crop_height:
            crop_aspect = crop_width / crop_height

        if crop_aspect >= 1:
            output_width = int(resolution)
            output_height = int(resolution / crop_aspect)
        else:
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

            # Optional visual override (image or video)
            visual_override_file = None
            visual_override_is_image = False
            if use_static_image:
                visual_override_files = list(job_dir.glob("visual.*"))
                if not visual_override_files:
                    visual_override_files = list(job_dir.glob("image.*"))
                if visual_override_files:
                    visual_override_file = str(visual_override_files[0])
                    guessed_mime, _ = mimetypes.guess_type(visual_override_file)
                    visual_override_is_image = bool(guessed_mime and guessed_mime.startswith("image/"))
                else:
                    raise RuntimeError("Different visual source is enabled, but no image/video file was provided.")

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

            if use_static_image and visual_override_file:
                visual_probe = run_ffmpeg(
                    [FFPROBE, "-v", "quiet", "-print_format", "json", "-show_streams", visual_override_file],
                    env=env, timeout=15
                )
                visual_info = json.loads(visual_probe.stdout)
                visual_stream = next(
                    (s for s in visual_info.get("streams", []) if s.get("codec_type") == "video"),
                    None
                )
                if visual_stream:
                    crop_src_width = int(visual_stream.get("width", crop_src_width))
                    crop_src_height = int(visual_stream.get("height", crop_src_height))

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

                analysis_args = [FFMPEG]
                if use_separate_audio and audio_seek_start > 0:
                    analysis_args.extend(["-ss", str(audio_seek_start)])
                elif trim_start > 0:
                    analysis_args.extend(["-ss", str(trim_start)])

                analysis_args.extend(["-i", audio_source])

                if duration > 0:
                     analysis_args.extend(["-t", str(duration)])

                analysis_args.extend([
                    "-af", f"aresample={sample_rate},loudnorm=I=-16:TP=-1.5:LRA=11:print_format=json",
                    "-f", "null", NULL_DEVICE
                ])

                measure_result = subprocess.run(
                    analysis_args, capture_output=True, text=True, timeout=120, env=env
                )

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

            cmd = [FFMPEG, "-y"]

            # Input 0: Video Source
            if use_static_image and visual_override_file:
                if visual_override_is_image:
                    cmd.extend(["-loop", "1", "-i", visual_override_file])
                else:
                    if trim_start > 0:
                        cmd.extend(["-ss", str(trim_start)])
                    cmd.extend(["-i", visual_override_file])
                vf = f"crop={crop_width}:{crop_height}:{crop_x}:{crop_y}:exact=1,scale={output_width}:{output_height},setsar=1"
            else:
                if trim_start > 0:
                    cmd.extend(["-ss", str(trim_start)])
                cmd.extend(["-i", input_file])
                vf = f"crop={crop_width}:{crop_height}:{crop_x}:{crop_y}:exact=1,scale={output_width}:{output_height},setsar=1"

            # Input 1 (or 0): Audio Source
            separate_audio_input_idx = None

            if use_separate_audio:
                if audio_seek_start > 0:
                    cmd.extend(["-ss", str(audio_seek_start)])
                cmd.extend(["-i", separate_audio_file])
                separate_audio_input_idx = 1
            elif use_static_image:
                if trim_start > 0:
                    cmd.extend(["-ss", str(trim_start)])
                cmd.extend(["-i", input_file])
                separate_audio_input_idx = 1
            else:
                separate_audio_input_idx = 0

            # Common Output Duration
            if duration > 0:
                 cmd.extend(["-t", str(duration)])

            # Audio Filter Chain
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
                "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", str(int(output_video_fps)),
                "-c:a", "pcm_s16le",
                cropped
            ])

            run_ffmpeg(cmd, env=env)

            # ── Stage 4: Extract last frame + create still buffer ──
            if buffer_duration > 0:
                jobs[job_id] = {"status": "processing", "progress": 65, "stage": "Creating end buffer..."}
                if use_static_image and visual_override_file and visual_override_is_image:
                    still_image_input = visual_override_file
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

                        run_ffmpeg([
                            FFMPEG, "-i", cropped,
                            "-ss", f"{last_frame_ts:.6f}",
                            "-frames:v", "1", "-y", last_frame,
                        ], env=env, timeout=30)
                        extracted_last_frame = last_frame_path.exists() and last_frame_path.stat().st_size > 0
                    except Exception:
                        extracted_last_frame = False

                    if not extracted_last_frame:
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

                # Create still buffer with silence
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
                    "-c:a", "pcm_s16le",
                    "-y", concatenated,
                ], env=env, timeout=60)
                final_input = concatenated
            else:
                final_input = cropped

            # ── Stage 6: Final compression - ONLY AAC encode happens here ──
            jobs[job_id] = {"status": "processing", "progress": 90, "stage": "Final encoding..."}
            output_file = str(get_output_dir() / f"alert_{job_id}.mp4")
            run_ffmpeg([
                FFMPEG, "-i", final_input,
                "-c:v", "libx264", "-crf", enc_crf, "-preset", enc_speed,
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


    # ── Route: Download clip ───────────────────────────────────────

    @app.route("/api/download", methods=["POST"])
    def download_clip():
        data = request.get_json(silent=True) or {}
        url = data.get("url", "").strip()
        start = str(data.get("start", "0:00.00")).strip()
        end = str(data.get("end", "")).strip()

        url = clean_video_url(url)

        audio_url = data.get("audio_url", "").strip()
        audio_url = clean_video_url(audio_url) if audio_url else ""
        audio_start = data.get("audio_start", "").strip()
        audio_end = data.get("audio_end", "").strip()
        use_separate_audio = bool(audio_url and audio_start and audio_end)

        if not url:
            return jsonify({"error": "Missing url"}), 400

        start_sec = parse_timestamp_to_seconds(start) if start else 0.0
        end_sec = None
        if end:
            end_sec = parse_timestamp_to_seconds(end)
            if end_sec <= start_sec:
                return jsonify({"error": "End time must be after start time"}), 400
        elif start_sec > 0:
            return jsonify({"error": "End time is required when trimming from a non-zero start"}), 400

        audio_start_sec = 0
        audio_end_sec = 0
        if use_separate_audio:
            audio_start_sec = parse_timestamp_to_seconds(audio_start)
            audio_end_sec = parse_timestamp_to_seconds(audio_end)
            if audio_end_sec <= audio_start_sec:
                return jsonify({"error": "Audio end time must be after start time"}), 400

        job_id = uuid.uuid4().hex[:8]

        jobs[job_id] = {"status": "downloading", "progress": 0, "stage": "Downloading video clip..."}
        range_label = f"{start or '0:00.00'}-{end}" if end else "full source"
        print(f"Starting download job {job_id} for {url} ({range_label})")

        thread = threading.Thread(
            target=run_download_pipeline,
            args=(job_id, url, start_sec, end_sec, use_separate_audio, audio_url, audio_start_sec, audio_end_sec),
            daemon=True
        )
        thread.start()

        return jsonify({"job_id": job_id, "status": "downloading"})


    # ── Route: Upload local video ──────────────────────────────────

    @app.route("/api/upload-video", methods=["POST"])
    def upload_video():
        """Handle local video file upload."""
        if 'video' not in request.files:
            return jsonify({"error": "No video file provided"}), 400

        file = request.files['video']
        if not file.filename:
            return jsonify({"error": "No file selected"}), 400

        allowed_extensions = {'mp4', 'mov', 'avi', 'mkv', 'webm', 'wmv', 'm4v'}
        ext = file.filename.rsplit('.', 1)[1].lower() if '.' in file.filename else ''
        if ext not in allowed_extensions:
            return jsonify({"error": f"Unsupported file format. Allowed: {', '.join(allowed_extensions)}"}), 400

        job_id = uuid.uuid4().hex[:8]
        job_dir = DOWNLOADS_DIR / job_id
        job_dir.mkdir(parents=True, exist_ok=True)

        save_path = job_dir / f"clip.{ext}"
        file.save(str(save_path))

        jobs[job_id] = {"status": "uploading", "progress": 50, "stage": "Processing upload..."}
        print(f"Upload job {job_id}: saved {file.filename}")

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


    # ── Route: Video info ──────────────────────────────────────────

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


    # ── Route: Preview frame ───────────────────────────────────────

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


    # ── Route: Process video ───────────────────────────────────────

    @app.route("/api/process", methods=["POST"])
    def process_video():
        if request.is_json:
            data = request.get_json()
        else:
            raw_crop = request.form.get("crop")
            raw_settings = request.form.get("settings")
            data = {
                "job_id": request.form.get("job_id"),
                "crop": json.loads(raw_crop) if raw_crop else {},
                "trim_start": request.form.get("trim_start"),
                "trim_end": request.form.get("trim_end"),
                "use_separate_audio": request.form.get("use_separate_audio") == "true",
                "use_alternate_visual": request.form.get("use_alternate_visual") == "true",
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
        crop_ratio = str(crop.get("ratio", "")).strip().lower()
        allowed_crop_ratios = {"original", "1:1", "16:9", "9:16", "4:3", "3:4", "21:9"}
        if crop_ratio and crop_ratio not in allowed_crop_ratios:
            return jsonify({"error": "Invalid crop ratio"}), 400
        crop_width = int(crop.get("width", crop.get("size", 720)))
        crop_height = int(crop.get("height", crop.get("size", 720)))
        trim_start = float(data.get("trim_start", 0))
        trim_end = float(data.get("trim_end", 0))
        visual_trim_duration = max(0.0, trim_end - trim_start)
        use_separate_audio = data.get("use_separate_audio", False)
        audio_source_type = data.get("audio_source_type", "url")
        audio_url = data.get("audio_url", "").strip()
        audio_start_raw = data.get("audio_start", "")
        audio_end_raw = data.get("audio_end", "")
        use_static_image = bool(data.get("use_static_image", False) or data.get("use_alternate_visual", False))
        if use_separate_audio and use_static_image:
            return jsonify({"error": "Choose either different audio or different image/video, not both."}), 400

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
        allowed_fade_durations = {0.2, 0.35, 0.5, 1.0}
        if audio_fade_duration not in allowed_fade_durations:
            audio_fade_duration = 0.35

        export_preset = str(settings.get("exportPreset", "stream_alert")).strip().lower()
        if export_preset not in {"stream_alert", "tiktok_reel", "discord_clip", "quality"}:
            export_preset = "stream_alert"

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
                if visual_trim_duration > 0:
                    audio_end_sec = audio_start_sec + visual_trim_duration

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

                for old_audio in job_dir.glob("audio.*"):
                    old_audio.unlink(missing_ok=True)
                audio_file.save(str(job_dir / f"audio.{ext}"))
            else:
                for old_audio in job_dir.glob("audio.*"):
                    old_audio.unlink(missing_ok=True)
                audio_url = clean_video_url(audio_url)
                if not audio_url:
                    return jsonify({"error": "Please provide a separate audio URL"}), 400

        # Handle alternate visual upload (image/video) if present
        alternate_visual_file = request.files.get("alternate_visual") or request.files.get("static_image")
        if use_static_image and alternate_visual_file is not None:
            file = alternate_visual_file
            if file.filename:
                job_dir = DOWNLOADS_DIR / job_id
                job_dir.mkdir(parents=True, exist_ok=True)
                ext = file.filename.rsplit('.', 1)[1].lower() if '.' in file.filename else ""
                allowed_visual_exts = {
                    "jpg", "jpeg", "png", "webp", "bmp", "gif",
                    "mp4", "mov", "avi", "mkv", "webm", "wmv", "m4v",
                }
                if ext not in allowed_visual_exts:
                    return jsonify({"error": "Unsupported image/video format for alternate visual source"}), 400

                for old_visual in list(job_dir.glob("visual.*")) + list(job_dir.glob("image.*")):
                    old_visual.unlink(missing_ok=True)

                save_path = job_dir / f"visual.{ext}"
                file.save(str(save_path))

        if use_static_image:
            job_dir = DOWNLOADS_DIR / job_id
            has_visual_override = any(job_dir.glob("visual.*")) or any(job_dir.glob("image.*"))
            if not has_visual_override:
                return jsonify({"error": "Please provide a different image or video file"}), 400

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
                "export_preset": export_preset,
            },
            daemon=True,
        )
        thread.start()

        return jsonify({"status": "processing", "job_id": job_id})


    # ── Route: Load separate audio ─────────────────────────────────

    @app.route("/api/load-separate-audio/<job_id>", methods=["POST"])
    def load_separate_audio(job_id):
        if not is_safe_job_id(job_id):
            return jsonify({"error": "Invalid job_id"}), 400
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


    # ── Route: Serve source clip (for preview) ─────────────────────

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
        return send_file(str(clip_path), mimetype=mimetype or "application/octet-stream")


    @app.route("/api/serve-audio/<job_id>")
    def serve_audio(job_id):
        if not is_safe_job_id(job_id):
            return jsonify({"error": "Invalid job_id"}), 400
        job_dir = DOWNLOADS_DIR / job_id
        files = list(job_dir.glob("audio.*"))
        if not files:
            return jsonify({"error": "Audio file not found"}), 404
        audio_path = files[0]
        mimetype, _ = mimetypes.guess_type(str(audio_path))
        return send_file(str(audio_path), mimetype=mimetype or "application/octet-stream")
