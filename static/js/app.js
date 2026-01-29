/**
 * Main application logic for deutschmark's Alert! Alert!
 * Manages the 4-step workflow, API calls, and UI state.
 */
const App = (() => {
    // State
    let videoUrl = "";
    let videoDuration = 0;
    let jobId = "";
    let videoInfo = null; // { width, height, fps, duration }
    let cropPreview = null;
    let pollTimer = null;

    // Audio source state
    let audioUrl = "";
    let audioDuration = 0;
    let audioValidated = false;
    let useSeparateAudio = false;

    // Static image state
    let useStaticImage = false;
    let staticImageFile = null;

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

    async function api(endpoint, options = {}) {
        const resp = await fetch(endpoint, {
            headers: { "Content-Type": "application/json" },
            ...options,
        });
        return resp.json();
    }

    // ── Initialization ──────────────────────────────────────────

    async function init() {
        // Check dependencies
        try {
            const deps = await api("/api/check-deps");
            const missing = [];
            const instructions = [];

            // Update settings status indicators
            const ffmpegStatus = $("dep-ffmpeg-status");
            const ytdlpStatus = $("dep-ytdlp-status");

            if (deps.ffmpeg?.installed) {
                ffmpegStatus.textContent = "✓ Installed";
                ffmpegStatus.className = "dep-status installed";
            } else {
                ffmpegStatus.textContent = "✗ Missing";
                ffmpegStatus.className = "dep-status missing";
                missing.push("FFmpeg");
                instructions.push("FFmpeg: Run 'winget install Gyan.FFmpeg' or download from ffmpeg.org");
            }

            if (deps["yt-dlp"]?.installed) {
                ytdlpStatus.textContent = "✓ Installed";
                ytdlpStatus.className = "dep-status installed";
            } else {
                ytdlpStatus.textContent = "✗ Missing";
                ytdlpStatus.className = "dep-status missing";
                missing.push("yt-dlp");
                instructions.push("yt-dlp: Run 'pip install yt-dlp' in Command Prompt");
            }

            // Show banner if missing dependencies
            if (missing.length > 0) {
                const banner = $("dep-banner");
                const msg = $("dep-message");
                const instEl = $("dep-instructions");

                msg.innerHTML = `<strong>⚠️ Missing dependencies:</strong> ${missing.join(", ")}`;
                instEl.innerHTML = `<strong>How to fix:</strong><br>${instructions.join("<br>")}`;

                banner.classList.add("banner-error");
                show(banner);
            }
        } catch (e) {
            // Server not running - show connection error
            const banner = $("dep-banner");
            const msg = $("dep-message");
            msg.innerHTML = "<strong>⚠️ Cannot connect to server.</strong> Make sure the app is running.";
            banner.classList.add("banner-error");
            show(banner);
        }

        // Audio clip duration
        $("audio-start-input").addEventListener("input", updateAudioClipDuration);
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
        $("volume-slider").addEventListener("input", onVolumeChange);

        // Allow Enter to validate
        $("url-input").addEventListener("keydown", (e) => {
            if (e.key === "Enter") validateUrl();
        });

        $("audio-url-input").addEventListener("keydown", (e) => {
            if (e.key === "Enter") validateAudioUrl();
        });

        // Audio source toggle
        $("use-separate-audio").addEventListener("change", onAudioToggle);

        // Static image toggle
        $("use-static-image").addEventListener("change", onStaticImageToggle);
        $("static-image-input").addEventListener("change", onStaticImageSelect);

        // Local video file upload
        $("local-video-input").addEventListener("change", onLocalVideoSelect);
        setupFileDragDrop();

        // Close settings when clicking outside
        document.addEventListener("click", (e) => {
            const dropdown = document.querySelector(".settings-dropdown");
            if (dropdown && !dropdown.contains(e.target)) {
                hide("settings-menu");
                $("settings-btn").classList.remove("active");
            }
        });

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
    }

    function onAudioToggle() {
        useSeparateAudio = $("use-separate-audio").checked;
        if (useSeparateAudio) {
            show("audio-source-section");
        } else {
            hide("audio-source-section");
            // Reset audio validation state
            audioValidated = false;
            audioUrl = "";
        }
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
    }

    function onVolumeChange() {
        const vol = parseInt($("volume-slider").value);
        const video = $("crop-video");
        if (video) {
            video.volume = vol / 100;
            video.muted = vol === 0;
        }
        $("volume-value").textContent = vol + "%";

        // Update mute button state
        const muteBtn = $("preview-mute-btn");
        if (muteBtn) {
            muteBtn.textContent = vol === 0 ? "🔇 Unmute" : "🔊 Mute";
        }
    }

    // ── Step 1: Validate URL & Download ────────────────────────────────────

    async function validateUrl() {
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
            $("download-status-text").textContent = "Loading preview...";

            // Get video info
            videoInfo = await api(`/api/video-info/${jobId}`);

            // Load video preview
            loadVideoPreview();

            hide("download-status");
            enableStep(2);
            enableStep(3);
            enableStep(4);

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

    // ── Toggle Handlers ──────────────────────────────────────────

    function onAudioToggle(e) {
        useSeparateAudio = e.target.checked;
        if (useSeparateAudio) {
            show("audio-source-section");
        } else {
            hide("audio-source-section");
            audioValidated = false;
        }
    }

    function onStaticImageToggle(e) {
        useStaticImage = e.target.checked;
        if (useStaticImage) {
            show("static-image-section");
        } else {
            hide("static-image-section");
            hide("image-preview-container");
            staticImageFile = null;
        }
    }

    function onStaticImageSelect(e) {
        const file = e.target.files[0];
        if (file) {
            staticImageFile = file;
            const reader = new FileReader();
            reader.onload = (ev) => {
                $("static-image-preview").src = ev.target.result;
                show("image-preview-container");
            };
            reader.readAsDataURL(file);
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
                setLoading("download-btn", false);
                return;
            }

            jobId = data.job_id;
            videoDuration = data.duration || 0;
            $("video-title").textContent = file.name;
            $("video-duration").textContent = formatDuration(videoDuration);
            show("video-info");

            $("download-status-text").textContent = "Loading preview...";

            // Poll for completion
            pollDownload(jobId);

        } catch (e) {
            showError("step2-error", "Download failed: " + e.message);
            hide("download-status");
            setLoading("download-btn", false);
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
                    setLoading("download-btn", false);
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

            hide("download-status");
            enableStep(2);
            enableStep(3);
            enableStep(4);

        } catch (e) {
            showError("step1-error", "Failed to load video: " + e.message);
            hide("download-status");
        } finally {
            setLoading("source-file-btn", false);
        }
    }

    // ── Video Preview ────────────────────────────────────────────

    function loadVideoPreview() {
        if (!cropPreview) {
            cropPreview = new CropPreview();
        }

        // Reset previous state
        cropPreview.reset();

        // Show the video sidebar
        show("video-sidebar");

        const video = document.getElementById("crop-video");

        video.onerror = () => {
            console.error("Video load error");
            showError("step2-error", "Failed to load video preview.");
        };

        // Start loading
        video.src = `/api/serve-clip/${jobId}?t=${Date.now()}`;

        video.onloadedmetadata = () => {
            cropPreview.initialize(video.videoWidth, video.videoHeight);

            // Initialize trim sliders
            const dur = video.duration;
            initTrimSliders(dur);
        };

        // Handle looping within trim region
        video.ontimeupdate = () => {
            if (!video.paused && video.currentTime >= trimEnd) {
                video.currentTime = trimStart;
            }
        };
    }

    // ── Step 4: Process & Export ─────────────────────────────────

    async function processVideo() {
        if (!cropPreview) {
            showError("step4-error", "Crop preview not ready.");
            return;
        }

        hideError("step4-error");
        const cropParams = cropPreview.getCropParams();

        setLoading("process-btn", true);
        show("progress-section");
        hide("download-section");

        try {
            const formData = new FormData();
            formData.append("job_id", jobId);
            formData.append("crop", JSON.stringify(cropParams));
            formData.append("trim_start", trimStart);
            formData.append("trim_end", trimEnd);
            formData.append("use_separate_audio", useSeparateAudio);
            formData.append("use_static_image", useStaticImage);
            formData.append("settings", JSON.stringify(getSettings()));

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
                    $("progress-bar").style.width = "100%";
                    $("progress-text").textContent = "Done!";
                    show("download-section");
                    setLoading("process-btn", false);
                    return;
                }

                if (data.status === "error") {
                    clearInterval(pollTimer);
                    pollTimer = null;
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

    function toggleSettings() {
        const menu = $("settings-menu");
        const btn = $("settings-btn");
        if (menu.classList.contains("hidden")) {
            show(menu);
            btn.classList.add("active");
        } else {
            hide(menu);
            btn.classList.remove("active");
        }
    }

    function getSettings() {
        return {
            resolution: $("setting-resolution")?.value || "720",
            bufferDuration: $("setting-buffer")?.value || "2",
            normalizeAudio: $("setting-normalize-audio")?.checked ?? true
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
            }
        } catch (e) {
            // Ignore errors
        }

        // Save on change
        ["setting-resolution", "setting-buffer", "setting-normalize-audio"].forEach(id => {
            $(id)?.addEventListener("change", saveSettings);
        });
    }

    function saveSettings() {
        try {
            localStorage.setItem("alertCreatorSettings", JSON.stringify(getSettings()));
        } catch (e) {
            // Ignore errors
        }
    }

    function resetSettings() {
        $("setting-resolution").value = "720";
        $("setting-buffer").value = "2";
        $("setting-normalize-audio").checked = true;
        saveSettings();

        // Visual feedback
        const btn = document.querySelector(".settings-reset");
        btn.textContent = "Reset!";
        setTimeout(() => btn.textContent = "Reset to Defaults", 1000);
    }

    async function shutdownApp() {
        if (!confirm("Are you sure you want to quit the application?")) return;
        try {
            await api("/api/shutdown", { method: "POST" });
            document.body.innerHTML = "<div style='display:flex;justify-content:center;align-items:center;height:100vh;background:#0f0f0f;color:#fff;font-family:sans-serif;'><h2>Application has been closed. You can close this tab.</h2></div>";
        } catch (e) {
            alert("Failed to quit app");
        }
    }

    return {
        validateUrl,
        validateAudioUrl,
        setSourceType,
        processVideo,
        downloadResult,
        toggleSettings,
        resetSettings,
        shutdownApp,
    };
})();

