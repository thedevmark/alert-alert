"""Reel Maker pipeline — multi-clip VOD extraction, concatenation, and reel export."""

import json
import uuid
import threading
from datetime import datetime, timezone
from pathlib import Path

from flask import request, jsonify, send_file

# In-memory reel project storage
reel_projects = {}


def _utc_now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _project_dir(project_id):
    from app import DOWNLOADS_DIR
    path = DOWNLOADS_DIR / f"reel_{project_id}"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _project_file(project_id):
    return _project_dir(project_id) / "project.json"


def save_reel_project(project_id):
    project = reel_projects.get(project_id)
    if not project:
        return
    project["project_id"] = project_id
    project.setdefault("created_at", _utc_now_iso())
    project["updated_at"] = _utc_now_iso()
    project_copy = json.loads(json.dumps(project))
    with open(_project_file(project_id), "w", encoding="utf-8") as f:
        json.dump(project_copy, f, indent=2)


def load_reel_project(project_id):
    if project_id in reel_projects:
        return reel_projects[project_id]

    project_file = _project_file(project_id)
    if not project_file.exists():
        return None

    with open(project_file, "r", encoding="utf-8") as f:
        project = json.load(f)
    reel_projects[project_id] = project
    return project


def register_reel_routes(app):
    """Register all reel-maker routes on the Flask app."""

    from app import (
        jobs,
        DOWNLOADS_DIR,
        PROCESSING_DIR,
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
        get_output_dir,
        _get_video_info,
    )

    def clamp_seconds(value, minimum=0.0, maximum=None):
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            parsed = minimum
        parsed = max(minimum, parsed)
        if maximum is not None:
            parsed = min(maximum, parsed)
        return round(parsed, 3)

    def format_timecode(value):
        total = max(0.0, float(value or 0))
        hours = int(total // 3600)
        minutes = int((total % 3600) // 60)
        seconds = total - (hours * 3600) - (minutes * 60)
        whole_seconds = int(seconds)
        centiseconds = int(round((seconds - whole_seconds) * 100))
        if centiseconds == 100:
            whole_seconds += 1
            centiseconds = 0
        if whole_seconds == 60:
            minutes += 1
            whole_seconds = 0
        if hours > 0:
            return f"{hours}:{minutes:02d}:{whole_seconds:02d}.{centiseconds:02d}"
        return f"{minutes}:{whole_seconds:02d}.{centiseconds:02d}"

    def build_moment(start, end, title, kind, score=None, duration=0):
        max_duration = float(duration or 0) if duration else None
        start_sec = clamp_seconds(start, 0.0, max_duration)
        end_sec = clamp_seconds(end, start_sec + 0.25, max_duration)
        if end_sec <= start_sec:
            return None
        return {
            "title": str(title or kind.title()).strip() or kind.title(),
            "kind": kind,
            "score": score,
            "start_sec": start_sec,
            "end_sec": end_sec,
            "start": format_timecode(start_sec),
            "end": format_timecode(end_sec),
        }

    def extract_source_moments(info, duration=0):
        duration = float(duration or info.get("duration") or 0)
        moments = []

        chapters = info.get("chapters") or []
        for idx, chapter in enumerate(chapters):
            moment = build_moment(
                chapter.get("start_time", chapter.get("start", 0)),
                chapter.get("end_time", chapter.get("end", 0)),
                chapter.get("title") or f"Chapter {idx + 1}",
                "chapter",
                duration=duration,
            )
            if moment:
                moments.append(moment)

        sections = info.get("sections") or info.get("highlights") or []
        for idx, section in enumerate(sections):
            moment = build_moment(
                section.get("start_time", section.get("start", 0)),
                section.get("end_time", section.get("end", 0)),
                section.get("title") or section.get("name") or f"Moment {idx + 1}",
                "section",
                score=section.get("score"),
                duration=duration,
            )
            if moment:
                moments.append(moment)

        heatmap = info.get("heatmap") or []
        if not moments and heatmap:
            ranked = sorted(
                heatmap,
                key=lambda item: float(item.get("value") or item.get("score") or item.get("heat") or 0),
                reverse=True,
            )
            for idx, item in enumerate(ranked[:8]):
                if "start_time" in item and "end_time" in item:
                    start_sec = item.get("start_time")
                    end_sec = item.get("end_time")
                else:
                    center = float(item.get("time") or item.get("position") or item.get("start_time") or 0)
                    start_sec = max(0, center - 8)
                    end_sec = center + 18
                moment = build_moment(
                    start_sec,
                    end_sec,
                    f"Peak {idx + 1}",
                    "heatmap",
                    score=item.get("value") or item.get("score") or item.get("heat"),
                    duration=duration,
                )
                if moment:
                    moments.append(moment)

        deduped = []
        seen = set()
        for moment in moments:
            key = (round(moment["start_sec"], 1), round(moment["end_sec"], 1), moment["kind"])
            if key in seen:
                continue
            seen.add(key)
            deduped.append(moment)
        return deduped

    def build_preview_source_commands(url):
        youtube = is_youtube_url(url)
        profiles = []
        if youtube:
            profiles = [
                [
                    YTDLP,
                    "--no-playlist",
                    "--force-ipv4",
                    "-f", "b[ext=mp4]/b[ext=webm]/b",
                    "--get-url",
                    "--extractor-args", "youtube:player_client=web",
                    url,
                ],
                [
                    YTDLP,
                    "--no-playlist",
                    "--force-ipv4",
                    "-f", "b[ext=mp4]/b[ext=webm]/b",
                    "--get-url",
                    "--extractor-args", "youtube:player_client=android",
                    url,
                ],
            ]
        else:
            profiles = [[
                YTDLP,
                "--no-playlist",
                "--force-ipv4",
                "-f", "b[ext=mp4]/b[ext=webm]/b",
                "--get-url",
                url,
            ]]

        if FFMPEG_DIR:
            for cmd in profiles:
                cmd[1:1] = ["--ffmpeg-location", FFMPEG_DIR]
        return profiles

    def get_preview_stream_url(url):
        last_error = "Unable to load preview stream."
        for cmd in build_preview_source_commands(url):
            result = run_subprocess(cmd, timeout=90)
            if result.returncode == 0:
                lines = [line.strip() for line in (result.stdout or "").splitlines() if line.strip()]
                if lines:
                    return lines[0]
            last_error = summarize_ytdlp_error(result.stderr or result.stdout)
        raise RuntimeError(last_error)

    # ── Project management ─────────────────────────────────────────

    @app.route("/api/reel/create-project", methods=["POST"])
    def reel_create_project():
        project_id = uuid.uuid4().hex[:10]
        project_dir = DOWNLOADS_DIR / f"reel_{project_id}"
        project_dir.mkdir(parents=True, exist_ok=True)

        reel_projects[project_id] = {
            "project_id": project_id,
            "status": "created",
            "source_type": "url",
            "vod_url": "",
            "vod_title": "",
            "vod_duration": 0,
            "local_file_uploaded": False,
            "source_moments": [],
            "clips": [],
            "concat_file": None,
            "export_file": None,
            "captions": None,
            "speakers": {},
            "caption_style": None,
        }
        save_reel_project(project_id)
        return jsonify({"project_id": project_id})

    @app.route("/api/reel/projects")
    def reel_list_projects():
        projects = []
        for project_file in DOWNLOADS_DIR.glob("reel_*/project.json"):
            try:
                with open(project_file, "r", encoding="utf-8") as f:
                    project = json.load(f)
                project_id = project.get("project_id") or project_file.parent.name.replace("reel_", "", 1)
                projects.append({
                    "project_id": project_id,
                    "title": project.get("vod_title") or "Untitled reel project",
                    "source_type": project.get("source_type", "url"),
                    "status": project.get("status", "created"),
                    "vod_duration": project.get("vod_duration", 0),
                    "clip_count": len(project.get("clips", [])),
                    "has_captions": bool(project.get("captions", {}).get("words")),
                    "has_concat": bool(project.get("concat_file")),
                    "has_export": bool(project.get("export_file")),
                    "local_file_uploaded": bool(project.get("local_file_uploaded")),
                    "created_at": project.get("created_at"),
                    "updated_at": project.get("updated_at"),
                })
            except Exception:
                continue

        projects.sort(key=lambda item: item.get("updated_at") or "", reverse=True)
        return jsonify({"projects": projects[:12]})

    @app.route("/api/reel/project/<project_id>")
    def reel_get_project(project_id):
        project = load_reel_project(project_id)
        if not project:
            return jsonify({"error": "Project not found"}), 404
        return jsonify(project)

    # ── Set VOD source ─────────────────────────────────────────────

    @app.route("/api/reel/set-vod", methods=["POST"])
    def reel_set_vod():
        data = request.get_json()
        project_id = data.get("project_id", "")
        project = load_reel_project(project_id)
        if not project:
            return jsonify({"error": "Project not found"}), 404

        url = clean_video_url(data.get("url", "").strip())
        title = data.get("title", "")
        duration = data.get("duration", 0)
        source_type = str(data.get("source_type", "url")).strip().lower()
        if source_type not in {"url", "file"}:
            return jsonify({"error": "Invalid source type"}), 400

        project["vod_url"] = url
        project["vod_title"] = title
        project["vod_duration"] = duration
        project["source_type"] = source_type
        project["local_file_uploaded"] = bool(url and url.startswith("local:"))
        project["source_moments"] = []
        project["concat_file"] = None
        project["export_file"] = None
        project["captions"] = None
        project["speakers"] = {}
        project["caption_style"] = None
        if source_type == "file" and not project["local_file_uploaded"]:
            project["status"] = "file_selected"
        for clip in project["clips"]:
            clip["status"] = "pending"
            clip["filename"] = None
        save_reel_project(project_id)
        return jsonify({"status": "ok"})

    @app.route("/api/reel/preview-source", methods=["POST"])
    def reel_preview_source():
        data = request.get_json()
        url = clean_video_url(str(data.get("url", "")).strip())
        if not url:
            return jsonify({"error": "Missing VOD URL"}), 400
        try:
            return jsonify({"stream_url": get_preview_stream_url(url)})
        except Exception as e:
            return jsonify({"error": str(e)}), 400

    @app.route("/api/reel/import-source-moments", methods=["POST"])
    def reel_import_source_moments():
        data = request.get_json() or {}
        project_id = data.get("project_id", "")
        project = load_reel_project(project_id)
        if not project:
            return jsonify({"error": "Project not found"}), 404

        if project.get("source_type") != "url":
            return jsonify({"error": "Source moment import is available for remote VOD URLs only."}), 400

        url = clean_video_url(str(project.get("vod_url", "")).strip())
        if not url:
            return jsonify({"error": "Load a VOD URL first."}), 400

        try:
            info = _get_video_info(url)
        except Exception as e:
            return jsonify({"error": str(e)}), 400

        moments = extract_source_moments(info, project.get("vod_duration"))
        project["source_moments"] = moments

        if not moments:
            save_reel_project(project_id)
            return jsonify({
                "status": "empty",
                "moments": [],
                "imported_clips": [],
                "imported_count": 0,
                "message": "No source moments were exposed by this VOD.",
            })

        existing_keys = {
            (round(float(clip.get("start_sec", 0)), 1), round(float(clip.get("end_sec", 0)), 1))
            for clip in project.get("clips", [])
        }
        imported = []
        for moment in moments:
            key = (round(moment["start_sec"], 1), round(moment["end_sec"], 1))
            if key in existing_keys:
                continue
            clip = {
                "id": uuid.uuid4().hex[:6],
                "title": moment["title"],
                "note": "",
                "source_kind": moment["kind"],
                "start": moment["start"],
                "end": moment["end"],
                "start_sec": moment["start_sec"],
                "end_sec": moment["end_sec"],
                "status": "pending",
                "filename": None,
                "imported": True,
            }
            project["clips"].append(clip)
            imported.append(clip)
            existing_keys.add(key)

        save_reel_project(project_id)
        return jsonify({
            "status": "ok",
            "moments": moments,
            "imported_clips": imported,
            "imported_count": len(imported),
            "message": f"Imported {len(imported)} source moment(s).",
        })

    # ── Clip management ────────────────────────────────────────────

    @app.route("/api/reel/add-clip", methods=["POST"])
    def reel_add_clip():
        data = request.get_json()
        project_id = data.get("project_id", "")
        project = load_reel_project(project_id)
        if not project:
            return jsonify({"error": "Project not found"}), 404

        start = data.get("start", "0:00")
        end = data.get("end", "0:10")

        clip_id = uuid.uuid4().hex[:6]
        clip_title = str(data.get("title", "")).strip() or f"Clip {len(project['clips']) + 1}"
        clip = {
            "id": clip_id,
            "title": clip_title,
            "note": str(data.get("note", "")).strip(),
            "start": start,
            "end": end,
            "start_sec": parse_timestamp_to_seconds(start),
            "end_sec": parse_timestamp_to_seconds(end),
            "status": "pending",
            "filename": None,
        }
        project["clips"].append(clip)
        save_reel_project(project_id)
        return jsonify({"clip": clip})

    @app.route("/api/reel/update-clip", methods=["POST"])
    def reel_update_clip():
        data = request.get_json()
        project_id = data.get("project_id", "")
        project = load_reel_project(project_id)
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
        if "title" in data:
            clip["title"] = str(data.get("title", "")).strip()
        if "note" in data:
            clip["note"] = str(data.get("note", "")).strip()
        save_reel_project(project_id)
        return jsonify({"clip": clip})

    @app.route("/api/reel/remove-clip", methods=["POST"])
    def reel_remove_clip():
        data = request.get_json()
        project_id = data.get("project_id", "")
        project = load_reel_project(project_id)
        if not project:
            return jsonify({"error": "Project not found"}), 404

        clip_id = data.get("clip_id", "")
        project["clips"] = [c for c in project["clips"] if c["id"] != clip_id]
        save_reel_project(project_id)
        return jsonify({"status": "removed"})

    @app.route("/api/reel/reorder-clips", methods=["POST"])
    def reel_reorder_clips():
        data = request.get_json()
        project_id = data.get("project_id", "")
        project = load_reel_project(project_id)
        if not project:
            return jsonify({"error": "Project not found"}), 404

        new_order = data.get("clip_ids", [])
        clip_map = {c["id"]: c for c in project["clips"]}
        reordered = [clip_map[cid] for cid in new_order if cid in clip_map]
        # Append any clips not in the new order at the end
        remaining = [c for c in project["clips"] if c["id"] not in new_order]
        project["clips"] = reordered + remaining
        save_reel_project(project_id)
        return jsonify({"clips": project["clips"]})

    # ── Download and concat clips ──────────────────────────────────

    @app.route("/api/reel/download-clips", methods=["POST"])
    def reel_download_clips():
        data = request.get_json()
        project_id = data.get("project_id", "")
        project = load_reel_project(project_id)
        if not project:
            return jsonify({"error": "Project not found"}), 404

        if not project["clips"]:
            return jsonify({"error": "No clips to download"}), 400

        url = project.get("vod_url", "")
        if not url and project.get("source_type") == "file":
            return jsonify({"error": "Local VOD not uploaded. Re-select the file, then try again."}), 400
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
                project["export_file"] = None
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
                save_reel_project(project_id)

            except Exception as e:
                jobs[job_id] = {"status": "error", "error": str(e)}

        thread = threading.Thread(target=run_clip_downloads, daemon=True)
        thread.start()
        return jsonify({"job_id": job_id})

    # ── Serve concat preview ───────────────────────────────────────

    def _asset_item(kind, label, category, *, exists=True, detail="", preview_url=None, download_url=None, external_url=None):
        return {
            "kind": kind,
            "label": label,
            "category": category,
            "exists": bool(exists),
            "detail": detail,
            "preview_url": preview_url,
            "download_url": download_url,
            "external_url": external_url,
        }

    @app.route("/api/reel/assets/<project_id>")
    def reel_assets(project_id):
        project = load_reel_project(project_id)
        if not project:
            return jsonify({"error": "Project not found"}), 404

        project_dir = _project_dir(project_id)
        items = []

        vod_url = str(project.get("vod_url", "") or "")
        if project.get("source_type") == "file":
            source_ready = vod_url.startswith("local:")
            items.append(_asset_item(
                "source",
                project.get("vod_title") or "Local source VOD",
                "source",
                exists=source_ready,
                detail="Uploaded local VOD" if source_ready else "Selected locally, not uploaded yet",
                preview_url=f"/api/reel/serve-vod/{project_id}" if source_ready else None,
            ))
        elif vod_url:
            items.append(_asset_item(
                "source",
                project.get("vod_title") or "Remote source VOD",
                "source",
                detail="Remote VOD URL",
                external_url=vod_url,
            ))

        for clip in project.get("clips", []):
            filename = clip.get("filename")
            clip_path = project_dir / filename if filename else None
            exists = bool(filename and clip_path and clip_path.exists())
            detail = f"{clip.get('start', '0:00')} - {clip.get('end', '0:00')}"
            if clip.get("status"):
                detail = f"{detail} · {clip['status']}"
            items.append(_asset_item(
                "clip",
                clip.get("title") or f"Clip {clip.get('id', '')}",
                "clips",
                exists=exists,
                detail=detail,
                preview_url=f"/api/reel/project-file/{project_id}/{filename}" if exists else None,
                download_url=f"/api/reel/project-file/{project_id}/{filename}" if exists else None,
            ))

        concat_file = project.get("concat_file")
        if concat_file:
            concat_path = Path(concat_file)
            items.append(_asset_item(
                "sequence",
                concat_path.name,
                "generated",
                exists=concat_path.exists(),
                detail="Stitched preview sequence",
                preview_url=f"/api/reel/serve-concat/{project_id}" if concat_path.exists() else None,
                download_url=f"/api/reel/project-file/{project_id}/{concat_path.name}" if concat_path.exists() and concat_path.parent == project_dir else None,
            ))

        captions_ass = project_dir / "captions.ass"
        if captions_ass.exists():
            items.append(_asset_item(
                "captions",
                captions_ass.name,
                "generated",
                detail="Current caption script",
                download_url=f"/api/reel/project-file/{project_id}/{captions_ass.name}",
            ))

        transcript_audio = project_dir / "transcript_audio.wav"
        if transcript_audio.exists():
            items.append(_asset_item(
                "audio",
                transcript_audio.name,
                "generated",
                detail="Extracted transcript audio",
                download_url=f"/api/reel/project-file/{project_id}/{transcript_audio.name}",
            ))

        export_file = project.get("export_file")
        if export_file:
            export_path = Path(export_file)
            items.append(_asset_item(
                "export",
                export_path.name,
                "exports",
                exists=export_path.exists(),
                detail="Finished short-form export",
                download_url=f"/api/reel/download/{project_id}" if export_path.exists() else None,
            ))

        return jsonify({"items": items})

    @app.route("/api/reel/project-file/<project_id>/<path:filename>")
    def reel_project_file(project_id, filename):
        project = load_reel_project(project_id)
        if not project:
            return jsonify({"error": "Project not found"}), 404

        project_dir = _project_dir(project_id).resolve()
        requested = (project_dir / filename).resolve()
        if project_dir not in requested.parents and requested != project_dir:
            return jsonify({"error": "Invalid file path"}), 400
        if not requested.exists() or not requested.is_file():
            return jsonify({"error": "Project file not found"}), 404

        return send_file(str(requested), as_attachment=False)

    @app.route("/api/reel/serve-concat/<project_id>")
    def reel_serve_concat(project_id):
        project = load_reel_project(project_id)
        if not project or not project.get("concat_file"):
            return jsonify({"error": "No concatenated video available"}), 404

        import mimetypes
        concat_path = project["concat_file"]
        if not Path(concat_path).exists():
            return jsonify({"error": "Concat file not found"}), 404

        mimetype, _ = mimetypes.guess_type(concat_path)
        return send_file(concat_path, mimetype=mimetype or "video/mp4")

    @app.route("/api/reel/serve-vod/<project_id>")
    def reel_serve_vod(project_id):
        project = load_reel_project(project_id)
        if not project or project.get("source_type") != "file":
            return jsonify({"error": "No local VOD available"}), 404

        vod_url = str(project.get("vod_url", ""))
        if not vod_url.startswith("local:"):
            return jsonify({"error": "Local VOD file needs to be selected again"}), 404

        import mimetypes
        vod_path = Path(vod_url[6:])
        if not vod_path.exists():
            return jsonify({"error": "Local VOD file is missing"}), 404

        mimetype, _ = mimetypes.guess_type(str(vod_path))
        return send_file(str(vod_path), mimetype=mimetype or "video/mp4")

    # ── Upload local video as VOD ──────────────────────────────────

    @app.route("/api/reel/upload-vod", methods=["POST"])
    def reel_upload_vod():
        project_id = request.form.get("project_id", "")
        project = load_reel_project(project_id)
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
        project["local_file_uploaded"] = True
        project["source_moments"] = []
        project["concat_file"] = None
        project["export_file"] = None
        project["captions"] = None
        project["speakers"] = {}
        project["caption_style"] = None
        for clip in project["clips"]:
            clip["status"] = "pending"
            clip["filename"] = None
        save_reel_project(project_id)

        return jsonify({
            "status": "uploaded",
            "filename": file.filename,
            "duration": round(duration, 2),
        })

    @app.route("/api/reel/thumbnail/<project_id>")
    def reel_thumbnail(project_id):
        project = load_reel_project(project_id)
        if not project:
            return jsonify({"error": "Project not found"}), 404

        try:
            timestamp = max(0.0, float(request.args.get("ts", "0")))
        except (TypeError, ValueError):
            return jsonify({"error": "Invalid timestamp"}), 400

        project_dir = DOWNLOADS_DIR / f"reel_{project_id}"
        project_dir.mkdir(parents=True, exist_ok=True)
        thumbnail_path = project_dir / f"thumb_{int(timestamp * 100):08d}.jpg"
        if thumbnail_path.exists():
            return send_file(str(thumbnail_path), mimetype="image/jpeg")

        vod_url = project.get("vod_url", "")
        if not vod_url:
            return jsonify({"error": "No VOD source available"}), 400

        input_source = None
        if vod_url.startswith("local:"):
            local_path = Path(vod_url[6:])
            if not local_path.exists():
                return jsonify({"error": "Local VOD file is missing"}), 404
            input_source = str(local_path)
        else:
            try:
                input_source = get_preview_stream_url(vod_url)
            except Exception as e:
                return jsonify({"error": str(e)}), 400

        try:
            run_ffmpeg([
                FFMPEG,
                "-ss", f"{timestamp:.3f}",
                "-i", input_source,
                "-frames:v", "1",
                "-q:v", "2",
                "-y", str(thumbnail_path),
            ], env=get_env(), timeout=90)
        except Exception as e:
            return jsonify({"error": str(e)}), 400

        return send_file(str(thumbnail_path), mimetype="image/jpeg")

    def escape_filter_path(path_value):
        return str(path_value).replace("\\", "/").replace(":", "\\:").replace("'", "\\'").replace(",", "\\,")

    @app.route("/api/reel/export", methods=["POST"])
    def reel_export():
        data = request.get_json()
        project_id = data.get("project_id", "")
        project = load_reel_project(project_id)
        if not project:
            return jsonify({"error": "Project not found"}), 404

        concat_file = project.get("concat_file")
        if not concat_file or not Path(concat_file).exists():
            return jsonify({"error": "Download and stitch clips first"}), 400

        resolution = str(data.get("resolution", "1080")).strip()
        allowed_resolutions = {"1080": (1080, 1920), "1440": (1440, 2560), "2160": (2160, 3840)}
        if resolution not in allowed_resolutions:
            return jsonify({"error": "Invalid export resolution"}), 400

        output_width, output_height = allowed_resolutions[resolution]
        job_id = f"reel_export_{project_id}"
        jobs[job_id] = {"status": "processing", "progress": 0, "stage": "Preparing reel export..."}

        def run_export():
            try:
                from captions import generate_ass_subtitles

                env = get_env()
                project_dir = DOWNLOADS_DIR / f"reel_{project_id}"
                project_dir.mkdir(parents=True, exist_ok=True)

                jobs[job_id] = {"status": "processing", "progress": 10, "stage": "Preparing captions..."}

                ass_path = None
                if project.get("captions") and project.get("speakers"):
                    ass_content = generate_ass_subtitles(
                        project["captions"]["words"],
                        project["speakers"],
                        play_res_x=output_width,
                        play_res_y=output_height,
                        style=project.get("caption_style"),
                    )
                    ass_path = project_dir / f"captions_export_{output_width}x{output_height}.ass"
                    with open(ass_path, "w", encoding="utf-8") as f:
                        f.write(ass_content)

                jobs[job_id] = {"status": "processing", "progress": 35, "stage": "Rendering vertical reel..."}

                filter_parts = [
                    f"[0:v]scale={output_width}:{output_height}:force_original_aspect_ratio=increase,"
                    f"crop={output_width}:{output_height},boxblur=20:10[bg]",
                    f"[0:v]scale={output_width}:{output_height}:force_original_aspect_ratio=decrease[fg]",
                ]

                if ass_path is not None:
                    escaped_ass_path = escape_filter_path(ass_path.resolve())
                    filter_parts.append(
                        f"[bg][fg]overlay=(W-w)/2:(H-h)/2[base]"
                    )
                    filter_parts.append(
                        f"[base]subtitles='{escaped_ass_path}'[outv]"
                    )
                else:
                    filter_parts.append(
                        f"[bg][fg]overlay=(W-w)/2:(H-h)/2[outv]"
                    )

                output_file = get_output_dir() / f"reel_{project_id}_{output_width}x{output_height}.mp4"
                run_ffmpeg([
                    FFMPEG,
                    "-i", str(Path(concat_file)),
                    "-filter_complex", ";".join(filter_parts),
                    "-map", "[outv]",
                    "-map", "0:a?",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", "30",
                    "-preset", "medium", "-crf", "20",
                    "-c:a", "aac", "-b:a", "192k",
                    "-movflags", "+faststart",
                    "-y", str(output_file),
                ], env=env, timeout=1800)

                project["export_file"] = str(output_file)
                project["status"] = "export_ready"
                save_reel_project(project_id)

                jobs[job_id] = {
                    "status": "complete",
                    "progress": 100,
                    "stage": "Reel export complete!",
                    "filename": output_file.name,
                }
            except Exception as e:
                jobs[job_id] = {"status": "error", "error": str(e)}

        thread = threading.Thread(target=run_export, daemon=True)
        thread.start()
        return jsonify({"job_id": job_id})

    @app.route("/api/reel/download/<project_id>")
    def reel_download(project_id):
        project = load_reel_project(project_id)
        if not project or not project.get("export_file"):
            return jsonify({"error": "No exported reel available"}), 404

        export_path = Path(project["export_file"])
        if not export_path.exists():
            return jsonify({"error": "Export file not found"}), 404

        return send_file(str(export_path), as_attachment=True, download_name=export_path.name)
