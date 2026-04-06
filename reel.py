"""Reel Maker pipeline — multi-clip VOD extraction, concatenation, and reel export."""

import json
import re
import uuid
import threading
from datetime import datetime, timezone
from pathlib import Path

from flask import request, jsonify, send_file

# In-memory reel project storage
reel_projects = {}

DEFAULT_FACECAM_LAYOUT = {
    "enabled": False,
    "x": 0.72,
    "y": 0.04,
    "width": 0.24,
    "height": 0.24,
    "anchor": "top_right",
}


def normalize_facecam_layout(layout=None):
    base = dict(DEFAULT_FACECAM_LAYOUT)
    if isinstance(layout, dict):
        base["enabled"] = bool(layout.get("enabled", base["enabled"]))
        for key in ("x", "y", "width", "height"):
            try:
                base[key] = float(layout.get(key, base[key]))
            except (TypeError, ValueError):
                pass
        anchor = str(layout.get("anchor", base["anchor"])).strip().lower()
        if anchor:
            base["anchor"] = anchor

    base["width"] = max(0.08, min(0.7, base["width"]))
    base["height"] = max(0.08, min(0.7, base["height"]))
    base["x"] = max(0.0, min(1.0 - base["width"], base["x"]))
    base["y"] = max(0.0, min(1.0 - base["height"], base["y"]))
    return base


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
    project["facecam_layout"] = normalize_facecam_layout(project.get("facecam_layout"))
    project.setdefault("created_at", _utc_now_iso())
    project["updated_at"] = _utc_now_iso()
    project_copy = json.loads(json.dumps(project))
    with open(_project_file(project_id), "w", encoding="utf-8") as f:
        json.dump(project_copy, f, indent=2)


def load_reel_project(project_id):
    if project_id in reel_projects:
        reel_projects[project_id].setdefault("shortform_recipe", {})
        reel_projects[project_id]["facecam_layout"] = normalize_facecam_layout(
            reel_projects[project_id].get("facecam_layout")
        )
        return reel_projects[project_id]

    project_file = _project_file(project_id)
    if not project_file.exists():
        return None

    with open(project_file, "r", encoding="utf-8") as f:
        project = json.load(f)
    project.setdefault("shortform_recipe", {})
    project["facecam_layout"] = normalize_facecam_layout(project.get("facecam_layout"))
    reel_projects[project_id] = project
    return project


def _project_title(project):
    if not project:
        return "Untitled video project"
    session = project.get("stream_session") or {}
    return (
        project.get("vod_title")
        or session.get("session_label")
        or session.get("game_title")
        or "Untitled video project"
    )


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

    def build_moment(start, end, title, kind, score=None, duration=0, extra_fields=None):
        max_duration = float(duration or 0) if duration else None
        start_sec = clamp_seconds(start, 0.0, max_duration)
        end_sec = clamp_seconds(end, start_sec + 0.25, max_duration)
        if end_sec <= start_sec:
            return None
        moment = {
            "title": str(title or kind.title()).strip() or kind.title(),
            "kind": kind,
            "score": score,
            "start_sec": start_sec,
            "end_sec": end_sec,
            "start": format_timecode(start_sec),
            "end": format_timecode(end_sec),
        }
        if isinstance(extra_fields, dict):
            moment.update(extra_fields)
        return moment

    def dedupe_moments(moments):
        deduped = []
        seen = set()
        for moment in moments or []:
            key = (
                round(float(moment.get("start_sec", 0)), 1),
                round(float(moment.get("end_sec", 0)), 1),
                str(moment.get("kind", "")).strip().lower(),
            )
            if key in seen:
                continue
            seen.add(key)
            deduped.append(moment)
        return deduped

    def merge_project_source_moments(project, imported_moments):
        manual_markers = list(project.get("stream_markers") or [])
        project["source_moments"] = dedupe_moments([*manual_markers, *(imported_moments or [])])
        return project["source_moments"]

    def parse_marker_time_token(token):
        if token is None:
            return None
        raw = str(token).strip().lower().strip("[](){}")
        if not raw:
            return None
        compact = re.sub(r"\s+", "", raw)
        if ":" in compact:
            try:
                return parse_timestamp_to_seconds(compact)
            except Exception:
                return None
        if not any(ch in compact for ch in "hms"):
            return None
        match = re.fullmatch(r"(?:(\d+)h)?(?:(\d+)m)?(?:(\d+(?:\.\d+)?)s?)?", compact)
        if not match:
            return None
        hours = float(match.group(1) or 0)
        minutes = float(match.group(2) or 0)
        seconds = float(match.group(3) or 0)
        total = (hours * 3600) + (minutes * 60) + seconds
        return total if total >= 0 else None

    def parse_stream_markers(text, *, pre_roll=8.0, post_roll=22.0, duration=0):
        marker_lines = [line.strip() for line in str(text or "").splitlines() if line.strip()]
        parsed_markers = []
        range_pattern = re.compile(
            r"^\s*([0-9:\.\[\]\(\)\{\}\s]+|\d+h\d*m\d+(?:\.\d+)?s?|\d+h\d*m|\d+m\d+(?:\.\d+)?s?|\d+(?:\.\d+)?s)\s*(?:-|–|—|to|->|→)\s*([0-9:\.\[\]\(\)\{\}\s]+|\d+h\d*m\d+(?:\.\d+)?s?|\d+h\d*m|\d+m\d+(?:\.\d+)?s?|\d+(?:\.\d+)?s)\s*(.*)$",
            re.IGNORECASE,
        )
        single_pattern = re.compile(
            r"^\s*([0-9:\.\[\]\(\)\{\}\s]+|\d+h\d*m\d+(?:\.\d+)?s?|\d+h\d*m|\d+m\d+(?:\.\d+)?s?|\d+(?:\.\d+)?s)\s*(.*)$",
            re.IGNORECASE,
        )

        for idx, line in enumerate(marker_lines, start=1):
            stripped = re.sub(r"^[\-\*\u2022]+\s*", "", line).strip()
            if not stripped:
                continue

            start_sec = None
            end_sec = None
            title = ""

            range_match = range_pattern.match(stripped)
            if range_match:
                start_sec = parse_marker_time_token(range_match.group(1))
                end_sec = parse_marker_time_token(range_match.group(2))
                title = str(range_match.group(3) or "").strip(" -:\t")
            else:
                single_match = single_pattern.match(stripped)
                if single_match:
                    start_sec = parse_marker_time_token(single_match.group(1))
                    title = str(single_match.group(2) or "").strip(" -:\t")

            if start_sec is None:
                url_match = re.search(r"[?&#]t=([^&#\s]+)", stripped, re.IGNORECASE)
                if url_match:
                    start_sec = parse_marker_time_token(url_match.group(1))
                    title = re.sub(r"https?://\S+", "", stripped).strip(" -:\t")

            if start_sec is None:
                continue

            if end_sec is None:
                end_sec = start_sec + max(0.5, float(post_roll or 0))
                start_sec = max(0.0, start_sec - max(0.0, float(pre_roll or 0)))

            marker = build_moment(
                start_sec,
                end_sec,
                title or f"Marker {idx}",
                "stream_marker",
                duration=duration,
            )
            if not marker:
                continue
            marker["source_text"] = line
            parsed_markers.append(marker)

        return dedupe_moments(parsed_markers)

    def extract_twitch_clip_moments(clips, duration=0):
        moments = []
        for idx, entry in enumerate(clips or [], start=1):
            if not isinstance(entry, dict):
                continue
            try:
                vod_offset = float(entry.get("vod_offset"))
                clip_duration = max(0.25, float(entry.get("duration") or 0))
            except (TypeError, ValueError):
                continue

            moment = build_moment(
                vod_offset,
                vod_offset + clip_duration,
                entry.get("title") or f"Twitch Clip {idx}",
                "twitch_clip",
                score=entry.get("view_count"),
                duration=duration,
                extra_fields={
                    "clip_id": str(entry.get("id", "")).strip(),
                    "clip_url": str(entry.get("url", "")).strip(),
                    "created_at": str(entry.get("created_at", "")).strip(),
                    "creator_login": str(entry.get("creator_login", "")).strip(),
                    "creator_name": str(entry.get("creator_name", "")).strip(),
                    "thumbnail_url": str(entry.get("thumbnail_url", "")).strip(),
                    "video_id": str(entry.get("video_id", "")).strip(),
                    "view_count": entry.get("view_count"),
                    "vod_offset": vod_offset,
                },
            )
            if moment:
                moments.append(moment)

        return dedupe_moments(moments)

    def extract_editor_feed_moments(items, duration=0):
        moments = []
        allowed_kinds = {"twitch_clip", "stream_marker", "chat_bookmark", "music_event", "source_moment"}
        for idx, entry in enumerate(items or [], start=1):
            if not isinstance(entry, dict):
                continue

            kind = str(entry.get("kind", "") or entry.get("source_kind", "")).strip().lower() or "source_moment"
            if kind not in allowed_kinds:
                kind = "source_moment"

            title = str(entry.get("title", "")).strip() or f"Feed Item {idx}"
            start_raw = entry.get("startSec", entry.get("start_sec", 0))
            end_raw = entry.get("endSec", entry.get("end_sec", 0))
            try:
                start_sec = float(start_raw)
            except (TypeError, ValueError):
                start_sec = 0.0
            try:
                end_sec = float(end_raw)
            except (TypeError, ValueError):
                end_sec = start_sec + 30.0
            if end_sec <= start_sec:
                end_sec = start_sec + 30.0

            extra_fields = {
                "clip_id": str(entry.get("clipId", entry.get("clip_id", ""))).strip(),
                "clip_url": str(entry.get("clipUrl", entry.get("clip_url", ""))).strip(),
                "created_at": str(entry.get("createdAt", entry.get("created_at", ""))).strip(),
                "feed_id": str(entry.get("id", "")).strip(),
                "note": str(entry.get("note", "")).strip(),
                "project_id": str(entry.get("projectId", entry.get("project_id", ""))).strip(),
                "session_label": str(entry.get("sessionLabel", entry.get("session_label", ""))).strip(),
                "video_id": str(entry.get("videoId", entry.get("video_id", ""))).strip(),
                "vod_offset": entry.get("vodOffset", entry.get("vod_offset", start_sec)),
                "vod_url": str(entry.get("vodUrl", entry.get("vod_url", ""))).strip(),
            }

            channel_name = str(entry.get("channelName", entry.get("channel_name", ""))).strip()
            if channel_name:
                extra_fields["channel_name"] = channel_name

            score = entry.get("viewCount", entry.get("view_count"))
            moment = build_moment(
                start_sec,
                end_sec,
                title,
                kind,
                score=score,
                duration=duration,
                extra_fields=extra_fields,
            )
            if moment:
                moments.append(moment)

        return dedupe_moments(moments)

    SHORTFORM_PRESET_RECIPES = {
        "gameplay_focus": {
            "label": "Gameplay Focus",
            "description": "Fast gameplay-first short with readable captions and clean pacing notes.",
            "layout_mode": "gameplay_focus",
            "caption_style": {
                "preset": "pathos_clean",
                "font_scale": 1.0,
                "max_words": 6,
                "margin_v": 112,
                "outline": 4,
                "shadow": 2,
                "background_opacity": 48,
            },
            "note_prefix": "Short preset: Gameplay Focus. Keep gameplay dominant and captions compact.",
        },
        "facecam_top": {
            "label": "Facecam Top",
            "description": "Vertical streamer layout that reserves more room for camera reactions near the top.",
            "layout_mode": "facecam_top",
            "caption_style": {
                "preset": "broadcast_bold",
                "font_scale": 1.12,
                "max_words": 5,
                "margin_v": 156,
                "outline": 5,
                "shadow": 1,
                "background_opacity": 30,
            },
            "note_prefix": "Short preset: Facecam Top. Leave upper-frame room for camera and reactions.",
        },
        "baked_hype": {
            "label": "Baked Text Punch",
            "description": "Hard-hitting caption treatment for hype clips, pop-offs, and strong hooks.",
            "layout_mode": "baked_hype",
            "caption_style": {
                "preset": "broadcast_bold",
                "font_scale": 1.22,
                "max_words": 4,
                "margin_v": 132,
                "outline": 6,
                "shadow": 2,
                "background_opacity": 42,
                "all_caps": True,
            },
            "note_prefix": "Short preset: Baked Text Punch. Push the hook fast and keep captions loud.",
        },
    }

    def resolve_shortform_recipe(preset_name=None):
        from captions import normalize_caption_style

        preset_key = str(preset_name or "gameplay_focus").strip().lower() or "gameplay_focus"
        base = SHORTFORM_PRESET_RECIPES.get(preset_key) or SHORTFORM_PRESET_RECIPES["gameplay_focus"]
        return {
            "preset": preset_key if preset_key in SHORTFORM_PRESET_RECIPES else "gameplay_focus",
            "label": base["label"],
            "description": base["description"],
            "layout_mode": base["layout_mode"],
            "format_preset": "shorts",
            "note_prefix": base["note_prefix"],
            "caption_style": normalize_caption_style(base.get("caption_style")),
        }

    def moment_key(item, kind_key=None):
        kind = str(kind_key if kind_key is not None else item.get("kind", item.get("source_kind", ""))).strip().lower()
        return (
            round(float(item.get("start_sec", 0) or 0), 1),
            round(float(item.get("end_sec", 0) or 0), 1),
            kind,
        )

    def find_matching_clip(project, moment_or_clip):
        key = moment_key(moment_or_clip)
        for clip in project.get("clips", []):
            if moment_key(clip) == key:
                return clip
        return None

    def add_or_update_source_moment(project, moment):
        if not isinstance(moment, dict):
            return project.get("source_moments", [])
        existing = list(project.get("source_moments") or [])
        markers = list(project.get("stream_markers") or [])
        project["source_moments"] = dedupe_moments([*markers, *existing, moment])
        return project["source_moments"]

    def ensure_clip_for_moment(project, moment):
        if not isinstance(moment, dict):
            return None, False

        add_or_update_source_moment(project, moment)
        clip = find_matching_clip(project, moment)
        if clip is not None:
            return clip, False

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
        return clip, True

    def apply_short_recipe_to_clip(clip, recipe):
        existing_note = str(clip.get("note", "") or "").strip()
        cleaned_note = re.sub(r"(?im)^short preset:.*(?:\n|$)", "", existing_note).strip()
        preset_line = str(recipe.get("note_prefix", "") or "").strip()
        clip["note"] = "\n".join(part for part in [preset_line, cleaned_note] if part).strip()
        clip["short_preset"] = recipe.get("preset", "")
        clip["composition_profile"] = recipe.get("layout_mode", "")
        clip["short_ready"] = True
        if "include_in_longform" not in clip:
            clip["include_in_longform"] = True
        else:
            clip["include_in_longform"] = bool(clip.get("include_in_longform"))
        return clip

    COMPOSITION_PROFILE_EXPORTS = {
        "gameplay_focus": {
            "label": "Gameplay Focus",
            "vertical": {
                "layout": "stacked_portrait",
                "fg_scale_width": 1.0,
                "fg_scale_height": 1.0,
                "overlay_y": "(H-h)/2",
                "caption_style": {
                    "preset": "pathos_clean",
                    "max_words": 6,
                    "margin_v": 112,
                    "outline": 4,
                    "shadow": 2,
                    "background_opacity": 48,
                },
                "render_label": "gameplay-first framing",
            },
            "landscape": {
                "layout": "fill_crop",
                "caption_style": {
                    "preset": "pathos_clean",
                    "max_words": 7,
                    "margin_v": 78,
                    "outline": 3,
                    "shadow": 1,
                    "background_opacity": 26,
                },
                "render_label": "landscape gameplay recut",
            },
        },
        "facecam_top": {
            "label": "Facecam Top",
            "vertical": {
                "layout": "stacked_portrait",
                "fg_scale_width": 0.96,
                "fg_scale_height": 0.82,
                "overlay_y": "max(56,(H-h)*0.18)",
                "caption_style": {
                    "preset": "broadcast_bold",
                    "max_words": 5,
                    "margin_v": 168,
                    "outline": 5,
                    "shadow": 1,
                    "background_opacity": 28,
                },
                "render_label": "upper-frame facecam framing",
            },
            "landscape": {
                "layout": "fill_crop",
                "caption_style": {
                    "preset": "broadcast_bold",
                    "font_scale": 0.96,
                    "max_words": 6,
                    "margin_v": 86,
                    "outline": 4,
                    "shadow": 1,
                    "background_opacity": 18,
                },
                "render_label": "landscape facecam recut",
            },
        },
        "baked_hype": {
            "label": "Baked Text Punch",
            "vertical": {
                "layout": "stacked_portrait",
                "fg_scale_width": 1.04,
                "fg_scale_height": 0.9,
                "overlay_y": "max(44,(H-h)*0.22)",
                "fg_eq": "eq=contrast=1.08:saturation=1.12:brightness=0.015",
                "caption_style": {
                    "preset": "broadcast_bold",
                    "font_scale": 1.22,
                    "max_words": 4,
                    "margin_v": 136,
                    "outline": 6,
                    "shadow": 2,
                    "background_opacity": 42,
                    "all_caps": True,
                },
                "render_label": "punchy hype framing",
            },
            "landscape": {
                "layout": "fill_crop",
                "fg_eq": "eq=contrast=1.05:saturation=1.08:brightness=0.01",
                "caption_style": {
                    "preset": "broadcast_bold",
                    "font_scale": 1.0,
                    "max_words": 5,
                    "margin_v": 84,
                    "outline": 5,
                    "shadow": 1,
                    "background_opacity": 18,
                    "all_caps": True,
                },
                "render_label": "landscape hype recut",
            },
        },
    }

    def even_scale_size(value, minimum=2):
        number = max(minimum, int(round(float(value or minimum))))
        if number % 2:
            number += 1
        return number

    def clamp_unit(value, minimum=0.0, maximum=1.0):
        try:
            number = float(value)
        except (TypeError, ValueError):
            number = minimum
        return max(minimum, min(maximum, number))

    def clip_duration_seconds(clip):
        try:
            start_sec = float(clip.get("start_sec", 0) or 0)
            end_sec = float(clip.get("end_sec", 0) or 0)
        except (TypeError, ValueError):
            return 0.25
        return max(0.25, end_sec - start_sec)

    def resolve_effective_export_profile(project, output_width, output_height, input_width=1920, input_height=1080):
        from captions import normalize_caption_style

        base_recipe = project.get("shortform_recipe") or resolve_shortform_recipe()
        default_key = str(base_recipe.get("layout_mode") or "gameplay_focus").strip().lower() or "gameplay_focus"
        if default_key not in COMPOSITION_PROFILE_EXPORTS:
            default_key = "gameplay_focus"

        weighted_profiles = {}
        profiled_clips = [
            clip for clip in project.get("clips", [])
            if str(clip.get("composition_profile", "")).strip().lower() in COMPOSITION_PROFILE_EXPORTS
        ]
        preferred_clips = [clip for clip in profiled_clips if bool(clip.get("short_ready"))] or profiled_clips

        for clip in preferred_clips:
            profile_key = str(clip.get("composition_profile", "")).strip().lower()
            bucket = weighted_profiles.setdefault(profile_key, {"count": 0, "duration": 0.0})
            bucket["count"] += 1
            bucket["duration"] += clip_duration_seconds(clip)

        selected_key = default_key
        if weighted_profiles:
            selected_key = max(
                weighted_profiles.items(),
                key=lambda item: (
                    item[1]["count"],
                    item[1]["duration"],
                    1 if item[0] == default_key else 0,
                ),
            )[0]

        orientation = "landscape" if output_width > output_height else "vertical"
        profile_config = COMPOSITION_PROFILE_EXPORTS.get(selected_key, COMPOSITION_PROFILE_EXPORTS["gameplay_focus"])
        variant_config = dict(profile_config.get(orientation) or {})

        base_style = project.get("caption_style") or base_recipe.get("caption_style")
        normalized_base_style = normalize_caption_style(base_style)
        effective_style = normalize_caption_style({
            **normalized_base_style,
            **(variant_config.get("caption_style") or {}),
        })

        facecam_layout = normalize_facecam_layout(project.get("facecam_layout"))
        overlay_x = str(variant_config.get("overlay_x") or "(W-w)/2")
        overlay_y = str(variant_config.get("overlay_y") or "(H-h)/2")
        fg_render_width = None
        fg_render_height = None

        if variant_config.get("layout", "stacked_portrait") != "fill_crop":
            source_width = max(2, even_scale_size(input_width))
            source_height = max(2, even_scale_size(input_height))
            fg_box_width = max(2, even_scale_size(output_width * float(variant_config.get("fg_scale_width", 1.0) or 1.0)))
            fg_box_height = max(2, even_scale_size(output_height * float(variant_config.get("fg_scale_height", 1.0) or 1.0)))
            scale_factor = min(fg_box_width / max(1, source_width), fg_box_height / max(1, source_height))
            fg_render_width = max(2, even_scale_size(source_width * scale_factor))
            fg_render_height = max(2, even_scale_size(source_height * scale_factor))

            default_targets = {
                "gameplay_focus": {"center_x": 0.5, "center_y": 0.5},
                "facecam_top": {"center_x": 0.5, "center_y": 0.34, "facecam_center_x": 0.5, "facecam_center_y": 0.18},
                "baked_hype": {"center_x": 0.5, "center_y": 0.42},
            }
            target = default_targets.get(selected_key, default_targets["gameplay_focus"])
            max_overlay_x = max(0.0, output_width - fg_render_width)
            max_overlay_y = max(0.0, output_height - fg_render_height)
            resolved_overlay_x = clamp_unit((target["center_x"] * output_width - (fg_render_width / 2)) / max(1, max_overlay_x), 0.0, 1.0) * max_overlay_x if max_overlay_x else 0.0
            resolved_overlay_y = clamp_unit((target["center_y"] * output_height - (fg_render_height / 2)) / max(1, max_overlay_y), 0.0, 1.0) * max_overlay_y if max_overlay_y else 0.0

            if selected_key == "facecam_top" and facecam_layout.get("enabled"):
                facecam_center_x = (facecam_layout["x"] + (facecam_layout["width"] / 2)) * fg_render_width
                facecam_center_y = (facecam_layout["y"] + (facecam_layout["height"] / 2)) * fg_render_height
                target_facecam_x = target.get("facecam_center_x", 0.5) * output_width
                target_facecam_y = target.get("facecam_center_y", 0.18) * output_height
                resolved_overlay_x = min(max(0.0, target_facecam_x - facecam_center_x), max_overlay_x)
                resolved_overlay_y = min(max(0.0, target_facecam_y - facecam_center_y), max_overlay_y)

                facecam_top = resolved_overlay_y + (facecam_layout["y"] * fg_render_height)
                if facecam_top > output_height * 0.44:
                    effective_style["margin_v"] = max(
                        effective_style["margin_v"],
                        int(output_height - facecam_top + 40),
                    )

            overlay_x = f"{resolved_overlay_x:.2f}"
            overlay_y = f"{resolved_overlay_y:.2f}"

        return {
            "key": selected_key,
            "label": profile_config.get("label") or selected_key.replace("_", " ").title(),
            "orientation": orientation,
            "layout": variant_config.get("layout", "stacked_portrait"),
            "fg_scale_width": float(variant_config.get("fg_scale_width", 1.0) or 1.0),
            "fg_scale_height": float(variant_config.get("fg_scale_height", 1.0) or 1.0),
            "overlay_x": overlay_x,
            "overlay_y": overlay_y,
            "fg_eq": str(variant_config.get("fg_eq") or "").strip(),
            "bg_eq": str(variant_config.get("bg_eq") or "").strip(),
            "caption_style": effective_style,
            "render_label": str(variant_config.get("render_label") or profile_config.get("label") or "streamer composition").strip(),
            "fg_render_width": fg_render_width,
            "fg_render_height": fg_render_height,
            "facecam_layout": facecam_layout,
        }

    def build_export_filter_graph(output_width, output_height, export_profile, ass_path=None):
        filter_parts = []
        layout_mode = export_profile.get("layout", "stacked_portrait")

        if layout_mode == "fill_crop":
            base_chain = (
                f"[0:v]scale={output_width}:{output_height}:force_original_aspect_ratio=increase,"
                f"crop={output_width}:{output_height}"
            )
            if export_profile.get("fg_eq"):
                base_chain += f",{export_profile['fg_eq']}"
            base_chain += "[base]"
            filter_parts.append(base_chain)
        else:
            bg_chain = (
                f"[0:v]scale={output_width}:{output_height}:force_original_aspect_ratio=increase,"
                f"crop={output_width}:{output_height},boxblur=20:10"
            )
            if export_profile.get("bg_eq"):
                bg_chain += f",{export_profile['bg_eq']}"
            bg_chain += "[bg]"
            filter_parts.append(bg_chain)

            fg_width = int(export_profile.get("fg_render_width") or even_scale_size(output_width * export_profile.get("fg_scale_width", 1.0)))
            fg_height = int(export_profile.get("fg_render_height") or even_scale_size(output_height * export_profile.get("fg_scale_height", 1.0)))
            fg_chain = f"[0:v]scale={fg_width}:{fg_height}"
            if export_profile.get("fg_eq"):
                fg_chain += f",{export_profile['fg_eq']}"
            fg_chain += "[fg]"
            filter_parts.append(fg_chain)
            filter_parts.append(
                f"[bg][fg]overlay={export_profile.get('overlay_x', '(W-w)/2')}:{export_profile.get('overlay_y', '(H-h)/2')}[base]"
            )

        if ass_path is not None:
            escaped_ass_path = escape_filter_path(ass_path.resolve())
            filter_parts.append(f"[base]subtitles='{escaped_ass_path}'[outv]")
        else:
            filter_parts.append("[base]null[outv]")

        return filter_parts

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

        return dedupe_moments(moments)

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

    def probe_media_dimensions(path_value):
        try:
            result = run_subprocess([
                FFPROBE,
                "-v", "error",
                "-select_streams", "v:0",
                "-show_entries", "stream=width,height",
                "-of", "json",
                str(path_value),
            ], timeout=30)
            if result.returncode != 0:
                return None, None
            payload = json.loads(result.stdout or "{}")
            streams = payload.get("streams") or []
            if not streams:
                return None, None
            stream = streams[0] or {}
            width = int(stream.get("width") or 0)
            height = int(stream.get("height") or 0)
            if width <= 0 or height <= 0:
                return None, None
            return width, height
        except Exception:
            return None, None

    # ── Project management ─────────────────────────────────────────

    @app.route("/api/reel/create-project", methods=["POST"])
    def reel_create_project():
        project_id = uuid.uuid4().hex[:10]
        project_dir = DOWNLOADS_DIR / f"reel_{project_id}"
        project_dir.mkdir(parents=True, exist_ok=True)

        reel_projects[project_id] = {
            "project_id": project_id,
            "status": "created",
            "project_role": "shortform",
            "derived_from_project_id": None,
            "source_type": "url",
            "vod_url": "",
            "vod_title": "",
            "vod_duration": 0,
            "export_format_preset": "shorts",
            "local_file_uploaded": False,
            "stream_session": {
                "platform": "twitch",
                "channel_name": "",
                "game_title": "",
                "session_label": "",
                "session_date": "",
                "notes": "",
            },
            "facecam_layout": normalize_facecam_layout(),
            "marker_defaults": {
                "pre_roll": 8.0,
                "post_roll": 22.0,
            },
            "shortform_recipe": resolve_shortform_recipe(),
            "stream_markers": [],
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
                    "title": _project_title(project),
                    "source_type": project.get("source_type", "url"),
                    "status": project.get("status", "created"),
                    "project_role": project.get("project_role", "shortform"),
                    "derived_from_project_id": project.get("derived_from_project_id"),
                    "vod_duration": project.get("vod_duration", 0),
                    "export_format_preset": project.get("export_format_preset", "shorts"),
                    "clip_count": len(project.get("clips", [])),
                    "short_ready_count": sum(1 for clip in project.get("clips", []) if bool(clip.get("short_ready"))),
                    "queued_longform_count": sum(
                        1 for clip in project.get("clips", [])
                        if bool(clip.get("short_ready")) and bool(clip.get("include_in_longform", True))
                    ),
                    "has_captions": bool(project.get("captions", {}).get("words")),
                    "has_concat": bool(project.get("concat_file")),
                    "has_export": bool(project.get("export_file")),
                    "local_file_uploaded": bool(project.get("local_file_uploaded")),
                    "stream_session": project.get("stream_session") or {},
                    "shortform_recipe": project.get("shortform_recipe") or resolve_shortform_recipe(),
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
        project.setdefault("stream_session", {
            "platform": "twitch",
            "channel_name": "",
            "game_title": "",
            "session_label": "",
            "session_date": "",
            "notes": "",
        })
        project["facecam_layout"] = normalize_facecam_layout(project.get("facecam_layout"))
        project.setdefault("marker_defaults", {"pre_roll": 8.0, "post_roll": 22.0})
        project["shortform_recipe"] = resolve_shortform_recipe()
        project["stream_markers"] = []
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

    @app.route("/api/reel/set-export-format", methods=["POST"])
    def reel_set_export_format():
        data = request.get_json() or {}
        project_id = data.get("project_id", "")
        project = load_reel_project(project_id)
        if not project:
            return jsonify({"error": "Project not found"}), 404

        format_preset = str(data.get("format_preset", "shorts")).strip().lower()
        allowed_presets = {"shorts", "portrait_feed", "square", "landscape"}
        if format_preset not in allowed_presets:
            return jsonify({"error": "Invalid export format preset"}), 400

        project["export_format_preset"] = format_preset
        save_reel_project(project_id)
        return jsonify({"format_preset": format_preset})

    @app.route("/api/reel/set-session", methods=["POST"])
    def reel_set_session():
        data = request.get_json() or {}
        project_id = data.get("project_id", "")
        project = load_reel_project(project_id)
        if not project:
            return jsonify({"error": "Project not found"}), 404

        allowed_platforms = {"twitch", "youtube", "kick", "other"}
        platform_value = str(data.get("platform", "twitch")).strip().lower() or "twitch"
        if platform_value not in allowed_platforms:
            platform_value = "twitch"

        session = project.get("stream_session") or {}
        session.update({
            "platform": platform_value,
            "channel_name": str(data.get("channel_name", "")).strip(),
            "game_title": str(data.get("game_title", "")).strip(),
            "session_label": str(data.get("session_label", "")).strip(),
            "session_date": str(data.get("session_date", "")).strip(),
            "notes": str(data.get("notes", "")).strip(),
        })
        project["stream_session"] = session

        marker_defaults = project.get("marker_defaults") or {}
        try:
            marker_defaults["pre_roll"] = max(0.0, min(120.0, float(data.get("pre_roll", marker_defaults.get("pre_roll", 8.0)))))
        except (TypeError, ValueError):
            marker_defaults["pre_roll"] = 8.0
        try:
            marker_defaults["post_roll"] = max(1.0, min(240.0, float(data.get("post_roll", marker_defaults.get("post_roll", 22.0)))))
        except (TypeError, ValueError):
            marker_defaults["post_roll"] = 22.0
        project["marker_defaults"] = marker_defaults
        project["facecam_layout"] = normalize_facecam_layout(data.get("facecam_layout", project.get("facecam_layout")))

        save_reel_project(project_id)
        return jsonify({
            "stream_session": session,
            "marker_defaults": marker_defaults,
            "facecam_layout": project.get("facecam_layout"),
        })

    @app.route("/api/reel/preview-source", methods=["POST"])
    def reel_preview_source():
        data = request.get_json()
        url = clean_video_url(str(data.get("url", "")).strip())
        if not url:
            return jsonify({"error": "Missing video URL"}), 400
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
            return jsonify({"error": "Source moment import is available for remote video URLs only."}), 400

        url = clean_video_url(str(project.get("vod_url", "")).strip())
        if not url:
            return jsonify({"error": "Load a video URL first."}), 400

        try:
            info = _get_video_info(url)
        except Exception as e:
            return jsonify({"error": str(e)}), 400

        moments = extract_source_moments(info, project.get("vod_duration"))
        merge_project_source_moments(project, moments)

        if not moments:
            save_reel_project(project_id)
            return jsonify({
                "status": "empty",
                "moments": project.get("source_moments", []),
                "imported_clips": [],
                "imported_count": 0,
                "message": "No source moments were exposed by this video.",
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
            "moments": project.get("source_moments", []),
            "imported_clips": imported,
            "imported_count": len(imported),
            "message": f"Imported {len(imported)} source moment(s).",
        })

    @app.route("/api/reel/import-stream-markers", methods=["POST"])
    def reel_import_stream_markers():
        data = request.get_json() or {}
        project_id = data.get("project_id", "")
        project = load_reel_project(project_id)
        if not project:
            return jsonify({"error": "Project not found"}), 404

        markers_text = str(data.get("markers_text", "") or "")
        if not markers_text.strip():
            return jsonify({"error": "Paste one or more stream marker timestamps first."}), 400

        try:
            pre_roll = max(0.0, min(120.0, float(data.get("pre_roll", project.get("marker_defaults", {}).get("pre_roll", 8.0)))))
        except (TypeError, ValueError):
            pre_roll = float(project.get("marker_defaults", {}).get("pre_roll", 8.0) or 8.0)
        try:
            post_roll = max(1.0, min(240.0, float(data.get("post_roll", project.get("marker_defaults", {}).get("post_roll", 22.0)))))
        except (TypeError, ValueError):
            post_roll = float(project.get("marker_defaults", {}).get("post_roll", 22.0) or 22.0)

        markers = parse_stream_markers(
            markers_text,
            pre_roll=pre_roll,
            post_roll=post_roll,
            duration=project.get("vod_duration", 0),
        )
        if not markers:
            return jsonify({"error": "No valid marker timestamps were found. Use one timestamp per line, like 1:23:45 Big win."}), 400

        project["stream_markers"] = markers
        project["marker_defaults"] = {"pre_roll": pre_roll, "post_roll": post_roll}
        merge_project_source_moments(project, [
            moment for moment in project.get("source_moments", [])
            if str(moment.get("kind", "")).strip().lower() != "stream_marker"
        ])

        existing_keys = {
            (round(float(clip.get("start_sec", 0)), 1), round(float(clip.get("end_sec", 0)), 1))
            for clip in project.get("clips", [])
        }
        imported = []
        for marker in markers:
            key = (round(marker["start_sec"], 1), round(marker["end_sec"], 1))
            if key in existing_keys:
                continue
            clip = {
                "id": uuid.uuid4().hex[:6],
                "title": marker["title"],
                "note": f"Imported from stream marker: {marker.get('source_text', marker['start'])}",
                "source_kind": "stream_marker",
                "start": marker["start"],
                "end": marker["end"],
                "start_sec": marker["start_sec"],
                "end_sec": marker["end_sec"],
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
            "moments": project.get("source_moments", []),
            "stream_markers": markers,
            "imported_clips": imported,
            "imported_count": len(imported),
            "message": f"Imported {len(imported)} stream marker clip(s).",
        })

    @app.route("/api/reel/import-twitch-clips", methods=["POST"])
    def reel_import_twitch_clips():
        data = request.get_json() or {}
        project_id = data.get("project_id", "")
        project = load_reel_project(project_id)
        if not project:
            return jsonify({"error": "Project not found"}), 404

        raw_clips = data.get("clips") or []
        if not isinstance(raw_clips, list) or not raw_clips:
            return jsonify({"error": "No Twitch clips were provided."}), 400

        twitch_clips = extract_twitch_clip_moments(raw_clips, project.get("vod_duration", 0))
        if not twitch_clips:
            return jsonify({
                "error": "No Twitch clips could be mapped back to this VOD yet. Twitch may not have populated the VOD offset for those clips.",
            }), 400

        merge_project_source_moments(project, [
            *[
                moment for moment in project.get("source_moments", [])
                if str(moment.get("kind", "")).strip().lower() != "twitch_clip"
            ],
            *twitch_clips,
        ])

        existing_keys = {
            (round(float(clip.get("start_sec", 0)), 1), round(float(clip.get("end_sec", 0)), 1))
            for clip in project.get("clips", [])
        }
        imported = []
        for twitch_clip in twitch_clips:
            key = (round(twitch_clip["start_sec"], 1), round(twitch_clip["end_sec"], 1))
            if key in existing_keys:
                continue
            clip_url = twitch_clip.get("clip_url") or ""
            creator_name = twitch_clip.get("creator_name") or twitch_clip.get("creator_login") or "Twitch viewer"
            clip = {
                "id": uuid.uuid4().hex[:6],
                "title": twitch_clip["title"],
                "note": (
                    f"Imported from Twitch clip by {creator_name}."
                    + (f" {clip_url}" if clip_url else "")
                ).strip(),
                "source_kind": "twitch_clip",
                "start": twitch_clip["start"],
                "end": twitch_clip["end"],
                "start_sec": twitch_clip["start_sec"],
                "end_sec": twitch_clip["end_sec"],
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
            "moments": project.get("source_moments", []),
            "twitch_clips": twitch_clips,
            "imported_clips": imported,
            "imported_count": len(imported),
            "message": f"Imported {len(imported)} Twitch clip(s).",
        })

    @app.route("/api/reel/import-editor-feed", methods=["POST"])
    def reel_import_editor_feed():
        data = request.get_json() or {}
        project_id = data.get("project_id", "")
        project = load_reel_project(project_id)
        if not project:
            return jsonify({"error": "Project not found"}), 404

        raw_items = data.get("items") or []
        if not isinstance(raw_items, list) or not raw_items:
            return jsonify({"error": "No editor feed items were provided."}), 400

        feed_moments = extract_editor_feed_moments(raw_items, project.get("vod_duration", 0))
        if not feed_moments:
            return jsonify({"error": "No valid editor feed items could be imported."}), 400

        existing_feed_ids = {
            str(moment.get("feed_id", "")).strip()
            for moment in project.get("source_moments", [])
            if str(moment.get("feed_id", "")).strip()
        }
        imported_clips = []
        imported_feed_ids = []
        created_count = 0

        for moment in feed_moments:
            feed_id = str(moment.get("feed_id", "")).strip()
            if feed_id and feed_id in existing_feed_ids:
                continue

            clip, created = ensure_clip_for_moment(project, moment)
            if clip is not None and moment.get("note"):
                clip["note"] = str(moment.get("note", "")).strip()
            if created and clip is not None:
                imported_clips.append(clip)
                created_count += 1
            if feed_id:
                existing_feed_ids.add(feed_id)
                imported_feed_ids.append(feed_id)

        save_reel_project(project_id)
        return jsonify({
            "status": "ok",
            "imported_clips": imported_clips,
            "imported_count": len(imported_feed_ids),
            "imported_feed_ids": imported_feed_ids,
            "moments": project.get("source_moments", []),
            "message": (
                f"Imported {len(imported_feed_ids)} feed item(s)"
                + (f" and created {created_count} clip(s)." if created_count else ".")
            ),
        })

    @app.route("/api/reel/prepare-short", methods=["POST"])
    def reel_prepare_short():
        data = request.get_json() or {}
        project_id = data.get("project_id", "")
        project = load_reel_project(project_id)
        if not project:
            return jsonify({"error": "Project not found"}), 404

        recipe = resolve_shortform_recipe(data.get("preset"))
        project["shortform_recipe"] = recipe
        project["export_format_preset"] = recipe.get("format_preset", "shorts")
        project["caption_style"] = recipe.get("caption_style")

        clip = None
        clip_id = str(data.get("clip_id", "") or "").strip()
        if clip_id:
            clip = next((item for item in project.get("clips", []) if item.get("id") == clip_id), None)
            if not clip:
                return jsonify({"error": "Clip not found"}), 404

        created_clip = False
        if clip is None:
            moment = data.get("moment") or {}
            if not isinstance(moment, dict):
                return jsonify({"error": "A source moment is required to prepare a short."}), 400

            try:
                start_sec = float(moment.get("start_sec", 0))
                end_sec = float(moment.get("end_sec", 0))
            except (TypeError, ValueError):
                return jsonify({"error": "Invalid source moment timing."}), 400

            normalized_moment = build_moment(
                start_sec,
                end_sec,
                moment.get("title") or "Source Moment",
                str(moment.get("kind", moment.get("source_kind", "source_moment"))).strip().lower() or "source_moment",
                score=moment.get("score"),
                duration=project.get("vod_duration", 0),
                extra_fields={
                    key: value for key, value in moment.items()
                    if key not in {"title", "kind", "source_kind", "score", "start", "end", "start_sec", "end_sec"}
                },
            )
            if not normalized_moment:
                return jsonify({"error": "The selected source moment could not be prepared."}), 400

            clip, created_clip = ensure_clip_for_moment(project, normalized_moment)

        apply_short_recipe_to_clip(clip, recipe)
        save_reel_project(project_id)
        return jsonify({
            "status": "ok",
            "clip": clip,
            "created_clip": created_clip,
            "caption_style": project.get("caption_style"),
            "format_preset": project.get("export_format_preset", "shorts"),
            "moments": project.get("source_moments", []),
            "shortform_recipe": project.get("shortform_recipe"),
        })

    @app.route("/api/reel/bulk-prepare-shorts", methods=["POST"])
    def reel_bulk_prepare_shorts():
        data = request.get_json() or {}
        project_id = data.get("project_id", "")
        project = load_reel_project(project_id)
        if not project:
            return jsonify({"error": "Project not found"}), 404

        recipe = resolve_shortform_recipe(data.get("preset"))
        project["shortform_recipe"] = recipe
        project["export_format_preset"] = recipe.get("format_preset", "shorts")
        project["caption_style"] = recipe.get("caption_style")

        mode = str(data.get("mode", "all")).strip().lower() or "all"
        only_unprepared = bool(data.get("only_unprepared", True))
        allowed_modes = {"all", "twitch_clips", "markers", "non_prepared"}
        if mode not in allowed_modes:
            return jsonify({"error": "Invalid bulk prepare mode."}), 400

        prepared = []
        created_count = 0
        updated_count = 0
        skipped_count = 0

        for moment in project.get("source_moments", []):
            kind = str(moment.get("kind", "")).strip().lower()
            if mode == "twitch_clips" and kind != "twitch_clip":
                continue
            if mode == "markers" and kind != "stream_marker":
                continue

            clip = find_matching_clip(project, moment)
            if only_unprepared and clip is not None and bool(clip.get("short_ready")):
                skipped_count += 1
                continue

            clip, created = ensure_clip_for_moment(project, moment)
            if clip is None:
                skipped_count += 1
                continue

            if mode == "non_prepared" and bool(clip.get("short_ready")):
                skipped_count += 1
                continue

            apply_short_recipe_to_clip(clip, recipe)
            prepared.append(clip)
            if created:
                created_count += 1
            else:
                updated_count += 1

        if not prepared:
            return jsonify({
                "error": "No session moments matched this bulk short-prep action yet.",
                "moments": project.get("source_moments", []),
                "prepared_count": 0,
                "created_count": created_count,
                "updated_count": updated_count,
                "skipped_count": skipped_count,
                "shortform_recipe": project.get("shortform_recipe"),
            }), 400

        save_reel_project(project_id)
        return jsonify({
            "status": "ok",
            "moments": project.get("source_moments", []),
            "prepared_clips": prepared,
            "prepared_count": len(prepared),
            "created_count": created_count,
            "updated_count": updated_count,
            "skipped_count": skipped_count,
            "shortform_recipe": project.get("shortform_recipe"),
            "caption_style": project.get("caption_style"),
            "format_preset": project.get("export_format_preset", "shorts"),
            "message": f"Prepared {len(prepared)} session moment(s) as shorts.",
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
        if "fade_in" in data:
            try:
                clip["fade_in"] = max(0.0, min(5.0, float(data["fade_in"])))
            except (TypeError, ValueError):
                clip["fade_in"] = 0.0
        if "fade_out" in data:
            try:
                clip["fade_out"] = max(0.0, min(5.0, float(data["fade_out"])))
            except (TypeError, ValueError):
                clip["fade_out"] = 0.0
        if "include_in_longform" in data:
            clip["include_in_longform"] = bool(data.get("include_in_longform"))
        save_reel_project(project_id)
        return jsonify({"clip": clip})

    @app.route("/api/reel/bulk-set-longform-queue", methods=["POST"])
    def reel_bulk_set_longform_queue():
        data = request.get_json() or {}
        project_id = data.get("project_id", "")
        project = load_reel_project(project_id)
        if not project:
            return jsonify({"error": "Project not found"}), 404

        include_in_longform = bool(data.get("include_in_longform", True))
        target = str(data.get("target", "prepared")).strip().lower() or "prepared"
        if target not in {"prepared", "all"}:
            return jsonify({"error": "Invalid bulk queue target."}), 400

        updated = []
        for clip in project.get("clips", []):
            if target == "prepared" and not bool(clip.get("short_ready")):
                continue
            clip["include_in_longform"] = include_in_longform
            updated.append({
                "id": clip.get("id"),
                "title": clip.get("title"),
                "short_ready": bool(clip.get("short_ready")),
                "include_in_longform": bool(clip.get("include_in_longform")),
            })

        if not updated:
            return jsonify({"error": "No matching clips were available to update."}), 400

        save_reel_project(project_id)
        return jsonify({
            "status": "ok",
            "updated_count": len(updated),
            "include_in_longform": include_in_longform,
            "target": target,
            "updated_clips": updated,
            "queued_prepared_count": sum(
                1 for clip in project.get("clips", [])
                if bool(clip.get("short_ready")) and bool(clip.get("include_in_longform", True))
            ),
        })

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

    @app.route("/api/reel/rename", methods=["POST"])
    def reel_rename_project():
        data = request.get_json()
        project_id = data.get("project_id", "")
        project = load_reel_project(project_id)
        if not project:
            return jsonify({"error": "Project not found"}), 404
        new_title = str(data.get("title", "")).strip()
        if not new_title:
            return jsonify({"error": "Title cannot be empty"}), 400
        project["vod_title"] = new_title
        save_reel_project(project_id)
        return jsonify({"title": new_title})

    @app.route("/api/reel/create-longform-version", methods=["POST"])
    def reel_create_longform_version():
        data = request.get_json() or {}
        source_project_id = data.get("project_id", "")
        source_project = load_reel_project(source_project_id)
        if not source_project:
            return jsonify({"error": "Project not found"}), 404
        if str(source_project.get("project_role", "shortform")).strip().lower() == "longform":
            return jsonify({"error": "This project is already a longform edit."}), 400

        prepared_source_clips = [
            json.loads(json.dumps(clip))
            for clip in source_project.get("clips", [])
            if bool(clip.get("short_ready")) and bool(clip.get("include_in_longform", True))
        ]
        if not prepared_source_clips:
            return jsonify({
                "error": "Queue at least one prepared short for longform first. Use the Session Inbox to keep or skip each prepared short.",
            }), 400

        selected_keys = {moment_key(clip) for clip in prepared_source_clips}

        new_project_id = uuid.uuid4().hex[:10]
        _project_dir(new_project_id)

        cloned_project = json.loads(json.dumps(source_project))
        cloned_project["clips"] = []
        for clip in prepared_source_clips:
            original_clip_id = clip.get("id")
            clip["id"] = uuid.uuid4().hex[:6]
            clip["status"] = "pending"
            clip["filename"] = None
            clip["derived_from_clip_id"] = original_clip_id
            clip["derived_from_project_id"] = source_project_id
            cloned_project["clips"].append(clip)

        cloned_project["source_moments"] = [
            json.loads(json.dumps(moment))
            for moment in source_project.get("source_moments", [])
            if moment_key(moment) in selected_keys
        ]
        cloned_project["stream_markers"] = [
            json.loads(json.dumps(marker))
            for marker in source_project.get("stream_markers", [])
            if moment_key(marker) in selected_keys
        ]

        original_title = _project_title(source_project)
        requested_title = str(data.get("title", "")).strip()
        cloned_project.update({
            "project_id": new_project_id,
            "status": "created",
            "project_role": "longform",
            "derived_from_project_id": source_project_id,
            "vod_title": requested_title or f"{original_title} · Longform",
            "export_format_preset": "landscape",
            "captions": None,
            "speakers": {},
            "concat_file": None,
            "export_file": None,
            "created_at": _utc_now_iso(),
            "updated_at": _utc_now_iso(),
        })

        if cloned_project.get("source_type") == "file":
            vod_url = str(cloned_project.get("vod_url", "") or "")
            cloned_project["local_file_uploaded"] = bool(vod_url.startswith("local:") and Path(vod_url[6:]).exists())

        reel_projects[new_project_id] = cloned_project
        save_reel_project(new_project_id)
        return jsonify({
            "project_id": new_project_id,
            "title": cloned_project["vod_title"],
            "derived_from_project_id": source_project_id,
            "selection_mode": "prepared_shorts",
            "selected_clip_count": len(cloned_project["clips"]),
            "source_clip_count": len(source_project.get("clips", [])),
        })

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
            return jsonify({"error": "Local video not uploaded. Re-select the file, then try again."}), 400
        if not url:
            return jsonify({"error": "No video URL set"}), 400

        job_id = f"reel_dl_{project_id}"
        jobs[job_id] = {"status": "processing", "progress": 0, "stage": "Starting clip downloads..."}

        def run_clip_downloads():
            try:
                project_dir = DOWNLOADS_DIR / f"reel_{project_id}"
                project_dir.mkdir(parents=True, exist_ok=True)
                is_local_vod = url.startswith("local:")
                local_vod_path = Path(url[6:]) if is_local_vod else None
                if is_local_vod and (not local_vod_path or not local_vod_path.exists()):
                    jobs[job_id] = {"status": "error", "error": "Local video file is missing. Upload it again."}
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
                    for i, clip in enumerate(downloaded):
                        filter_parts.append(f"[{i}:v]scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=30[v{i}]")
                        fade_in = float(clip.get("fade_in") or 0)
                        fade_out = float(clip.get("fade_out") or 0)
                        clip_dur = float(clip.get("end_sec", 0)) - float(clip.get("start_sec", 0))
                        audio_chain = "aformat=sample_fmts=s16:sample_rates=48000:channel_layouts=stereo"
                        if fade_in > 0:
                            audio_chain += f",afade=t=in:st=0:d={fade_in:.2f}"
                        if fade_out > 0 and clip_dur > fade_out:
                            audio_chain += f",afade=t=out:st={max(0.0, clip_dur - fade_out):.3f}:d={fade_out:.2f}"
                        filter_parts.append(f"[{i}:a]{audio_chain}[a{i}]")
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

                # Invalidate cached waveform so it gets regenerated for the new concat
                waveform_cache = project_dir / "concat_waveform.png"
                if waveform_cache.exists():
                    waveform_cache.unlink(missing_ok=True)

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

    # ── Detect moments (silence/scene) ────────────────────────────

    @app.route("/api/reel/detect-moments", methods=["POST"])
    def reel_detect_moments():
        import re as _re
        data = request.get_json()
        project_id = data.get("project_id", "")
        project = load_reel_project(project_id)
        if not project:
            return jsonify({"error": "Project not found"}), 404

        url = project.get("vod_url", "")
        if url and url.startswith("local:"):
            local_path = Path(url[6:])
            if not local_path.exists():
                return jsonify({"error": "Local video file not found"}), 404
        elif project.get("concat_file") and Path(project["concat_file"]).exists():
            local_path = Path(project["concat_file"])
        else:
            return jsonify({"error": "No local video available. Upload a local file or download/stitch clips first."}), 400

        job_id = f"reel_detect_{project_id}"
        jobs[job_id] = {"status": "processing", "progress": 0, "stage": "Detecting quiet moments..."}

        def run_detect():
            try:
                env = get_env()
                jobs[job_id] = {"status": "processing", "progress": 20, "stage": "Detecting silence..."}

                # Run silencedetect — output is in stderr; stdout is discarded via null muxer
                r = run_subprocess(
                    [FFMPEG, "-i", str(local_path),
                     "-af", "silencedetect=noise=-35dB:d=0.5",
                     "-f", "null", "-"],
                    timeout=300,
                )
                output = r.stderr or ""

                silence_starts = [float(m) for m in _re.findall(r"silence_start: ([0-9.]+)", output)]
                silence_ends   = [float(m) for m in _re.findall(r"silence_end: ([0-9.]+)", output)]

                jobs[job_id] = {"status": "processing", "progress": 60, "stage": "Detecting scene cuts..."}

                # Also run blackdetect to find scene transitions
                r2 = run_subprocess(
                    [FFMPEG, "-i", str(local_path),
                     "-vf", "blackdetect=d=0.1:pix_th=0.1",
                     "-an", "-f", "null", "-"],
                    timeout=300,
                )
                output2 = r2.stderr or ""
                black_starts = [float(m) for m in _re.findall(r"black_start:([0-9.]+)", output2)]
                black_ends   = [float(m) for m in _re.findall(r"black_end:([0-9.]+)", output2)]
                scene_cuts = [round((s + e) / 2, 2) for s, e in zip(black_starts, black_ends)]

                # Build non-silent windows (the interesting moments)
                duration = probe_media_duration(str(local_path))
                silence_intervals = list(zip(silence_starts, silence_ends + [duration]))

                moments = []
                prev_end = 0.0
                for s_start, s_end in silence_intervals:
                    seg_start = prev_end
                    seg_end   = s_start
                    if seg_end - seg_start >= 2.0:
                        moments.append({
                            "start": round(seg_start, 2),
                            "end":   round(seg_end, 2),
                            "type":  "speech",
                        })
                    prev_end = s_end

                if duration - prev_end >= 2.0:
                    moments.append({"start": round(prev_end, 2), "end": round(duration, 2), "type": "speech"})

                jobs[job_id] = {
                    "status": "complete",
                    "progress": 100,
                    "stage": f"Found {len(moments)} moment(s), {len(scene_cuts)} scene cut(s).",
                    "moments": moments,
                    "scene_cuts": scene_cuts,
                }
            except Exception as e:
                jobs[job_id] = {"status": "error", "error": str(e)}

        threading.Thread(target=run_detect, daemon=True).start()
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
                project.get("vod_title") or "Local source video",
                "source",
                exists=source_ready,
                detail="Uploaded local source video" if source_ready else "Selected locally, not uploaded yet",
                preview_url=f"/api/reel/serve-vod/{project_id}" if source_ready else None,
            ))
        elif vod_url:
            items.append(_asset_item(
                "source",
                project.get("vod_title") or "Remote source video",
                "source",
                detail="Remote source URL",
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
                detail="Finished video export",
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

    @app.route("/api/reel/waveform/<project_id>")
    def reel_waveform(project_id):
        """Return a waveform PNG for the stitched concat video."""
        import subprocess as _sp
        import platform as _platform
        project = load_reel_project(project_id)
        if not project or not project.get("concat_file"):
            return jsonify({"error": "No concat file available"}), 404
        concat_path = project["concat_file"]
        if not Path(concat_path).exists():
            return jsonify({"error": "Concat file not found"}), 404

        project_dir = DOWNLOADS_DIR / f"reel_{project_id}"
        waveform_path = project_dir / "concat_waveform.png"
        if not waveform_path.exists():
            cmd = [FFMPEG, "-y", "-i", concat_path,
                   "-filter_complex", "showwavespic=s=1200x80:colors=#56a3ff|#3a7acc",
                   "-frames:v", "1", str(waveform_path)]
            extra = {}
            if _platform.system() == "Windows":
                extra["creationflags"] = _sp.CREATE_NO_WINDOW
            result = _sp.run(cmd, capture_output=True, timeout=30, **extra)
            if result.returncode != 0 or not waveform_path.exists():
                return jsonify({"error": "Waveform generation failed"}), 500
        return send_file(str(waveform_path), mimetype="image/png")

    @app.route("/api/reel/serve-vod/<project_id>")
    def reel_serve_vod(project_id):
        project = load_reel_project(project_id)
        if not project or project.get("source_type") != "file":
            return jsonify({"error": "No local video available"}), 404

        vod_url = str(project.get("vod_url", ""))
        if not vod_url.startswith("local:"):
            return jsonify({"error": "Local video file needs to be selected again"}), 404

        import mimetypes
        vod_path = Path(vod_url[6:])
        if not vod_path.exists():
            return jsonify({"error": "Local video file is missing"}), 404

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
            return jsonify({"error": "No video source available"}), 400

        input_source = None
        if vod_url.startswith("local:"):
            local_path = Path(vod_url[6:])
            if not local_path.exists():
                return jsonify({"error": "Local video file is missing"}), 404
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
        data = request.get_json() or {}
        project_id = data.get("project_id", "")
        project = load_reel_project(project_id)
        if not project:
            return jsonify({"error": "Project not found"}), 404

        concat_file = project.get("concat_file")
        if not concat_file or not Path(concat_file).exists():
            return jsonify({"error": "Download and stitch clips first"}), 400

        format_presets = {
            "shorts": {
                "size": (1080, 1920),
                "label": "Shorts / TikTok / Reels",
                "stage": "Rendering vertical video...",
                "suffix": "shorts",
                "crf": 20,
            },
            "portrait_feed": {
                "size": (1080, 1350),
                "label": "4:5 feed",
                "stage": "Rendering 4:5 feed video...",
                "suffix": "portrait-feed",
                "crf": 20,
            },
            "square": {
                "size": (1080, 1080),
                "label": "square",
                "stage": "Rendering square video...",
                "suffix": "square",
                "crf": 20,
            },
            "landscape": {
                "size": (1920, 1080),
                "label": "16:9 landscape",
                "stage": "Rendering landscape video...",
                "suffix": "landscape",
                "crf": 20,
            },
        }
        allowed_resolutions = {"1080": (1080, 1920), "1440": (1440, 2560), "2160": (2160, 3840)}
        requested_preset = str(
            data.get("format_preset") or project.get("export_format_preset") or "shorts"
        ).strip().lower()
        preset = format_presets.get(requested_preset)

        if preset is not None:
            output_width, output_height = preset["size"]
            preset_label = preset["label"]
            render_stage = preset["stage"]
            filename_suffix = preset["suffix"]
            default_crf = preset["crf"]
            resolved_format_preset = requested_preset
        else:
            resolution = str(data.get("resolution", "1080")).strip()
            if resolution not in allowed_resolutions:
                return jsonify({"error": "Invalid export format preset"}), 400
            output_width, output_height = allowed_resolutions[resolution]
            preset_label = f"{output_width}x{output_height} vertical"
            render_stage = "Rendering vertical video..."
            filename_suffix = f"{output_width}x{output_height}"
            default_crf = 20
            resolved_format_preset = str(project.get("export_format_preset", "shorts")).strip().lower() or "shorts"

        try:
            export_crf = int(data.get("crf", default_crf))
        except (TypeError, ValueError):
            export_crf = default_crf
        if export_crf not in {18, 20, 23, 26}:
            export_crf = default_crf

        burn_captions = bool(data.get("burn_captions", True))
        input_width, input_height = probe_media_dimensions(Path(concat_file))
        export_profile = resolve_effective_export_profile(
            project,
            output_width,
            output_height,
            input_width=input_width or 1920,
            input_height=input_height or 1080,
        )
        profile_label = export_profile.get("label", "Gameplay Focus")
        render_stage = f"{render_stage.rstrip('.')} with {export_profile.get('render_label', profile_label)}..."

        job_id = f"reel_export_{project_id}"
        jobs[job_id] = {
            "status": "processing",
            "progress": 0,
            "stage": f"Preparing {preset_label} export with {profile_label} composition...",
        }

        def run_export():
            try:
                from captions import generate_ass_subtitles

                env = get_env()
                project_dir = DOWNLOADS_DIR / f"reel_{project_id}"
                project_dir.mkdir(parents=True, exist_ok=True)

                jobs[job_id] = {"status": "processing", "progress": 10, "stage": "Preparing captions..."}

                ass_path = None
                if burn_captions and project.get("captions") and project.get("speakers"):
                    ass_content = generate_ass_subtitles(
                        project["captions"]["words"],
                        project["speakers"],
                        play_res_x=output_width,
                        play_res_y=output_height,
                        style=export_profile.get("caption_style"),
                    )
                    ass_path = project_dir / f"captions_export_{output_width}x{output_height}.ass"
                    with open(ass_path, "w", encoding="utf-8") as f:
                        f.write(ass_content)

                jobs[job_id] = {"status": "processing", "progress": 35, "stage": render_stage}

                filter_parts = build_export_filter_graph(output_width, output_height, export_profile, ass_path=ass_path)

                output_file = get_output_dir() / f"video_{project_id}_{filename_suffix}.mp4"
                run_ffmpeg([
                    FFMPEG,
                    "-i", str(Path(concat_file)),
                    "-filter_complex", ";".join(filter_parts),
                    "-map", "[outv]",
                    "-map", "0:a?",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", "30",
                    "-preset", "medium", "-crf", str(export_crf),
                    "-c:a", "aac", "-b:a", "192k",
                    "-movflags", "+faststart",
                    "-y", str(output_file),
                ], env=env, timeout=1800)

                project["export_file"] = str(output_file)
                project["export_format_preset"] = resolved_format_preset
                project["last_export_profile"] = export_profile.get("key")
                project["status"] = "export_ready"
                save_reel_project(project_id)

                file_size_mb = round(output_file.stat().st_size / 1_048_576, 1) if output_file.exists() else 0
                jobs[job_id] = {
                    "status": "complete",
                    "progress": 100,
                    "stage": f"Video export complete! ({file_size_mb} MB)",
                    "filename": output_file.name,
                    "file_size_mb": file_size_mb,
                }
            except Exception as e:
                jobs[job_id] = {"status": "error", "error": str(e)}

        thread = threading.Thread(target=run_export, daemon=True)
        thread.start()
        return jsonify({
            "job_id": job_id,
            "composition_profile": export_profile.get("key"),
            "composition_label": profile_label,
        })

    @app.route("/api/reel/download/<project_id>")
    def reel_download(project_id):
        project = load_reel_project(project_id)
        if not project or not project.get("export_file"):
            return jsonify({"error": "No exported video available"}), 404

        export_path = Path(project["export_file"])
        if not export_path.exists():
            return jsonify({"error": "Export file not found"}), 404

        return send_file(str(export_path), as_attachment=True, download_name=export_path.name)
