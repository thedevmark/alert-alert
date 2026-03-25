/**
 * Reel Maker — frontend module for multi-clip VOD extraction and reel creation.
 */
const ReelMaker = (() => {
    const LAST_PROJECT_STORAGE_KEY = "alertAlertReelLastProjectId";
    const MIN_CLIP_DURATION = 0.1;

    // ── State ──────────────────────────────────────────────────────
    let projectId = "";
    let vodUrl = "";
    let vodDuration = 0;
    let sourceType = "url"; // "url" | "file"
    let localVodFile = null;
    let localVodUploaded = false;
    let localVodObjectUrl = "";
    let remotePreviewUrl = "";
    let pollTimer = null;
    let concatReady = false;
    let exportedReelReady = false;
    let draggedClipId = "";
    let activeClipId = "";
    let recentProjects = [];
    let projectAssets = [];
    let sourceMoments = [];
    let previewMode = "source";
    let timelineZoom = 1;
    let timelineDragState = null;
    let clipInspectorTimer = null;

    // ── Helpers ────────────────────────────────────────────────────

    const $ = (id) => document.getElementById(id);
    const show = (el) => { if (typeof el === "string") el = $(el); if (el) el.classList.remove("hidden"); };
    const hide = (el) => { if (typeof el === "string") el = $(el); if (el) el.classList.add("hidden"); };
    const escapeHtml = (value) => String(value || "")
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#39;");

    function showError(stepId, msg) {
        const el = $(stepId);
        if (el) { el.textContent = msg; el.classList.remove("hidden"); }
    }
    function hideError(stepId) {
        const el = $(stepId);
        if (el) { el.textContent = ""; el.classList.add("hidden"); }
    }

    function setSourceMomentStatus(message) {
        const el = $("reel-source-moments-status");
        if (el) {
            el.textContent = message || "Source moments will be imported here when the VOD exposes them.";
        }
    }

    function applyTimelineZoom() {
        const widthPct = Math.max(100, Math.round(timelineZoom * 100));
        ["reel-timeline-track", "reel-sequence-track"].forEach((id) => {
            const el = $(id);
            if (el) {
                el.style.width = `${widthPct}%`;
            }
        });
        const label = $("reel-timeline-zoom-value");
        if (label) {
            label.textContent = `${widthPct}%`;
        }
    }

    function setTimelineZoom(value) {
        const parsed = Number(value);
        timelineZoom = Number.isFinite(parsed) ? Math.min(4, Math.max(1, parsed)) : 1;
        if ($("reel-timeline-zoom")) {
            $("reel-timeline-zoom").value = String(timelineZoom);
        }
        applyTimelineZoom();
    }

    function rememberProject(id) {
        try {
            if (id) localStorage.setItem(LAST_PROJECT_STORAGE_KEY, id);
        } catch (e) {
            // Ignore storage failures.
        }
    }

    function getRememberedProject() {
        try {
            return localStorage.getItem(LAST_PROJECT_STORAGE_KEY) || "";
        } catch (e) {
            return "";
        }
    }

    function clearRememberedProject() {
        try {
            localStorage.removeItem(LAST_PROJECT_STORAGE_KEY);
        } catch (e) {
            // Ignore storage failures.
        }
    }

    function setProjectIdentity(id) {
        projectId = id || "";
        if (projectId) {
            rememberProject(projectId);
        } else {
            clearRememberedProject();
        }
        renderProjectChrome();
        loadProjectAssets();
        updateToolbarState();
    }

    function clearPreview() {
        const video = $("reel-preview-video");
        if (video) {
            video.pause();
            video.removeAttribute("src");
            video.load();
        }
        hide("reel-video-sidebar");
        renderPreviewTimelines();
    }

    function triggerDownload(url) {
        if (!url) return;
        const link = document.createElement("a");
        link.href = url;
        link.target = "_blank";
        link.rel = "noopener";
        link.click();
    }

    function openExternal(url) {
        if (!url) return;
        const link = document.createElement("a");
        link.href = url;
        link.target = "_blank";
        link.rel = "noopener noreferrer";
        link.click();
    }

    function formatRelativeDate(value) {
        if (!value) return "saved recently";
        const ts = new Date(value).getTime();
        if (!Number.isFinite(ts)) return "saved recently";
        const diffSeconds = Math.max(0, Math.round((Date.now() - ts) / 1000));
        if (diffSeconds < 60) return "saved just now";
        if (diffSeconds < 3600) return `saved ${Math.floor(diffSeconds / 60)}m ago`;
        if (diffSeconds < 86400) return `saved ${Math.floor(diffSeconds / 3600)}h ago`;
        return `saved ${Math.floor(diffSeconds / 86400)}d ago`;
    }

    function buildProjectMeta(project) {
        if (!project) return "Short-form projects autosave locally and can be resumed later.";
        const flags = [];
        const clipCount = Number(project.clip_count ?? project.clips?.length ?? 0);
        flags.push(clipCount === 1 ? "1 clip" : `${clipCount} clips`);
        if (project.has_captions || project.captions?.words?.length) flags.push("captions");
        if (project.has_export || project.export_file) flags.push("export ready");
        else if (project.has_concat || project.concat_file) flags.push("stitched");
        return `${flags.join(" · ")} · ${formatRelativeDate(project.updated_at)}`;
    }

    function renderProjectChrome(project = null) {
        const badge = $("reel-project-badge");
        const meta = $("reel-project-meta");
        if (!badge || !meta) return;

        const summary = project || recentProjects.find((item) => item.project_id === projectId);
        if (!projectId) {
            hide(badge);
            meta.textContent = "Short-form projects autosave locally and can be resumed later.";
            return;
        }

        badge.textContent = `Project ${projectId}`;
        show(badge);
        meta.textContent = summary
            ? `${summary.title || "Untitled short-form project"} · ${buildProjectMeta(summary)}`
            : `Autosaving locally as project ${projectId}.`;
    }

    function clampNumber(value, min, max) {
        return Math.min(max, Math.max(min, value));
    }

    function getClipRow(clipId) {
        return document.querySelector(`.clip-item[data-clip-id="${clipId}"]`);
    }

    function getActiveClipRow() {
        return activeClipId ? getClipRow(activeClipId) : null;
    }

    function syncActiveClipClasses() {
        document.querySelectorAll(".clip-item").forEach((row) => {
            row.classList.toggle("active", row.dataset.clipId === activeClipId);
        });
        document.querySelectorAll(".reel-timeline-segment").forEach((segment) => {
            segment.classList.toggle("active", segment.dataset.clipId === activeClipId);
        });
    }

    function getTimelineDuration() {
        const video = $("reel-preview-video");
        return Math.max(0, Number(vodDuration) || Number(video?.duration) || 0);
    }

    function getTimelineTrackRect() {
        return $("reel-timeline-track")?.getBoundingClientRect() || null;
    }

    function positionTimelineSegment(segment, start, end, duration) {
        const safeDuration = Math.max(0, Number(duration) || 0);
        if (!segment || safeDuration <= 0) return;
        const safeStart = clampNumber(Number(start) || 0, 0, Math.max(0, safeDuration - MIN_CLIP_DURATION));
        const safeEnd = clampNumber(Number(end) || 0, safeStart + MIN_CLIP_DURATION, safeDuration);
        segment.dataset.startSec = String(safeStart);
        segment.dataset.endSec = String(safeEnd);
        segment.style.left = `${(safeStart / safeDuration) * 100}%`;
        segment.style.width = `${Math.max(1.2, ((safeEnd - safeStart) / safeDuration) * 100)}%`;
        segment.title = `${formatTimestamp(safeStart)} - ${formatTimestamp(safeEnd)}`;
    }

    function applyClipDraft(clipId, start, end, options = {}) {
        const { syncPreview = false } = options;
        const row = getClipRow(clipId);
        const duration = getTimelineDuration();
        if (!row || duration <= 0) return;

        const safeStart = clampNumber(Number(start) || 0, 0, Math.max(0, duration - MIN_CLIP_DURATION));
        const safeEnd = clampNumber(Number(end) || 0, safeStart + MIN_CLIP_DURATION, duration);
        const startInput = row.querySelector(".clip-start");
        const endInput = row.querySelector(".clip-end");
        if (startInput) startInput.value = formatTimestamp(safeStart);
        if (endInput) endInput.value = formatTimestamp(safeEnd);

        const segment = document.querySelector(`.reel-timeline-segment[data-clip-id="${clipId}"]`);
        positionTimelineSegment(segment, safeStart, safeEnd, duration);

        const label = segment?.querySelector(".reel-timeline-label");
        if (label) {
            label.textContent = row.querySelector(".clip-title")?.value?.trim() || "Clip";
        }

        if (syncPreview && previewMode === "source") {
            const video = $("reel-preview-video");
            if (video) {
                const targetTime = timelineDragState?.mode === "end" ? safeEnd : safeStart;
                video.currentTime = clampNumber(targetTime, 0, duration);
            }
        }

        if (clipId === activeClipId) {
            renderClipInspector();
        }
    }

    function renderClipInspector() {
        const card = $("reel-clip-inspector");
        const heading = $("reel-inspector-heading");
        const sourceKind = $("reel-inspector-source-kind");
        const titleInput = $("reel-clip-title-input");
        const noteInput = $("reel-clip-note-input");
        const rangeInput = $("reel-inspector-range");
        const durationInput = $("reel-inspector-duration");
        if (!card || !heading || !titleInput || !noteInput || !rangeInput || !durationInput || !sourceKind) return;

        const row = getActiveClipRow();
        if (!row) {
            hide(card);
            heading.textContent = "No clip selected";
            titleInput.value = "";
            noteInput.value = "";
            rangeInput.value = "";
            durationInput.value = "";
            hide(sourceKind);
            return;
        }

        const clipNumber = row.querySelector(".clip-number")?.textContent || "Clip";
        const title = row.querySelector(".clip-title")?.value?.trim() || clipNumber;
        const start = parseTimestamp(row.querySelector(".clip-start")?.value || "0");
        const end = parseTimestamp(row.querySelector(".clip-end")?.value || "0");
        const sourceLabel = row.dataset.sourceKind || "";

        show(card);
        heading.textContent = `${clipNumber} · ${title}`;
        titleInput.value = row.querySelector(".clip-title")?.value || "";
        noteInput.value = row.dataset.note || "";
        rangeInput.value = `${formatTimestamp(start)} - ${formatTimestamp(end)}`;
        durationInput.value = formatTimestamp(Math.max(0, end - start));

        if (sourceLabel) {
            sourceKind.textContent = sourceLabel.replace(/[_-]+/g, " ");
            show(sourceKind);
        } else {
            hide(sourceKind);
        }
    }

    function scheduleInspectorMetaSave(field, value) {
        if (!activeClipId) return;
        if (clipInspectorTimer) {
            clearTimeout(clipInspectorTimer);
        }
        clipInspectorTimer = window.setTimeout(() => {
            clipInspectorTimer = null;
            updateClipMeta(activeClipId, field, value);
        }, 250);
    }

    function jumpToActiveClip() {
        const row = getActiveClipRow();
        const video = $("reel-preview-video");
        if (!row || !video) return;

        if (previewMode === "sequence" && concatReady) {
            const segments = getSequenceSegments();
            const sequenceSegment = segments.find((segment) => segment.id === activeClipId);
            if (sequenceSegment) {
                video.currentTime = sequenceSegment.sequenceStart;
                renderPreviewTimelines();
                return;
            }
        }

        video.currentTime = parseTimestamp(row.querySelector(".clip-start")?.value || "0");
        renderPreviewTimelines();
    }

    async function updateClipMeta(clipId, field, value) {
        if (!projectId) return;
        const body = { project_id: projectId, clip_id: clipId };
        body[field] = value;
        const data = await api("/api/reel/update-clip", {
            method: "POST",
            body: JSON.stringify(body),
        });
        if (!data.error) {
            const row = document.querySelector(`.clip-item[data-clip-id="${clipId}"]`);
            if (row && field === "note") {
                row.dataset.note = value || "";
                const noteBtn = row.querySelector(".clip-note-btn");
                if (noteBtn) {
                    noteBtn.classList.toggle("has-note", Boolean(value));
                    noteBtn.textContent = value ? "Note*" : "Note";
                }
            }
            if (row && field === "title") {
                row.title = value || "";
                const titleInput = row.querySelector(".clip-title");
                if (titleInput && titleInput.value !== value) {
                    titleInput.value = value || "";
                }
            }
            await loadRecentProjects();
            await loadProjectAssets();
            if (clipId === activeClipId) {
                renderClipInspector();
            }
            renderPreviewTimelines();
        }
    }

    async function editClipNote(clipId) {
        setActiveClip(clipId);
        const noteInput = $("reel-clip-note-input");
        if (noteInput) {
            noteInput.focus();
            noteInput.select();
        }
    }

    function updateToolbarState() {
        const isUrlSource = sourceType === "url";
        if ($("reel-url-input")) $("reel-url-input").disabled = !isUrlSource;
        if ($("reel-validate-btn")) $("reel-validate-btn").disabled = !isUrlSource;
        if ($("reel-import-moments-btn")) {
            $("reel-import-moments-btn").disabled = !isUrlSource || !projectId || !vodUrl;
        }
        if ($("reel-add-clip-btn")) $("reel-add-clip-btn").disabled = !projectId || !vodDuration;
        if ($("reel-download-clips-btn")) $("reel-download-clips-btn").disabled = !projectId || getClipRows().length === 0;
        if ($("reel-transcribe-btn")) $("reel-transcribe-btn").disabled = !concatReady;
        if ($("reel-export-btn")) $("reel-export-btn").disabled = !concatReady;
        $("reel-preview-source-btn")?.classList.toggle("active", previewMode === "source");
        $("reel-preview-sequence-btn")?.classList.toggle("active", previewMode === "sequence");
        const canPreviewSource = sourceType === "file"
            ? Boolean(localVodObjectUrl || localVodUploaded)
            : Boolean(projectId && vodUrl);
        if ($("reel-preview-source-btn")) $("reel-preview-source-btn").disabled = !canPreviewSource;
        if ($("reel-preview-sequence-btn")) $("reel-preview-sequence-btn").disabled = !concatReady;
    }

    function renderRecentProjects() {
        const card = $("reel-recent-projects-card");
        const container = $("reel-recent-projects");
        if (!card || !container) return;

        if (!recentProjects.length) {
            hide(card);
            container.innerHTML = "";
            renderProjectChrome();
            return;
        }

        show(card);
        container.innerHTML = "";

        recentProjects.forEach((project) => {
            const row = document.createElement("div");
            row.className = `reel-project-row${project.project_id === projectId ? " current" : ""}`;

            const main = document.createElement("div");
            main.className = "reel-project-row-main";
            main.innerHTML = `
                <span class="reel-project-row-title">${escapeHtml(project.title || "Untitled short-form project")}</span>
                <div class="reel-project-row-meta">${escapeHtml(buildProjectMeta(project))}</div>
            `;

            const actions = document.createElement("div");
            actions.className = "reel-project-row-actions";

            const resumeBtn = document.createElement("button");
            resumeBtn.type = "button";
            resumeBtn.className = "secondary-btn";
            resumeBtn.textContent = project.project_id === projectId ? "Open" : "Resume";
            resumeBtn.addEventListener("click", () => {
                resumeProject(project.project_id);
            });

            actions.appendChild(resumeBtn);
            row.appendChild(main);
            row.appendChild(actions);
            container.appendChild(row);
        });

        renderProjectChrome();
    }

    function renderAssetBin() {
        const card = $("reel-assets-card");
        const list = $("reel-assets-list");
        const count = $("reel-assets-count");
        if (!card || !list || !count) return;

        if (!projectId || projectAssets.length === 0) {
            hide(card);
            list.innerHTML = "";
            count.textContent = "0 items";
            return;
        }

        show(card);
        list.innerHTML = "";
        count.textContent = `${projectAssets.length} item${projectAssets.length === 1 ? "" : "s"}`;

        projectAssets.forEach((asset) => {
            const row = document.createElement("div");
            row.className = "reel-asset-row";

            const main = document.createElement("div");
            main.className = "reel-asset-main";
            main.innerHTML = `
                <span class="reel-asset-pill">${escapeHtml(asset.category || asset.kind || "asset")}</span>
                <span class="reel-asset-label">${escapeHtml(asset.label || "Untitled asset")}</span>
                <div class="reel-asset-meta">${escapeHtml(asset.detail || (asset.exists ? "Ready" : "Pending"))}</div>
            `;

            const actions = document.createElement("div");
            actions.className = "reel-asset-actions";

            if (asset.preview_url) {
                const previewBtn = document.createElement("button");
                previewBtn.type = "button";
                previewBtn.className = "secondary-btn";
                previewBtn.textContent = "Preview";
                previewBtn.addEventListener("click", () => {
                    loadPreview(asset.preview_url, asset.kind === "sequence" ? "sequence" : "source");
                });
                actions.appendChild(previewBtn);
            }

            if (asset.download_url) {
                const openBtn = document.createElement("button");
                openBtn.type = "button";
                openBtn.className = "secondary-btn";
                openBtn.textContent = asset.kind === "export" ? "Download" : "Open";
                openBtn.addEventListener("click", () => {
                    triggerDownload(asset.download_url);
                });
                actions.appendChild(openBtn);
            }

            if (asset.external_url) {
                const visitBtn = document.createElement("button");
                visitBtn.type = "button";
                visitBtn.className = "secondary-btn";
                visitBtn.textContent = "Visit";
                visitBtn.addEventListener("click", () => {
                    openExternal(asset.external_url);
                });
                actions.appendChild(visitBtn);
            }

            row.appendChild(main);
            row.appendChild(actions);
            list.appendChild(row);
        });
    }

    async function loadProjectAssets() {
        if (!projectId) {
            projectAssets = [];
            renderAssetBin();
            return [];
        }
        try {
            const data = await api(`/api/reel/assets/${projectId}`);
            projectAssets = data.items || [];
        } catch (e) {
            projectAssets = [];
        }
        renderAssetBin();
        return projectAssets;
    }

    async function loadRecentProjects() {
        try {
            const data = await api("/api/reel/projects");
            recentProjects = data.projects || [];
            renderRecentProjects();
        } catch (e) {
            recentProjects = [];
            renderRecentProjects();
        }
        return recentProjects;
    }

    function resetEditorState(options = {}) {
        const { forgetProject = false } = options;
        if (pollTimer) {
            clearInterval(pollTimer);
            pollTimer = null;
        }

        clearLocalVodPreviewUrl();
        clearRemotePreviewUrl();
        clearPreview();
        projectAssets = [];

        vodUrl = "";
        vodDuration = 0;
        sourceType = "url";
        localVodFile = null;
        localVodUploaded = false;
        concatReady = false;
        exportedReelReady = false;
        draggedClipId = "";
        activeClipId = "";
        sourceMoments = [];
        previewMode = "source";
        timelineZoom = 1;
        timelineDragState = null;
        if (clipInspectorTimer) {
            clearTimeout(clipInspectorTimer);
            clipInspectorTimer = null;
        }

        if (forgetProject) {
            setProjectIdentity("");
        }

        $("reel-url-input").value = "";
        if ($("reel-video-input")) $("reel-video-input").value = "";
        $("reel-vod-title").textContent = "";
        $("reel-vod-duration").textContent = "";
        $("reel-clip-list").innerHTML = "";
        $("reel-download-bar").style.width = "0%";
        $("reel-download-status-text").textContent = "Starting...";
        $("reel-transcribe-bar").style.width = "0%";
        $("reel-transcribe-status").textContent = "Starting...";
        setSourceMomentStatus("");
        if ($("reel-timeline-zoom")) $("reel-timeline-zoom").value = "1";

        hide("reel-vod-info");
        hide("reel-clip-actions");
        hide("reel-download-status");
        hide("reel-transcribe-progress");
        hide("reel-caption-editor");
        hide("reel-local-file-restore");
        hideError("reel-step1-error");
        hideError("reel-step2-error");
        hideError("reel-step3-error");
        hideError("reel-step4-error");

        disableStep(2);
        disableStep(3);
        disableStep(4);
        $("reel-transcribe-btn").disabled = true;
        resetExportState();
        setSourceType("url");
        applyTimelineZoom();
        renderTimeline();

        if (typeof CaptionEditor !== "undefined" && typeof CaptionEditor.reset === "function") {
            CaptionEditor.reset();
        }

        renderClipInspector();
        renderAssetBin();
        renderProjectChrome();
        updateToolbarState();
    }

    function resetExportState() {
        exportedReelReady = false;
        hide("reel-export-download");
        hide("reel-export-download-btn");
        hide("reel-export-progress");
        $("reel-export-bar").style.width = "0%";
        $("reel-export-status").textContent = "Starting...";
        $("reel-export-btn").disabled = true;
    }

    function markExportDirty() {
        exportedReelReady = false;
        hide("reel-export-download");
        hide("reel-export-download-btn");
        hide("reel-export-progress");
        $("reel-export-bar").style.width = "0%";
        $("reel-export-status").textContent = "Starting...";
        $("reel-export-btn").disabled = !concatReady;
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

    function formatTimestamp(seconds) {
        const total = Math.max(0, Number(seconds) || 0);
        const hours = Math.floor(total / 3600);
        const mins = Math.floor((total % 3600) / 60);
        const secs = total - (hours * 3600) - (mins * 60);
        const wholeSeconds = Math.floor(secs);
        const centiseconds = Math.round((secs - wholeSeconds) * 100);
        const secondLabel = `${wholeSeconds.toString().padStart(2, "0")}.${centiseconds.toString().padStart(2, "0")}`;
        if (hours > 0) {
            return `${hours}:${mins.toString().padStart(2, "0")}:${secondLabel}`;
        }
        return `${mins}:${secondLabel}`;
    }

    function parseTimestamp(value) {
        const trimmed = String(value || "").trim();
        if (!trimmed) return 0;
        const parts = trimmed.split(":").map((part) => part.trim());
        if (parts.length === 3) {
            return (parseFloat(parts[0]) || 0) * 3600
                + (parseFloat(parts[1]) || 0) * 60
                + (parseFloat(parts[2]) || 0);
        }
        if (parts.length === 2) {
            return (parseFloat(parts[0]) || 0) * 60
                + (parseFloat(parts[1]) || 0);
        }
        return parseFloat(parts[0]) || 0;
    }

    async function createThumbnailFromPreview(timestamp) {
        const preview = $("reel-preview-video");
        if (!preview || !preview.currentSrc) {
            throw new Error("Preview source not available");
        }

        return await new Promise((resolve, reject) => {
            const tempVideo = document.createElement("video");
            tempVideo.preload = "auto";
            tempVideo.muted = true;
            tempVideo.playsInline = true;
            tempVideo.crossOrigin = "anonymous";

            const cleanup = () => {
                tempVideo.src = "";
                tempVideo.load();
            };

            tempVideo.onloadedmetadata = () => {
                const safeTime = Math.max(0, Math.min(Number(tempVideo.duration || timestamp || 0), Number(timestamp) || 0));
                tempVideo.currentTime = safeTime;
            };
            tempVideo.onseeked = () => {
                try {
                    const canvas = document.createElement("canvas");
                    const width = Math.max(160, tempVideo.videoWidth || 320);
                    const height = Math.max(90, tempVideo.videoHeight || 180);
                    canvas.width = width;
                    canvas.height = height;
                    const ctx = canvas.getContext("2d");
                    ctx.drawImage(tempVideo, 0, 0, width, height);
                    const dataUrl = canvas.toDataURL("image/jpeg", 0.82);
                    cleanup();
                    resolve(dataUrl);
                } catch (e) {
                    cleanup();
                    reject(e);
                }
            };
            tempVideo.onerror = () => {
                cleanup();
                reject(new Error("Preview thumbnail capture failed"));
            };

            tempVideo.src = preview.currentSrc || preview.src;
            tempVideo.load();
        });
    }

    function setClipThumbnail(clipId, imageSrc) {
        const row = document.querySelector(`.clip-item[data-clip-id="${clipId}"]`);
        if (!row) return;
        const img = row.querySelector(".clip-thumb-image");
        const empty = row.querySelector(".clip-thumb-empty");
        if (!img || !empty) return;
        if (imageSrc) {
            img.src = imageSrc;
            img.classList.remove("hidden");
            empty.classList.add("hidden");
        } else {
            img.removeAttribute("src");
            img.classList.add("hidden");
            empty.classList.remove("hidden");
        }
    }

    async function refreshClipThumbnail(clipId, timestamp = null) {
        const row = document.querySelector(`.clip-item[data-clip-id="${clipId}"]`);
        if (!row) return;
        const resolvedTimestamp = timestamp ?? parseTimestamp(row.querySelector(".clip-start")?.value || "0");
        if (!Number.isFinite(resolvedTimestamp) || resolvedTimestamp < 0) return;

        try {
            const dataUrl = await createThumbnailFromPreview(resolvedTimestamp);
            setClipThumbnail(clipId, dataUrl);
            return;
        } catch (e) {
            if (!projectId) return;
        }

        setClipThumbnail(clipId, `/api/reel/thumbnail/${projectId}?ts=${resolvedTimestamp.toFixed(3)}&t=${Date.now()}`);
    }

    function setActiveClip(clipId) {
        activeClipId = clipId || "";
        syncActiveClipClasses();
        renderClipInspector();
        renderPreviewTimelines();
    }

    function getClipRows() {
        return Array.from($("reel-clip-list")?.children || []);
    }

    function getClipSegments() {
        return getClipRows().map((row) => {
            const start = parseTimestamp(row.querySelector(".clip-start")?.value || "0");
            const end = parseTimestamp(row.querySelector(".clip-end")?.value || "0");
            return {
                id: row.dataset.clipId || "",
                title: row.querySelector(".clip-title")?.value?.trim() || row.title || "",
                start,
                end,
            };
        }).filter((clip) => clip.id && clip.end > clip.start);
    }

    function getSequenceSegments() {
        let cursor = 0;
        return getClipSegments().map((clip) => {
            const duration = Math.max(0, clip.end - clip.start);
            const segment = {
                ...clip,
                sequenceStart: cursor,
                sequenceEnd: cursor + duration,
                duration,
            };
            cursor += duration;
            return segment;
        }).filter((clip) => clip.duration > 0);
    }

    function renderSequenceTimeline() {
        const track = $("reel-sequence-track");
        const clipsEl = $("reel-sequence-clips");
        const playhead = $("reel-sequence-playhead");
        const timeLabel = $("reel-sequence-time");
        const card = $("reel-sequence-card");
        const activeLabel = $("reel-sequence-active");
        const video = $("reel-preview-video");
        if (!track || !clipsEl || !playhead || !timeLabel || !card || !activeLabel) return;

        const segments = getSequenceSegments();
        const duration = segments.length > 0 ? segments[segments.length - 1].sequenceEnd : 0;
        const currentTime = Math.max(0, Number(video?.currentTime) || 0);
        const clampedCurrent = duration > 0 ? Math.min(currentTime, duration) : 0;

        if (previewMode !== "sequence" || duration <= 0) {
            hide(card);
            return;
        }

        show(card);
        timeLabel.textContent = `${formatTime(clampedCurrent)} / ${formatTime(duration)}`;
        playhead.style.left = `${(clampedCurrent / duration) * 100}%`;
        clipsEl.innerHTML = "";

        const currentSegment = segments.find((segment) => clampedCurrent >= segment.sequenceStart && clampedCurrent < segment.sequenceEnd)
            || segments[segments.length - 1];
        activeLabel.textContent = currentSegment
            ? `${currentSegment.title || `Clip ${segments.indexOf(currentSegment) + 1}`} · ${formatTime(currentSegment.duration)}`
            : "Sequence preview shows the stitched clip order before export.";

        segments.forEach((segment, index) => {
            const segmentEl = document.createElement("div");
            segmentEl.className = `reel-timeline-segment${segment.id === activeClipId ? " active" : ""}`;
            segmentEl.dataset.clipId = segment.id;
            segmentEl.style.left = `${(segment.sequenceStart / duration) * 100}%`;
            segmentEl.style.width = `${Math.max(1.2, (segment.duration / duration) * 100)}%`;
            segmentEl.title = `${segment.title || `Clip ${index + 1}`} · ${formatTime(segment.duration)}`;

            const label = document.createElement("span");
            label.className = "reel-timeline-label";
            label.textContent = segment.title || `Clip ${index + 1}`;

            const startHandle = document.createElement("button");
            startHandle.type = "button";
            startHandle.className = "reel-timeline-handle reel-timeline-handle-start";
            startHandle.title = "Trim sequence clip start";
            startHandle.setAttribute("aria-label", "Trim sequence clip start");
            startHandle.addEventListener("pointerdown", (event) => {
                event.stopPropagation();
                startTimelineDrag(event, segment, "start", "sequence");
            });

            const endHandle = document.createElement("button");
            endHandle.type = "button";
            endHandle.className = "reel-timeline-handle reel-timeline-handle-end";
            endHandle.title = "Trim sequence clip end";
            endHandle.setAttribute("aria-label", "Trim sequence clip end");
            endHandle.addEventListener("pointerdown", (event) => {
                event.stopPropagation();
                startTimelineDrag(event, segment, "end", "sequence");
            });

            segmentEl.appendChild(label);
            segmentEl.appendChild(startHandle);
            segmentEl.appendChild(endHandle);
            segmentEl.addEventListener("click", (event) => {
                event.stopPropagation();
                setActiveClip(segment.id);
                if (video) video.currentTime = segment.sequenceStart;
                renderPreviewTimelines();
            });
            clipsEl.appendChild(segmentEl);
        });
    }

    function renderPreviewTimelines() {
        renderTimeline();
        renderSequenceTimeline();
        updateToolbarState();
    }

    function renderTimeline() {
        const track = $("reel-timeline-track");
        const clipsEl = $("reel-timeline-clips");
        const playhead = $("reel-timeline-playhead");
        const timeLabel = $("reel-preview-time");
        const card = $("reel-timeline-card");
        const video = $("reel-preview-video");
        if (!track || !clipsEl || !playhead || !timeLabel || !card) return;

        if (previewMode !== "source") {
            hide(card);
            return;
        }

        const duration = Math.max(0, Number(vodDuration) || Number(video?.duration) || 0);
        const currentTime = Math.max(0, Number(video?.currentTime) || 0);
        const clampedCurrent = duration > 0 ? Math.min(currentTime, duration) : 0;

        timeLabel.textContent = `${formatTime(clampedCurrent)} / ${formatTime(duration)}`;

        if (duration <= 0) {
            hide(card);
            return;
        }

        show(card);
        playhead.style.left = `${(clampedCurrent / duration) * 100}%`;
        clipsEl.innerHTML = "";

        getClipSegments().forEach((clip) => {
            const segment = document.createElement("div");
            segment.className = `reel-timeline-segment${clip.id === activeClipId ? " active" : ""}`;
            segment.dataset.clipId = clip.id;
            positionTimelineSegment(segment, clip.start, clip.end, duration);

            const label = document.createElement("span");
            label.className = "reel-timeline-label";
            label.textContent = clip.title || "Clip";

            const startHandle = document.createElement("button");
            startHandle.type = "button";
            startHandle.className = "reel-timeline-handle reel-timeline-handle-start";
            startHandle.title = "Drag to trim clip start";
            startHandle.setAttribute("aria-label", "Trim clip start");
            startHandle.addEventListener("pointerdown", (event) => {
                event.stopPropagation();
                startTimelineDrag(event, clip, "start");
            });

            const endHandle = document.createElement("button");
            endHandle.type = "button";
            endHandle.className = "reel-timeline-handle reel-timeline-handle-end";
            endHandle.title = "Drag to trim clip end";
            endHandle.setAttribute("aria-label", "Trim clip end");
            endHandle.addEventListener("pointerdown", (event) => {
                event.stopPropagation();
                startTimelineDrag(event, clip, "end");
            });

            segment.appendChild(label);
            segment.appendChild(startHandle);
            segment.appendChild(endHandle);
            segment.addEventListener("pointerdown", (event) => {
                if (event.target.closest(".reel-timeline-handle")) return;
                startTimelineDrag(event, clip, "move");
            });
            clipsEl.appendChild(segment);
        });
    }

    function seekPreviewToPercent(percent) {
        const video = $("reel-preview-video");
        const duration = Math.max(0, Number(vodDuration) || Number(video?.duration) || 0);
        if (!video || duration <= 0) return;
        const clamped = Math.max(0, Math.min(1, percent));
        video.currentTime = duration * clamped;
        renderTimeline();
    }

    function onTimelinePointer(event) {
        if (event.target.closest(".reel-timeline-segment")) return;
        const track = $("reel-timeline-track");
        if (!track) return;
        const rect = track.getBoundingClientRect();
        if (!rect.width) return;
        seekPreviewToPercent((event.clientX - rect.left) / rect.width);
    }

    function seekSequenceToPercent(percent) {
        const video = $("reel-preview-video");
        const segments = getSequenceSegments();
        const duration = segments.length > 0 ? segments[segments.length - 1].sequenceEnd : 0;
        if (!video || duration <= 0) return;
        const clamped = Math.max(0, Math.min(1, percent));
        video.currentTime = duration * clamped;
        renderPreviewTimelines();
    }

    function onSequencePointer(event) {
        if (event.target.closest(".reel-timeline-segment")) return;
        const track = $("reel-sequence-track");
        if (!track) return;
        const rect = track.getBoundingClientRect();
        if (!rect.width) return;
        seekSequenceToPercent((event.clientX - rect.left) / rect.width);
    }

    async function nudgePreview(seconds) {
        const video = $("reel-preview-video");
        const duration = Math.max(0, Number(vodDuration) || Number(video?.duration) || 0);
        if (!video || duration <= 0) return;
        const target = Math.max(0, Math.min(duration, (video.currentTime || 0) + seconds));
        video.currentTime = target;
        renderPreviewTimelines();
    }

    function startTimelineDrag(event, clip, mode, timelineKind = "source") {
        const sourceDuration = getTimelineDuration();
        const sequenceSegments = getSequenceSegments();
        const duration = timelineKind === "sequence"
            ? (sequenceSegments.length > 0 ? sequenceSegments[sequenceSegments.length - 1].sequenceEnd : 0)
            : sourceDuration;
        const rect = timelineKind === "sequence"
            ? $("reel-sequence-track")?.getBoundingClientRect()
            : getTimelineTrackRect();
        if (!duration || !rect?.width) return;

        event.preventDefault();
        activeClipId = clip.id;
        syncActiveClipClasses();

        timelineDragState = {
            clipId: clip.id,
            mode,
            pointerId: event.pointerId,
            initialStart: Number(clip.start) || 0,
            initialEnd: Number(clip.end) || 0,
            duration,
            sourceDuration,
            startX: event.clientX,
            moved: false,
            pointerTarget: event.currentTarget,
            timelineKind,
        };

        if (typeof event.currentTarget?.setPointerCapture === "function") {
            try {
                event.currentTarget.setPointerCapture(event.pointerId);
            } catch (e) {
                // Ignore pointer capture failures.
            }
        }
    }

    function onTimelineDragMove(event) {
        if (!timelineDragState || event.pointerId !== timelineDragState.pointerId) return;

        const rect = timelineDragState.timelineKind === "sequence"
            ? $("reel-sequence-track")?.getBoundingClientRect()
            : getTimelineTrackRect();
        if (!rect?.width) return;

        const deltaSeconds = ((event.clientX - timelineDragState.startX) / rect.width) * timelineDragState.duration;
        const clipDuration = Math.max(MIN_CLIP_DURATION, timelineDragState.initialEnd - timelineDragState.initialStart);
        const sourceDuration = Math.max(MIN_CLIP_DURATION, timelineDragState.sourceDuration || getTimelineDuration());

        let start = timelineDragState.initialStart;
        let end = timelineDragState.initialEnd;

        if (timelineDragState.mode === "start") {
            start = clampNumber(
                timelineDragState.initialStart + deltaSeconds,
                0,
                timelineDragState.initialEnd - MIN_CLIP_DURATION
            );
        } else if (timelineDragState.mode === "end") {
            end = clampNumber(
                timelineDragState.initialEnd + deltaSeconds,
                timelineDragState.initialStart + MIN_CLIP_DURATION,
                sourceDuration
            );
        } else {
            start = clampNumber(
                timelineDragState.initialStart + deltaSeconds,
                0,
                sourceDuration - clipDuration
            );
            end = start + clipDuration;
        }

        timelineDragState.moved = timelineDragState.moved || Math.abs(deltaSeconds) > 0.01;
        applyClipDraft(timelineDragState.clipId, start, end, { syncPreview: true });
        renderPreviewTimelines();
    }

    async function onTimelineDragEnd(event) {
        if (!timelineDragState || event.pointerId !== timelineDragState.pointerId) return;

        const drag = timelineDragState;
        timelineDragState = null;

        if (typeof drag.pointerTarget?.releasePointerCapture === "function") {
            try {
                drag.pointerTarget.releasePointerCapture(event.pointerId);
            } catch (e) {
                // Ignore pointer capture failures.
            }
        }

        const row = getClipRow(drag.clipId);
        if (!row) return;
        const start = parseTimestamp(row.querySelector(".clip-start")?.value || "0");
        const end = parseTimestamp(row.querySelector(".clip-end")?.value || "0");

        if (!drag.moved) {
            const video = $("reel-preview-video");
            if (video) {
                video.currentTime = start;
            }
            setActiveClip(drag.clipId);
            return;
        }

        await api("/api/reel/update-clip", {
            method: "POST",
            body: JSON.stringify({
                project_id: projectId,
                clip_id: drag.clipId,
                start: formatTimestamp(start),
                end: formatTimestamp(end),
            }),
        });
        await loadRecentProjects();
        refreshClipThumbnail(drag.clipId, start);
        renderPreviewTimelines();
    }

    async function handlePreviewShortcut(event) {
        const target = event.target;
        const typing = target && (
            target.tagName === "INPUT"
            || target.tagName === "TEXTAREA"
            || target.tagName === "SELECT"
            || target.isContentEditable
        );
        if (typing || !projectId || $("reel-workflow")?.classList.contains("hidden")) return;

        const key = event.key.toLowerCase();
        if (key === " " || event.code === "Space") {
            event.preventDefault();
            togglePlay();
            return;
        }
        if (key === "i") {
            event.preventDefault();
            if (activeClipId) await capturePreviewTime(activeClipId, "start");
            return;
        }
        if (key === "o") {
            event.preventDefault();
            if (activeClipId) await capturePreviewTime(activeClipId, "end");
            return;
        }
        if (key === "[" || key === "]") {
            event.preventDefault();
            const delta = event.shiftKey ? 0.1 : 1;
            await nudgePreview(key === "[" ? -delta : delta);
        }
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
        updateToolbarState();
    }

    function clearLocalVodPreviewUrl() {
        if (localVodObjectUrl) {
            URL.revokeObjectURL(localVodObjectUrl);
            localVodObjectUrl = "";
        }
    }

    function clearRemotePreviewUrl() {
        remotePreviewUrl = "";
    }

    function openLocalVodPicker() {
        setSourceType("file");
        $("reel-video-input")?.click();
    }

    async function importSourceMoments(options = {}) {
        const { auto = false } = options;
        hideError("reel-step2-error");

        if (!projectId || sourceType !== "url" || !vodUrl) {
            if (!auto) {
                showError("reel-step2-error", "Load a remote VOD URL first.");
            }
            return { imported_count: 0, imported_clips: [] };
        }

        const btn = $("reel-import-moments-btn");
        const previousLabel = btn?.textContent || "Import Moments";
        if (btn) {
            btn.disabled = true;
            btn.textContent = auto ? "Scanning..." : "Importing...";
        }

        try {
            const data = await api("/api/reel/import-source-moments", {
                method: "POST",
                body: JSON.stringify({ project_id: projectId }),
            });

            if (data.error) {
                if (!auto) {
                    showError("reel-step2-error", data.error);
                }
                setSourceMomentStatus(data.error);
                return { imported_count: 0, imported_clips: [], error: data.error };
            }

            sourceMoments = data.moments || [];
            const importedClips = data.imported_clips || [];
            importedClips.forEach((clip) => renderClipItem(clip));
            if (importedClips.length > 0) {
                show("reel-clip-actions");
                enableStep(2);
            }

            if (data.message) {
                setSourceMomentStatus(data.message);
            } else if (sourceMoments.length > 0) {
                setSourceMomentStatus(`Detected ${sourceMoments.length} source moment(s) on this VOD.`);
            } else {
                setSourceMomentStatus("No source moments were exposed by this VOD.");
            }

            await loadRecentProjects();
            await loadProjectAssets();
            updateToolbarState();
            return data;
        } catch (e) {
            const message = e.message || "Moment import failed.";
            if (!auto) {
                showError("reel-step2-error", message);
            }
            setSourceMomentStatus(message);
            return { imported_count: 0, imported_clips: [], error: message };
        } finally {
            if (btn) {
                btn.textContent = previousLabel;
            }
            updateToolbarState();
        }
    }

    async function restoreProjectPreview(project) {
        hide("reel-local-file-restore");

        if (project.concat_file) {
            loadConcatPreview();
            return;
        }

        if ((project.source_type || "url") === "file") {
            if (project.vod_url && project.vod_url.startsWith("local:")) {
                loadPreview(`/api/reel/serve-vod/${projectId}`, "source");
                return;
            }
            clearPreview();
            show("reel-local-file-restore");
            return;
        }

        if (project.vod_url) {
            await loadRemoteVodPreview(project.vod_url);
            return;
        }

        clearPreview();
    }

    async function applyProjectState(project, restoredProjectId) {
        resetEditorState();
        setProjectIdentity(restoredProjectId);

        vodUrl = project.vod_url || "";
        vodDuration = Number(project.vod_duration || 0);
        sourceType = project.source_type || "url";
        localVodUploaded = Boolean(project.local_file_uploaded);
        sourceMoments = project.source_moments || [];
        localVodFile = null;
        activeClipId = "";
        concatReady = Boolean(project.concat_file);
        exportedReelReady = Boolean(project.export_file);

        setSourceType(sourceType);
        $("reel-url-input").value = sourceType === "url" ? vodUrl : "";

        if (project.vod_title || vodDuration) {
            $("reel-vod-title").textContent = project.vod_title || "Untitled VOD";
            $("reel-vod-duration").textContent = formatTime(vodDuration);
            show("reel-vod-info");
            enableStep(2);
        }

        const clipList = $("reel-clip-list");
        clipList.innerHTML = "";
        (project.clips || []).forEach((clip) => renderClipItem(clip));
        if (clipList.children.length > 0) {
            show("reel-clip-actions");
        }

        if (sourceMoments.length > 0) {
            setSourceMomentStatus(`Loaded ${sourceMoments.length} source moment(s) from this VOD.`);
        }

        if (concatReady) {
            enableStep(3);
            enableStep(4);
            $("reel-transcribe-btn").disabled = false;
            $("reel-export-btn").disabled = false;
        } else {
            $("reel-transcribe-btn").disabled = true;
            $("reel-export-btn").disabled = true;
        }

        if (exportedReelReady) {
            $("reel-export-btn").disabled = false;
            $("reel-export-status").textContent = "Export ready for download.";
            show("reel-export-download");
            show("reel-export-download-btn");
        } else {
            markExportDirty();
        }

        await restoreProjectPreview(project);

        if (project.captions?.words?.length) {
            show("reel-caption-editor");
            if (typeof CaptionEditor !== "undefined") {
                await CaptionEditor.init(restoredProjectId);
            }
        }

        renderProjectChrome(project);
        await loadProjectAssets();
        updateToolbarState();
    }

    async function previewSource() {
        hideError("reel-step1-error");
        hide("reel-local-file-restore");

        if (sourceType === "file") {
            if (localVodObjectUrl) {
                loadPreview(localVodObjectUrl, "source");
                return;
            }
            if (localVodUploaded && projectId) {
                loadPreview(`/api/reel/serve-vod/${projectId}`, "source");
                return;
            }
            show("reel-local-file-restore");
            showError("reel-step1-error", "Re-select the local source file to preview it again.");
            return;
        }

        if (vodUrl) {
            await loadRemoteVodPreview(vodUrl);
        }
    }

    function previewSequence() {
        if (!concatReady || !projectId) return;
        loadConcatPreview();
    }

    async function resumeProject(resumeProjectId, options = {}) {
        const { silent = false } = options;
        if (!resumeProjectId) return false;

        try {
            const project = await api(`/api/reel/project/${resumeProjectId}`);
            if (project.error) {
                throw new Error(project.error);
            }
            await applyProjectState(project, resumeProjectId);
            await loadRecentProjects();
            return true;
        } catch (e) {
            if (getRememberedProject() === resumeProjectId) {
                clearRememberedProject();
            }
            if (!silent) {
                showError("reel-step1-error", e.message || "Failed to load project.");
            }
            await loadRecentProjects();
            return false;
        }
    }

    async function restoreLastProject() {
        const rememberedProjectId = getRememberedProject();
        if (!rememberedProjectId) return false;
        return resumeProject(rememberedProjectId, { silent: true });
    }

    async function startNewProject() {
        resetEditorState({ forgetProject: true });
        await loadRecentProjects();
    }

    // ── Create project ────────────────────────────────────────────

    async function ensureProject() {
        if (projectId) return projectId;
        const data = await api("/api/reel/create-project", { method: "POST" });
        setProjectIdentity(data.project_id);
        await loadRecentProjects();
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
            resetExportState();
            clearLocalVodPreviewUrl();
            clearRemotePreviewUrl();
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
                    source_type: "url",
                }),
            });

            $("reel-vod-title").textContent = data.title || "Unknown";
            $("reel-vod-duration").textContent = formatTime(vodDuration);
            show("reel-vod-info");
            hide("reel-local-file-restore");
            await loadProjectAssets();
            await loadRemoteVodPreview(url);
            await loadRecentProjects();
            const importResult = await importSourceMoments({ auto: true });

            enableStep(2);

            // Auto-add first clip if none exist
            if ((importResult.imported_count || 0) === 0 && $("reel-clip-list").children.length === 0) {
                await addClip();
            }
        } catch (e) {
            showError("reel-step1-error", e.message || "Validation failed");
        } finally {
            btn.disabled = false;
            btn.textContent = "Load VOD";
            updateToolbarState();
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
        await loadRecentProjects();
        await loadProjectAssets();
        updateToolbarState();
    }

    function renumberClipItems() {
        Array.from($("reel-clip-list").children).forEach((el, i) => {
            const num = el.querySelector(".clip-number");
            if (num) num.textContent = `#${i + 1}`;
        });
    }

    async function syncClipOrder() {
        if (!projectId) return;
        const clipIds = Array.from($("reel-clip-list").children).map((el) => el.dataset.clipId);
        await api("/api/reel/reorder-clips", {
            method: "POST",
            body: JSON.stringify({ project_id: projectId, clip_ids: clipIds }),
        });
    }

    function attachClipDragHandlers(div) {
        div.draggable = true;
        div.addEventListener("click", () => {
            setActiveClip(div.dataset.clipId || "");
        });
        div.addEventListener("dragstart", () => {
            draggedClipId = div.dataset.clipId || "";
            div.classList.add("dragging");
        });

        div.addEventListener("dragend", async () => {
            div.classList.remove("dragging");
            draggedClipId = "";
            renumberClipItems();
            renderClipInspector();
            await syncClipOrder();
            await loadProjectAssets();
            renderPreviewTimelines();
        });

        div.addEventListener("dragover", (event) => {
            event.preventDefault();
            const dragging = $("reel-clip-list").querySelector(".clip-item.dragging");
            if (!dragging || dragging === div) return;

            const rect = div.getBoundingClientRect();
            const shouldInsertAfter = (event.clientY - rect.top) > (rect.height / 2);
            if (shouldInsertAfter) {
                div.parentNode.insertBefore(dragging, div.nextSibling);
            } else {
                div.parentNode.insertBefore(dragging, div);
            }
        });
    }

    async function handleLocalFile(file) {
        if (!file) return;

        hideError("reel-step1-error");
        clearLocalVodPreviewUrl();
        localVodFile = file;
        localVodUploaded = false;
        vodUrl = "";
        concatReady = false;
        resetExportState();
        clearRemotePreviewUrl();

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
            await api("/api/reel/set-vod", {
                method: "POST",
                body: JSON.stringify({
                    project_id: projectId,
                    source_type: "file",
                    url: "",
                    title: file.name,
                    duration: vodDuration,
                }),
            });

            $("reel-vod-title").textContent = file.name || "Local video";
            $("reel-vod-duration").textContent = formatTime(vodDuration);
            show("reel-vod-info");
            hide("reel-local-file-restore");
            loadPreview(localVodObjectUrl);
            await loadRecentProjects();
            await loadProjectAssets();
            enableStep(2);
            setSourceMomentStatus("Local VOD loaded. Source moment import is available for remote VOD URLs only.");

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
        hide("reel-local-file-restore");
        localVodUploaded = true;
        await loadRecentProjects();
        await loadProjectAssets();
        if (previewMode === "source") {
            loadPreview(`/api/reel/serve-vod/${projectId}`, "source");
        }
        updateToolbarState();
        return true;
    }

    function renderClipItem(clip) {
        const container = $("reel-clip-list");
        const idx = container.children.length + 1;

        const div = document.createElement("div");
        div.className = "clip-item";
        div.dataset.clipId = clip.id;
        div.dataset.note = clip.note || "";
        div.dataset.sourceKind = clip.source_kind || "";
        div.title = clip.title || "";
        div.innerHTML = `
            <span class="clip-drag-handle" title="Drag to reorder">&#9776;</span>
            <div class="clip-thumb">
                <img class="clip-thumb-image hidden" alt="Clip thumbnail">
                <span class="clip-thumb-empty">No frame</span>
            </div>
            <span class="clip-number">#${idx}</span>
            <input type="text" class="clip-title" value="${escapeHtml(clip.title || clip.source_kind || "")}"
                   placeholder="Clip title"
                   onchange="ReelMaker.updateClipMeta('${clip.id}', 'title', this.value)">
            <button class="clip-note-btn${clip.note ? " has-note" : ""}" type="button"
                    onclick="ReelMaker.editClipNote('${clip.id}')">${clip.note ? "Note*" : "Note"}</button>
            <input type="text" class="clip-start" value="${clip.start}" placeholder="0:00"
                   onchange="ReelMaker.updateClipTime('${clip.id}', 'start', this.value)">
            <button class="clip-time-btn" type="button" onclick="ReelMaker.capturePreviewTime('${clip.id}', 'start')">Set Start</button>
            <span class="clip-dash">&ndash;</span>
            <input type="text" class="clip-end" value="${clip.end}" placeholder="0:30"
                   onchange="ReelMaker.updateClipTime('${clip.id}', 'end', this.value)">
            <button class="clip-time-btn" type="button" onclick="ReelMaker.capturePreviewTime('${clip.id}', 'end')">Set End</button>
            <button class="clip-remove-btn" onclick="ReelMaker.removeClip('${clip.id}')" title="Remove clip">&times;</button>
        `;
        attachClipDragHandlers(div);
        container.appendChild(div);
        if (!activeClipId) {
            setActiveClip(clip.id);
        } else {
            syncActiveClipClasses();
            renderPreviewTimelines();
        }
        refreshClipThumbnail(clip.id);
        renderClipInspector();
        updateToolbarState();
    }

    async function updateClipTime(clipId, field, value) {
        if (!projectId) return;
        const row = getClipRow(clipId);
        const body = { project_id: projectId, clip_id: clipId };
        body[field] = value;
        const data = await api("/api/reel/update-clip", {
            method: "POST",
            body: JSON.stringify(body),
        });
        if (!data.error && row) {
            const start = parseTimestamp(row.querySelector(".clip-start")?.value || "0");
            const end = parseTimestamp(row.querySelector(".clip-end")?.value || "0");
            applyClipDraft(clipId, start, end);
        }
        renderPreviewTimelines();
        if (field === "start") {
            refreshClipThumbnail(clipId, parseTimestamp(value));
        }
        if (clipId === activeClipId) {
            renderClipInspector();
        }
        await loadRecentProjects();
        await loadProjectAssets();
    }

    async function capturePreviewTime(clipId, field) {
        const video = $("reel-preview-video");
        if (!video) return;
        const timestamp = formatTimestamp(video.currentTime || 0);
        const row = document.querySelector(`.clip-item[data-clip-id="${clipId}"]`);
        if (!row) return;
        const input = row.querySelector(field === "start" ? ".clip-start" : ".clip-end");
        if (!input) return;
        input.value = timestamp;
        await updateClipTime(clipId, field, timestamp);
        setActiveClip(clipId);
        renderPreviewTimelines();
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
        renumberClipItems();
        if (activeClipId === clipId) {
            const first = container.querySelector(".clip-item");
            setActiveClip(first?.dataset.clipId || "");
        } else {
            renderPreviewTimelines();
        }

        if (container.children.length === 0) {
            hide("reel-clip-actions");
        }
        renderClipInspector();
        await loadRecentProjects();
        await loadProjectAssets();
        updateToolbarState();
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
        resetExportState();

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

                        // Enable export step
                        enableStep(4);
                        $("reel-export-btn").disabled = false;

                        // Load preview
                        loadConcatPreview();
                        loadRecentProjects();
                        loadProjectAssets();
                        updateToolbarState();
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
        loadPreview(`/api/reel/serve-concat/${projectId}`, "sequence");
    }

    async function loadRemoteVodPreview(url) {
        const data = await api("/api/reel/preview-source", {
            method: "POST",
            body: JSON.stringify({ url }),
        });
        if (data.error) {
            showError("reel-step1-error", data.error);
            return;
        }
        remotePreviewUrl = data.stream_url || "";
        if (remotePreviewUrl) {
            hide("reel-local-file-restore");
            loadPreview(remotePreviewUrl, "source");
        }
    }

    function loadPreview(src, mode = "source") {
        const video = $("reel-preview-video");
        if (!video || !src) return;
        previewMode = mode;
        video.src = src;
        video.load();
        show("reel-video-sidebar");
        renderPreviewTimelines();
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
        markExportDirty();

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
                        loadRecentProjects();
                        loadProjectAssets();
                        updateToolbarState();
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
        if (!projectId || !concatReady) {
            showError("reel-step4-error", "Download clips first.");
            return;
        }

        const resolution = $("reel-setting-resolution").value;
        const btn = $("reel-export-btn");
        btn.disabled = true;
        show("reel-export-progress");
        hide("reel-export-download");

        try {
            const data = await api("/api/reel/export", {
                method: "POST",
                body: JSON.stringify({
                    project_id: projectId,
                    resolution,
                }),
            });

            if (data.error) {
                showError("reel-step4-error", data.error);
                btn.disabled = false;
                hide("reel-export-progress");
                return;
            }

            const jobId = data.job_id;
            const timer = setInterval(async () => {
                try {
                    const status = await api(`/api/status/${jobId}`);
                    $("reel-export-bar").style.width = `${status.progress || 0}%`;
                    $("reel-export-status").textContent = status.stage || "Processing...";

                    if (status.status === "complete") {
                        clearInterval(timer);
                        btn.disabled = false;
                        exportedReelReady = true;
                        $("reel-export-status").textContent = status.stage || "Short-form export complete!";
                        show("reel-export-download");
                        show("reel-export-download-btn");
                        loadRecentProjects();
                        loadProjectAssets();
                        updateToolbarState();
                    } else if (status.status === "error") {
                        clearInterval(timer);
                        btn.disabled = false;
                        showError("reel-step4-error", status.error || "Export failed");
                    }
                } catch (e) {
                    // Keep polling
                }
            }, 1000);
        } catch (e) {
            showError("reel-step4-error", e.message || "Export failed");
            btn.disabled = false;
            hide("reel-export-progress");
        }
    }

    function downloadReel() {
        if (!projectId || !exportedReelReady) return;
        window.location.href = `/api/reel/download/${projectId}`;
    }

    async function init() {
        $("reel-video-input")?.addEventListener("change", onLocalVideoSelect);
        $("reel-url-input")?.addEventListener("keydown", (event) => {
            if (event.key === "Enter") validateUrl();
        });
        $("reel-clip-title-input")?.addEventListener("input", (event) => {
            const row = getActiveClipRow();
            if (!row) return;
            const value = event.target.value;
            const rowTitle = row.querySelector(".clip-title");
            if (rowTitle) {
                rowTitle.value = value;
            }
            row.title = value;
            const heading = $("reel-inspector-heading");
            const clipNumber = row.querySelector(".clip-number")?.textContent || "Clip";
            if (heading) {
                heading.textContent = `${clipNumber} · ${value.trim() || clipNumber}`;
            }
            renderPreviewTimelines();
            scheduleInspectorMetaSave("title", value);
        });
        $("reel-clip-note-input")?.addEventListener("input", (event) => {
            const row = getActiveClipRow();
            if (!row) return;
            const value = event.target.value;
            row.dataset.note = value;
            const noteBtn = row.querySelector(".clip-note-btn");
            if (noteBtn) {
                noteBtn.classList.toggle("has-note", Boolean(value.trim()));
                noteBtn.textContent = value.trim() ? "Note*" : "Note";
            }
            scheduleInspectorMetaSave("note", value);
        });
        $("reel-inspector-jump-btn")?.addEventListener("click", jumpToActiveClip);
        $("reel-inspector-start-btn")?.addEventListener("click", async () => {
            if (activeClipId) await capturePreviewTime(activeClipId, "start");
        });
        $("reel-inspector-end-btn")?.addEventListener("click", async () => {
            if (activeClipId) await capturePreviewTime(activeClipId, "end");
        });
        $("reel-preview-video")?.addEventListener("loadedmetadata", renderTimeline);
        $("reel-preview-video")?.addEventListener("loadedmetadata", renderSequenceTimeline);
        $("reel-preview-video")?.addEventListener("timeupdate", renderPreviewTimelines);
        $("reel-preview-video")?.addEventListener("seeked", renderPreviewTimelines);
        $("reel-timeline-track")?.addEventListener("click", onTimelinePointer);
        $("reel-sequence-track")?.addEventListener("click", onSequencePointer);
        window.addEventListener("pointermove", onTimelineDragMove);
        window.addEventListener("pointerup", onTimelineDragEnd);
        window.addEventListener("pointercancel", onTimelineDragEnd);
        $("reel-timeline-zoom")?.addEventListener("input", (event) => {
            setTimelineZoom(event.target.value);
        });
        document.addEventListener("keydown", handlePreviewShortcut);
        setupFileDropZone();
        resetEditorState({ forgetProject: false });
        await loadRecentProjects();
        await restoreLastProject();
        updateToolbarState();
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
        importSourceMoments,
        updateClipMeta,
        editClipNote,
        updateClipTime,
        removeClip,
        downloadAllClips,
        previewSource,
        previewSequence,
        togglePlay,
        toggleMute,
        transcribe,
        exportReel,
        downloadReel,
        markExportDirty,
        capturePreviewTime,
        resumeProject,
        reloadRecentProjects: loadRecentProjects,
        refreshAssets: loadProjectAssets,
        startNewProject,
        openLocalVodPicker,
        getProjectId: () => projectId,
    };
})();
