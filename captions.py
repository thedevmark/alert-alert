"""Caption pipeline — speech-to-text, speaker diarization, and ASS subtitle generation."""

import json
import os
import platform
import subprocess
import threading
from pathlib import Path

from flask import request, jsonify

# Lazy-loaded model references
_whisper_models = {}
_whisper_lock = threading.Lock()
_diarize_pipeline = None
_diarize_lock = threading.Lock()

# Default speaker colors (gold, blue, coral, green, pink)
DEFAULT_SPEAKER_COLORS = ["#FFD700", "#00BFFF", "#FF6B6B", "#7CFC00", "#FF69B4"]
DEFAULT_CAPTION_STYLE = {
    "preset": "pathos_clean",
    "font_family": "Arial",
    "font_scale": 1.0,
    "max_words": 6,
    "margin_v": 120,
    "outline": 4,
    "shadow": 2,
    "background_opacity": 50,
    "all_caps": False,
    "karaoke": True,
    "bold": True,
}
CAPTION_STYLE_PRESETS = {
    "pathos_clean": {
        "font_family": "Arial",
        "font_scale": 1.0,
        "max_words": 6,
        "margin_v": 120,
        "outline": 4,
        "shadow": 2,
        "background_opacity": 50,
        "all_caps": False,
        "karaoke": True,
        "bold": True,
    },
    "broadcast_bold": {
        "font_family": "Impact",
        "font_scale": 1.16,
        "max_words": 5,
        "margin_v": 136,
        "outline": 5,
        "shadow": 1,
        "background_opacity": 36,
        "all_caps": True,
        "karaoke": True,
        "bold": True,
    },
    "minimal_clean": {
        "font_family": "Tahoma",
        "font_scale": 0.9,
        "max_words": 7,
        "margin_v": 108,
        "outline": 2,
        "shadow": 1,
        "background_opacity": 18,
        "all_caps": False,
        "karaoke": False,
        "bold": False,
    },
}


def normalize_caption_style(style=None):
    """Merge caller-provided caption style with defaults and presets."""
    resolved = dict(DEFAULT_CAPTION_STYLE)
    if isinstance(style, dict):
        preset_name = str(style.get("preset", resolved["preset"])).strip() or resolved["preset"]
        if preset_name in CAPTION_STYLE_PRESETS:
            resolved.update(CAPTION_STYLE_PRESETS[preset_name])
        resolved.update({key: value for key, value in style.items() if value is not None})

    resolved["preset"] = str(resolved.get("preset", "pathos_clean")).strip() or "pathos_clean"
    resolved["font_family"] = str(resolved.get("font_family", "Arial")).strip() or "Arial"
    resolved["font_scale"] = max(0.65, min(1.8, float(resolved.get("font_scale", 1.0) or 1.0)))
    resolved["max_words"] = max(2, min(12, int(resolved.get("max_words", 6) or 6)))
    resolved["margin_v"] = max(40, min(260, int(resolved.get("margin_v", 120) or 120)))
    resolved["outline"] = max(0, min(8, float(resolved.get("outline", 4) or 0)))
    resolved["shadow"] = max(0, min(8, float(resolved.get("shadow", 2) or 0)))
    resolved["background_opacity"] = max(0, min(100, int(resolved.get("background_opacity", 50) or 0)))
    resolved["all_caps"] = bool(resolved.get("all_caps", False))
    resolved["karaoke"] = bool(resolved.get("karaoke", True))
    resolved["bold"] = bool(resolved.get("bold", True))
    return resolved


def _get_whisper_model(model_size="large-v3", cache_dir=None):
    """Lazy-load and cache the faster-whisper model."""
    cache_key = (model_size, str(cache_dir or ""))
    if cache_key in _whisper_models:
        return _whisper_models[cache_key]
    with _whisper_lock:
        if cache_key in _whisper_models:
            return _whisper_models[cache_key]
        from faster_whisper import WhisperModel
        try:
            import torch
            device = "cuda" if torch.cuda.is_available() else "cpu"
            compute_type = "float16" if device == "cuda" else "int8"
        except ImportError:
            device = "cpu"
            compute_type = "int8"

        _whisper_models[cache_key] = WhisperModel(
            model_size,
            device=device,
            compute_type=compute_type,
            download_root=cache_dir,
        )
        return _whisper_models[cache_key]


def _get_diarize_pipeline(hf_token):
    """Lazy-load and cache the pyannote speaker diarization pipeline."""
    global _diarize_pipeline
    if _diarize_pipeline is not None:
        return _diarize_pipeline
    with _diarize_lock:
        if _diarize_pipeline is not None:
            return _diarize_pipeline
        from pyannote.audio import Pipeline
        _diarize_pipeline = Pipeline.from_pretrained(
            "pyannote/speaker-diarization-3.1",
            use_auth_token=hf_token,
        )
        try:
            import torch
            if torch.cuda.is_available():
                _diarize_pipeline.to(torch.device("cuda"))
        except ImportError:
            pass
        return _diarize_pipeline


def group_words_into_lines(words, max_words=6):
    """Group words into display lines, respecting sentence boundaries."""
    lines = []
    current_line = []

    def flush_current_line():
        if not current_line:
            return
        lines.append({
            "words": list(current_line),
            "speaker": current_line[0].get("speaker", "SPEAKER_0"),
            "start": current_line[0]["start"],
            "end": current_line[-1]["end"],
            "enabled": bool(current_line[0].get("enabled", True)),
        })
        current_line.clear()

    for word in words:
        if current_line:
            current_enabled = bool(current_line[0].get("enabled", True))
            word_enabled = bool(word.get("enabled", True))
            current_speaker = current_line[0].get("speaker", "SPEAKER_0")
            word_speaker = word.get("speaker", "SPEAKER_0")
            if word_enabled != current_enabled or word_speaker != current_speaker:
                flush_current_line()

        current_line.append(word)
        is_sentence_end = word["text"].rstrip().endswith((".", "!", "?", ","))
        if len(current_line) >= max_words or is_sentence_end:
            flush_current_line()
    flush_current_line()
    return lines


def format_ass_time(seconds):
    """Convert seconds to ASS timestamp H:MM:SS.cc"""
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    cs = int((seconds % 1) * 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def generate_ass_subtitles(words, speakers, play_res_x=1080, play_res_y=1920, style=None):
    """Generate ASS subtitle content with per-speaker colors and word-level timing."""
    style_config = normalize_caption_style(style)

    # ASS header
    ass = f"""[Script Info]
Title: Reel Captions
ScriptType: v4.00+
PlayResX: {play_res_x}
PlayResY: {play_res_y}
WrapStyle: 0

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
"""

    # Create a style per speaker
    for speaker_id, speaker_data in speakers.items():
        hex_color = speaker_data.get("color", "#FFFFFF").lstrip("#")
        if len(hex_color) == 6:
            r, g, b = int(hex_color[0:2], 16), int(hex_color[2:4], 16), int(hex_color[4:6], 16)
        else:
            r, g, b = 255, 255, 255
        # ASS uses &HBBGGRR& format (BGR, not RGB)
        ass_color = f"&H00{b:02X}{g:02X}{r:02X}&"
        outline_color = "&H00000000&"
        alpha = int(round((100 - style_config["background_opacity"]) * 255 / 100))
        back_color = f"&H{alpha:02X}000000&"
        style_name = speaker_id.replace(" ", "_")
        font_size = int(72 * style_config["font_scale"] * play_res_x / 1080)
        bold_flag = -1 if style_config["bold"] else 0
        ass += (
            f"Style: {style_name},{style_config['font_family']},{font_size},"
            f"{ass_color},&H000000FF,{outline_color},{back_color},"
            f"{bold_flag},0,0,0,100,100,0,0,1,{style_config['outline']},{style_config['shadow']},2,30,30,{style_config['margin_v']},1\n"
        )

    ass += "\n[Events]\n"
    ass += "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"

    # Group words into lines
    lines = group_words_into_lines(words, max_words=style_config["max_words"])

    for line in lines:
        if not line.get("enabled", True):
            continue

        speaker = line["speaker"]
        style = speaker.replace(" ", "_")
        start = format_ass_time(line["start"])
        end = format_ass_time(line["end"])

        if style_config["karaoke"]:
            text_parts = []
            for word in line["words"]:
                duration_cs = max(1, int((word["end"] - word["start"]) * 100))
                rendered_word = word["text"].upper() if style_config["all_caps"] else word["text"]
                text_parts.append(f"{{\\kf{duration_cs}}}{rendered_word}")
            text = " ".join(text_parts)
        else:
            text = " ".join(
                word["text"].upper() if style_config["all_caps"] else word["text"]
                for word in line["words"]
            )

        ass += f"Dialogue: 0,{start},{end},{style},,0,0,0,,{text}\n"

    return ass


def register_caption_routes(app):
    """Register caption-related API routes."""

    from app import (
        jobs,
        DOWNLOADS_DIR,
        RUNTIME_DIR,
        FFMPEG,
        get_env,
        run_ffmpeg,
    )
    from reel import load_reel_project, save_reel_project

    WHISPER_CACHE_DIR = str(RUNTIME_DIR / "whisper-models")

    # ── Check ML dependencies ──────────────────────────────────────

    @app.route("/api/reel/check-ml-deps")
    def reel_check_ml_deps():
        from app import get_caption_dependency_status
        return jsonify(get_caption_dependency_status())

    # ── Transcription ──────────────────────────────────────────────

    @app.route("/api/reel/transcribe", methods=["POST"])
    def reel_transcribe():
        data = request.get_json()
        project_id = data.get("project_id", "")
        project = load_reel_project(project_id)
        if not project:
            return jsonify({"error": "Project not found"}), 404

        if not project.get("concat_file"):
            return jsonify({"error": "Download and stitch clips first"}), 400

        language = data.get("language", "en")
        model_size = data.get("model_size", "large-v3")
        hf_token = data.get("hf_token")

        job_id = f"transcribe_{project_id}"
        jobs[job_id] = {"status": "processing", "progress": 0, "stage": "Preparing audio..."}

        def run_transcription():
            try:
                project_dir = DOWNLOADS_DIR / f"reel_{project_id}"
                concat_file = project["concat_file"]
                env = get_env()

                # Extract audio to WAV for Whisper
                audio_wav = str(project_dir / "transcript_audio.wav")
                jobs[job_id] = {"status": "processing", "progress": 5, "stage": "Extracting audio..."}

                run_ffmpeg([
                    FFMPEG, "-i", concat_file,
                    "-vn", "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1",
                    "-y", audio_wav,
                ], env=env, timeout=120)

                # Run Whisper STT
                jobs[job_id] = {"status": "processing", "progress": 10, "stage": f"Loading speech model ({model_size})..."}

                model = _get_whisper_model(model_size=model_size, cache_dir=WHISPER_CACHE_DIR)

                jobs[job_id] = {"status": "processing", "progress": 15, "stage": "Transcribing speech..."}

                segments, info = model.transcribe(
                    audio_wav,
                    language=language,
                    word_timestamps=True,
                    vad_filter=True,
                    vad_parameters={"min_silence_duration_ms": 500},
                )

                # Collect word-level data
                words = []
                for segment in segments:
                    if segment.words:
                        for word in segment.words:
                            words.append({
                                "text": word.word.strip(),
                                "start": round(word.start, 3),
                                "end": round(word.end, 3),
                                "confidence": round(word.probability, 3),
                                "speaker": "SPEAKER_0",
                            })

                    # Update progress based on segment timing
                    if info.duration and info.duration > 0:
                        pct = min(55, 15 + int((segment.end / info.duration) * 40))
                        jobs[job_id] = {"status": "processing", "progress": pct, "stage": f"Transcribing... ({len(words)} words)"}

                if not words:
                    jobs[job_id] = {
                        "status": "complete",
                        "progress": 100,
                        "stage": "No speech detected.",
                        "word_count": 0,
                        "speaker_count": 0,
                    }
                    project["captions"] = {"words": [], "language": language, "duration": 0}
                    project["speakers"] = {}
                    project["caption_style"] = normalize_caption_style(project.get("caption_style"))
                    project["export_file"] = None
                    project["status"] = "captions_ready"
                    save_reel_project(project_id)
                    return

                # Speaker diarization (optional)
                if hf_token:
                    jobs[job_id] = {"status": "processing", "progress": 60, "stage": "Identifying speakers..."}
                    try:
                        pipeline = _get_diarize_pipeline(hf_token)

                        jobs[job_id] = {"status": "processing", "progress": 65, "stage": "Running speaker diarization..."}
                        diarization = pipeline(audio_wav)

                        # Build speaker segments for fast lookup
                        speaker_segments = []
                        for turn, _, speaker in diarization.itertracks(yield_label=True):
                            speaker_segments.append((turn.start, turn.end, speaker))

                        # Assign speaker to each word based on overlap
                        for word in words:
                            word_mid = (word["start"] + word["end"]) / 2
                            best_speaker = "SPEAKER_0"
                            for seg_start, seg_end, speaker in speaker_segments:
                                if seg_start <= word_mid <= seg_end:
                                    best_speaker = speaker
                                    break
                            word["speaker"] = best_speaker

                        jobs[job_id] = {"status": "processing", "progress": 85, "stage": "Finalizing..."}
                    except Exception as e:
                        print(f"Speaker diarization failed (non-fatal): {e}")
                        # Continue without diarization — all words stay as SPEAKER_0

                # Auto-detect unique speakers and assign colors
                unique_speakers = sorted(set(w["speaker"] for w in words))
                speakers = {}
                for i, s in enumerate(unique_speakers):
                    speakers[s] = {
                        "name": f"Speaker {i + 1}",
                        "color": DEFAULT_SPEAKER_COLORS[i % len(DEFAULT_SPEAKER_COLORS)],
                    }

                # Store in project
                project["captions"] = {
                    "words": words,
                    "language": language,
                    "duration": getattr(info, "duration", 0),
                }
                project["speakers"] = speakers
                project["caption_style"] = normalize_caption_style(project.get("caption_style"))
                project["export_file"] = None
                project["status"] = "captions_ready"

                # Save ASS file
                ass_content = generate_ass_subtitles(
                    words,
                    speakers,
                    style=project.get("caption_style"),
                )
                ass_path = project_dir / "captions.ass"
                with open(str(ass_path), "w", encoding="utf-8") as f:
                    f.write(ass_content)
                save_reel_project(project_id)

                jobs[job_id] = {
                    "status": "complete",
                    "progress": 100,
                    "stage": "Transcription complete!",
                    "word_count": len(words),
                    "speaker_count": len(unique_speakers),
                }

            except ImportError as e:
                module = str(e).replace("No module named ", "").strip("'")
                jobs[job_id] = {
                    "status": "error",
                    "error": f"Missing dependency: {module}. Install with: pip install {module}",
                }
            except Exception as e:
                jobs[job_id] = {"status": "error", "error": str(e)}

        thread = threading.Thread(target=run_transcription, daemon=True)
        thread.start()
        return jsonify({"job_id": job_id})

    # ── Get/update captions ────────────────────────────────────────

    @app.route("/api/reel/captions/<project_id>")
    def reel_get_captions(project_id):
        project = load_reel_project(project_id)
        if not project or not project.get("captions"):
            return jsonify({"error": "No captions available"}), 404
        return jsonify({
            "words": project["captions"]["words"],
            "speakers": project["speakers"],
            "language": project["captions"].get("language", "en"),
            "lines": group_words_into_lines(
                project["captions"]["words"],
                max_words=normalize_caption_style(project.get("caption_style")).get("max_words", 6),
            ),
            "style": normalize_caption_style(project.get("caption_style")),
            "style_presets": CAPTION_STYLE_PRESETS,
        })

    @app.route("/api/reel/captions/<project_id>", methods=["PUT"])
    def reel_update_captions(project_id):
        project = load_reel_project(project_id)
        if not project:
            return jsonify({"error": "Project not found"}), 404

        data = request.get_json()

        if "words" in data:
            if not project.get("captions"):
                project["captions"] = {"words": [], "language": "en", "duration": 0}
            project["captions"]["words"] = data["words"]

        if "speakers" in data:
            project["speakers"] = data["speakers"]
        if "style" in data:
            project["caption_style"] = normalize_caption_style(data.get("style"))
        else:
            project["caption_style"] = normalize_caption_style(project.get("caption_style"))

        project["export_file"] = None
        project["status"] = "captions_ready"

        # Regenerate ASS file
        if project.get("captions") and project.get("speakers"):
            project_dir = DOWNLOADS_DIR / f"reel_{project_id}"
            ass_content = generate_ass_subtitles(
                project["captions"]["words"],
                project["speakers"],
                style=project.get("caption_style"),
            )
            ass_path = project_dir / "captions.ass"
            with open(str(ass_path), "w", encoding="utf-8") as f:
                f.write(ass_content)
        save_reel_project(project_id)

        return jsonify({"status": "saved"})
