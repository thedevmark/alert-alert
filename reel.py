"""Reel Maker pipeline — multi-clip VOD extraction, concatenation, and reel export."""

import json
import uuid
import os
import platform
import subprocess
import threading
from pathlib import Path

from flask import request, jsonify, send_file

# In-memory reel project storage
reel_projects = {}


def register_reel_routes(app):
    """Register all reel-maker routes on the Flask app."""

    from app import (
        jobs,
        DOWNLOADS_DIR,
        PROCESSING_DIR,
        OUTPUT_DIR,
        FFMPEG,
        FFPROBE,
        YTDLP,
        FFMPEG_DIR,
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
    )

    # ── Project management ─────────────────────────────────────────

    @app.route("/api/reel/create-project", methods=["POST"])
    def reel_create_project():
        project_id = uuid.uuid4().hex[:10]
        project_dir = DOWNLOADS_DIR / f"reel_{project_id}"
        project_dir.mkdir(parents=True, exist_ok=True)

        reel_projects[project_id] = {
            "status": "created",
            "source_type": "url",
            "vod_url": "",
            "vod_title": "",
            "vod_duration": 0,
            "clips": [],
            "concat_file": None,
            "captions": None,
            "speakers": {},
        }
        return jsonify({"project_id": project_id})

    @app.route("/api/reel/project/<project_id>")
    def reel_get_project(project_id):
        project = reel_projects.get(project_id)
        if not project:
            return jsonify({"error": "Project not found"}), 404
        return jsonify(project)

    # ── Set VOD source ─────────────────────────────────────────────

    @app.route("/api/reel/set-vod", methods=["POST"])
    def reel_set_vod():
        data = request.get_json()
        project_id = data.get("project_id", "")
        project = reel_projects.get(project_id)
        if not project:
            return jsonify({"error": "Project not found"}), 404

        url = clean_video_url(data.get("url", "").strip())
        title = data.get("title", "")
        duration = data.get("duration", 0)

        project["vod_url"] = url
        project["vod_title"] = title
        project["vod_duration"] = duration
        project["source_type"] = "url"
        project["concat_file"] = None
        project["captions"] = None
        project["speakers"] = {}
        for clip in project["clips"]:
            clip["status"] = "pending"
            clip["filename"] = None
        return jsonify({"status": "ok"})

    # ── Clip management ────────────────────────────────────────────

    @app.route("/api/reel/add-clip", methods=["POST"])
    def reel_add_clip():
        data = request.get_json()
        project_id = data.get("project_id", "")
        project = reel_projects.get(project_id)
        if not project:
            return jsonify({"error": "Project not found"}), 404

        start = data.get("start", "0:00")
        end = data.get("end", "0:10")

        clip_id = uuid.uuid4().hex[:6]
        clip = {
            "id": clip_id,
            "start": start,
            "end": end,
            "start_sec": parse_timestamp_to_seconds(start),
            "end_sec": parse_timestamp_to_seconds(end),
            "status": "pending",
            "filename": None,
        }
        project["clips"].append(clip)
        return jsonify({"clip": clip})

    @app.route("/api/reel/update-clip", methods=["POST"])
    def reel_update_clip():
        data = request.get_json()
        project_id = data.get("project_id", "")
        project = reel_projects.get(project_id)
        if not project:
            return jsonify({"error": "Project not found"}), 404

        clip_id = data.get("clip_id", "")
        clip = next((c for c in project["clips"] if c["id"] == clip_id), None)
        if not clip:
            return jsonify({"error": "Clip not found"}), 404

        if "start" in data:
            clip["start"] = data["start"]
            clip["start_sec"] = parse_timestamp_to_seconds(data["start"])
        if "end" in data:
            clip["end"] = data["end"]
            clip["end_sec"] = parse_timestamp_to_seconds(data["end"])
        return jsonify({"clip": clip})

    @app.route("/api/reel/remove-clip", methods=["POST"])
    def reel_remove_clip():
        data = request.get_json()
        project_id = data.get("project_id", "")
        project = reel_projects.get(project_id)
        if not project:
            return jsonify({"error": "Project not found"}), 404

        clip_id = data.get("clip_id", "")
        project["clips"] = [c for c in project["clips"] if c["id"] != clip_id]
        return jsonify({"status": "removed"})

    @app.route("/api/reel/reorder-clips", methods=["POST"])
    def reel_reorder_clips():
        data = request.get_json()
        project_id = data.get("project_id", "")
        project = reel_projects.get(project_id)
        if not project:
            return jsonify({"error": "Project not found"}), 404

        new_order = data.get("clip_ids", [])
        clip_map = {c["id"]: c for c in project["clips"]}
        reordered = [clip_map[cid] for cid in new_order if cid in clip_map]
        # Append any clips not in the new order at the end
        remaining = [c for c in project["clips"] if c["id"] not in new_order]
        project["clips"] = reordered + remaining
        return jsonify({"clips": project["clips"]})

    # ── Download and concat clips ──────────────────────────────────

    @app.route("/api/reel/download-clips", methods=["POST"])
    def reel_download_clips():
        data = request.get_json()
        project_id = data.get("project_id", "")
        project = reel_projects.get(project_id)
        if not project:
            return jsonify({"error": "Project not found"}), 404

        if not project["clips"]:
            return jsonify({"error": "No clips to download"}), 400

        url = project.get("vod_url", "")
        if not url:
            return jsonify({"error": "No VOD URL set"}), 400

        job_id = f"reel_dl_{project_id}"
        jobs[job_id] = {"status": "processing", "progress": 0, "stage": "Starting clip downloads..."}

        def run_clip_downloads():
            try:
                project_dir = DOWNLOADS_DIR / f"reel_{project_id}"
                project_dir.mkdir(parents=True, exist_ok=True)
                is_local_vod = url.startswith("local:")
                local_vod_path = Path(url[6:]) if is_local_vod else None
                if is_local_vod and (not local_vod_path or not local_vod_path.exists()):
                    jobs[job_id] = {"status": "error", "error": "Local VOD file is missing. Upload it again."}
                    return

                youtube = is_youtube_url(url)
                env = get_env()

                total_clips = len(project["clips"])

                for idx, clip in enumerate(project["clips"]):
                    clip_num = idx + 1
                    pct = int((idx / total_clips) * 80)
                    jobs[job_id] = {
                        "status": "processing",
                        "progress": pct,
                        "stage": f"Downloading clip {clip_num}/{total_clips}...",
                    }

                    clip_file = project_dir / f"clip_{clip['id']}.mp4"
                    start_sec = clip["start_sec"]
                    end_sec = clip["end_sec"]

                    if end_sec <= start_sec:
                        clip["status"] = "error"
                        continue

                    if is_local_vod:
                        try:
                            run_ffmpeg([
                                FFMPEG,
                                "-ss", str(start_sec),
                                "-i", str(local_vod_path),
                                "-t", str(end_sec - start_sec),
                                "-c:v", "libx264", "-crf", "18", "-preset", "fast",
                                "-c:a", "aac", "-b:a", "192k",
                                "-movflags", "+faststart",
                                "-y", str(clip_file),
                            ], env=env, timeout=300)
                        except Exception:
                            clip["status"] = "error"
                            continue
                    else:
                        # Use yt-dlp with --download-sections for timestamp extraction
                        output_template = str(project_dir / f"clip_{clip['id']}.%(ext)s")
                        fmt = "b[ext=mp4]/b[ext=webm]/b" if youtube else "bv*+ba/b"
                        cmd = [
                            YTDLP,
                            "--no-playlist",
                            "--force-ipv4",
                            "-f", fmt,
                            "--download-sections", f"*{start_sec}-{end_sec}",
                            "--retries", "3",
                            "-o", output_template,
                        ]
                        if youtube:
                            cmd.extend(["--extractor-args", "youtube:player_client=web"])
                        if FFMPEG_DIR:
                            cmd.extend(["--ffmpeg-location", FFMPEG_DIR])
                        cmd.append(url)

                        r = run_subprocess(cmd, timeout=300)
                        if r.returncode != 0:
                            # Try without sections (full download + ffmpeg trim)
                            full_output = str(project_dir / f"full_{clip['id']}.%(ext)s")
                            cmd_full = [
                                YTDLP,
                                "--no-playlist",
                                "--force-ipv4",
                                "-f", fmt,
                                "--retries", "3",
                                "-o", full_output,
                            ]
                            if youtube:
                                cmd_full.extend(["--extractor-args", "youtube:player_client=web"])
                            if FFMPEG_DIR:
                                cmd_full.extend(["--ffmpeg-location", FFMPEG_DIR])
                            cmd_full.append(url)

                            r2 = run_subprocess(cmd_full, timeout=360)
                            if r2.returncode != 0:
                                clip["status"] = "error"
                                continue

                            # Find downloaded file and trim with ffmpeg
                            full_files = list(project_dir.glob(f"full_{clip['id']}.*"))
                            if not full_files:
                                clip["status"] = "error"
                                continue

                            trimmed_file = str(project_dir / f"clip_{clip['id']}.mp4")
                            try:
                                run_ffmpeg([
                                    FFMPEG, "-ss", str(start_sec), "-i", str(full_files[0]),
                                    "-t", str(end_sec - start_sec),
                                    "-c:v", "libx264", "-crf", "18", "-preset", "fast",
                                    "-c:a", "aac", "-b:a", "192k",
                                    "-y", trimmed_file,
                                ], env=env, timeout=120)
                            except Exception:
                                clip["status"] = "error"
                                continue
                            finally:
                                # Cleanup full download
                                for f in full_files:
                                    f.unlink(missing_ok=True)

                    # Find the downloaded clip file
                    clip_files = list(project_dir.glob(f"clip_{clip['id']}.*"))
                    if clip_files:
                        clip["filename"] = clip_files[0].name
                        clip["status"] = "downloaded"
                    else:
                        clip["status"] = "error"

                # Concatenate all downloaded clips
                downloaded = [c for c in project["clips"] if c["status"] == "downloaded"]
                if not downloaded:
                    jobs[job_id] = {"status": "error", "error": "No clips downloaded successfully"}
                    return

                jobs[job_id] = {"status": "processing", "progress": 85, "stage": "Stitching clips together..."}

                if len(downloaded) == 1:
                    # Single clip, just use it directly
                    concat_path = str(project_dir / downloaded[0]["filename"])
                else:
                    # Create concat list file
                    concat_list = project_dir / "concat_list.txt"
                    with open(str(concat_list), "w") as f:
                        for clip in downloaded:
                            clip_path = str(project_dir / clip["filename"]).replace("\\", "/")
                            f.write(f"file '{clip_path}'\n")

                    # Re-encode and concat (safe for mixed formats)
                    concat_path = str(project_dir / "concat.mp4")

                    # Build filter_complex for N inputs
                    n = len(downloaded)
                    inputs = []
                    for clip in downloaded:
                        inputs.extend(["-i", str(project_dir / clip["filename"])])

                    filter_parts = []
                    concat_inputs = []
                    for i in range(n):
                        filter_parts.append(f"[{i}:v]scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=30[v{i}]")
                        filter_parts.append(f"[{i}:a]aformat=sample_fmts=s16:sample_rates=48000:channel_layouts=stereo[a{i}]")
                        concat_inputs.append(f"[v{i}][a{i}]")

                    filter_complex = ";".join(filter_parts) + ";" + "".join(concat_inputs) + f"concat=n={n}:v=1:a=1[outv][outa]"

                    cmd = [FFMPEG] + inputs + [
                        "-filter_complex", filter_complex,
                        "-map", "[outv]", "-map", "[outa]",
                        "-c:v", "libx264", "-crf", "18", "-preset", "fast",
                        "-c:a", "aac", "-b:a", "192k",
                        "-movflags", "+faststart",
                        "-y", concat_path,
                    ]

                    try:
                        run_ffmpeg(cmd, env=env, timeout=300)
                    except Exception as e:
                        jobs[job_id] = {"status": "error", "error": f"Concat failed: {str(e)}"}
                        return

                project["concat_file"] = concat_path
                project["status"] = "clips_ready"

                # Get duration of concat file
                duration = probe_media_duration(concat_path)

                jobs[job_id] = {
                    "status": "complete",
                    "progress": 100,
                    "stage": "All clips downloaded and stitched!",
                    "concat_duration": round(duration, 2),
                    "clips_downloaded": len(downloaded),
                    "clips_failed": len(project["clips"]) - len(downloaded),
                }

            except Exception as e:
                jobs[job_id] = {"status": "error", "error": str(e)}

        thread = threading.Thread(target=run_clip_downloads, daemon=True)
        thread.start()
        return jsonify({"job_id": job_id})

    # ── Serve concat preview ───────────────────────────────────────

    @app.route("/api/reel/serve-concat/<project_id>")
    def reel_serve_concat(project_id):
        project = reel_projects.get(project_id)
        if not project or not project.get("concat_file"):
            return jsonify({"error": "No concatenated video available"}), 404

        import mimetypes
        concat_path = project["concat_file"]
        if not Path(concat_path).exists():
            return jsonify({"error": "Concat file not found"}), 404

        mimetype, _ = mimetypes.guess_type(concat_path)
        return send_file(concat_path, mimetype=mimetype or "video/mp4")

    # ── Upload local video as VOD ──────────────────────────────────

    @app.route("/api/reel/upload-vod", methods=["POST"])
    def reel_upload_vod():
        project_id = request.form.get("project_id", "")
        project = reel_projects.get(project_id)
        if not project:
            return jsonify({"error": "Project not found"}), 404

        if "video" not in request.files:
            return jsonify({"error": "No video file provided"}), 400

        file = request.files["video"]
        if not file.filename:
            return jsonify({"error": "No file selected"}), 400

        allowed_extensions = {"mp4", "mov", "avi", "mkv", "webm", "wmv", "m4v"}
        ext = file.filename.rsplit(".", 1)[1].lower() if "." in file.filename else ""
        if ext not in allowed_extensions:
            return jsonify({"error": f"Unsupported format. Allowed: {', '.join(allowed_extensions)}"}), 400

        project_dir = DOWNLOADS_DIR / f"reel_{project_id}"
        project_dir.mkdir(parents=True, exist_ok=True)

        for old_vod in project_dir.glob("vod.*"):
            old_vod.unlink(missing_ok=True)
        save_path = project_dir / f"vod.{ext}"
        file.save(str(save_path))

        duration = probe_media_duration(str(save_path))
        project["vod_url"] = f"local:{save_path}"
        project["vod_title"] = file.filename
        project["vod_duration"] = duration
        project["source_type"] = "file"
        project["concat_file"] = None
        project["captions"] = None
        project["speakers"] = {}
        for clip in project["clips"]:
            clip["status"] = "pending"
            clip["filename"] = None

        return jsonify({
            "status": "uploaded",
            "filename": file.filename,
            "duration": round(duration, 2),
        })
