"""yt-dlp profile builder, retry-loop runner, and stderr classification helpers.

Single source of truth for the duplicated download scaffolding that previously
lived in both ``app.py`` (audio) and ``alert.py`` (video).
"""

from typing import Callable, List, Literal, Optional, Sequence, Tuple


# ── Stderr classification ──────────────────────────────────────────

def summarize_ytdlp_error(stderr_text: str) -> str:
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


def looks_like_youtube_challenge_issue(stderr_text: str) -> bool:
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


def looks_like_age_restricted_issue(stderr_text: str) -> bool:
    s = (stderr_text or "").lower()
    markers = [
        "age-restricted",
        "sign in to confirm your age",
        "confirm your age",
        "this video may be inappropriate",
        "this content may be inappropriate",
    ]
    return any(m in s for m in markers)


# ── Profile builder ────────────────────────────────────────────────

# Per-media-kind constants. Names are user-visible (alert.py surfaces video
# profile names in the job stage string) so they MUST stay byte-identical to
# what was previously hardcoded in app.py / alert.py.
_AUDIO_NAMES = {
    "web": "web client",
    "android": "android client",
    "mweb": "mweb client",
    "tv_embedded": "tv embedded client",
    "challenge": "web + challenge solver",
    "browser": "{browser} cookies",
    "compatibility_yt": "compatibility format",
    "fallback_standard": "standard",
    "fallback_compat": "compatibility",
}

_VIDEO_NAMES = {
    "web": "web progressive",
    "android": "android progressive",
    "mweb": "mweb progressive",
    "tv_embedded": "tv embedded progressive",
    "challenge": "web progressive + challenge solver",
    "browser": "{browser} cookies progressive",
    "adaptive_merge": "web adaptive merge",
    "compatibility_yt": "compatibility adaptive",
    "fallback_standard": "standard",
    "fallback_compat": "compatibility",
}


def build_ytdlp_profiles(
    media_kind: Literal["audio", "video"],
    youtube: bool,
    has_deno: bool,
) -> List[dict]:
    """Build the ordered list of yt-dlp profile attempts.

    Each profile is a dict with keys ``name``, ``format``, ``sort``, ``extra``.
    Callers turn these into argv via their own ``build_*_cmd`` helpers because
    the actual command shape (``-x --audio-format wav`` for audio,
    ``--download-sections`` placement for video, etc.) still differs.
    """
    if media_kind == "audio":
        names = _AUDIO_NAMES
        progressive_format = "ba[ext=m4a]/bestaudio/b"
        sort = "ext:m4a,abr"
        adaptive_merge_format = None  # audio has no adaptive-merge profile
        compat_format = "b/bestaudio"
        fallback_standard_format = "bestaudio/b"
        fallback_standard_sort = "abr"
        fallback_compat_format = "b/bestaudio"
    elif media_kind == "video":
        names = _VIDEO_NAMES
        progressive_format = "b[ext=mp4]/b[ext=webm]/b"
        sort = "res,ext:mp4:m4a"
        adaptive_merge_format = "bv*[ext=mp4]+ba[ext=m4a]/bv*+ba/b"
        compat_format = adaptive_merge_format
        fallback_standard_format = "bv*+ba/b"
        fallback_standard_sort = "res"
        fallback_compat_format = "b/bv*+ba"
    else:
        raise ValueError(f"unknown media_kind: {media_kind!r}")

    if not youtube:
        return [
            {"name": names["fallback_standard"], "format": fallback_standard_format,
             "sort": fallback_standard_sort, "extra": []},
            {"name": names["fallback_compat"], "format": fallback_compat_format,
             "sort": None, "extra": []},
        ]

    profiles: List[dict] = [
        {
            "name": names["web"],
            "format": progressive_format,
            "sort": sort,
            "extra": ["--extractor-args", "youtube:player_client=web"],
        },
        {
            "name": names["android"],
            "format": progressive_format,
            "sort": sort,
            "extra": ["--extractor-args", "youtube:player_client=android"],
        },
        {
            "name": names["mweb"],
            "format": progressive_format,
            "sort": sort,
            "extra": ["--extractor-args", "youtube:player_client=mweb"],
        },
        {
            "name": names["tv_embedded"],
            "format": progressive_format,
            "sort": sort,
            "extra": ["--extractor-args", "youtube:player_client=tv_embedded,web"],
        },
    ]

    if media_kind == "video":
        profiles.append(
            {
                "name": names["adaptive_merge"],
                "format": adaptive_merge_format,
                "sort": sort,
                "extra": ["--extractor-args", "youtube:player_client=web"],
            }
        )

    if has_deno:
        profiles.append(
            {
                "name": names["challenge"],
                "format": progressive_format,
                "sort": sort,
                "extra": [
                    "--remote-components", "ejs:github",
                    "--extractor-args", "youtube:player_client=web",
                ],
            }
        )

    for browser in ["chrome", "edge", "firefox"]:
        profiles.append(
            {
                "name": names["browser"].format(browser=browser),
                "format": progressive_format,
                "sort": sort,
                "extra": [
                    "--cookies-from-browser", browser,
                    "--extractor-args", "youtube:player_client=web",
                ],
            }
        )

    profiles.append(
        {"name": names["compatibility_yt"], "format": compat_format, "sort": None, "extra": []}
    )
    return profiles


# ── Retry-loop runner ──────────────────────────────────────────────

def run_ytdlp_with_retries(
    job_dir,
    file_glob: str,
    build_cmd: Callable[[dict, bool], Sequence[str]],
    profiles: List[dict],
    section_modes: List[bool],
    run_subprocess: Callable[..., object],
    timeout: int = 300,
    default_error: str = "Download failed",
    on_attempt: Optional[Callable[[int, int, dict, bool], None]] = None,
    clean_first_attempt: bool = True,
) -> Tuple[bool, str, str]:
    """Execute the section_modes × profiles retry loop.

    Returns ``(success, last_stderr, last_error)``. The caller handles
    success-path file lookup and the failure-path hint formatting because
    audio (raises) and video (writes to jobs) diverge there.

    Args:
        job_dir: Path-like directory where downloads land.
        file_glob: Glob pattern for stale partials to clean (e.g. ``"clip.*"``).
        build_cmd: ``(profile, use_sections) -> argv`` to launch yt-dlp.
        profiles: From ``build_ytdlp_profiles``.
        section_modes: Ordered list of ``use_sections`` values to try.
        run_subprocess: The subprocess wrapper from app.py (passed in to
            avoid a circular import and to keep the Windows-specific flags
            in one place).
        timeout: Per-attempt timeout in seconds.
        default_error: Initial value for ``last_error`` when the loop runs
            zero attempts (defensive only).
        on_attempt: Optional callback invoked before each subprocess call as
            ``on_attempt(attempt_idx, total_attempts, profile, use_sections)``.
            Used by the video pipeline to update job progress + stage.
        clean_first_attempt: If False, skip the stale-file cleanup on the
            very first attempt. Matches alert.py's video loop, which only
            cleans on ``attempt_idx > 1``. Audio uses True (clean every time).
    """
    total_attempts = len(section_modes) * len(profiles)
    attempt_idx = 0
    last_error = default_error
    last_stderr = ""
    success = False

    for use_sections in section_modes:
        for profile in profiles:
            attempt_idx += 1
            if clean_first_attempt or attempt_idx > 1:
                for stale in job_dir.glob(file_glob):
                    stale.unlink(missing_ok=True)

            if on_attempt is not None:
                on_attempt(attempt_idx, total_attempts, profile, use_sections)

            r = run_subprocess(build_cmd(profile, use_sections), timeout=timeout)
            if r.returncode == 0:
                success = True
                break

            last_stderr = r.stderr or ""
            last_error = summarize_ytdlp_error(last_stderr)

        if success:
            break

    return success, last_stderr, last_error
