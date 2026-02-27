/**
 * Main application logic for deutschmark's Alert! Alert!
 * Manages the 4-step workflow, API calls, and UI state.
 */
const App = (() => {
    // State
    const DEFAULT_PREVIEW_VOLUME = 50;
    let videoUrl = "";
    let videoDuration = 0;
    let jobId = "";
    let videoInfo = null; // { width, height, fps, duration }
    let cropPreview = null;
    let pollTimer = null;
    let downloadableJobId = "";
    let dependencyInstallAttempted = false;
    let dependencyInstallInFlight = false;

    // Audio source state
    let audioUrl = "";
    let audioDuration = 0;
    let audioValidated = false;
    let audioSourceType = "url"; // "url" or "file"
    let separateAudioFile = null;
    let useSeparateAudio = false;
    let separateAudioPreview = null;
    let separateAudioPreviewLoaded = false;
    let separateAudioSyncBound = false;

    // Static image state
    let useStaticImage = false;
    let staticImageFile = null;
    let staticImagePreviewUrl = "";

    // Local video state
    let sourceType = "url"; // "url" or "file"
    let localVideoFile = null;

    // Trim state
    let trimStart = 0;
    let trimEnd = 0;

    // ── Helpers ─────────────────────────────────────────────────

    function $(id) {
        return document.getElementById(id);
    }

    function show(el) {
        if (typeof el === "string") el = $(el);
        el.classList.remove("hidden");
    }

    function hide(el) {
        if (typeof el === "string") el = $(el);
        el.classList.add("hidden");
    }

    function enableStep(stepNum) {
        const step = $(`step-${stepNum}`);
        step.classList.remove("disabled");
    }

    function disableStep(stepNum) {
        const step = $(`step-${stepNum}`);
        step.classList.add("disabled");
    }

    function showError(id, msg) {
        const el = $(id);
        el.textContent = msg;
        show(el);
    }

    function hideError(id) {
        hide(id);
    }

    function clearWorkflowErrors() {
        hideError("step1-error");
        hideError("step2-error");
        hideError("step4-error");
    }

    function lockExportForCurrentWorkflow() {
        downloadableJobId = "";
        hide("download-section");
        const exportBtn = $("export-btn");
        if (exportBtn) exportBtn.disabled = true;
    }

    function setLoading(btnId, loading) {
        const btn = $(btnId);
        btn.disabled = loading;
        if (loading) {
            btn.dataset.origText = btn.textContent;
            btn.textContent = "Working...";
            startLoadingAnimation();
        } else {
            btn.textContent = btn.dataset.origText || btn.textContent;
            stopLoadingAnimation();
        }
    }

    function formatDuration(seconds) {
        const m = Math.floor(seconds / 60);
        const s = Math.floor(seconds % 60);
        return `${m}:${s.toString().padStart(2, "0")}`;
    }

    /**
     * Parse timestamp string to seconds.
     * Supports: "1:30", "1:30:00", "90" (as seconds), "1.5" (as seconds)
     */
    function parseTimestamp(ts) {
        const trimmed = ts.trim();
        if (!trimmed) return 0;

        // If contains colon, parse as time format
        if (trimmed.includes(":")) {
            const parts = trimmed.split(":").map(Number);
            if (parts.length === 3) return parts[0] * 3600 + parts[1] * 60 + parts[2];
            if (parts.length === 2) return parts[0] * 60 + parts[1];
            return parts[0] || 0;
        }

        // Otherwise treat as seconds
        return parseFloat(trimmed) || 0;
    }

    /**
     * Format a timestamp input value to proper MM:SS or H:MM:SS format.
     * Called on blur to auto-format user input.
     */
    function formatTimestampInput(input) {
        const value = input.value.trim();
        if (!value) return;

        const seconds = parseTimestamp(value);
        if (isNaN(seconds) || seconds < 0) {
            input.value = "0:00";
            return;
        }

        const hours = Math.floor(seconds / 3600);
        const mins = Math.floor((seconds % 3600) / 60);
        const secs = Math.floor(seconds % 60);

        if (hours > 0) {
            input.value = `${hours}:${mins.toString().padStart(2, "0")}:${secs.toString().padStart(2, "0")}`;
        } else {
            input.value = `${mins}:${secs.toString().padStart(2, "0")}`;
        }
    }

    function getSeparateAudioElement() {
        if (!separateAudioPreview) {
            separateAudioPreview = new Audio();
            separateAudioPreview.preload = "auto";
            separateAudioPreview.crossOrigin = "anonymous";
        }
        return separateAudioPreview;
    }

    function hasActiveSeparateAudioPreview() {
        return !!(useSeparateAudio && separateAudioPreviewLoaded);
    }

    function getSeparateAudioStartSeconds() {
        return parseTimestamp($("audio-start-input")?.value || "0:00");
    }

    function getSeparateAudioEndSeconds() {
        return parseTimestamp($("audio-end-input")?.value || "0:00");
    }

    function clearLoadedSeparateAudioPreview() {
        separateAudioPreviewLoaded = false;
        if (separateAudioPreview) {
            separateAudioPreview.pause();
            separateAudioPreview.removeAttribute("src");
            separateAudioPreview.load();
        }
        updatePreviewAudioRouting();
    }

    function bindSeparateAudioSync() {
        if (separateAudioSyncBound) return;
        const video = $("crop-video");
        if (!video) return;
        separateAudioSyncBound = true;

        video.addEventListener("play", () => {
            syncSeparateAudioWithVideo(true);
        });
        video.addEventListener("pause", () => {
            if (!separateAudioPreview) return;
            separateAudioPreview.pause();
        });
        video.addEventListener("seeking", () => {
            syncSeparateAudioWithVideo(true);
        });
        video.addEventListener("timeupdate", () => {
            syncSeparateAudioWithVideo(false);
        });
        video.addEventListener("ratechange", () => {
            if (!separateAudioPreview) return;
            separateAudioPreview.playbackRate = video.playbackRate || 1;
        });
    }

    function syncSeparateAudioWithVideo(forceSeek = false) {
        if (!hasActiveSeparateAudioPreview()) return;

        const video = $("crop-video");
        const audio = getSeparateAudioElement();
        if (!video || !audio || !audio.src) return;

        const audioStart = getSeparateAudioStartSeconds();
        const audioEnd = getSeparateAudioEndSeconds();
        const clipOffset = Math.max(0, (video.currentTime || 0) - trimStart);
        const targetAudioTime = Math.max(0, audioStart + clipOffset);
        const drift = Math.abs((audio.currentTime || 0) - targetAudioTime);

        if (forceSeek || drift > 0.2) {
            try {
                audio.currentTime = targetAudioTime;
            } catch (e) {
                // Ignore seek errors while metadata is still settling.
            }
        }

        if (!video.paused) {
            if (audioEnd > audioStart && targetAudioTime >= audioEnd) {
                if (!audio.paused) audio.pause();
                return;
            }
            if (audio.paused) {
                audio.play().catch(() => { });
            }
        } else if (!audio.paused) {
            audio.pause();
        }
    }

    async function loadAudioSourceForPreview(srcUrl) {
        const audio = getSeparateAudioElement();
        await new Promise((resolve, reject) => {
            audio.onloadedmetadata = () => resolve();
            audio.onerror = () => reject(new Error("Failed to load separate audio preview."));
            audio.src = srcUrl;
            audio.load();
        });
        separateAudioPreviewLoaded = true;
        updatePreviewAudioRouting();
        syncSeparateAudioWithVideo(true);
        onVolumeChange();
    }

    function updatePreviewAudioRouting() {
        const video = $("crop-video");
        const muteBtn = $("preview-mute-btn");
        if (!video) return;

        if (hasActiveSeparateAudioPreview()) {
            video.muted = true;
            if (muteBtn) {
                muteBtn.disabled = true;
                muteBtn.textContent = "🎵 Separate Audio";
            }
            return;
        }

        if (muteBtn) {
            muteBtn.disabled = false;
            const vol = parseInt($("volume-slider")?.value || "50", 10);
            muteBtn.textContent = vol === 0 ? "🔇 Unmute" : "🔊 Mute";
        }

        if (separateAudioPreview && !separateAudioPreview.paused) {
            separateAudioPreview.pause();
        }
    }

    async function api(endpoint, options = {}) {
        const resp = await fetch(endpoint, {
            headers: { "Content-Type": "application/json" },
            ...options,
        });
        return resp.json();
    }

    function setDependencyBanner(messageHtml, instructionsHtml = "", showAsError = true) {
        const banner = $("dep-banner");
        const msg = $("dep-message");
        const instEl = $("dep-instructions");
        if (!banner || !msg || !instEl) return;
        msg.innerHTML = messageHtml;
        instEl.innerHTML = instructionsHtml;
        banner.classList.toggle("banner-error", showAsError);
        show(banner);
    }

    function setPanelOpen(panelId, isOpen) {
        const panel = $(panelId);
        if (!panel) return;
        panel.classList.toggle("open", !!isOpen);
        document.querySelectorAll(`[data-panel="${panelId}"]`).forEach((trigger) => {
            trigger.classList.toggle("active", !!isOpen);
        });
    }

    function toggleSettingsPanel(panelId) {
        const panel = $(panelId);
        if (!panel) return;
        const shouldOpen = !panel.classList.contains("open");
        setPanelOpen(panelId, shouldOpen);
    }

    function openSettingsPanel(panelId, stepId = "") {
        const panel = $(panelId);
        if (!panel) return;
        setPanelOpen(panelId, true);

        const step = stepId ? $(stepId) : panel.closest(".step");
        if (step) {
            step.classList.remove("disabled");
            step.scrollIntoView({ behavior: "smooth", block: "start" });
        } else {
            panel.scrollIntoView({ behavior: "smooth", block: "start" });
        }
    }

    function updateAudioFadeNote(settings = getSettings()) {
        const note = $("audio-fade-note");
        if (!note) return;
        note.textContent = `Fade length set to ${settings.audioFadeDuration}s in Audio Processing Settings`;
    }

    function updateSettingsPanelLabels(settings = getSettings()) {
        const outputPillValue = $("output-pill-value");
        const audioPillValue = $("audio-pill-value");
        if (outputPillValue) {
            const bufferValue = String(settings.bufferDuration || "2");
            outputPillValue.textContent = bufferValue === "0"
                ? `${settings.resolution}p · No buffer`
                : `${settings.resolution}p · Buffer ${bufferValue}s`;
        }
        if (audioPillValue) {
            const normalizeLabel = settings.normalizeAudio ? "Normalize On" : "Normalize Off";
            audioPillValue.textContent = `${normalizeLabel} · ${settings.audioFadeDuration}s`;
        }
        updateAudioFadeNote(settings);
    }

    function renderDependencyStatus(deps) {
        const missing = [];
        const instructions = [];

        const ffmpegStatus = $("dep-ffmpeg-status");
        const ytdlpStatus = $("dep-ytdlp-status");
        const denoStatus = $("dep-deno-status");
        const installBtn = $("auto-install-deps-btn");

        if (deps.ffmpeg?.installed && deps.ffprobe?.installed) {
            ffmpegStatus.textContent = "✓ Installed";
            ffmpegStatus.className = "dep-status installed";
        } else {
            ffmpegStatus.textContent = "✗ Missing";
            ffmpegStatus.className = "dep-status missing";
            missing.push("FFmpeg/ffprobe");
            instructions.push("FFmpeg: Use Auto Install in Step 1. If needed, run 'winget install Gyan.FFmpeg'.");
        }

        if (deps["yt-dlp"]?.installed) {
            ytdlpStatus.textContent = "✓ Installed";
            ytdlpStatus.className = "dep-status installed";
        } else {
            ytdlpStatus.textContent = "✗ Missing";
            ytdlpStatus.className = "dep-status missing";
            missing.push("yt-dlp");
            instructions.push("yt-dlp: Use Auto Install in Step 1, or install manually.");
        }

        if (denoStatus) {
            if (deps.deno?.installed) {
                denoStatus.textContent = "✓ Installed";
                denoStatus.className = "dep-status installed";
            } else {
                denoStatus.textContent = "⚠ Optional";
                denoStatus.className = "dep-status missing";
                instructions.push("Deno is optional. Install with 'winget install DenoLand.Deno' for better YouTube challenge handling.");
            }
        }

        if (installBtn) {
            const shouldShowInstall = missing.length > 0 && deps.auto_install_available;
            installBtn.classList.toggle("hidden", !shouldShowInstall);
            installBtn.disabled = dependencyInstallInFlight;
        }

        const depPillValue = $("dependency-pill-value");
        if (depPillValue) {
            if (deps.bootstrap?.status === "installing") {
                depPillValue.textContent = "Installing...";
            } else if (missing.length === 0) {
                depPillValue.textContent = "Ready";
            } else {
                depPillValue.textContent = `Needs Setup (${missing.length})`;
            }
        }

        if (missing.length > 0) {
            setPanelOpen("dependency-settings-panel", true);
            const bootstrapMessage = deps.bootstrap?.message || "";
            const bootstrapError = deps.bootstrap?.last_error || "";
            const extra = bootstrapError
                ? `<br><strong>Auto-install error:</strong> ${bootstrapError}`
                : (bootstrapMessage ? `<br>${bootstrapMessage}` : "");
            setDependencyBanner(
                `<strong>Missing dependencies:</strong> ${missing.join(", ")}${extra}`,
                `<strong>How to fix:</strong><br>${instructions.join("<br>")}`,
                true
            );
        } else {
            hide("dep-banner");
        }

        return missing;
    }

    async function installMissingDependencies(manual = false) {
        if (dependencyInstallInFlight) return;
        dependencyInstallInFlight = true;
        const installBtn = $("auto-install-deps-btn");
        const previousLabel = installBtn?.textContent || "";
        if (installBtn) {
            installBtn.disabled = true;
            installBtn.textContent = "Installing...";
        }
        setDependencyBanner(
            "<strong>Installing dependencies...</strong> This may take a minute on first run.",
            "Downloading ffmpeg and yt-dlp to your local app folder.",
            false
        );
        try {
            const deps = await api("/api/bootstrap-deps", { method: "POST" });
            renderDependencyStatus(deps);
            if ((deps.required_missing || []).length === 0) {
                if (installBtn) installBtn.classList.add("hidden");
            } else if (manual) {
                setDependencyBanner(
                    "<strong>Dependencies are still missing.</strong>",
                    "Use the Step 1 troubleshooting list, then restart the app after manual install.",
                    true
                );
            }
        } catch (e) {
            setDependencyBanner(
                "<strong>Dependency installation failed.</strong>",
                "Check your internet connection and try Auto Install again.",
                true
            );
        } finally {
            dependencyInstallInFlight = false;
            if (installBtn && !installBtn.classList.contains("hidden")) {
                installBtn.disabled = false;
                installBtn.textContent = previousLabel || "Auto Install Missing";
            }
        }
    }

    // ── Initialization ──────────────────────────────────────────

    async function init() {
        lockExportForCurrentWorkflow();

        // Check dependencies
        try {
            const deps = await api("/api/check-deps");
            const missing = renderDependencyStatus(deps);

            if (missing.length > 0 && deps.auto_install_available && !dependencyInstallAttempted) {
                dependencyInstallAttempted = true;
                await installMissingDependencies(false);
            }
        } catch (e) {
            // Server not running - show connection error
            setDependencyBanner(
                "<strong>Cannot connect to server.</strong>",
                "Make sure the app is running.",
                true
            );
        }

        // Audio clip duration
        $("audio-start-input").addEventListener("input", onAudioStartInputChange);
        $("audio-end-input").addEventListener("input", updateAudioClipDuration);

        // Auto-format timestamps on blur
        const timestampInputs = ["audio-start-input", "audio-end-input"];
        timestampInputs.forEach(id => {
            $(id).addEventListener("blur", (e) => formatTimestampInput(e.target));
        });

        // Basic trim sliders
        $("trim-start-slider").addEventListener("input", onTrimSliderChange);
        $("trim-end-slider").addEventListener("input", onTrimSliderChange);

        // Volume slider
        $("volume-slider").value = String(DEFAULT_PREVIEW_VOLUME);
        $("volume-value").textContent = `${DEFAULT_PREVIEW_VOLUME}%`;
        $("volume-slider").addEventListener("input", onVolumeChange);
        onVolumeChange();

        // Allow Enter to validate
        $("url-input").addEventListener("keydown", (e) => {
            if (e.key === "Enter") validateUrl();
        });

        $("audio-url-input").addEventListener("keydown", (e) => {
            if (e.key === "Enter") validateAudioUrl();
        });
        $("audio-url-input").addEventListener("input", () => {
            audioValidated = false;
            clearLoadedSeparateAudioPreview();
        });
        $("audio-file-input").addEventListener("change", onAudioFileSelect);

        // Audio source toggle
        $("use-separate-audio").addEventListener("change", onAudioToggle);

        // Static image toggle
        $("use-static-image").addEventListener("change", onStaticImageToggle);
        $("static-image-input").addEventListener("change", onStaticImageSelect);

        // Local video file upload
        $("local-video-input").addEventListener("change", onLocalVideoSelect);
        setupFileDragDrop();
        bindSeparateAudioSync();

        // Load saved settings
        loadSettings();

        // ASCII star animation
        initStarAnimation();
    }

    // Loading animation state
    let loadingAnimationTimer = null;
    let loadingCount = 0; // Track nested loading states

    function initStarAnimation() {
        // Just ensure bangs show static "!" on init
        const bang1 = $("ascii-bang-1");
        const bang2 = $("ascii-bang-2");
        if (!bang1 || !bang2) return;
        bang1.textContent = "!";
        bang2.textContent = "!";
    }

    function startLoadingAnimation() {
        loadingCount++;
        if (loadingAnimationTimer) return; // Already animating

        const bang1 = $("ascii-bang-1");
        const bang2 = $("ascii-bang-2");
        if (!bang1 || !bang2) return;

        bang1.classList.add("loading");
        bang2.classList.add("loading");

        const frames = [".", "·", ":", "¡", "!", "❗", "‼", "❗", "!", "¡", ":", "·"];
        let frameIndex1 = 0;
        let frameIndex2 = 6;

        loadingAnimationTimer = setInterval(() => {
            bang1.textContent = frames[frameIndex1];
            bang2.textContent = frames[frameIndex2];
            frameIndex1 = (frameIndex1 + 1) % frames.length;
            frameIndex2 = (frameIndex2 + 1) % frames.length;
        }, 150);
    }

    function stopLoadingAnimation() {
        loadingCount--;
        if (loadingCount > 0) return; // Still have other loading operations
        loadingCount = 0; // Ensure it doesn't go negative

        if (loadingAnimationTimer) {
            clearInterval(loadingAnimationTimer);
            loadingAnimationTimer = null;
        }

        const bang1 = $("ascii-bang-1");
        const bang2 = $("ascii-bang-2");
        if (!bang1 || !bang2) return;

        bang1.classList.remove("loading");
        bang2.classList.remove("loading");
        bang1.textContent = "!";
        bang2.textContent = "!";
    }

    function updateAudioClipDuration() {
        const start = parseTimestamp($("audio-start-input").value);
        const end = parseTimestamp($("audio-end-input").value);
        if (end > start) {
            $("audio-clip-duration").textContent = `Audio: ${formatDuration(end - start)}`;
        } else {
            $("audio-clip-duration").textContent = "";
        }
        syncSeparateAudioWithVideo(true);
    }

    function formatSecondsForTimestampInput(seconds) {
        const safe = Math.max(0, Number(seconds) || 0);
        const hours = Math.floor(safe / 3600);
        const mins = Math.floor((safe % 3600) / 60);
        const secs = Math.floor(safe % 60);
        if (hours > 0) {
            return `${hours}:${mins.toString().padStart(2, "0")}:${secs.toString().padStart(2, "0")}`;
        }
        return `${mins}:${secs.toString().padStart(2, "0")}`;
    }

    function getTargetClipDurationSeconds() {
        const trimmed = Math.max(0, trimEnd - trimStart);
        if (trimmed > 0) return trimmed;
        return clipDuration || videoDuration || (videoInfo && Number(videoInfo.duration)) || 0;
    }

    function syncAudioEndToClipDuration() {
        if (!useSeparateAudio) return;
        const startInput = $("audio-start-input");
        const endInput = $("audio-end-input");
        if (!startInput || !endInput) return;
        const clipDur = getTargetClipDurationSeconds();
        if (clipDur <= 0) return;

        const start = parseTimestamp(startInput.value);
        const newEnd = start + clipDur;
        endInput.value = formatSecondsForTimestampInput(newEnd);
        updateAudioClipDuration();
    }

    function onAudioStartInputChange() {
        if (useSeparateAudio) {
            syncAudioEndToClipDuration();
        } else {
            updateAudioClipDuration();
        }
        syncSeparateAudioWithVideo(true);
    }

    function debounce(fn, ms) {
        let timer;
        return (...args) => {
            clearTimeout(timer);
            timer = setTimeout(() => fn(...args), ms);
        };
    }

    // ── Trim Slider Handlers ────────────────────────────────────

    let clipDuration = 0; // Duration of the downloaded clip

    function onTrimSliderChange() {
        if (!clipDuration) return;

        const startPct = parseFloat($("trim-start-slider").value);
        const endPct = parseFloat($("trim-end-slider").value);

        // Convert percentage to seconds
        trimStart = (startPct / 100) * clipDuration;
        trimEnd = (endPct / 100) * clipDuration;

        // Ensure start doesn't exceed end
        if (trimStart >= trimEnd - 0.5) {
            if (this.id === "trim-start-slider") {
                trimStart = trimEnd - 0.5;
                $("trim-start-slider").value = (trimStart / clipDuration) * 100;
            } else {
                trimEnd = trimStart + 0.5;
                $("trim-end-slider").value = (trimEnd / clipDuration) * 100;
            }
        }

        // Update displays
        $("trim-start-val").textContent = formatDuration(trimStart);
        $("trim-end-val").textContent = formatDuration(trimEnd);
        $("trim-duration").textContent = "Duration: " + formatDuration(trimEnd - trimStart);

        // Sync video playback
        const video = $("crop-video");
        if (video && video.src) {
            video.currentTime = trimStart;
        }

        if (useSeparateAudio) {
            syncAudioEndToClipDuration();
        }
        syncSeparateAudioWithVideo(true);
    }

    function initTrimSliders(duration) {
        clipDuration = duration;
        trimStart = 0;
        trimEnd = duration;

        $("trim-start-slider").value = 0;
        $("trim-end-slider").value = 100;
        $("trim-start-val").textContent = formatDuration(0);
        $("trim-end-val").textContent = formatDuration(duration);
        $("trim-duration").textContent = "Duration: " + formatDuration(duration);
        if (useSeparateAudio) {
            syncAudioEndToClipDuration();
        }
    }

    function onVolumeChange() {
        const vol = parseInt($("volume-slider").value);
        const video = $("crop-video");
        const audio = separateAudioPreview;
        const useExternalAudio = hasActiveSeparateAudioPreview();
        if (video) {
            video.volume = vol / 100;
            video.muted = useExternalAudio ? true : vol === 0;
        }
        if (audio) {
            audio.volume = vol / 100;
            audio.muted = vol === 0;
        }
        $("volume-value").textContent = vol + "%";
        updatePreviewAudioRouting();
    }

    // ── Step 1: Validate URL & Download ────────────────────────────────────

    async function validateUrl() {
        lockExportForCurrentWorkflow();
        clearWorkflowErrors();
        const url = $("url-input").value.trim();
        if (!url) {
            showError("step1-error", "Please enter a video URL.");
            return;
        }
        hideError("step1-error");
        hide("video-info");
        setLoading("validate-btn", true);

        try {
            // Step 1: Validate the URL
            $("download-status-text").textContent = "Validating URL...";
            show("download-status");

            const data = await api("/api/validate-url", {
                method: "POST",
                body: JSON.stringify({ url }),
            });

            if (!data.valid) {
                showError("step1-error", data.error || "Invalid URL");
                hide("download-status");
                return;
            }

            videoUrl = url;
            videoDuration = data.duration || 0;
            $("video-title").textContent = data.title;
            $("video-duration").textContent = formatDuration(videoDuration);
            show("video-info");

            // Step 2: Automatically download the full video
            $("download-status-text").textContent = "Downloading video...";

            const downloadData = await api("/api/download", {
                method: "POST",
                body: JSON.stringify({
                    url: videoUrl,
                    start: "0:00",
                    end: formatDuration(videoDuration)
                }),
            });

            if (downloadData.error) {
                showError("step1-error", downloadData.error);
                hide("download-status");
                return;
            }

            jobId = downloadData.job_id;
            if (!jobId) {
                throw new Error("Download job did not return an id.");
            }
            clearLoadedSeparateAudioPreview();

            $("download-status-text").textContent = "Downloading video...";
            pollDownload(jobId);

        } catch (e) {
            showError("step1-error", "Failed to load video. Is the server running?");
            hide("download-status");
        } finally {
            setLoading("validate-btn", false);
        }
    }

    // ── Validate Audio URL ──────────────────────────────────────

    async function validateAudioUrl() {
        const url = $("audio-url-input").value.trim();
        if (!url) {
            showError("audio-url-error", "Please enter a video URL for audio.");
            return;
        }
        clearLoadedSeparateAudioPreview();
        hideError("audio-url-error");
        hide("audio-video-info");
        setLoading("validate-audio-btn", true);

        try {
            const data = await api("/api/validate-url", {
                method: "POST",
                body: JSON.stringify({ url }),
            });

            if (!data.valid) {
                showError("audio-url-error", data.error || "Invalid URL");
                audioValidated = false;
                return;
            }

            audioUrl = url;
            audioDuration = data.duration;
            audioValidated = true;
            $("audio-video-title").textContent = data.title;
            $("audio-video-duration").textContent = formatDuration(data.duration);
            show("audio-video-info");
        } catch (e) {
            showError("audio-url-error", "Failed to validate URL. Is the server running?");
            audioValidated = false;
        } finally {
            setLoading("validate-audio-btn", false);
        }
    }

    async function loadSeparateAudioUrl() {
        if (!jobId) {
            showError("audio-url-error", "Load the main video first.");
            return;
        }

        const rawUrl = $("audio-url-input").value.trim();
        if (!rawUrl) {
            showError("audio-url-error", "Please enter a video URL for audio.");
            return;
        }

        if (!audioValidated || audioUrl !== rawUrl) {
            await validateAudioUrl();
            if (!audioValidated) return;
        }

        hideError("audio-url-error");
        setLoading("load-audio-url-btn", true);
        try {
            const data = await api(`/api/load-separate-audio/${jobId}`, {
                method: "POST",
                body: JSON.stringify({
                    source_type: "url",
                    audio_url: audioUrl,
                }),
            });

            if (data.error) {
                throw new Error(data.error);
            }

            audioDuration = Number(data.duration || 0);
            await loadAudioSourceForPreview(`/api/serve-audio/${jobId}?t=${Date.now()}`);
            if (useSeparateAudio) {
                syncAudioEndToClipDuration();
            }
        } catch (e) {
            clearLoadedSeparateAudioPreview();
            showError("audio-url-error", e.message || "Failed to load separate audio URL.");
        } finally {
            setLoading("load-audio-url-btn", false);
        }
    }

    async function loadSeparateAudioFile() {
        if (!jobId) {
            showError("audio-url-error", "Load the main video first.");
            return;
        }
        if (!separateAudioFile) {
            showError("audio-url-error", "Please choose a local audio file first.");
            return;
        }

        hideError("audio-url-error");
        setLoading("load-audio-file-btn", true);

        try {
            const formData = new FormData();
            formData.append("source_type", "file");
            formData.append("audio_file", separateAudioFile);

            const resp = await fetch(`/api/load-separate-audio/${jobId}`, {
                method: "POST",
                body: formData,
            });
            const data = await resp.json();
            if (!resp.ok || data.error) {
                throw new Error(data.error || "Failed to load local audio file.");
            }

            audioDuration = Number(data.duration || 0);
            await loadAudioSourceForPreview(`/api/serve-audio/${jobId}?t=${Date.now()}`);
            if (useSeparateAudio) {
                syncAudioEndToClipDuration();
            }
        } catch (e) {
            clearLoadedSeparateAudioPreview();
            showError("audio-url-error", e.message || "Failed to load local audio file.");
        } finally {
            setLoading("load-audio-file-btn", false);
        }
    }

    // ── Toggle Handlers ──────────────────────────────────────────

    function setAudioSourceType(type) {
        audioSourceType = type;

        $("audio-source-url-btn").classList.toggle("active", type === "url");
        $("audio-source-file-btn").classList.toggle("active", type === "file");
        $("audio-url-section").classList.toggle("hidden", type !== "url");
        $("audio-file-section").classList.toggle("hidden", type !== "file");

        hideError("audio-url-error");
        clearLoadedSeparateAudioPreview();
    }

    function onAudioFileSelect(e) {
        const file = e.target.files[0];
        if (!file) return;

        const isAudio = file.type.startsWith("audio/");
        const isVideo = file.type.startsWith("video/");
        if (!isAudio && !isVideo) {
            showError("audio-url-error", "Please select an audio or video file.");
            separateAudioFile = null;
            hide("audio-file-info");
            clearLoadedSeparateAudioPreview();
            return;
        }

        separateAudioFile = file;
        $("audio-file-name").textContent = file.name;
        show("audio-file-info");
        hideError("audio-url-error");
        clearLoadedSeparateAudioPreview();
    }

    function onAudioToggle(e) {
        useSeparateAudio = e.target.checked;
        if (useSeparateAudio) {
            show("audio-source-section");
            setAudioSourceType(audioSourceType);
            if (!$("audio-start-input").value.trim()) {
                $("audio-start-input").value = "0:00";
            }
            syncAudioEndToClipDuration();
            updatePreviewAudioRouting();
            syncSeparateAudioWithVideo(true);
        } else {
            hide("audio-source-section");
            audioValidated = false;
            audioUrl = "";
            audioDuration = 0;
            separateAudioFile = null;
            $("audio-url-input").value = "";
            $("audio-file-input").value = "";
            hide("audio-video-info");
            hide("audio-file-info");
            hideError("audio-url-error");
            clearLoadedSeparateAudioPreview();
        }
    }

    function onStaticImageToggle(e) {
        useStaticImage = e.target.checked;
        if (useStaticImage) {
            show("static-image-section");
            if (jobId && staticImagePreviewUrl) {
                loadImagePreview(staticImagePreviewUrl);
            }
        } else {
            hide("static-image-section");
            if (jobId) {
                loadVideoPreview();
            }
        }
    }

    function onStaticImageSelect(e) {
        const file = e.target.files[0];
        if (!file) return;

        if (!file.type.startsWith("image/")) {
            showError("step2-error", "Please select an image file for static image mode.");
            return;
        }

        staticImageFile = file;
        hideError("step2-error");

        const reader = new FileReader();
        reader.onload = (ev) => {
            staticImagePreviewUrl = ev.target.result;
            $("static-image-preview").src = staticImagePreviewUrl;
            show("image-preview-container");

            if (useStaticImage && jobId) {
                loadImagePreview(staticImagePreviewUrl);
            }
        };
        reader.readAsDataURL(file);
    }

    // ── Source Type Toggle ──────────────────────────────────────

    function setSourceType(type) {
        sourceType = type;

        // Update button states
        $("source-url-btn").classList.toggle("active", type === "url");
        $("source-file-btn").classList.toggle("active", type === "file");

        // Show/hide sections
        if (type === "url") {
            show("url-source-section");
            hide("file-source-section");
        } else {
            hide("url-source-section");
            show("file-source-section");
        }

        // Clear errors
        hideError("step1-error");
    }

    function setupFileDragDrop() {
        const dropZone = $("file-drop-zone");
        if (!dropZone) return;

        ["dragenter", "dragover", "dragleave", "drop"].forEach(eventName => {
            dropZone.addEventListener(eventName, (e) => {
                e.preventDefault();
                e.stopPropagation();
            });
        });

        ["dragenter", "dragover"].forEach(eventName => {
            dropZone.addEventListener(eventName, () => {
                dropZone.classList.add("drag-over");
            });
        });

        ["dragleave", "drop"].forEach(eventName => {
            dropZone.addEventListener(eventName, () => {
                dropZone.classList.remove("drag-over");
            });
        });

        dropZone.addEventListener("drop", (e) => {
            const files = e.dataTransfer.files;
            if (files.length > 0 && files[0].type.startsWith("video/")) {
                $("local-video-input").files = files;
                onLocalVideoSelect({ target: { files } });
            }
        });

        // Click anywhere on drop zone to open file picker
        dropZone.addEventListener("click", (e) => {
            if (e.target.tagName !== "BUTTON") {
                $("local-video-input").click();
            }
        });
    }

    function onLocalVideoSelect(e) {
        const file = e.target.files[0];
        if (!file) return;

        if (!file.type.startsWith("video/")) {
            showError("step1-error", "Please select a video file.");
            return;
        }

        localVideoFile = file;
        $("selected-file-name").textContent = file.name;
        show("selected-file-info");
        hideError("step1-error");

        // Automatically upload and process the file
        uploadLocalVideo(file);
    }

    async function uploadLocalVideo(file) {
        lockExportForCurrentWorkflow();
        clearWorkflowErrors();
        setLoading("source-file-btn", true);
        $("download-status-text").textContent = "Uploading video...";
        show("download-status");

        try {
            const formData = new FormData();
            formData.append("video", file);

            const resp = await fetch("/api/upload-video", {
                method: "POST",
                body: formData,
            });

            const data = await resp.json();

            if (data.error) {
                showError("step1-error", data.error);
                hide("download-status");
                setLoading("source-file-btn", false);
                return;
            }

            jobId = data.job_id;
            videoDuration = data.duration || 0;
            clearLoadedSeparateAudioPreview();
            $("video-title").textContent = file.name;
            $("video-duration").textContent = formatDuration(videoDuration);
            show("video-info");

            $("download-status-text").textContent = "Loading preview...";

            // Poll for completion
            pollDownload(jobId);

        } catch (e) {
            showError("step2-error", "Download failed: " + e.message);
            hide("download-status");
            setLoading("source-file-btn", false);
        }
    }

    function pollDownload(id) {
        if (pollTimer) clearInterval(pollTimer);

        pollTimer = setInterval(async () => {
             try {
                const data = await api(`/api/status/${id}`);

                if (data.status === "error") {
                    clearInterval(pollTimer);
                    pollTimer = null;
                    showError("step2-error", data.error || "Download failed");
                    hide("download-status");
                    setLoading("source-file-btn", false);
                    setLoading("validate-btn", false);
                    return;
                }

                if (data.status === "downloaded") {
                    clearInterval(pollTimer);
                    pollTimer = null;
                    $("download-status-text").textContent = "Downloaded! Loading preview...";

                    finishDownload();
                    return;
                }

                // Update progress text if needed
                if (data.stage) {
                     $("download-status-text").textContent = data.stage;
                }

             } catch (e) {
                 // ignore network errors, retry
             }
        }, 1000);
    }

    async function finishDownload() {
        try {
            // Get video info
            videoInfo = await api(`/api/video-info/${jobId}`);

            // Load video preview
            loadVideoPreview();

            clearWorkflowErrors();
            hide("download-status");
            enableStep(2);
            enableStep(3);
            enableStep(4);

        } catch (e) {
            showError("step1-error", "Failed to load video: " + e.message);
            hide("download-status");
        } finally {
            setLoading("source-file-btn", false);
            setLoading("validate-btn", false);
        }
    }

    // ── Video Preview ────────────────────────────────────────────

    function setPreviewControlVisibility(visible) {
        const controls = $("preview-controls");
        const volume = $("preview-volume-control");
        if (controls) controls.classList.toggle("hidden", !visible);
        if (volume) volume.classList.toggle("hidden", !visible);
    }

    function loadImagePreview(imageUrl) {
        if (!cropPreview) {
            cropPreview = new CropPreview();
        }
        if (cropPreview.setResizeLock) cropPreview.setResizeLock(false);
        if (!imageUrl) return;

        // Reset previous media state
        cropPreview.reset();
        show("video-sidebar");
        setPreviewControlVisibility(true);

        cropPreview.initializeImage(imageUrl);

        // In static image mode, keep crop image visible but use hidden crop-video for audio preview.
        const audioPreview = $("crop-video");
        audioPreview.loop = false;
        const playBtn = $("preview-play-btn");
        if (playBtn) playBtn.textContent = "▶ Play Audio";

        audioPreview.onerror = () => {
            console.error("Audio preview load error");
            showError("step2-error", "Failed to load audio preview.");
        };

        audioPreview.src = `/api/serve-clip/${jobId}?t=${Date.now()}`;
        audioPreview.onloadedmetadata = () => {
            onVolumeChange();
            updatePreviewAudioRouting();
            // Use actual source duration when available.
            const dur = audioPreview.duration || (videoInfo && Number(videoInfo.duration)) || videoDuration || clipDuration || 0;
            initTrimSliders(Math.max(0, dur));
            audioPreview.currentTime = trimStart;
            audioPreview.pause();
            syncSeparateAudioWithVideo(true);
            if (playBtn) playBtn.textContent = "▶ Play Audio";
        };

        audioPreview.ontimeupdate = () => {
            if (!audioPreview.paused && audioPreview.currentTime >= trimEnd) {
                audioPreview.currentTime = trimStart;
            }
            syncSeparateAudioWithVideo(false);
        };
        audioPreview.onended = () => {
            audioPreview.currentTime = trimStart;
            syncSeparateAudioWithVideo(true);
            audioPreview.play().catch(() => { });
        };

        // Immediate fallback duration so sliders are usable before metadata resolves.
        const fallbackDur = (videoInfo && Number(videoInfo.duration)) || videoDuration || clipDuration || 0;
        initTrimSliders(Math.max(0, fallbackDur));
    }

    function loadVideoPreview() {
        if (useStaticImage && staticImagePreviewUrl) {
            loadImagePreview(staticImagePreviewUrl);
            return;
        }

        if (!cropPreview) {
            cropPreview = new CropPreview();
        }
        if (cropPreview.setResizeLock) cropPreview.setResizeLock(false);

        // Reset previous state
        cropPreview.reset();

        // Show the video sidebar
        show("video-sidebar");
        setPreviewControlVisibility(true);

        const video = document.getElementById("crop-video");
        video.loop = false;

        video.onerror = () => {
            console.error("Video load error");
            showError("step2-error", "Failed to load video preview.");
        };

        // Start loading
        video.src = `/api/serve-clip/${jobId}?t=${Date.now()}`;

        video.onloadedmetadata = () => {
            // Re-apply slider volume to the newly loaded media.
            onVolumeChange();
            updatePreviewAudioRouting();
            cropPreview.initialize(video.videoWidth, video.videoHeight);

            // Initialize trim sliders
            const dur = video.duration;
            initTrimSliders(dur);
            syncSeparateAudioWithVideo(true);
        };

        // Handle looping within trim region
        video.ontimeupdate = () => {
            if (!video.paused && video.currentTime >= trimEnd) {
                video.currentTime = trimStart;
            }
            syncSeparateAudioWithVideo(false);
        };
        video.onended = () => {
            video.currentTime = trimStart;
            syncSeparateAudioWithVideo(true);
            video.play().catch(() => { });
        };
    }

    // ── Step 4: Process & Export ─────────────────────────────────

    async function processVideo() {
        if (!cropPreview) {
            showError("step4-error", "Crop preview not ready.");
            return;
        }

        if (useStaticImage && !staticImageFile) {
            showError("step4-error", "Please select a static image file.");
            return;
        }
        if (useSeparateAudio && !separateAudioPreviewLoaded) {
            showError("step4-error", "Load the separate audio source first (Load URL / Load File).");
            return;
        }

        hideError("step4-error");
        const cropParams = cropPreview.getCropParams();
        if (cropPreview.setResizeLock) cropPreview.setResizeLock(true);

        setLoading("process-btn", true);
        show("progress-section");
        requestAnimationFrame(() => cropPreview?.realignLockedOverlay?.());
        setTimeout(() => cropPreview?.realignLockedOverlay?.(), 200);
        lockExportForCurrentWorkflow();

        try {
            const settings = getSettings();
            const formData = new FormData();
            formData.append("job_id", jobId);
            formData.append("crop", JSON.stringify(cropParams));
            formData.append("trim_start", trimStart);
            formData.append("trim_end", trimEnd);
            formData.append("use_separate_audio", useSeparateAudio);
            formData.append("use_static_image", useStaticImage);
            formData.append("audio_fade_mode", $("audio-fade-mode")?.value || "none");
            formData.append("audio_fade_duration", settings.audioFadeDuration || "0.35");
            formData.append("settings", JSON.stringify(settings));

            if (useSeparateAudio) {
                const audioStart = $("audio-start-input").value.trim();
                const audioEnd = $("audio-end-input").value.trim();
                formData.append("audio_source_type", audioSourceType);
                formData.append("audio_start", audioStart);
                formData.append("audio_end", audioEnd);

                if (audioSourceType === "url") {
                    if (!audioValidated || !audioUrl) {
                        throw new Error("Please validate the separate audio URL first.");
                    }
                    formData.append("audio_url", audioUrl);
                } else {
                    if (!separateAudioFile) {
                        throw new Error("Please select a local audio file.");
                    }
                    formData.append("separate_audio_file", separateAudioFile);
                }
            }

            if (useStaticImage && staticImageFile) {
                formData.append("static_image", staticImageFile);
            }

            const resp = await fetch("/api/process", {
                method: "POST",
                body: formData,
            });

            if (!resp.ok) {
                const err = await resp.json();
                throw new Error(err.error || "Processing failed");
            }

            // Start polling
            pollProgress();
        } catch (e) {
            showError("step4-error", "Failed to start processing: " + e.message);
            if (cropPreview?.setResizeLock) cropPreview.setResizeLock(false);
            setLoading("process-btn", false);
        }
    }

    function pollProgress() {
        if (pollTimer) clearInterval(pollTimer);

        pollTimer = setInterval(async () => {
            try {
                const data = await api(`/api/status/${jobId}`);

                if (data.status === "complete") {
                    clearInterval(pollTimer);
                    pollTimer = null;
                    if (cropPreview?.setResizeLock) cropPreview.setResizeLock(false);
                    $("progress-bar").style.width = "100%";
                    $("progress-text").textContent = "Done!";
                    downloadableJobId = jobId;
                    const exportBtn = $("export-btn");
                    if (exportBtn) exportBtn.disabled = false;
                    show("download-section");
                    setLoading("process-btn", false);
                    return;
                }

                if (data.status === "error") {
                    clearInterval(pollTimer);
                    pollTimer = null;
                    if (cropPreview?.setResizeLock) cropPreview.setResizeLock(false);
                    showError("step4-error", data.error || "Processing failed");
                    hide("progress-section");
                    setLoading("process-btn", false);
                    return;
                }

                // Update progress
                const pct = data.progress || 0;
                $("progress-bar").style.width = pct + "%";
                $("progress-text").textContent = data.stage || "Processing...";

            } catch (e) {
                // Network error, keep trying
            }
        }, 1000);
    }

    function downloadResult() {
        if (!jobId || downloadableJobId !== jobId) {
            showError("step4-error", "Result is not ready yet. Process this clip first.");
            return;
        }
        window.location.href = `/api/download-result/${jobId}`;
    }

    // ── Public API ──────────────────────────────────────────────

    // Auto-init when DOM is ready
    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", init);
    } else {
        init();
    }

    // ── Settings ────────────────────────────────────────────────

    function getSettings() {
        return {
            resolution: $("setting-resolution")?.value || "720",
            bufferDuration: $("setting-buffer")?.value || "2",
            normalizeAudio: $("setting-normalize-audio")?.checked ?? true,
            audioFadeDuration: $("setting-audio-fade-duration")?.value || "0.35",
        };
    }

    function loadSettings() {
        try {
            const saved = localStorage.getItem("alertCreatorSettings");
            if (saved) {
                const settings = JSON.parse(saved);
                if (settings.resolution) $("setting-resolution").value = settings.resolution;
                if (settings.bufferDuration) $("setting-buffer").value = settings.bufferDuration;
                if (typeof settings.normalizeAudio === "boolean") {
                    $("setting-normalize-audio").checked = settings.normalizeAudio;
                }
                if (settings.audioFadeDuration) {
                    $("setting-audio-fade-duration").value = settings.audioFadeDuration;
                }
            }
        } catch (e) {
            // Ignore errors
        }

        updateSettingsPanelLabels(getSettings());

        // Save on change
        ["setting-resolution", "setting-buffer", "setting-normalize-audio", "setting-audio-fade-duration"].forEach(id => {
            $(id)?.addEventListener("change", saveSettings);
        });
    }

    function saveSettings() {
        const settings = getSettings();
        try {
            localStorage.setItem("alertCreatorSettings", JSON.stringify(settings));
        } catch (e) {
            // Ignore errors
        }
        updateSettingsPanelLabels(settings);
    }

    function resetSettings() {
        $("setting-resolution").value = "720";
        $("setting-buffer").value = "2";
        $("setting-normalize-audio").checked = true;
        $("setting-audio-fade-duration").value = "0.35";
        saveSettings();

        // Visual feedback
        const btn = $("reset-settings-btn") || $("navbar-reset-btn");
        if (!btn) return;
        btn.textContent = "Reset!";
        setTimeout(() => {
            const labelEl = btn.querySelector(".pill-label");
            if (labelEl) {
                labelEl.textContent = "Reset to Defaults";
            } else {
                btn.textContent = "Reset to Defaults";
            }
        }, 1000);

        const labelEl = btn.querySelector(".pill-label");
        if (labelEl) {
            labelEl.textContent = "Reset!";
        }
    }

    async function shutdownApp() {
        if (!confirm("Are you sure you want to quit the application?")) return;
        try {
            await api("/api/shutdown", { method: "POST" });
            document.body.innerHTML = "<div style='display:flex;justify-content:center;align-items:center;height:100vh;background:#0f151d;color:#d7e0eb;font-family:sans-serif;'><h2>Application has been closed. You can close this tab.</h2></div>";
        } catch (e) {
            alert("Failed to quit app");
        }
    }

    return {
        validateUrl,
        validateAudioUrl,
        loadSeparateAudioUrl,
        loadSeparateAudioFile,
        setSourceType,
        setAudioSourceType,
        processVideo,
        downloadResult,
        resetSettings,
        shutdownApp,
        installMissingDependencies,
        toggleSettingsPanel,
        openSettingsPanel,
    };
})();
