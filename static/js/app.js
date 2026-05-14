/**
 * Main application logic for deutschmark's Alert! Alert!
 * Manages the 4-step workflow, API calls, and UI state.
 */
const App = (() => {
    const AUTO_DOWNLOAD_CONSENT_KEY = "alertCreatorAutoDownloadConsent";
    const DEFAULT_THEME_ID = "cobalt-night";
    const AVAILABLE_THEME_IDS = new Set([
        "cobalt-night",
        "graphite-terminal",
        "nord-stack",
        "forest-syntax",
        "signal-teal",
    ]);

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
    let dependencyUpdateInFlight = false;
    let lastDeps = null;

    // ── Onboarding state ────────────────────────────────────────
    const ONBOARDING_DONE_KEY = "alertAlertOnboardingDone";
    const TOUR_SAMPLE_URL = "https://www.youtube.com/watch?v=dQw4w9WgXcQ";
    let onboardingPhase = null; // null | "welcome" | "deps" | "tour" | "done"

    let dependencyDropdownCloseHandlersBound = false;
    let dependencyBannerTimer = null;
    let storageConfig = null;

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
    let staticImageSourceType = "image"; // "image" or "video"

    // Local video state
    let sourceType = "url"; // "url" or "file"
    let localVideoFile = null;

    // Trim state
    let trimStart = 0;
    let trimEnd = 0;
    const AUDIO_TRIM_MIN_SPAN = 0.05;

    // ── Helpers ─────────────────────────────────────────────────

    function $(id) {
        return document.getElementById(id);
    }

    function normalizeThemeId(themeId) {
        const normalized = String(themeId || "").trim();
        return AVAILABLE_THEME_IDS.has(normalized) ? normalized : DEFAULT_THEME_ID;
    }

    function applyTheme(themeId) {
        const normalized = normalizeThemeId(themeId);
        document.documentElement.dataset.theme = normalized;
        const select = $("setting-theme");
        if (select && select.value !== normalized) {
            select.value = normalized;
        }
        return normalized;
    }

    function show(el) {
        if (typeof el === "string") el = $(el);
        el.classList.remove("hidden");
    }

    function hide(el) {
        if (typeof el === "string") el = $(el);
        el.classList.add("hidden");
    }

    function clearAlternateVisualPreviewUrl() {
        if (staticImageSourceType === "video" && staticImagePreviewUrl.startsWith("blob:")) {
            URL.revokeObjectURL(staticImagePreviewUrl);
        }
        staticImagePreviewUrl = "";
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
        const totalHundredths = Math.max(0, Math.round((Number(seconds) || 0) * 100));
        const mins = Math.floor(totalHundredths / 6000);
        const secs = Math.floor((totalHundredths % 6000) / 100);
        const hundredths = totalHundredths % 100;
        return `${mins}:${secs.toString().padStart(2, "0")}.${hundredths.toString().padStart(2, "0")}`;
    }

    /**
     * Parse timestamp string to seconds.
     * Supports:
     * - "1:30.25", "1:30", "1:30:00.50"
     * - compact centiseconds like "0:012" (0m, 0.12s) or "0:1425" (0m, 14.25s)
     * - "90" (as seconds), "1.5" (as seconds)
     */
    function parseTimestamp(ts) {
        const trimmed = String(ts || "").trim();
        if (!trimmed) return 0;

        const parseSecondPart = (part) => {
            const clean = String(part || "").trim();
            if (!clean) return 0;
            if (clean.includes(".")) return parseFloat(clean) || 0;
            if (/^\d{3,4}$/.test(clean)) {
                const whole = parseInt(clean.slice(0, -2) || "0", 10);
                const hundredths = parseInt(clean.slice(-2), 10);
                return whole + hundredths / 100;
            }
            return parseFloat(clean) || 0;
        };

        // If contains colon, parse as time format
        if (trimmed.includes(":")) {
            const parts = trimmed.split(":").map((p) => p.trim());
            if (parts.length === 3) {
                const hours = parseFloat(parts[0]) || 0;
                const mins = parseFloat(parts[1]) || 0;
                const secs = parseSecondPart(parts[2]);
                return hours * 3600 + mins * 60 + secs;
            }
            if (parts.length === 2) {
                const mins = parseFloat(parts[0]) || 0;
                const secs = parseSecondPart(parts[1]);
                return mins * 60 + secs;
            }
            return parseSecondPart(parts[0]);
        }

        // Otherwise treat as seconds
        return parseFloat(trimmed) || 0;
    }

    /**
     * Format a timestamp input value to MM:SS.hh precision.
     * Called on blur to auto-format user input.
     */
    function formatTimestampInput(input) {
        const value = input.value.trim();
        if (!value) return;

        const seconds = parseTimestamp(value);
        if (isNaN(seconds) || seconds < 0) {
            input.value = "0:00.00";
            return;
        }

        input.value = formatDuration(seconds);
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
        return parseTimestamp($("audio-start-input")?.value || "0:00.00");
    }

    function getSeparateAudioEndSeconds() {
        return parseTimestamp($("audio-end-input")?.value || "0:00.00");
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
        audioDuration = Number(audio.duration || audioDuration || 0);
        separateAudioPreviewLoaded = true;
        applySeparateAudioTrimConstraints("start", { syncPreview: false });
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

    function escapeHtml(value) {
        return String(value || "")
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;")
            .replace(/'/g, "&#39;");
    }

    function getAutoDownloadConsent() {
        try {
            return localStorage.getItem(AUTO_DOWNLOAD_CONSENT_KEY) || "";
        } catch (e) {
            return "";
        }
    }

    function setAutoDownloadConsent(value) {
        try {
            localStorage.setItem(AUTO_DOWNLOAD_CONSENT_KEY, value);
        } catch (e) {
            // Ignore storage errors.
        }
    }

    function buildDependencyDisclosureHtml(deps) {
        const disclosure = deps?.download_disclosure || {};
        const runtimePath = escapeHtml(disclosure.runtime_path || "your local app runtime folder");
        const sources = disclosure.sources || {};
        const ffmpegSource = escapeHtml(sources.ffmpeg || "FFmpeg mirror");
        const ytdlpSource = escapeHtml(sources["yt-dlp"] || "yt-dlp releases");
        const denoSource = escapeHtml(sources.deno || "Deno releases");
        return [
            "<strong>Before download:</strong> this app can save runtime tools on your computer.",
            `<strong>Install location:</strong> <code>${runtimePath}</code>`,
            "<strong>Tools:</strong> required ffmpeg/ffprobe + yt-dlp, optional deno",
            `<strong>Sources:</strong><br><code>${ffmpegSource}</code><br><code>${ytdlpSource}</code><br><code>${denoSource}</code>`,
        ].join("<br>");
    }

    function showAutoDownloadConsentPrompt(deps) {
        setPanelOpen("dependency-settings-panel", true);
        const actions = `
            <div class="dep-choice-actions">
                <button type="button" class="dep-choice-btn" onclick="App.allowDependencyAutoDownload()">Allow Auto-Download</button>
                <button type="button" class="dep-choice-btn secondary-btn" onclick="App.useManualDependencySetup()">Manual Install Only</button>
            </div>
        `;
        setDependencyBanner(
            "<strong>Permission required:</strong> allow dependency downloads?",
            `${buildDependencyDisclosureHtml(deps)}${actions}`,
            false
        );
    }

    function hideDependencyBanner() {
        if (dependencyBannerTimer) {
            clearTimeout(dependencyBannerTimer);
            dependencyBannerTimer = null;
        }
        hide("dep-banner");
    }

    function renderStorageConfig(config) {
        storageConfig = config || null;
        const input = $("output-dir-input");
        const status = $("output-dir-status");
        if (!input || !status || !storageConfig) return;
        input.value = storageConfig.output_dir || "";
        const usingDefault = !storageConfig.custom_output_dir;
        status.textContent = usingDefault
            ? `Finished exports save to the default folder: ${storageConfig.output_dir}`
            : `Finished exports save to: ${storageConfig.output_dir}`;
    }

    async function loadStorageConfig() {
        try {
            const config = await api("/api/storage-config");
            renderStorageConfig(config);
        } catch (e) {
            const status = $("output-dir-status");
            if (status) status.textContent = "Could not load save location settings.";
        }
    }

    async function applyOutputFolder() {
        const input = $("output-dir-input");
        if (!input) return;
        const data = await api("/api/storage-config", {
            method: "PUT",
            body: JSON.stringify({ output_dir: input.value.trim() }),
        });
        if (data.error) {
            setDependencyBanner(
                "<strong>Save location update failed.</strong>",
                escapeHtml(data.error),
                true
            );
            return;
        }
        renderStorageConfig(data);
    }

    async function resetOutputFolder() {
        const data = await api("/api/storage-config/reset", { method: "POST" });
        if (data.error) {
            setDependencyBanner(
                "<strong>Save location reset failed.</strong>",
                escapeHtml(data.error),
                true
            );
            return;
        }
        renderStorageConfig(data);
    }

    async function chooseOutputFolder() {
        const data = await api("/api/storage-config/choose", { method: "POST" });
        if (data.status === "cancelled") return;
        if (data.error) {
            setDependencyBanner(
                "<strong>Folder picker failed.</strong>",
                `${escapeHtml(data.error)}<br>Paste a path into Save Location and click <strong>Use Path</strong> instead.`,
                true
            );
            return;
        }
        renderStorageConfig(data);
    }

    function setDependencyBanner(messageHtml, instructionsHtml = "", showAsError = true, autoHideMs = 0) {
        const banner = $("dep-banner");
        const msg = $("dep-message");
        const instEl = $("dep-instructions");
        if (!banner || !msg || !instEl) return;
        if (dependencyBannerTimer) {
            clearTimeout(dependencyBannerTimer);
            dependencyBannerTimer = null;
        }
        msg.innerHTML = messageHtml;
        instEl.innerHTML = instructionsHtml;
        banner.classList.toggle("banner-error", showAsError);
        show(banner);
        if (autoHideMs > 0) {
            dependencyBannerTimer = setTimeout(() => {
                hideDependencyBanner();
            }, autoHideMs);
        }
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

    function bindDependencyDropdownCloseHandlers() {
        if (dependencyDropdownCloseHandlersBound) return;
        dependencyDropdownCloseHandlersBound = true;

        document.addEventListener("click", (event) => {
            const panel = $("dependency-settings-panel");
            const toggleBtn = $("dependency-settings-toggle");
            if (!panel || !toggleBtn || !panel.classList.contains("open")) return;

            const target = event.target;
            if (panel.contains(target) || toggleBtn.contains(target)) return;
            setPanelOpen("dependency-settings-panel", false);
        });

        document.addEventListener("keydown", (event) => {
            if (event.key !== "Escape") return;
            const panel = $("dependency-settings-panel");
            if (!panel || !panel.classList.contains("open")) return;
            setPanelOpen("dependency-settings-panel", false);
        });
    }

    function updateAudioFadeNote(settings = getSettings()) {
        const note = $("audio-fade-note");
        if (!note) return;
        note.textContent = `Fade length set to ${settings.audioFadeDuration}s in Audio Processing Settings`;
    }

    function updateSettingsPanelLabels(settings = getSettings()) {
        const audioPillValue = $("audio-pill-value");
        if (audioPillValue) {
            const normalizeLabel = settings.normalizeAudio ? "Normalize Audio On" : "Normalize Audio Off";
            audioPillValue.textContent = `${normalizeLabel} · ${settings.audioFadeDuration}s`;
        }
        updateAudioFadeNote(settings);
    }

    function renderDependencyStatus(deps) {
        lastDeps = deps;
        document.dispatchEvent(new CustomEvent("dm:deps-status", {
            detail: deps,
        }));
        const missing = [];
        const instructions = [];

        const ffmpegStatus = $("dep-ffmpeg-status");
        const ytdlpStatus = $("dep-ytdlp-status");
        const denoStatus = $("dep-deno-status");
        const installBtn = $("auto-install-deps-btn");
        const updateBtn = $("update-ytdlp-btn");

        if (deps.ffmpeg?.installed && deps.ffprobe?.installed) {
            ffmpegStatus.textContent = "✓ Installed";
            ffmpegStatus.className = "dep-status installed";
        } else {
            ffmpegStatus.textContent = "✗ Missing";
            ffmpegStatus.className = "dep-status missing";
            missing.push("FFmpeg/ffprobe");
            instructions.push("FFmpeg: Use Auto Install in Dependency Setup (top-right). If needed, run 'winget install Gyan.FFmpeg'.");
        }

        if (deps["yt-dlp"]?.installed) {
            ytdlpStatus.textContent = "✓ Installed";
            ytdlpStatus.className = "dep-status installed";
        } else {
            ytdlpStatus.textContent = "✗ Missing";
            ytdlpStatus.className = "dep-status missing";
            missing.push("yt-dlp");
            instructions.push("yt-dlp: Use Auto Install in Dependency Setup (top-right), or install manually.");
        }

        if (denoStatus) {
            if (deps.deno?.installed) {
                denoStatus.textContent = "✓ Installed";
                denoStatus.className = "dep-status installed";
            } else {
                denoStatus.textContent = "⚠ Optional (Missing)";
                denoStatus.className = "dep-status missing";
                instructions.push("When auto-download is allowed, Deno install is attempted by default. If still missing, retry Auto Install or run 'winget install DenoLand.Deno' for better YouTube challenge handling.");
            }
        }

        if (installBtn) {
            const shouldShowInstall = deps.auto_install_available && (missing.length > 0 || !deps.deno?.installed);
            installBtn.classList.toggle("hidden", !shouldShowInstall);
            installBtn.disabled = dependencyInstallInFlight || dependencyUpdateInFlight || deps.ytdlp_update?.status === "updating";
        }

        if (updateBtn) {
            const updateAvailable = !!deps.ytdlp_update_available;
            const updateStatus = deps.ytdlp_update?.status || "idle";
            updateBtn.classList.toggle("hidden", !updateAvailable);
            updateBtn.disabled = dependencyUpdateInFlight
                || updateStatus === "updating"
                || dependencyInstallInFlight
                || deps.bootstrap?.status === "installing";
            updateBtn.textContent = updateStatus === "updating"
                ? "Updating yt-dlp..."
                : "Update yt-dlp (1-click)";
        }

        const depPillValue = $("dependency-pill-value");
        if (depPillValue) {
            if (deps.bootstrap?.status === "installing") {
                depPillValue.textContent = "Installing...";
            } else if (deps.ytdlp_update?.status === "updating") {
                depPillValue.textContent = "Updating yt-dlp...";
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
            hideDependencyBanner();
        }

        return missing;
    }

    async function installMissingDependencies(manual = false) {
        const consent = getAutoDownloadConsent();
        if (consent !== "allow") {
            if (!manual) {
                return;
            }
            setAutoDownloadConsent("allow");
        }

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
            "Downloading missing tools to your local app folder (includes optional Deno).",
            false
        );
        try {
            const deps = await api("/api/bootstrap-deps", { method: "POST" });
            renderDependencyStatus(deps);
            const requiredMissingCount = (deps.required_missing || []).length;
            const denoMissing = !deps.deno?.installed;
            if (requiredMissingCount === 0 && !denoMissing) {
                if (installBtn) installBtn.classList.add("hidden");
            } else if (manual && requiredMissingCount > 0) {
                setDependencyBanner(
                    "<strong>Dependencies are still missing.</strong>",
                    "Use the dependency troubleshooting list, then restart the app after manual install.",
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

    async function updateYtdlp() {
        if (dependencyUpdateInFlight) return;
        dependencyUpdateInFlight = true;

        const updateBtn = $("update-ytdlp-btn");
        const previousLabel = updateBtn?.textContent || "";
        if (updateBtn) {
            updateBtn.disabled = true;
            updateBtn.textContent = "Updating yt-dlp...";
        }

        setDependencyBanner(
            "<strong>Updating yt-dlp...</strong>",
            "Downloading the latest yt-dlp build to your local app runtime folder.",
            false
        );

        try {
            const deps = await api("/api/update-ytdlp", { method: "POST" });
            renderDependencyStatus(deps);

            const updateState = deps?.ytdlp_update || {};
            const updateError = updateState.last_error || "";
            const updateMessage = updateState.message || "";
            if (updateState.status === "failed") {
                setDependencyBanner(
                    "<strong>yt-dlp update failed.</strong>",
                    updateError || "Check internet access and retry.",
                    true
                );
            } else {
                setDependencyBanner(
                    "<strong>yt-dlp is ready.</strong>",
                    escapeHtml(updateMessage || "Update complete."),
                    false,
                    4000
                );
            }
        } catch (e) {
            setDependencyBanner(
                "<strong>yt-dlp update failed.</strong>",
                "Check your internet connection and try again.",
                true
            );
        } finally {
            dependencyUpdateInFlight = false;
            if (updateBtn && !updateBtn.classList.contains("hidden")) {
                updateBtn.disabled = false;
                updateBtn.textContent = previousLabel || "Update yt-dlp (1-click)";
            }
        }
    }

    // ── Onboarding ───────────────────────────────────────────────

    function isOnboardingDone() {
        return localStorage.getItem(ONBOARDING_DONE_KEY) === "1";
    }

    function markOnboardingDone() {
        localStorage.setItem(ONBOARDING_DONE_KEY, "1");
    }

    function clearOnboardingDone() {
        localStorage.removeItem(ONBOARDING_DONE_KEY);
    }

    function showWelcomeScreen() {
        onboardingPhase = "welcome";
        const el = $("welcome-screen");
        if (el) el.classList.remove("hidden");
    }

    function hideWelcomeScreen() {
        const el = $("welcome-screen");
        if (el) el.classList.add("hidden");
    }

    function onboardingSkip() {
        hideWelcomeScreen();
        markOnboardingDone();
        onboardingPhase = null;
    }

    function onboardingStartTour() {
        hideWelcomeScreen();
        if (depsAreReady()) {
            startTour();
        } else {
            showDepGate();
        }
    }

    function restartOnboarding() {
        setPanelOpen("dependency-settings-panel", false);
        clearOnboardingDone();
        showWelcomeScreen();
    }

    function showDepGate() {
        onboardingPhase = "deps";
        renderDepGate();
        const el = $("dep-gate-screen");
        if (el) el.classList.remove("hidden");
    }

    function hideDepGate() {
        const el = $("dep-gate-screen");
        if (el) el.classList.add("hidden");
    }

    function depsAreReady() {
        const d = lastDeps;
        if (!d) return false;
        return !!(d.ffmpeg?.installed && d.ffprobe?.installed && d["yt-dlp"]?.installed);
    }

    function renderDepGate() {
        const status = $("dep-gate-status");
        const installBtn = $("dep-gate-install-btn");
        if (!status || !installBtn) return;

        const rows = [
            { name: "FFmpeg / ffprobe", installed: !!(lastDeps?.ffmpeg?.installed && lastDeps?.ffprobe?.installed) },
            { name: "yt-dlp",           installed: !!lastDeps?.["yt-dlp"]?.installed },
        ];
        status.innerHTML = rows.map(r => `
            <div class="dep-gate-row">
                <span class="dep-gate-row-name">${escapeHtml(r.name)}</span>
                <span class="dep-gate-row-status ${r.installed ? "installed" : "missing"}">${r.installed ? "Installed" : "Missing"}</span>
            </div>
        `).join("");

        const ready = depsAreReady();
        const busy = dependencyInstallInFlight || lastDeps?.bootstrap?.status === "installing";

        if (ready) {
            installBtn.textContent = "Continue Tour";
            installBtn.disabled = false;
        } else if (busy) {
            installBtn.textContent = "Installing...";
            installBtn.disabled = true;
        } else {
            installBtn.textContent = "Install Required Tools";
            installBtn.disabled = false;
        }
    }

    async function onboardingInstallDeps() {
        if (depsAreReady()) {
            hideDepGate();
            startTour();
            return;
        }
        dependencyInstallInFlight = true;
        renderDepGate();
        try {
            const deps = await api("/api/bootstrap-deps", { method: "POST" });
            lastDeps = deps;
            renderDependencyStatus(deps);
        } catch (_) {}
        dependencyInstallInFlight = false;
        renderDepGate();
    }

    function startTour() {
        onboardingPhase = "tour";
        // Task 6 wires the tour state machine here. For now, mark done so the
        // skeleton doesn't strand the user.
        markOnboardingDone();
        onboardingPhase = null;
    }

    // ── Tour: coach-mark engine ─────────────────────────────────
    let tourReflowBound = false;
    let tourCurrentTarget = null;

    function showTourOverlay() {
        const el = $("tour-overlay");
        if (el) {
            el.classList.remove("hidden");
            el.setAttribute("aria-hidden", "false");
        }
        if (!tourReflowBound) {
            window.addEventListener("scroll", reflowSpotlight, true);
            window.addEventListener("resize", reflowSpotlight);
            tourReflowBound = true;
        }
    }

    function hideTourOverlay() {
        const el = $("tour-overlay");
        if (el) {
            el.classList.add("hidden");
            el.setAttribute("aria-hidden", "true");
        }
        tourCurrentTarget = null;
        if (tourReflowBound) {
            window.removeEventListener("scroll", reflowSpotlight, true);
            window.removeEventListener("resize", reflowSpotlight);
            tourReflowBound = false;
        }
    }

    function reflowSpotlight() {
        if (!tourCurrentTarget) return;
        const el = typeof tourCurrentTarget === "string" ? $(tourCurrentTarget) : tourCurrentTarget;
        if (!el) return;
        positionSpotlight(el);
    }

    function positionSpotlight(target) {
        const rect = target.getBoundingClientRect();
        const pad = 10;
        const top = Math.max(0, rect.top - pad);
        const left = Math.max(0, rect.left - pad);
        const width = rect.width + pad * 2;
        const height = rect.height + pad * 2;

        const vw = window.innerWidth;
        const vh = window.innerHeight;
        const scrim = $("tour-scrim");
        if (scrim) {
            // Outer rect + inner cutout subpath. Even-odd fill (default) creates the hole.
            scrim.style.clipPath =
                `path('M 0 0 H ${vw} V ${vh} H 0 Z ` +
                `M ${left} ${top} V ${top + height} H ${left + width} V ${top} Z')`;
        }

        const ring = $("tour-ring");
        if (ring) {
            ring.style.top = top + "px";
            ring.style.left = left + "px";
            ring.style.width = width + "px";
            ring.style.height = height + "px";
        }

        positionTooltip(rect);
    }

    function positionTooltip(targetRect) {
        const tooltip = $("tour-tooltip");
        if (!tooltip) return;
        const tw = tooltip.offsetWidth || 340;
        const th = tooltip.offsetHeight || 200;
        const margin = 16;
        const vw = window.innerWidth;
        const vh = window.innerHeight;

        // Prefer right of target; fall back to left, then below, then above.
        let top, left;
        if (targetRect.right + margin + tw <= vw) {
            left = targetRect.right + margin;
            top = Math.min(vh - th - margin, Math.max(margin, targetRect.top));
        } else if (targetRect.left - margin - tw >= 0) {
            left = targetRect.left - margin - tw;
            top = Math.min(vh - th - margin, Math.max(margin, targetRect.top));
        } else if (targetRect.bottom + margin + th <= vh) {
            left = Math.min(vw - tw - margin, Math.max(margin, targetRect.left));
            top = targetRect.bottom + margin;
        } else {
            left = Math.min(vw - tw - margin, Math.max(margin, targetRect.left));
            top = Math.max(margin, targetRect.top - th - margin);
        }
        tooltip.style.top = top + "px";
        tooltip.style.left = left + "px";
    }

    function waitForTarget(selector, timeoutMs = 5000) {
        return new Promise((resolve) => {
            const deadline = performance.now() + timeoutMs;
            const tick = () => {
                const el = document.querySelector(selector);
                if (el && el.offsetParent !== null) {
                    resolve(el);
                    return;
                }
                if (performance.now() > deadline) {
                    resolve(null);
                    return;
                }
                requestAnimationFrame(tick);
            };
            tick();
        });
    }

    async function spotlightTarget(selector) {
        const target = await waitForTarget(selector);
        if (!target) return null;
        tourCurrentTarget = target;
        positionSpotlight(target);
        return target;
    }

    function onboardingTourNext() { /* wired in Task 6 */ }

    // Temporary test hook for Task 4 verification. Removed in Task 6.
    window.__tourTest = (selector) => {
        showTourOverlay();
        spotlightTarget(selector || "#url-input");
    };

    async function allowDependencyAutoDownload() {
        setAutoDownloadConsent("allow");
        dependencyInstallAttempted = true;
        await installMissingDependencies(false);
    }

    function useManualDependencySetup() {
        setAutoDownloadConsent("manual");
        setDependencyBanner(
            "<strong>Auto-download is disabled.</strong>",
            "Install tools manually from the Dependency Setup links, or click <strong>Auto Install Missing</strong> any time to opt in later.",
            false
        );
    }

    // ── Keyboard Shortcuts ───────────────────────────────────────

    function initKeyboardShortcuts() {
        document.addEventListener("keydown", (e) => {
            // Skip when typing in any input
            const tag = document.activeElement?.tagName?.toLowerCase();
            if (tag === "input" || tag === "textarea" || tag === "select") return;
            // Skip ctrl/meta/alt combos
            if (e.ctrlKey || e.metaKey || e.altKey) return;

            if (e.key === "?" || e.key === "/") {
                e.preventDefault();
                toggleShortcutHelp();
                return;
            }

            const video = $("crop-video");
            if (e.key === " ") {
                if (!video || !video.src) return;
                e.preventDefault();
                video.paused ? video.play() : video.pause();
            } else if (e.key === "l" || e.key === "L") {
                e.preventDefault();
                toggleLoop();
            } else if (e.key === "i" || e.key === "I") {
                if (!video || !video.src) return;
                e.preventDefault();
                setTrimIn();
            } else if (e.key === "o" || e.key === "O") {
                if (!video || !video.src) return;
                e.preventDefault();
                setTrimOut();
            } else if (e.key === "ArrowLeft") {
                if (!video || !video.src) return;
                e.preventDefault();
                video.currentTime = Math.max(0, video.currentTime - (e.shiftKey ? 5 : 1 / 30));
                updatePreviewTimelineFromCurrentTime();
            } else if (e.key === "ArrowRight") {
                if (!video || !video.src) return;
                e.preventDefault();
                video.currentTime = Math.min(video.duration || 0, video.currentTime + (e.shiftKey ? 5 : 1 / 30));
                updatePreviewTimelineFromCurrentTime();
            }
        });
    }

    function toggleShortcutHelp() {
        let el = $("shortcut-help-overlay");
        if (el) { el.remove(); return; }
        el = document.createElement("div");
        el.id = "shortcut-help-overlay";
        el.className = "shortcut-help-overlay";
        el.innerHTML = `
            <div class="shortcut-help-card">
                <div class="shortcut-help-header">
                    <h3>Keyboard Shortcuts</h3>
                    <button onclick="this.closest('.shortcut-help-overlay').remove()" class="shortcut-help-close">&times;</button>
                </div>
                <div class="shortcut-help-cols">
                    <div>
                        <p class="shortcut-section">Preview</p>
                        <dl>
                            <dt>Space</dt><dd>Play / Pause</dd>
                            <dt>← →</dt><dd>Step 1 frame</dd>
                            <dt>Shift + ← →</dt><dd>Step 5 seconds</dd>
                            <dt>L</dt><dd>Toggle Loop</dd>
                        </dl>
                    </div>
                    <div>
                        <p class="shortcut-section">Trim</p>
                        <dl>
                            <dt>I</dt><dd>Set Trim In</dd>
                            <dt>O</dt><dd>Set Trim Out</dd>
                        </dl>
                        <p class="shortcut-section">General</p>
                        <dl>
                            <dt>?</dt><dd>This help</dd>
                        </dl>
                    </div>
                </div>
            </div>
        `;
        el.addEventListener("click", (ev) => { if (ev.target === el) el.remove(); });
        document.body.appendChild(el);
    }

    // ── Initialization ──────────────────────────────────────────

    async function init() {
        lockExportForCurrentWorkflow();
        await loadStorageConfig();

        // Check dependencies
        try {
            const deps = await api("/api/check-deps");
            const missing = renderDependencyStatus(deps);
            const hasMissingOptional = !deps.deno?.installed;
            const shouldOfferAutoInstall = deps.auto_install_available && (missing.length > 0 || hasMissingOptional);
            const consent = getAutoDownloadConsent();

            if (shouldOfferAutoInstall && !dependencyInstallAttempted) {
                if (consent === "allow") {
                    dependencyInstallAttempted = true;
                    await installMissingDependencies(false);
                } else if (!consent) {
                    showAutoDownloadConsentPrompt(deps);
                }
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
        $("audio-end-input").addEventListener("input", onAudioEndInputChange);
        $("audio-trim-start-slider")?.addEventListener("input", () => onAudioTrimSliderChange("start"));
        $("audio-trim-end-slider")?.addEventListener("input", () => onAudioTrimSliderChange("end"));

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
        $("preview-timeline-slider")?.addEventListener("input", onPreviewTimelineInput);

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
        bindDependencyDropdownCloseHandlers();
        initKeyboardShortcuts();
        applySeparateAudioTrimConstraints("start", { syncPreview: false });

        // ASCII star animation
        initStarAnimation();

        if (!isOnboardingDone()) {
            showWelcomeScreen();
        }
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

    function getAudioTrimRangeMaxSeconds() {
        const clipDur = getTargetClipDurationSeconds();
        const start = getSeparateAudioStartSeconds();
        const end = getSeparateAudioEndSeconds();
        return Math.max(clipDur, audioDuration || 0, start, end, 0);
    }

    function setAudioTrimSliderState(startSeconds, endSeconds, maxSeconds) {
        const startSlider = $("audio-trim-start-slider");
        const endSlider = $("audio-trim-end-slider");
        const startVal = $("audio-trim-start-val");
        const endVal = $("audio-trim-end-val");
        if (!startSlider || !endSlider || !startVal || !endVal) return;

        const safeMax = Math.max(0, Number(maxSeconds) || 0);
        const safeStart = Math.max(0, Math.min(safeMax || Number.POSITIVE_INFINITY, Number(startSeconds) || 0));
        const safeEnd = Math.max(0, Math.min(safeMax || Number.POSITIVE_INFINITY, Number(endSeconds) || 0));
        const disabled = safeMax <= 0;

        startSlider.disabled = disabled;
        endSlider.disabled = disabled;
        startSlider.min = "0";
        endSlider.min = "0";
        startSlider.max = safeMax.toFixed(3);
        endSlider.max = safeMax.toFixed(3);
        startSlider.value = safeStart.toFixed(3);
        endSlider.value = safeEnd.toFixed(3);
        startVal.textContent = formatDuration(safeStart);
        endVal.textContent = formatDuration(safeEnd);
    }

    function normalizeSeparateAudioTrimRange(startSeconds, endSeconds, anchor = "start") {
        const clipDur = getTargetClipDurationSeconds();
        const maxSeconds = getAudioTrimRangeMaxSeconds();
        const safeMax = Math.max(0, Number(maxSeconds) || 0);
        let start = Math.max(0, Number(startSeconds) || 0);
        let end = Math.max(0, Number(endSeconds) || 0);

        if (safeMax > 0) {
            start = Math.min(start, safeMax);
            end = Math.min(end, safeMax);
        }

        if (clipDur > 0) {
            if (anchor === "end") {
                start = end - clipDur;
            } else {
                end = start + clipDur;
            }

            if (start < 0) {
                start = 0;
                end = clipDur;
            }

            if (safeMax > 0 && end > safeMax) {
                end = safeMax;
                start = Math.max(0, end - clipDur);
            }

            // Source audio shorter than target clip: clamp to source bounds.
            if (safeMax > 0 && clipDur > safeMax) {
                start = 0;
                end = safeMax;
            }
        } else if (end <= start) {
            end = start + AUDIO_TRIM_MIN_SPAN;
            if (safeMax > 0 && end > safeMax) {
                end = safeMax;
                start = Math.max(0, end - AUDIO_TRIM_MIN_SPAN);
            }
        }

        return { start, end, maxSeconds: safeMax };
    }

    function updateAudioClipDuration(syncPreview = true) {
        const start = getSeparateAudioStartSeconds();
        const end = getSeparateAudioEndSeconds();
        const durationEl = $("audio-clip-duration");
        if (durationEl) {
            durationEl.textContent = end > start
                ? `Audio: ${formatDuration(end - start)}`
                : "";
        }
        setAudioTrimSliderState(start, end, getAudioTrimRangeMaxSeconds());
        if (syncPreview) {
            syncSeparateAudioWithVideo(true);
        }
    }

    function formatSecondsForTimestampInput(seconds) {
        return formatDuration(seconds);
    }

    function getTargetClipDurationSeconds() {
        const trimmed = Math.max(0, trimEnd - trimStart);
        if (trimmed > 0) return trimmed;
        return clipDuration || videoDuration || (videoInfo && Number(videoInfo.duration)) || 0;
    }

    function applySeparateAudioTrimConstraints(anchor = "start", options = {}) {
        const { syncPreview = true } = options;
        const startInput = $("audio-start-input");
        const endInput = $("audio-end-input");
        if (!startInput || !endInput) return;

        const parsedStart = parseTimestamp(startInput.value || "0");
        const parsedEnd = parseTimestamp(endInput.value || "0");
        const { start, end, maxSeconds } = normalizeSeparateAudioTrimRange(parsedStart, parsedEnd, anchor);

        startInput.value = formatSecondsForTimestampInput(start);
        endInput.value = formatSecondsForTimestampInput(end);
        setAudioTrimSliderState(start, end, maxSeconds);
        updateAudioClipDuration(syncPreview);
    }

    function syncAudioEndToClipDuration() {
        if (!useSeparateAudio) return;
        applySeparateAudioTrimConstraints("start");
    }

    function onAudioStartInputChange() {
        if (useSeparateAudio) {
            applySeparateAudioTrimConstraints("start");
        } else {
            updateAudioClipDuration();
        }
    }

    function onAudioEndInputChange() {
        if (useSeparateAudio) {
            applySeparateAudioTrimConstraints("end");
        } else {
            updateAudioClipDuration();
        }
    }

    function onAudioTrimSliderChange(anchor) {
        const startInput = $("audio-start-input");
        const endInput = $("audio-end-input");
        const startSlider = $("audio-trim-start-slider");
        const endSlider = $("audio-trim-end-slider");
        if (!startInput || !endInput || !startSlider || !endSlider) return;

        if (anchor === "end") {
            endInput.value = formatSecondsForTimestampInput(parseFloat(endSlider.value || "0"));
        } else {
            startInput.value = formatSecondsForTimestampInput(parseFloat(startSlider.value || "0"));
        }

        applySeparateAudioTrimConstraints(anchor);
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
        if (trimStart >= trimEnd - 0.01) {
            if (this.id === "trim-start-slider") {
                trimStart = trimEnd - 0.01;
                $("trim-start-slider").value = (trimStart / clipDuration) * 100;
            } else {
                trimEnd = trimStart + 0.01;
                $("trim-end-slider").value = (trimEnd / clipDuration) * 100;
            }
        }

        trimStart = Math.max(0, Math.round(trimStart * 100) / 100);
        trimEnd = Math.max(trimStart + 0.01, Math.round(trimEnd * 100) / 100);

        // Update displays
        $("trim-start-val").textContent = formatDuration(trimStart);
        $("trim-end-val").textContent = formatDuration(trimEnd);
        $("trim-duration").textContent = "Duration: " + formatDuration(trimEnd - trimStart);

        // Sync video playback
        const video = $("crop-video");
        if (video && video.src) {
            video.currentTime = trimStart;
        }
        updatePreviewTimelineFromCurrentTime();
        updateWaveformRegion();

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
        updatePreviewTimelineFromCurrentTime();
        updateWaveformRegion();
        if (useSeparateAudio) {
            syncAudioEndToClipDuration();
        }
    }

    // ── Waveform ─────────────────────────────────────────────────

    function updateWaveformRegion() {
        const region = $("trim-waveform-region");
        if (!region || !clipDuration) return;
        const startPct = (trimStart / clipDuration) * 100;
        const endPct   = (trimEnd   / clipDuration) * 100;
        region.style.left  = `${startPct}%`;
        region.style.width = `${Math.max(0, endPct - startPct)}%`;
    }

    async function loadWaveform(jId) {
        const bar = $("trim-waveform-bar");
        const img = $("trim-waveform-img");
        if (!bar || !img) return;
        try {
            const resp = await fetch(`/api/waveform/${jId}`);
            if (!resp.ok) return;
            const blob = await resp.blob();
            img.style.backgroundImage = `url(${URL.createObjectURL(blob)})`;
            bar.classList.remove("hidden");
            updateWaveformRegion();
        } catch (_) {}
    }

    // ── Preview Loop ─────────────────────────────────────────────

    function toggleLoop() {
        const video = $("crop-video");
        const btn = $("preview-loop-btn");
        if (!video) return;
        video.loop = !video.loop;
        if (btn) btn.classList.toggle("active", video.loop);
    }

    // ── Set In / Out ─────────────────────────────────────────────

    function setTrimIn() {
        const video = $("crop-video");
        if (!video || !video.src || !clipDuration) return;
        trimStart = Math.max(0, Math.round(video.currentTime * 100) / 100);
        if (trimStart >= trimEnd - 0.05) trimStart = Math.max(0, trimEnd - 0.1);
        $("trim-start-slider").value = (trimStart / clipDuration) * 100;
        $("trim-start-val").textContent = formatDuration(trimStart);
        $("trim-duration").textContent = "Duration: " + formatDuration(trimEnd - trimStart);
        updateWaveformRegion();
    }

    function setTrimOut() {
        const video = $("crop-video");
        if (!video || !video.src || !clipDuration) return;
        trimEnd = Math.min(clipDuration, Math.round(video.currentTime * 100) / 100);
        if (trimEnd <= trimStart + 0.05) trimEnd = Math.min(clipDuration, trimStart + 0.1);
        $("trim-end-slider").value = (trimEnd / clipDuration) * 100;
        $("trim-end-val").textContent = formatDuration(trimEnd);
        $("trim-duration").textContent = "Duration: " + formatDuration(trimEnd - trimStart);
        updateWaveformRegion();
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
            videoDuration = Number(data.duration || 0);
            $("video-title").textContent = data.title;
            $("video-duration").textContent = videoDuration > 0 ? formatDuration(videoDuration) : "Unknown";
            show("video-info");

            // Step 2: Automatically download the full video
            $("download-status-text").textContent = "Downloading video...";
            const fullSourceEnd = videoDuration > 0 ? formatDuration(videoDuration) : "";

            const downloadData = await api("/api/download", {
                method: "POST",
                body: JSON.stringify({
                    url: videoUrl,
                    start: "0:00.00",
                    end: fullSourceEnd,
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
            showError("step1-error", e.message || "Failed to load video.");
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
            audioDuration = Number(data.duration || 0);
            audioValidated = true;
            $("audio-video-title").textContent = data.title;
            $("audio-video-duration").textContent = audioDuration > 0 ? formatDuration(audioDuration) : "Unknown";
            show("audio-video-info");
            if (useSeparateAudio) {
                applySeparateAudioTrimConstraints("start", { syncPreview: false });
            }
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
            const staticImageToggle = $("use-static-image");
            if (staticImageToggle?.checked) {
                staticImageToggle.checked = false;
                onStaticImageToggle({ target: staticImageToggle });
            }
            show("audio-source-section");
            setAudioSourceType(audioSourceType);
            if (!$("audio-start-input").value.trim()) {
                $("audio-start-input").value = "0:00.00";
            }
            if (!$("audio-end-input").value.trim()) {
                $("audio-end-input").value = "0:14.00";
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
            $("audio-start-input").value = "0:00.00";
            $("audio-end-input").value = "0:14.00";
            updateAudioClipDuration(false);
        }
    }

    function onStaticImageToggle(e) {
        useStaticImage = e.target.checked;
        if (useStaticImage) {
            const separateAudioToggle = $("use-separate-audio");
            if (separateAudioToggle?.checked) {
                separateAudioToggle.checked = false;
                onAudioToggle({ target: separateAudioToggle });
            }
            show("static-image-section");
            if (jobId && staticImagePreviewUrl) {
                if (staticImageSourceType === "image") {
                    loadImagePreview(staticImagePreviewUrl);
                } else {
                    loadVideoPreview(staticImagePreviewUrl);
                }
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

        const isImage = file.type.startsWith("image/");
        const isVideo = file.type.startsWith("video/");
        if (!isImage && !isVideo) {
            showError("step2-error", "Please select an image or video file.");
            return;
        }

        staticImageFile = file;
        hideError("step2-error");
        clearAlternateVisualPreviewUrl();

        if (isImage) {
            staticImageSourceType = "image";
            const reader = new FileReader();
            reader.onload = (ev) => {
                staticImagePreviewUrl = String(ev.target?.result || "");
                $("static-image-preview").src = staticImagePreviewUrl;
                show("image-preview-container");

                if (useStaticImage && jobId) {
                    loadImagePreview(staticImagePreviewUrl);
                }
            };
            reader.readAsDataURL(file);
            return;
        }

        staticImageSourceType = "video";
        staticImagePreviewUrl = URL.createObjectURL(file);
        hide("image-preview-container");
        if (useStaticImage && jobId) {
            loadVideoPreview(staticImagePreviewUrl);
        }
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
            if (videoInfo && Number(videoInfo.duration) > 0) {
                videoDuration = Number(videoInfo.duration);
                $("video-duration").textContent = formatDuration(videoDuration);
            }

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
        const timeline = $("preview-timeline-control");
        if (controls) controls.classList.toggle("hidden", !visible);
        if (volume) volume.classList.toggle("hidden", !visible);
        if (timeline) timeline.classList.toggle("hidden", !visible);
    }

    function updatePreviewTimelineFromCurrentTime() {
        const slider = $("preview-timeline-slider");
        const valueLabel = $("preview-timeline-value");
        const video = $("crop-video");
        if (!slider || !valueLabel || !video || !video.src || clipDuration <= 0) {
            if (slider) {
                slider.disabled = true;
                slider.min = "0";
                slider.max = "0";
                slider.value = "0";
            }
            if (valueLabel) valueLabel.textContent = "0:00.00 / 0:00.00";
            return;
        }

        const start = Math.max(0, trimStart);
        const end = Math.max(start + 0.01, trimEnd);
        const rawCurrent = Number(video.currentTime);
        const current = Number.isFinite(rawCurrent)
            ? Math.max(start, Math.min(end, rawCurrent))
            : start;

        slider.disabled = false;
        slider.min = start.toFixed(3);
        slider.max = end.toFixed(3);
        slider.value = current.toFixed(3);

        const relativeCurrent = Math.max(0, current - start);
        const relativeTotal = Math.max(0, end - start);
        valueLabel.textContent = `${formatDuration(relativeCurrent)} / ${formatDuration(relativeTotal)}`;
    }

    function onPreviewTimelineInput(e) {
        const video = $("crop-video");
        if (!video || !video.src) return;

        const start = Math.max(0, trimStart);
        const end = Math.max(start + 0.01, trimEnd);
        let target = parseFloat(e.target.value);
        if (!Number.isFinite(target)) target = start;
        target = Math.max(start, Math.min(end, target));

        video.currentTime = target;
        syncSeparateAudioWithVideo(true);
        updatePreviewTimelineFromCurrentTime();
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

        // In image override mode, keep crop image visible but use hidden crop-video for audio preview.
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
            updatePreviewTimelineFromCurrentTime();
            if (playBtn) playBtn.textContent = "▶ Play Audio";
        };

        audioPreview.ontimeupdate = () => {
            if (!audioPreview.paused && audioPreview.currentTime >= trimEnd) {
                if (audioPreview.loop) {
                    audioPreview.currentTime = trimStart;
                } else {
                    audioPreview.pause();
                    audioPreview.currentTime = trimEnd;
                }
            }
            syncSeparateAudioWithVideo(false);
            updatePreviewTimelineFromCurrentTime();
        };
        audioPreview.onended = () => {
            audioPreview.currentTime = trimStart;
            syncSeparateAudioWithVideo(true);
            updatePreviewTimelineFromCurrentTime();
            if (audioPreview.loop) {
                audioPreview.play().catch(() => { });
            }
        };

        // Immediate fallback duration so sliders are usable before metadata resolves.
        const fallbackDur = (videoInfo && Number(videoInfo.duration)) || videoDuration || clipDuration || 0;
        initTrimSliders(Math.max(0, fallbackDur));
    }

    function loadVideoPreview(sourceUrl = "") {
        if (useStaticImage && staticImageSourceType === "image" && staticImagePreviewUrl) {
            loadImagePreview(staticImagePreviewUrl);
            return;
        }
        if (!sourceUrl && useStaticImage && staticImageSourceType === "video" && staticImagePreviewUrl) {
            sourceUrl = staticImagePreviewUrl;
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

        const targetUrl = sourceUrl || `/api/serve-clip/${jobId}?t=${Date.now()}`;
        const MEDIA_ERROR_HINT = {
            1: "playback aborted",
            2: "network error",
            3: "decode error (codec not supported by this build of QtWebEngine — verify the EXE was built with PySide6 ≥ 6.4 and proprietary codecs)",
            4: "source not supported (404, MIME mismatch, or unsupported codec)",
        };

        video.onerror = () => {
            const err = video.error;
            const code = err?.code;
            const detail = MEDIA_ERROR_HINT[code] || `unknown (code=${code})`;
            console.error("Video load error", { code, message: err?.message, src: targetUrl, networkState: video.networkState, readyState: video.readyState });
            showError("step2-error", `Failed to load video preview: ${detail}.`);
        };

        // Start loading
        video.src = targetUrl;

        video.onloadedmetadata = () => {
            // Re-apply slider volume to the newly loaded media.
            onVolumeChange();
            updatePreviewAudioRouting();
            cropPreview.initialize(video.videoWidth, video.videoHeight);

            // Initialize trim sliders
            const dur = video.duration;
            initTrimSliders(dur);
            syncSeparateAudioWithVideo(true);
            updatePreviewTimelineFromCurrentTime();

            // Show timecode overlay + In/Out buttons
            show("preview-timecode");
            show("set-in-btn");
            show("set-out-btn");

            // Load waveform
            if (jobId) loadWaveform(jobId);
        };

        // Handle looping within trim region (only when loop toggle is active)
        video.ontimeupdate = () => {
            if (!video.paused && video.currentTime >= trimEnd) {
                if (video.loop) {
                    video.currentTime = trimStart;
                } else {
                    video.pause();
                    video.currentTime = trimEnd;
                }
            }
            syncSeparateAudioWithVideo(false);
            updatePreviewTimelineFromCurrentTime();
            const tc = $("preview-timecode");
            if (tc) tc.textContent = formatDuration(video.currentTime);
        };
        video.onended = () => {
            video.currentTime = trimStart;
            syncSeparateAudioWithVideo(true);
            updatePreviewTimelineFromCurrentTime();
            if (video.loop) {
                video.play().catch(() => { });
            }
        };
    }

    // ── Step 4: Process & Export ─────────────────────────────────

    async function processVideo() {
        if (!cropPreview) {
            showError("step4-error", "Crop preview not ready.");
            return;
        }

        if (useStaticImage && !staticImageFile) {
            showError("step4-error", "Please select a different image or video file.");
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
            formData.append("use_alternate_visual", useStaticImage);
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
                formData.append("alternate_visual", staticImageFile);
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
            exportPreset: $("setting-export-preset")?.value || "stream_alert",
            theme: normalizeThemeId($("setting-theme")?.value || document.documentElement.dataset.theme || DEFAULT_THEME_ID),
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
                if (settings.exportPreset) {
                    const el = $("setting-export-preset");
                    if (el) el.value = settings.exportPreset;
                }
                applyTheme(settings.theme || document.documentElement.dataset.theme || DEFAULT_THEME_ID);
            } else {
                applyTheme(document.documentElement.dataset.theme || DEFAULT_THEME_ID);
            }
        } catch (e) {
            // Ignore errors
            applyTheme(document.documentElement.dataset.theme || DEFAULT_THEME_ID);
        }

        updateSettingsPanelLabels(getSettings());

        // Save on change
        ["setting-resolution", "setting-buffer", "setting-normalize-audio", "setting-audio-fade-duration", "setting-export-preset", "setting-theme"].forEach(id => {
            $(id)?.addEventListener("change", saveSettings);
        });
    }

    function saveSettings() {
        const settings = getSettings();
        settings.theme = applyTheme(settings.theme);
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
        if ($("setting-export-preset")) $("setting-export-preset").value = "stream_alert";
        applyTheme(DEFAULT_THEME_ID);
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
        setTrimIn,
        setTrimOut,
        toggleLoop,
        toggleShortcutHelp,
        installMissingDependencies,
        updateYtdlp,
        onboardingStartTour,
        onboardingSkip,
        onboardingInstallDeps,
        onboardingTourNext,
        restartOnboarding,
        chooseOutputFolder,
        applyOutputFolder,
        resetOutputFolder,
        allowDependencyAutoDownload,
        useManualDependencySetup,
        toggleSettingsPanel,
        openSettingsPanel,
        getDependencySnapshot() {
            return lastDeps;
        },
    };
})();
