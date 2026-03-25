/**
 * Reel Maker — frontend module for multi-clip VOD extraction and reel creation.
 */
const ReelMaker = (() => {
    // ── State ──────────────────────────────────────────────────────
    let projectId = "";
    let vodUrl = "";
    let vodDuration = 0;
    let sourceType = "url"; // "url" | "file"
    let localVodFile = null;
    let localVodUploaded = false;
    let localVodObjectUrl = "";
    let pollTimer = null;
    let concatReady = false;

    // ── Helpers ────────────────────────────────────────────────────

    const $ = (id) => document.getElementById(id);
    const show = (el) => { if (typeof el === "string") el = $(el); if (el) el.classList.remove("hidden"); };
    const hide = (el) => { if (typeof el === "string") el = $(el); if (el) el.classList.add("hidden"); };

    function showError(stepId, msg) {
        const el = $(stepId);
        if (el) { el.textContent = msg; el.classList.remove("hidden"); }
    }
    function hideError(stepId) {
        const el = $(stepId);
        if (el) { el.textContent = ""; el.classList.add("hidden"); }
    }

    function enableStep(n) {
        const el = $(`reel-step-${n}`);
        if (el) el.classList.remove("disabled");
    }
    function disableStep(n) {
        const el = $(`reel-step-${n}`);
        if (el) el.classList.add("disabled");
    }

    async function api(endpoint, options = {}) {
        const headers = options.body instanceof FormData
            ? (options.headers || {})
            : { "Content-Type": "application/json", ...(options.headers || {}) };
        const resp = await fetch(endpoint, {
            headers,
            ...options,
        });
        return resp.json();
    }

    function formatTime(seconds) {
        if (!seconds || seconds < 0) return "0:00";
        const m = Math.floor(seconds / 60);
        const s = Math.floor(seconds % 60);
        return `${m}:${s.toString().padStart(2, "0")}`;
    }

    // ── Source type toggle ─────────────────────────────────────────

    function setSourceType(type) {
        sourceType = type;
        $("reel-source-url-btn").classList.toggle("active", type === "url");
        $("reel-source-file-btn").classList.toggle("active", type === "file");
        if (type === "url") {
            show("reel-url-section");
            hide("reel-file-section");
        } else {
            hide("reel-url-section");
            show("reel-file-section");
        }
        hideError("reel-step1-error");
    }

    function clearLocalVodPreviewUrl() {
        if (localVodObjectUrl) {
            URL.revokeObjectURL(localVodObjectUrl);
            localVodObjectUrl = "";
        }
    }

    // ── Create project ────────────────────────────────────────────

    async function ensureProject() {
        if (projectId) return projectId;
        const data = await api("/api/reel/create-project", { method: "POST" });
        projectId = data.project_id;
        return projectId;
    }

    // ── Validate VOD URL ──────────────────────────────────────────

    async function validateUrl() {
        hideError("reel-step1-error");
        const url = $("reel-url-input").value.trim();
        if (!url) {
            showError("reel-step1-error", "Please enter a URL.");
            return;
        }

        const btn = $("reel-validate-btn");
        btn.disabled = true;
        btn.textContent = "Loading...";

        try {
            concatReady = false;
            localVodFile = null;
            localVodUploaded = false;
            clearLocalVodPreviewUrl();
            const data = await api("/api/validate-url", {
                method: "POST",
                body: JSON.stringify({ url }),
            });

            if (!data.valid) {
                showError("reel-step1-error", data.error || "Invalid URL");
                return;
            }

            vodUrl = url;
            vodDuration = data.duration || 0;

            // Store VOD info in project
            await ensureProject();
            await api("/api/reel/set-vod", {
                method: "POST",
                body: JSON.stringify({
                    project_id: projectId,
                    url: vodUrl,
                    title: data.title,
                    duration: vodDuration,
                }),
            });

            $("reel-vod-title").textContent = data.title || "Unknown";
            $("reel-vod-duration").textContent = formatTime(vodDuration);
            show("reel-vod-info");

            enableStep(2);

            // Auto-add first clip if none exist
            if ($("reel-clip-list").children.length === 0) {
                addClip();
            }
        } catch (e) {
            showError("reel-step1-error", e.message || "Validation failed");
        } finally {
            btn.disabled = false;
            btn.textContent = "Load VOD";
        }
    }

    // ── Clip management ───────────────────────────────────────────

    async function addClip() {
        await ensureProject();
        const defaultStart = "0:00";
        const defaultEnd = vodDuration > 10 ? formatTime(10) : formatTime(vodDuration);

        const data = await api("/api/reel/add-clip", {
            method: "POST",
            body: JSON.stringify({
                project_id: projectId,
                start: defaultStart,
                end: defaultEnd,
            }),
        });

        if (data.error) {
            showError("reel-step2-error", data.error);
            return;
        }

        renderClipItem(data.clip);
        show("reel-clip-actions");
    }

    async function handleLocalFile(file) {
        if (!file) return;

        hideError("reel-step1-error");
        clearLocalVodPreviewUrl();
        localVodFile = file;
        localVodUploaded = false;
        vodUrl = "";
        concatReady = false;

        localVodObjectUrl = URL.createObjectURL(file);
        const probeVideo = document.createElement("video");
        probeVideo.preload = "metadata";

        try {
            const duration = await new Promise((resolve, reject) => {
                probeVideo.onloadedmetadata = () => resolve(Number(probeVideo.duration || 0));
                probeVideo.onerror = () => reject(new Error("Could not read local video metadata."));
                probeVideo.src = localVodObjectUrl;
            });

            vodDuration = duration;
            await ensureProject();

            $("reel-vod-title").textContent = file.name || "Local video";
            $("reel-vod-duration").textContent = formatTime(vodDuration);
            show("reel-vod-info");
            enableStep(2);

            if ($("reel-clip-list").children.length === 0) {
                await addClip();
            }
        } catch (e) {
            showError("reel-step1-error", e.message || "Failed to load local video.");
        }
    }

    function onLocalVideoSelect(event) {
        const [file] = event.target.files || [];
        handleLocalFile(file);
    }

    function setupFileDropZone() {
        const dropZone = $("reel-file-drop-zone");
        const fileInput = $("reel-video-input");
        if (!dropZone || !fileInput) return;

        ["dragenter", "dragover"].forEach((eventName) => {
            dropZone.addEventListener(eventName, (event) => {
                event.preventDefault();
                event.stopPropagation();
                dropZone.classList.add("dragover");
            });
        });

        ["dragleave", "dragend", "drop"].forEach((eventName) => {
            dropZone.addEventListener(eventName, () => {
                dropZone.classList.remove("dragover");
            });
        });

        dropZone.addEventListener("drop", (event) => {
            event.preventDefault();
            event.stopPropagation();
            const [file] = event.dataTransfer?.files || [];
            if (!file) return;
            fileInput.files = event.dataTransfer.files;
            handleLocalFile(file);
        });
    }

    async function ensureLocalVodUploaded() {
        if (sourceType !== "file") return true;
        if (localVodUploaded) return true;
        if (!localVodFile) {
            showError("reel-step1-error", "Choose a local VOD file first.");
            return false;
        }

        await ensureProject();
        const formData = new FormData();
        formData.append("project_id", projectId);
        formData.append("video", localVodFile);

        const data = await api("/api/reel/upload-vod", {
            method: "POST",
            body: formData,
        });

        if (data.error) {
            showError("reel-step1-error", data.error);
            return false;
        }

        vodDuration = data.duration || vodDuration;
        $("reel-vod-title").textContent = data.filename || localVodFile.name || "Local video";
        $("reel-vod-duration").textContent = formatTime(vodDuration);
        show("reel-vod-info");
        localVodUploaded = true;
        return true;
    }

    function renderClipItem(clip) {
        const container = $("reel-clip-list");
        const idx = container.children.length + 1;

        const div = document.createElement("div");
        div.className = "clip-item";
        div.dataset.clipId = clip.id;
        div.innerHTML = `
            <span class="clip-drag-handle" title="Drag to reorder">&#9776;</span>
            <span class="clip-number">#${idx}</span>
            <input type="text" class="clip-start" value="${clip.start}" placeholder="0:00"
                   onchange="ReelMaker.updateClipTime('${clip.id}', 'start', this.value)">
            <span class="clip-dash">&ndash;</span>
            <input type="text" class="clip-end" value="${clip.end}" placeholder="0:30"
                   onchange="ReelMaker.updateClipTime('${clip.id}', 'end', this.value)">
            <button class="clip-remove-btn" onclick="ReelMaker.removeClip('${clip.id}')" title="Remove clip">&times;</button>
        `;
        container.appendChild(div);
    }

    async function updateClipTime(clipId, field, value) {
        if (!projectId) return;
        const body = { project_id: projectId, clip_id: clipId };
        body[field] = value;
        await api("/api/reel/update-clip", {
            method: "POST",
            body: JSON.stringify(body),
        });
    }

    async function removeClip(clipId) {
        if (!projectId) return;
        await api("/api/reel/remove-clip", {
            method: "POST",
            body: JSON.stringify({ project_id: projectId, clip_id: clipId }),
        });

        // Remove from DOM
        const container = $("reel-clip-list");
        const item = container.querySelector(`[data-clip-id="${clipId}"]`);
        if (item) item.remove();

        // Re-number clips
        Array.from(container.children).forEach((el, i) => {
            const num = el.querySelector(".clip-number");
            if (num) num.textContent = `#${i + 1}`;
        });

        if (container.children.length === 0) {
            hide("reel-clip-actions");
        }
    }

    // ── Download & stitch ─────────────────────────────────────────

    async function downloadAllClips() {
        hideError("reel-step2-error");
        if (!projectId) return;

        if (!(await ensureLocalVodUploaded())) {
            return;
        }

        const btn = $("reel-download-clips-btn");
        btn.disabled = true;
        show("reel-download-status");

        try {
            const data = await api("/api/reel/download-clips", {
                method: "POST",
                body: JSON.stringify({ project_id: projectId }),
            });

            if (data.error) {
                showError("reel-step2-error", data.error);
                btn.disabled = false;
                hide("reel-download-status");
                return;
            }

            // Poll for progress
            const jobId = data.job_id;
            pollTimer = setInterval(async () => {
                try {
                    const status = await api(`/api/status/${jobId}`);

                    $("reel-download-bar").style.width = `${status.progress || 0}%`;
                    $("reel-download-status-text").textContent = status.stage || "Processing...";

                    if (status.status === "complete") {
                        clearInterval(pollTimer);
                        pollTimer = null;
                        btn.disabled = false;
                        concatReady = true;

                        $("reel-download-status-text").textContent =
                            `Done! ${status.clips_downloaded} clip(s) stitched.` +
                            (status.clips_failed > 0 ? ` ${status.clips_failed} failed.` : "");

                        // Enable captions step
                        enableStep(3);
                        $("reel-transcribe-btn").disabled = false;

                        // Load preview
                        loadConcatPreview();
                    } else if (status.status === "error") {
                        clearInterval(pollTimer);
                        pollTimer = null;
                        btn.disabled = false;
                        showError("reel-step2-error", status.error || "Download failed");
                    }
                } catch (e) {
                    // Polling error, keep trying
                }
            }, 1000);
        } catch (e) {
            showError("reel-step2-error", e.message || "Download failed");
            btn.disabled = false;
            hide("reel-download-status");
        }
    }

    // ── Preview ───────────────────────────────────────────────────

    function loadConcatPreview() {
        if (!projectId) return;
        const video = $("reel-preview-video");
        video.src = `/api/reel/serve-concat/${projectId}`;
        video.load();
        show("reel-video-sidebar");
    }

    function togglePlay() {
        const video = $("reel-preview-video");
        const btn = $("reel-play-btn");
        if (video.paused) {
            video.play();
            btn.innerHTML = "&#9646;&#9646; Pause";
        } else {
            video.pause();
            btn.innerHTML = "&#9654; Play";
        }
    }

    function toggleMute() {
        const video = $("reel-preview-video");
        const btn = $("reel-mute-btn");
        video.muted = !video.muted;
        btn.innerHTML = video.muted ? "&#128263; Unmute" : "&#128266; Mute";
    }

    // ── Transcription (Phase 3 stub) ──────────────────────────────

    async function transcribe() {
        hideError("reel-step3-error");
        if (!projectId || !concatReady) {
            showError("reel-step3-error", "Download clips first.");
            return;
        }

        const language = $("reel-caption-language").value;
        const model = $("reel-whisper-model").value;
        const hfToken = $("reel-hf-token").value.trim();

        $("reel-transcribe-btn").disabled = true;
        show("reel-transcribe-progress");

        try {
            const data = await api("/api/reel/transcribe", {
                method: "POST",
                body: JSON.stringify({
                    project_id: projectId,
                    language: language,
                    model_size: model,
                    hf_token: hfToken || null,
                }),
            });

            if (data.error) {
                showError("reel-step3-error", data.error);
                $("reel-transcribe-btn").disabled = false;
                hide("reel-transcribe-progress");
                return;
            }

            // Poll transcription progress
            const jobId = data.job_id;
            const timer = setInterval(async () => {
                try {
                    const status = await api(`/api/status/${jobId}`);
                    $("reel-transcribe-bar").style.width = `${status.progress || 0}%`;
                    $("reel-transcribe-status").textContent = status.stage || "Processing...";

                    if (status.status === "complete") {
                        clearInterval(timer);
                        $("reel-transcribe-btn").disabled = false;
                        $("reel-transcribe-status").textContent =
                            `Done! ${status.word_count || 0} words, ${status.speaker_count || 1} speaker(s) detected.`;

                        // Load caption editor
                        if (typeof CaptionEditor !== "undefined") {
                            CaptionEditor.init(projectId);
                            show("reel-caption-editor");
                        }
                    } else if (status.status === "error") {
                        clearInterval(timer);
                        $("reel-transcribe-btn").disabled = false;
                        showError("reel-step3-error", status.error || "Transcription failed");
                    }
                } catch (e) {
                    // Keep polling
                }
            }, 1000);
        } catch (e) {
            showError("reel-step3-error", e.message || "Transcription failed");
            $("reel-transcribe-btn").disabled = false;
            hide("reel-transcribe-progress");
        }
    }

    // ── Export (stub for Phase 2+4) ───────────────────────────────

    async function exportReel() {
        hideError("reel-step4-error");
        showError("reel-step4-error", "Export pipeline coming in Phase 2 (vertical template) + Phase 4 (effects).");
    }

    function downloadReel() {
        // Placeholder
    }

    function init() {
        $("reel-video-input")?.addEventListener("change", onLocalVideoSelect);
        $("reel-url-input")?.addEventListener("keydown", (event) => {
            if (event.key === "Enter") validateUrl();
        });
        setupFileDropZone();
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", init);
    } else {
        init();
    }

    // ── Public API ────────────────────────────────────────────────

    return {
        setSourceType,
        validateUrl,
        addClip,
        updateClipTime,
        removeClip,
        downloadAllClips,
        togglePlay,
        toggleMute,
        transcribe,
        exportReel,
        downloadReel,
        getProjectId: () => projectId,
    };
})();
