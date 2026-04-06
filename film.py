"""Film Lab pipeline — photo style processing with mathematical film rendering, grain, and halation."""

import io
import json
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter
from flask import request, jsonify, send_file

# ── Built-in presets ─────────────────────────────────────────────────────────
# Built-ins live in code only — cannot be overwritten by the user.
#
# Derived from a scan of 45 shoot folders / 2,602 raw-to-edited pairs
# (D:\OneDrive\_Photo Library, 2023-2026, DxO FilmPack 6/7).
# Two measured scene families:
#
#   Ambient Film — daylight/ambient sets:
#     avg luminance −6.9 units, blue reduced most (B > G > R), sat +4.1pp,
#     shadow crush +3.1pp, contrast essentially flat.
#     → darker, denser, warm-biased, moodier blacks.
#
#   Flash Film — event/flash sets:
#     avg luminance +9.9 units, all channels lift equally (no forced warmth),
#     contrast +8.9, sat −1.3pp, shadow barely touched.
#     → brighter, punchier, colour-neutral, highlights preserved.

BUILTIN_PRESETS = {
    # ── Your signature looks ──────────────────────────────────────────────────
    "Ambient Film": {
        # Darker, denser, warm. Daylight, outdoor, low-key scenes.
        # Measured: luma −0.027, B channel compressed most, sat up, blacks crushed.
        "grade_strength":     0.85,
        "exposure_bias":     -0.025,
        "contrast_strength":  0.00,
        "grain_intensity":    0.065,
        "grain_size":         3,
        "halation_intensity": 0.50,
        "halation_radius":    38,
    },
    "Flash Film": {
        # Brighter, punchier, colour-neutral. Events, nightlife, flash.
        # Measured: luma +0.039, all channels lift equally, contrast +0.20, sat held back.
        "grade_strength":     0.50,
        "exposure_bias":      0.040,
        "contrast_strength":  0.20,
        "grain_intensity":    0.040,
        "grain_size":         2,
        "halation_intensity": 0.30,
        "halation_radius":    45,
    },
    # ── Fuji Gold 400 reference ───────────────────────────────────────────────
    "Fuji Gold 400 — Standard": {
        "grade_strength":     0.85,
        "exposure_bias":      0.00,
        "contrast_strength":  0.00,
        "grain_intensity":    0.055,
        "grain_size":         3,
        "halation_intensity": 0.45,
        "halation_radius":    38,
    },
    "Fuji Gold 400 — Subtle": {
        "grade_strength":     0.50,
        "exposure_bias":      0.00,
        "contrast_strength":  0.00,
        "grain_intensity":    0.030,
        "grain_size":         2,
        "halation_intensity": 0.20,
        "halation_radius":    25,
    },
}

RAW_EXTENSIONS   = {".arw", ".cr2", ".cr3", ".nef", ".orf", ".raf", ".dng", ".rw2", ".pef"}
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".webp", ".bmp"}
MAX_LONG_EDGE    = 6000


# ── Exposure and contrast ─────────────────────────────────────────────────────

def apply_exposure(img: np.ndarray, bias: float) -> np.ndarray:
    """
    Simple additive exposure shift applied before the colour grade.
    bias > 0 → brighter (Flash Film), bias < 0 → darker (Ambient Film).
    Kept linear so it interacts cleanly with the subsequent colour grade.
    """
    if bias == 0.0:
        return img
    return np.clip(img + bias, 0.0, 1.0)


def apply_contrast(img: np.ndarray, strength: float) -> np.ndarray:
    """
    S-curve contrast around the midpoint 0.5.
    strength > 0 → more contrast (Flash Film punch),
    strength < 0 → flatter (not used in current presets but available).
    Applied after the colour grade so it shapes the already-graded tones.
    """
    if strength == 0.0:
        return img
    return np.clip(0.5 + (img - 0.5) * (1.0 + strength), 0.0, 1.0)


# ── Fuji Gold 400 colour rendering ────────────────────────────────────────────
#
# Implemented from the film's documented characteristics — no LUT file needed.
#
# Key traits of Fuji Gold 400:
#   • Lifted shadows (filmic toe — blacks never reach pure black)
#   • Green-cyan crossover in deep shadows (Fuji's base colour bleed)
#   • Warm yellow-orange push in midtones
#   • Orange-red saturation boost (makes skin tones and warm colours pop)
#   • Blue channel compression — less blue overall = warmer image
#   • Smooth highlight rolloff with a slight warm cast
#
# All operations are done at full strength; `strength` blends the result
# back against the original, giving smooth 0–1 control with no artefacts.

def apply_film_color(img: np.ndarray, strength: float) -> np.ndarray:
    """
    Apply Fuji Gold 400 colour rendering to an (H, W, 3) float32 image.
    strength = 0 → untouched original; strength = 1 → full film look.
    """
    if strength <= 0:
        return img

    # Luminance of the original (used for tonal masks throughout)
    luma = 0.299 * img[:, :, 0] + 0.587 * img[:, :, 1] + 0.114 * img[:, :, 2]

    result = img.copy()

    # 1. Lift blacks — film base fog raises the shadow floor
    shadow_lift = 0.022
    result = result * (1.0 - shadow_lift * 2.0) + shadow_lift

    # 2. Per-channel tone response
    #    Red  γ < 1  → brighter / warmer
    #    Green γ slightly < 1 → subtle midtone lift (Fuji's green bias)
    #    Blue  γ > 1  → darker / compressed (cooler channel suppressed)
    result[:, :, 0] = np.power(result[:, :, 0].clip(1e-6, 1.0), 0.88)
    result[:, :, 1] = np.power(result[:, :, 1].clip(1e-6, 1.0), 0.96)
    result[:, :, 2] = np.power(result[:, :, 2].clip(1e-6, 1.0), 1.18)

    # 3. Shadow crossover — green-cyan bleed in the deepest shadows
    #    Mask: pixels below ~25% brightness, quadratic falloff
    shadow_mask = np.clip(1.0 - luma / 0.25, 0.0, 1.0) ** 2
    result[:, :, 0] -= shadow_mask * 0.012   # pull red back
    result[:, :, 1] += shadow_mask * 0.048   # push green
    result[:, :, 2] += shadow_mask * 0.022   # push blue (cyan = green + blue)

    # 4. Midtone warmth — yellow-orange push centred around 40% brightness
    #    Gaussian mask peaks in the mid-grey range
    mid_mask = np.exp(-((luma - 0.40) ** 2) / (2 * 0.18 ** 2))
    result[:, :, 0] += mid_mask * 0.032   # warm red
    result[:, :, 1] += mid_mask * 0.018   # green component of yellow
    result[:, :, 2] -= mid_mask * 0.022   # pull blue out

    # 5. Orange-red saturation boost
    #    Detect pixels where red leads both green and blue (orange-red hues).
    #    Push red up, pull blue down — boosts saturation without shifting hue.
    orange = np.clip(result[:, :, 0] - np.maximum(result[:, :, 1], result[:, :, 2]), 0.0, 1.0)
    result[:, :, 0] += orange * 0.055
    result[:, :, 2] -= orange * 0.030

    # 6. Highlight warmth — very subtle warm cast above ~70% brightness
    hi_mask = np.clip((luma - 0.70) * 3.5, 0.0, 1.0)
    result[:, :, 0] += hi_mask * 0.014
    result[:, :, 1] += hi_mask * 0.006
    result[:, :, 2] -= hi_mask * 0.010

    # Blend processed result with original by strength
    return np.clip(strength * result + (1.0 - strength) * img, 0.0, 1.0)


# ── Grain ─────────────────────────────────────────────────────────────────────

def add_grain(img: np.ndarray, intensity: float, size: int) -> np.ndarray:
    """
    Add luminosity-weighted coarse film grain.
    Grain is heavier in shadows/midtones and falls off toward highlights,
    matching the character of Fuji Gold 400's relatively coarse structure.
    """
    if intensity <= 0:
        return img

    h, w   = img.shape[:2]
    size   = max(1, int(size))
    nh, nw = max(1, h // size), max(1, w // size)

    # Generate noise at reduced resolution for coarse, clumped character
    small  = np.random.normal(0.0, intensity, (nh, nw, 3)).astype(np.float32)

    # Nearest-neighbour upscale — keeps blocky grain, avoids blurring
    coarse = np.repeat(np.repeat(small, size, axis=0), size, axis=1)
    ph = max(0, h - coarse.shape[0])
    pw = max(0, w - coarse.shape[1])
    if ph or pw:
        coarse = np.pad(coarse, ((0, ph), (0, pw), (0, 0)), mode="wrap")
    coarse = coarse[:h, :w]

    # Luminosity weighting: more grain in shadows/mids, less in highlights
    luma   = 0.299 * img[:, :, 0] + 0.587 * img[:, :, 1] + 0.114 * img[:, :, 2]
    weight = (1.0 - luma * 0.85)[..., np.newaxis]
    coarse *= weight

    return np.clip(img + coarse, 0.0, 1.0)


# ── Halation ──────────────────────────────────────────────────────────────────

def add_halation(img: np.ndarray, intensity: float, radius: float) -> np.ndarray:
    """
    Add halation — warm red-orange glow around bright highlights.
    Caused by light scattering back through film layers; very characteristic
    of Fuji Gold 400, especially around windows, sky, and lamp sources.
    """
    if intensity <= 0:
        return img

    # Isolate pixels above ~75% brightness
    highlights = np.clip(img - 0.75, 0.0, 1.0) * 4.0

    # Red-orange bloom weighted toward the brightest channel
    bloom       = np.zeros_like(img)
    bloom[:, :, 0] = (highlights[:, :, 0] * 0.78
                      + highlights[:, :, 1] * 0.17
                      + highlights[:, :, 2] * 0.05)
    bloom[:, :, 1] = bloom[:, :, 0] * 0.20   # orange warmth (red + a little green)

    # Gaussian blur via PIL — no scipy dependency
    for ch in range(2):
        ch_u8  = (bloom[:, :, ch] * 255).clip(0, 255).astype(np.uint8)
        ch_pil = Image.fromarray(ch_u8, mode="L")
        blurred = ch_pil.filter(ImageFilter.GaussianBlur(radius=float(radius)))
        bloom[:, :, ch] = np.array(blurred, dtype=np.float32) / 255.0

    return np.clip(img + bloom * intensity, 0.0, 1.0)


# ── Image loading ─────────────────────────────────────────────────────────────

def _load_image(path: Path) -> np.ndarray:
    """Load any supported image or RAW file to an (H, W, 3) float32 array in [0, 1]."""
    suffix = path.suffix.lower()

    if suffix in RAW_EXTENSIONS:
        try:
            import rawpy
        except ImportError:
            raise RuntimeError("rawpy is not installed — RAW file support unavailable.")
        with rawpy.imread(str(path)) as raw:
            rgb = raw.postprocess(use_camera_wb=True, output_bps=8)
        arr = rgb.astype(np.float32) / 255.0
    else:
        pil = Image.open(path).convert("RGB")
        arr = np.array(pil, dtype=np.float32) / 255.0

    # Cap long edge to avoid excessive memory use on large files
    h, w      = arr.shape[:2]
    long_edge = max(h, w)
    if long_edge > MAX_LONG_EDGE:
        scale     = MAX_LONG_EDGE / long_edge
        new_h, new_w = int(h * scale), int(w * scale)
        pil_resize = Image.fromarray((arr * 255).astype(np.uint8))
        pil_resize = pil_resize.resize((new_w, new_h), Image.LANCZOS)
        arr = np.array(pil_resize, dtype=np.float32) / 255.0

    return arr


# ── Pipeline ──────────────────────────────────────────────────────────────────

def process_photo(input_path: Path, params: dict) -> bytes:
    """
    Full processing pipeline. Returns JPEG bytes.
    Order: load → exposure → colour grade → contrast → halation → grain → export

    Exposure first so the colour grade sees the right tonal range.
    Contrast after the grade so it shapes the already-graded palette.
    Halation before grain so the bloom isn't textured by grain.
    """
    img = _load_image(input_path)

    img = apply_exposure(img, float(params.get("exposure_bias", 0.0)))

    img = apply_film_color(img, float(params.get("grade_strength", 0.85)))

    img = apply_contrast(img, float(params.get("contrast_strength", 0.0)))

    img = add_halation(
        img,
        intensity=float(params.get("halation_intensity", 0.45)),
        radius=float(params.get("halation_radius", 38)),
    )

    img = add_grain(
        img,
        intensity=float(params.get("grain_intensity", 0.055)),
        size=int(params.get("grain_size", 3)),
    )

    pil_out = Image.fromarray((img * 255).clip(0, 255).astype(np.uint8))
    buf     = io.BytesIO()
    pil_out.save(buf, format="JPEG", quality=95)
    buf.seek(0)
    return buf.read()


# ── Preset persistence ────────────────────────────────────────────────────────

def _load_user_presets(presets_file: Path) -> dict:
    if presets_file.exists():
        try:
            return json.loads(presets_file.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _save_user_presets(presets_file: Path, user_presets: dict):
    presets_file.write_text(
        json.dumps(user_presets, indent=2, ensure_ascii=False), encoding="utf-8"
    )


# ── Flask routes ──────────────────────────────────────────────────────────────

def register_film_routes(app):
    """Register all Film Lab routes on the Flask app."""

    from app import APP_STATE_DIR

    FILM_PRESETS_FILE = APP_STATE_DIR / "film_presets.json"

    @app.route("/api/film/presets")
    def film_get_presets():
        user_presets = _load_user_presets(FILM_PRESETS_FILE)
        result = []
        for name, params in BUILTIN_PRESETS.items():
            result.append({"name": name, "params": params, "builtin": True})
        for name, params in user_presets.items():
            result.append({"name": name, "params": params, "builtin": False})
        return jsonify(result)

    @app.route("/api/film/presets", methods=["POST"])
    def film_save_preset():
        data   = request.get_json(force=True, silent=True) or {}
        name   = str(data.get("name", "")).strip()
        params = data.get("params")
        if not name:
            return jsonify({"error": "Preset name is required."}), 400
        if name in BUILTIN_PRESETS:
            return jsonify({"error": "Cannot overwrite a built-in preset."}), 400
        if not isinstance(params, dict):
            return jsonify({"error": "Invalid params."}), 400
        user_presets        = _load_user_presets(FILM_PRESETS_FILE)
        user_presets[name]  = params
        _save_user_presets(FILM_PRESETS_FILE, user_presets)
        return jsonify({"ok": True})

    @app.route("/api/film/presets/<name>", methods=["DELETE"])
    def film_delete_preset(name):
        if name in BUILTIN_PRESETS:
            return jsonify({"error": "Cannot delete a built-in preset."}), 400
        user_presets = _load_user_presets(FILM_PRESETS_FILE)
        if name not in user_presets:
            return jsonify({"error": "Preset not found."}), 404
        del user_presets[name]
        _save_user_presets(FILM_PRESETS_FILE, user_presets)
        return jsonify({"ok": True})

    @app.route("/api/film/process", methods=["POST"])
    def film_process():
        if "file" not in request.files:
            return jsonify({"error": "No file uploaded."}), 400

        f      = request.files["file"]
        suffix = Path(f.filename).suffix.lower() if f.filename else ""
        if suffix not in RAW_EXTENSIONS | IMAGE_EXTENSIONS:
            return jsonify({"error": f"Unsupported file type: {suffix}"}), 400

        try:
            params = json.loads(request.form.get("params", "{}"))
        except Exception:
            params = {}

        tmp_path = Path(tempfile.mktemp(suffix=suffix))
        try:
            f.save(str(tmp_path))
            result_bytes = process_photo(tmp_path, params)
        except Exception as e:
            return jsonify({"error": str(e)}), 500
        finally:
            tmp_path.unlink(missing_ok=True)

        buf = io.BytesIO(result_bytes)
        buf.seek(0)
        return send_file(
            buf,
            mimetype="image/jpeg",
            as_attachment=True,
            download_name="film_processed.jpg",
        )
