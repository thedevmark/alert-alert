/**
 * Video Editor — frontend module for multi-clip video extraction and export.
 */
const ReelMaker = (() => {
    const LAST_PROJECT_STORAGE_KEY = "alertAlertReelLastProjectId";
    const FACECAM_LAYOUT_STORAGE_KEY = "alertAlertReelFacecamLayouts";
    const MIN_CLIP_DURATION = 0.1;
    const DEFAULT_EXPORT_FORMAT_PRESET = "shorts";
    const EXPORT_FORMAT_PRESETS = {
        shorts: {
            label: "Shorts / TikTok / Reels",
            summary: "1080x1920 MP4",
        },
        portrait_feed: {
            label: "4:5 Feed",
            summary: "1080x1350 MP4",
        },
        square: {
            label: "Square",
            summary: "1080x1080 MP4",
        },
        landscape: {
            label: "16:9 Landscape",
            summary: "1920x1080 MP4",
        },
    };
    const DEFAULT_SHORTFORM_PRESET = "gameplay_focus";
    const SHORTFORM_PRESETS = {
        gameplay_focus: {
            label: "Gameplay Focus",
            description: "Gameplay-first vertical short with clean pacing and readable captions.",
            layoutMode: "gameplay_focus",
            captionPreset: "pathos_clean",
        },
        facecam_top: {
            label: "Facecam Top",
            description: "Reserve more upper-frame space for streamer cam and reaction energy.",
            layoutMode: "facecam_top",
            captionPreset: "broadcast_bold",
        },
        baked_hype: {
            label: "Baked Text Punch",
            description: "Aggressive baked-in text treatment for hype clips and strong hooks.",
            layoutMode: "baked_hype",
            captionPreset: "broadcast_bold",
        },
    };
    const DEFAULT_FACECAM_LAYOUT = {
        enabled: false,
        x: 0.72,
        y: 0.04,
        width: 0.24,
        height: 0.24,
        anchor: "top_right",
    };
    const DEFAULT_WORKSPACE_ID = "session";
    const WORKSPACE_CONFIGS = {
        session: {
            label: "Session",
            focusPanel: "session",
            panels: ["project-browser", "project-files", "monitor", "session"],
        },
        inbox: {
            label: "Inbox",
            focusPanel: "inbox",
            panels: ["project-browser", "project-files", "monitor", "inbox", "inspector"],
        },
        inspector: {
            label: "Inspector",
            focusPanel: "inspector",
            panels: ["project-browser", "monitor", "inbox", "inspector"],
        },
        captions: {
            label: "Captions",
            focusPanel: "captions",
            panels: ["project-browser", "project-files", "monitor", "captions"],
        },
        output: {
            label: "Output",
            focusPanel: "output",
            panels: ["project-browser", "project-files", "monitor", "output"],
        },
    };
    const PANEL_WORKSPACE_MAP = {
        "project-browser": "session",
        "project-files": "session",
        monitor: "inbox",
        session: "session",
        inbox: "inbox",
        inspector: "inspector",
        captions: "captions",
        output: "output",
    };

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
    let connectedTwitchVideos = [];
    let connectedTwitchClips = [];
    let previewMode = "source";
    let timelineZoom = 1;
    let timelineDragState = null;
    let clipInspectorTimer = null;
    let exportFormatPreset = DEFAULT_EXPORT_FORMAT_PRESET;
    let shortformPreset = DEFAULT_SHORTFORM_PRESET;
    let facecamLayout = { ...DEFAULT_FACECAM_LAYOUT };
    let facecamGuideDrag = null;
    let projectRole = "shortform";
    let derivedFromProjectId = "";
    let pendingLongformProject = null;
    let dependencySnapshot = null;
    let openMenuName = "";
    let activeWorkspaceId = DEFAULT_WORKSPACE_ID;
    let activePanelId = WORKSPACE_CONFIGS[DEFAULT_WORKSPACE_ID].focusPanel;
    let workspacePanelOverrides = {};
    let activeTask = null;
    let lastOperationMessage = "";
    let lastOperationSource = "Workflow";
    let lastOperationTone = "info";
    let editorSummaryPublishTimer = null;
    let lastPublishedEditorSummaryKey = "";
    let importedEditorFeedIds = new Set();

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

    function getWorkspaceConfig(workspaceId = activeWorkspaceId) {
        return WORKSPACE_CONFIGS[workspaceId] || WORKSPACE_CONFIGS[DEFAULT_WORKSPACE_ID];
    }

    function getWorkspaceLabel(workspaceId = activeWorkspaceId) {
        return getWorkspaceConfig(workspaceId).label;
    }

    function getPanelElement(panelId) {
        return document.querySelector(`[data-reel-panel="${panelId}"]`);
    }

    function getWorkspaceOverrides(workspaceId = activeWorkspaceId) {
        if (!workspacePanelOverrides[workspaceId]) {
            workspacePanelOverrides[workspaceId] = {};
        }
        return workspacePanelOverrides[workspaceId];
    }

    function isPanelVisible(panelId, workspaceId = activeWorkspaceId) {
        const overrides = getWorkspaceOverrides(workspaceId);
        if (Object.prototype.hasOwnProperty.call(overrides, panelId)) {
            return Boolean(overrides[panelId]);
        }
        return getWorkspaceConfig(workspaceId).panels.includes(panelId);
    }

    function applyOperationRail(message, options = {}) {
        const { source = "Workflow", tone = "info" } = options;
        const rail = $("reel-operation-rail");
        const pill = $("reel-operation-pill");
        const text = $("reel-operation-text");
        if (!rail || !pill || !text) return;
        if (!message) {
            hide(rail);
            return;
        }

        rail.classList.remove("error", "success", "info");
        rail.classList.add(tone);
        pill.textContent = source;
        text.textContent = message;
        show(rail);
    }

    function renderWorkspaceControls() {
        const workflow = $("reel-workflow");
        if (workflow) {
            workflow.dataset.reelWorkspace = activeWorkspaceId;
        }
        document.querySelectorAll(".reel-workspace-btn").forEach((btn) => {
            const workspaceId = btn.dataset.reelWorkspace || "";
            const isActive = workspaceId === activeWorkspaceId;
            btn.classList.toggle("active", isActive);
            btn.setAttribute("aria-selected", isActive ? "true" : "false");
        });
        document.querySelectorAll("[data-reel-panel]").forEach((panel) => {
            const panelId = panel.dataset.reelPanel || "";
            const visible = isPanelVisible(panelId);
            panel.classList.toggle("reel-panel-collapsed", !visible);
            panel.classList.toggle("reel-panel-active", panelId === activePanelId);
        });
        document.querySelectorAll(".reel-panel-toggle[data-reel-panel-toggle]").forEach((btn) => {
            const panelId = btn.dataset.reelPanelToggle || "";
            const active = isPanelVisible(panelId);
            btn.classList.toggle("active", active);
            btn.setAttribute("aria-pressed", active ? "true" : "false");
        });
    }

    function renderActivityStatus() {
        const statusActivity = $("reel-status-activity");
        if (!statusActivity) return;

        const hasStoredMessage = Boolean(lastOperationMessage);
        const source = activeTask?.source || (hasStoredMessage ? lastOperationSource : "");
        const message = activeTask?.message || lastOperationMessage || "Ready";
        const tone = activeTask?.tone || lastOperationTone || "info";
        statusActivity.textContent = source ? `${source} · ${message}` : message;
        statusActivity.dataset.tone = tone;
    }

    function showError(stepId, msg) {
        const el = $(stepId);
        if (el) { el.textContent = msg; el.classList.remove("hidden"); }
        setOperationRail(msg, {
            source: mapStepIdToRailSource(stepId),
            tone: "error",
        });
    }
    function hideError(stepId) {
        const el = $(stepId);
        if (el) { el.textContent = ""; el.classList.add("hidden"); }
    }

    function mapStepIdToRailSource(stepId = "") {
        const normalized = String(stepId || "").trim();
        if (normalized === "reel-step1-error") return "Session";
        if (normalized === "reel-step2-error") return "Inbox";
        if (normalized === "reel-step3-error") return "Shorts";
        if (normalized === "reel-step4-error") return "Output";
        return "Workflow";
    }

    function setOperationRail(message, options = {}) {
        const { source = "Workflow", tone = "info" } = options;
        if (!message) {
            clearOperationRail();
            return;
        }
        lastOperationMessage = message;
        lastOperationSource = source;
        lastOperationTone = tone;
        applyOperationRail(message, { source, tone });
        renderActivityStatus();
    }

    function clearOperationRail() {
        lastOperationMessage = "";
        lastOperationSource = "Workflow";
        lastOperationTone = "info";
        if (activeTask?.message) {
            applyOperationRail(activeTask.message, {
                source: activeTask.source,
                tone: activeTask.tone,
            });
        } else {
            hide("reel-operation-rail");
        }
        renderActivityStatus();
    }

    function setActiveTask(message, options = {}) {
        if (!message) {
            clearActiveTask();
            return;
        }
        const { source = "Workflow", tone = "info" } = options;
        activeTask = { source, tone, message };
        applyOperationRail(message, { source, tone });
        renderActivityStatus();
    }

    function clearActiveTask(options = {}) {
        const { message = "", source = "Workflow", tone = "info" } = options;
        activeTask = null;
        if (message) {
            setOperationRail(message, { source, tone });
            return;
        }
        if (lastOperationMessage) {
            applyOperationRail(lastOperationMessage, {
                source: lastOperationSource,
                tone: lastOperationTone,
            });
        } else {
            hide("reel-operation-rail");
        }
        renderActivityStatus();
    }

    function getMenuTrigger(menuName) {
        return $(`reel-menu-${menuName}-btn`);
    }

    function getMenuPanel(menuName) {
        return $(`reel-menu-${menuName}-panel`);
    }

    function syncMenuState() {
        document.querySelectorAll(".reel-menu-trigger").forEach((trigger) => {
            const menuName = trigger.dataset.reelMenu || "";
            const isOpen = Boolean(openMenuName) && menuName === openMenuName;
            trigger.classList.toggle("active", isOpen);
            trigger.setAttribute("aria-expanded", isOpen ? "true" : "false");
        });
        document.querySelectorAll(".reel-menu-panel").forEach((panel) => {
            panel.classList.remove("open");
        });
        if (openMenuName) {
            getMenuPanel(openMenuName)?.classList.add("open");
        }
    }

    function closeMenus() {
        if (!openMenuName) return;
        openMenuName = "";
        syncMenuState();
    }

    function openMenu(menuName) {
        if (!menuName) return false;
        if ($("reel-workflow")?.classList.contains("hidden")) return false;
        openMenuName = menuName;
        syncMenuState();
        return true;
    }

    function toggleMenu(menuName) {
        if (!menuName) return false;
        if (openMenuName === menuName) {
            closeMenus();
            return false;
        }
        return openMenu(menuName);
    }

    function initChromeMenus() {
        document.querySelectorAll(".reel-menu-trigger").forEach((trigger) => {
            trigger.addEventListener("mouseenter", () => {
                const menuName = trigger.dataset.reelMenu || "";
                if (openMenuName && menuName && menuName !== openMenuName) {
                    openMenu(menuName);
                }
            });
        });
        document.querySelectorAll(".reel-menu-panel").forEach((panel) => {
            panel.addEventListener("click", (event) => {
                const actionBtn = event.target.closest(".reel-menu-action");
                if (actionBtn && !actionBtn.disabled) {
                    window.setTimeout(closeMenus, 0);
                }
            });
        });
        document.addEventListener("click", (event) => {
            if (!event.target.closest(".reel-menu-group")) {
                closeMenus();
            }
        });
        document.addEventListener("keydown", (event) => {
            if (event.key === "Escape" && openMenuName) {
                closeMenus();
            }
        });
        window.addEventListener("resize", closeMenus);
        syncMenuState();
    }

    function ensurePanelVisible(panelId, options = {}) {
        const { workspaceId = activeWorkspaceId } = options;
        const panel = getPanelElement(panelId);
        if (!panel) return null;
        if (!isPanelVisible(panelId, workspaceId)) {
            const overrides = getWorkspaceOverrides(workspaceId);
            overrides[panelId] = true;
        }
        renderWorkspaceControls();
        return panel;
    }

    function setWorkspace(workspaceId, options = {}) {
        if (!WORKSPACE_CONFIGS[workspaceId]) return false;
        const { scroll = true, focusPanelId = null } = options;
        activeWorkspaceId = workspaceId;
        activePanelId = focusPanelId || getWorkspaceConfig(workspaceId).focusPanel;
        ensurePanelVisible(activePanelId, { workspaceId });
        renderWorkspaceControls();
        updateWorkspaceChrome();
        if (scroll) {
            getPanelElement(activePanelId)?.scrollIntoView({ behavior: "smooth", block: "start" });
        }
        return true;
    }

    function focusPanel(panelId, options = {}) {
        const { scroll = true, workspaceId = PANEL_WORKSPACE_MAP[panelId] || activeWorkspaceId } = options;
        if (WORKSPACE_CONFIGS[workspaceId]) {
            activeWorkspaceId = workspaceId;
        }
        activePanelId = panelId;
        const panel = ensurePanelVisible(panelId);
        renderWorkspaceControls();
        updateWorkspaceChrome();
        if (scroll && panel) {
            panel.scrollIntoView({ behavior: "smooth", block: "start" });
        }
        return Boolean(panel);
    }

    function toggleWorkspacePanel(panelId) {
        const overrides = getWorkspaceOverrides();
        overrides[panelId] = !isPanelVisible(panelId);
        activePanelId = panelId;
        renderWorkspaceControls();
        updateWorkspaceChrome();
        return Boolean(overrides[panelId]);
    }

    function resetWorkspacePanels() {
        delete workspacePanelOverrides[activeWorkspaceId];
        activePanelId = getWorkspaceConfig(activeWorkspaceId).focusPanel;
        renderWorkspaceControls();
        updateWorkspaceChrome();
    }

    function setSourceMomentStatus(message) {
        const el = $("reel-source-moments-status");
        if (el) {
            el.textContent = message || "Source highlights and chapters will appear here when the platform exposes them.";
        }
    }

    function getTotalClipDuration() {
        return getClipRows().reduce((total, row) => {
            const start = parseTimestamp(row.querySelector(".clip-start")?.value || "0");
            const end = parseTimestamp(row.querySelector(".clip-end")?.value || "0");
            return end > start ? total + (end - start) : total;
        }, 0);
    }

    function hasCaptionData() {
        const editor = $("reel-caption-editor");
        return Boolean(editor && !editor.classList.contains("hidden"));
    }

    function renderTimeRuler(id, duration) {
        const ruler = $(id);
        if (!ruler) return;
        ruler.innerHTML = "";

        const total = Number(duration) || 0;
        if (total <= 0) return;

        let step = 5;
        if (total <= 15) step = 1;
        else if (total <= 45) step = 5;
        else if (total <= 120) step = 10;
        else if (total <= 300) step = 30;
        else step = 60;

        const ticks = [];
        for (let time = 0; time < total; time += step) {
            ticks.push(Number(time.toFixed(3)));
        }
        ticks.push(Number(total.toFixed(3)));

        Array.from(new Set(ticks)).forEach((time) => {
            const tick = document.createElement("div");
            tick.className = "reel-time-tick";
            tick.style.left = `${(time / total) * 100}%`;

            const label = document.createElement("span");
            label.className = "reel-time-label";
            label.textContent = formatTime(time);

            tick.appendChild(label);
            ruler.appendChild(tick);
        });
    }

    function renderGuideLane(id, segments, duration, options = {}) {
        const lane = $(id);
        if (!lane) return;
        lane.innerHTML = "";

        const total = Number(duration) || 0;
        if (total <= 0) return;

        const { kind = "audio", sequence = false } = options;
        const captionReady = hasCaptionData();
        let items = [];

        if (kind === "audio") {
            items = (segments || []).map((segment, index) => ({
                start: sequence ? segment.sequenceStart : segment.start,
                end: sequence ? segment.sequenceEnd : segment.end,
                title: segment.title || `Clip ${index + 1}`,
            }));
        } else if (kind === "caption" && captionReady) {
            if (sequence) {
                items = [{ start: 0, end: total, title: "Caption coverage" }];
            } else {
                items = (segments || []).map((segment, index) => ({
                    start: segment.start,
                    end: segment.end,
                    title: segment.title || `Clip ${index + 1}`,
                }));
            }
        }

        if (!items.length) {
            const empty = document.createElement("span");
            empty.className = "reel-guide-empty";
            empty.textContent = kind === "caption"
                ? (sequence ? "Transcribe to create a caption track" : "Caption markers appear after transcription")
                : "Audio follows the current edit";
            lane.appendChild(empty);
            return;
        }

        items.forEach((item, index) => {
            const chip = document.createElement("div");
            chip.className = `reel-guide-chip reel-guide-chip-${kind}`;
            chip.style.left = `${(item.start / total) * 100}%`;
            chip.style.width = `${Math.max(1, ((item.end - item.start) / total) * 100)}%`;
            chip.style.opacity = kind === "audio" ? String(0.45 + ((index % 4) * 0.12)) : "0.92";
            chip.title = item.title || `${kind} lane`;
            lane.appendChild(chip);
        });
    }

    function applyTimelineZoom() {
        const widthPct = Math.max(100, Math.round(timelineZoom * 100));
        ["reel-source-track-stack", "reel-sequence-track-stack"].forEach((id) => {
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

    async function persistExportFormatPreset() {
        if (!projectId) return;
        try {
            await api("/api/reel/set-export-format", {
                method: "POST",
                body: JSON.stringify({
                    project_id: projectId,
                    format_preset: exportFormatPreset,
                }),
            });
        } catch (e) {
            // Ignore persistence failures and keep the local selection active.
        }
    }

    function getExportFormatConfig() {
        return EXPORT_FORMAT_PRESETS[exportFormatPreset] || EXPORT_FORMAT_PRESETS[DEFAULT_EXPORT_FORMAT_PRESET];
    }

    function updateExportFormatUI() {
        const activePreset = getExportFormatConfig();
        document.querySelectorAll("[data-format-preset]").forEach((button) => {
            button.classList.toggle("active", button.dataset.formatPreset === exportFormatPreset);
        });
        ["reel-export-format-summary", "reel-delivery-format-summary"].forEach((id) => {
            const summary = $(id);
            if (summary) {
                summary.textContent = activePreset.summary;
                summary.title = activePreset.label;
            }
        });
        syncBurnCaptionsSummary();
        updateExportCompositionUI();
    }

    function setExportFormatPreset(preset, options = {}) {
        const { persist = true, markDirty = true } = options;
        const nextPreset = EXPORT_FORMAT_PRESETS[preset] ? preset : DEFAULT_EXPORT_FORMAT_PRESET;
        const changed = exportFormatPreset !== nextPreset;
        exportFormatPreset = nextPreset;
        updateExportFormatUI();
        if (changed && markDirty && concatReady) {
            markExportDirty();
        }
        updateToolbarState();
        if (persist && projectId) {
            persistExportFormatPreset();
        }
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
        hide("reel-preview-composition-badge");
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
        if (!project) return "Video projects autosave locally and can be resumed later.";
        const flags = [];
        const clipCount = Number(project.clip_count ?? project.clips?.length ?? 0);
        const session = project.stream_session || {};
        flags.push(clipCount === 1 ? "1 clip" : `${clipCount} clips`);
        flags.push(project.project_role === "longform" ? "longform" : "shortform");
        const shortRecipe = project.shortform_recipe || {};
        if (shortRecipe.label && project.project_role !== "longform") flags.push(shortRecipe.label);
        const shortReadyCount = Number(project.short_ready_count ?? project.clips?.filter?.((clip) => clip.short_ready)?.length ?? 0);
        const queuedLongformCount = Number(project.queued_longform_count ?? project.clips?.filter?.((clip) => clip.short_ready && clip.include_in_longform !== false)?.length ?? 0);
        if (shortReadyCount > 0 && project.project_role !== "longform") {
            flags.push(shortReadyCount === 1 ? "1 short ready" : `${shortReadyCount} shorts ready`);
        }
        if (queuedLongformCount > 0 && project.project_role !== "longform") {
            flags.push(queuedLongformCount === 1 ? "1 queued longform" : `${queuedLongformCount} queued longform`);
        }
        if (session.game_title) flags.push(session.game_title);
        if (project.has_captions || project.captions?.words?.length) flags.push("captions");
        if (project.has_export || project.export_file) flags.push("export ready");
        else if (project.has_concat || project.concat_file) flags.push("stitched");
        return `${flags.join(" · ")} · ${formatRelativeDate(project.updated_at)}`;
    }

    function getActiveProjectMeta() {
        return recentProjects.find((project) => project.project_id === projectId) || null;
    }

    function parseLaunchNumber(value) {
        const parsed = Number.parseFloat(String(value ?? "").trim());
        return Number.isFinite(parsed) ? parsed : null;
    }

    function buildLaunchVodUrl(videoId, explicitVodUrl = "") {
        const directUrl = String(explicitVodUrl || "").trim();
        if (directUrl) return directUrl;
        const cleanVideoId = String(videoId || "").trim();
        return cleanVideoId ? `https://www.twitch.tv/videos/${cleanVideoId}` : "";
    }

    function readLaunchParams() {
        const url = new URL(window.location.href);
        const params = url.searchParams;
        const videoId = String(params.get("video_id") || "").trim();
        return {
            candidateKind: String(params.get("candidate_kind") || "").trim().toLowerCase(),
            candidateTitle: String(params.get("candidate_title") || "").trim(),
            channelName: String(params.get("channel_name") || "").trim(),
            clipId: String(params.get("clip_id") || "").trim(),
            clipUrl: String(params.get("clip_url") || "").trim(),
            createdAt: String(params.get("created_at") || "").trim(),
            endSec: parseLaunchNumber(params.get("end_sec")),
            openNow: params.get("open_now") === "1",
            projectId: String(params.get("project_id") || "").trim(),
            sessionLabel: String(params.get("session_label") || "").trim(),
            startSec: parseLaunchNumber(params.get("start_sec")),
            videoId,
            viewCount: parseLaunchNumber(params.get("view_count")),
            vodOffset: parseLaunchNumber(params.get("vod_offset")),
            vodTitle: String(params.get("vod_title") || "").trim(),
            vodUrl: buildLaunchVodUrl(videoId, params.get("vod_url")),
        };
    }

    function consumeLaunchParams() {
        const url = new URL(window.location.href);
        [
            "mode",
            "open_now",
            "project_id",
            "channel_name",
            "session_label",
            "vod_title",
            "vod_url",
            "video_id",
            "clip_id",
            "clip_url",
            "created_at",
            "view_count",
            "vod_offset",
            "candidate_kind",
            "candidate_title",
            "start_sec",
            "end_sec",
        ].forEach((key) => url.searchParams.delete(key));
        window.history.replaceState({}, "", url.toString());
    }

    function pickLatestSourceMoment(moments = []) {
        return [...(Array.isArray(moments) ? moments : [])]
            .filter(Boolean)
            .sort((left, right) => {
                const leftCreated = Date.parse(left?.created_at || "");
                const rightCreated = Date.parse(right?.created_at || "");
                const leftHasCreated = Number.isFinite(leftCreated);
                const rightHasCreated = Number.isFinite(rightCreated);
                if (leftHasCreated && rightHasCreated && leftCreated !== rightCreated) {
                    return rightCreated - leftCreated;
                }
                if (rightHasCreated && !leftHasCreated) return 1;
                if (leftHasCreated && !rightHasCreated) return -1;

                const rightEnd = Number(right?.end_sec || 0);
                const leftEnd = Number(left?.end_sec || 0);
                if (rightEnd !== leftEnd) return rightEnd - leftEnd;

                return Number(right?.score || 0) - Number(left?.score || 0);
            })[0] || null;
    }

    function buildEditorSummaryCandidate(moment) {
        if (!moment) return null;
        const rawKind = String(moment.kind || moment.source_kind || "").trim().toLowerCase();
        const kind = ["twitch_clip", "stream_marker", "chat_bookmark", "music_event", "source_moment"].includes(rawKind)
            ? rawKind
            : "source_moment";
        return {
            clipId: String(moment.clip_id || "").trim() || null,
            clipUrl: String(moment.clip_url || "").trim() || null,
            createdAt: String(moment.created_at || "").trim() || null,
            endSec: Number(moment.end_sec || 0),
            kind,
            startSec: Number(moment.start_sec || 0),
            title: String(moment.title || "Untitled moment").trim() || "Untitled moment",
            videoId: String(moment.video_id || "").trim() || null,
            viewCount: Number.isFinite(Number(moment.view_count)) ? Number(moment.view_count) : null,
            vodOffset: Number.isFinite(Number(moment.vod_offset)) ? Number(moment.vod_offset) : null,
        };
    }

    function buildEmptyEditorSummary() {
        return {
            channelName: null,
            clipCount: 0,
            latestCandidate: null,
            localAppUrl: window.location.origin,
            projectId: null,
            sessionLabel: null,
            shortReadyCount: 0,
            sourceMomentCount: 0,
            updatedAt: new Date().toISOString(),
            vodTitle: null,
            vodUrl: null,
        };
    }

    function buildCurrentEditorSummary(options = {}) {
        if (options.clear) {
            return buildEmptyEditorSummary();
        }

        const projectMeta = options.projectMeta || getActiveProjectMeta() || {};
        const session = options.session || collectSessionDetails();
        const moments = Array.isArray(options.sourceMoments) ? options.sourceMoments : sourceMoments;
        const latestCandidate = buildEditorSummaryCandidate(
            options.latestCandidate || pickLatestSourceMoment(moments),
        );
        const vodTitleLabel = String($("reel-vod-title")?.textContent || "").trim();

        return {
            channelName: options.channelName ?? session.channel_name ?? projectMeta.stream_session?.channel_name ?? null,
            clipCount: Number(options.clipCount ?? projectMeta.clip_count ?? getClipRows().length ?? 0),
            latestCandidate,
            localAppUrl: window.location.origin,
            projectId: options.projectId ?? projectId ?? null,
            sessionLabel: options.sessionLabel ?? session.session_label ?? projectMeta.stream_session?.session_label ?? null,
            shortReadyCount: Number(options.shortReadyCount ?? projectMeta.short_ready_count ?? 0),
            sourceMomentCount: Number(options.sourceMomentCount ?? moments.length ?? 0),
            updatedAt: new Date().toISOString(),
            vodTitle: options.vodTitle ?? vodTitleLabel ?? projectMeta.title ?? null,
            vodUrl: options.vodUrl ?? vodUrl ?? null,
        };
    }

    async function publishEditorSummary(options = {}) {
        if (typeof DmAuth === "undefined" || typeof DmAuth.updateEditorSummary !== "function") {
            return null;
        }

        const authState = typeof DmAuth.getState === "function" ? DmAuth.getState() : null;
        if (!authState?.user) {
            return null;
        }

        const summary = buildCurrentEditorSummary(options);
        const payloadKey = JSON.stringify(summary);
        if (!options.force && payloadKey === lastPublishedEditorSummaryKey) {
            return summary;
        }

        try {
            await DmAuth.updateEditorSummary(summary);
            lastPublishedEditorSummaryKey = payloadKey;
            return summary;
        } catch (error) {
            return null;
        }
    }

    function queueEditorSummaryPublish(options = {}) {
        if (editorSummaryPublishTimer) {
            window.clearTimeout(editorSummaryPublishTimer);
        }

        editorSummaryPublishTimer = window.setTimeout(() => {
            editorSummaryPublishTimer = null;
            void publishEditorSummary(options);
        }, Number(options.delayMs || 180));
    }

    function extractVideoIdFromVodUrl(value) {
        const raw = String(value || "").trim();
        if (!raw) return "";
        const match = raw.match(/twitch\.tv\/videos\/(\d+)/i);
        return match ? String(match[1] || "").trim() : "";
    }

    function buildEditorFeedProjectContext() {
        const projectMeta = getActiveProjectMeta() || {};
        const session = collectSessionDetails();
        const activeVodUrl = String(vodUrl || projectMeta.vod_url || "").trim();
        return {
            channelName: String(session.channel_name || projectMeta.stream_session?.channel_name || "").trim(),
            projectId: String(projectId || "").trim(),
            sessionLabel: String(session.session_label || projectMeta.stream_session?.session_label || "").trim(),
            videoId: extractVideoIdFromVodUrl(activeVodUrl),
            vodUrl: activeVodUrl,
        };
    }

    function matchesEditorFeedItemToProject(item, context) {
        if (!item || typeof item !== "object") {
            return false;
        }

        const itemId = String(item.id || "").trim();
        if (itemId && importedEditorFeedIds.has(itemId)) {
            return false;
        }

        const itemProjectId = String(item.projectId || item.project_id || "").trim();
        if (itemProjectId && context.projectId) {
            return itemProjectId === context.projectId;
        }

        const itemVideoId = String(item.videoId || item.video_id || "").trim();
        if (itemVideoId && context.videoId) {
            return itemVideoId === context.videoId;
        }

        const itemVodUrl = String(item.vodUrl || item.vod_url || "").trim();
        if (itemVodUrl && context.vodUrl) {
            return itemVodUrl === context.vodUrl;
        }

        const itemSessionLabel = String(item.sessionLabel || item.session_label || "").trim().toLowerCase();
        const contextSessionLabel = String(context.sessionLabel || "").trim().toLowerCase();
        if (itemSessionLabel && contextSessionLabel && itemSessionLabel === contextSessionLabel) {
            const itemChannelName = String(item.channelName || item.channel_name || "").trim().toLowerCase();
            const contextChannelName = String(context.channelName || "").trim().toLowerCase();
            return !itemChannelName || !contextChannelName || itemChannelName === contextChannelName;
        }

        return false;
    }

    async function syncEditorFeedIntoProject(options = {}) {
        const { quiet = false } = options;
        if (!projectId || typeof DmAuth === "undefined" || typeof DmAuth.fetchEditorFeed !== "function") {
            return null;
        }

        const authState = typeof DmAuth.getState === "function" ? DmAuth.getState() : null;
        if (!authState?.user) {
            return null;
        }

        const context = buildEditorFeedProjectContext();
        if (!context.projectId && !context.videoId && !context.vodUrl && !context.sessionLabel) {
            return null;
        }

        try {
            const data = await DmAuth.fetchEditorFeed();
            const feedItems = Array.isArray(data?.items) ? data.items : [];
            const matchingItems = feedItems.filter((item) => matchesEditorFeedItemToProject(item, context));
            if (!matchingItems.length) {
                return null;
            }

            const importData = await api("/api/reel/import-editor-feed", {
                method: "POST",
                body: JSON.stringify({
                    items: matchingItems,
                    project_id: projectId,
                }),
            });

            if (importData.error) {
                if (!quiet) {
                    setOperationRail(importData.error, { source: "Toolkit", tone: "error" });
                }
                return null;
            }

            if (Array.isArray(importData.moments)) {
                sourceMoments = importData.moments;
            }

            connectedTwitchClips = sourceMoments.filter((moment) => String(moment?.kind || "").toLowerCase() === "twitch_clip");
            renderConnectedTwitchClips(connectedTwitchClips);

            const currentIds = new Set(getClipRows().map((row) => row.dataset.clipId));
            (importData.imported_clips || []).forEach((clip) => {
                if (!currentIds.has(clip.id)) {
                    renderClipItem(clip);
                    currentIds.add(clip.id);
                }
            });

            if ((importData.imported_count || 0) > 0) {
                const importedIds = Array.isArray(importData.imported_feed_ids) ? importData.imported_feed_ids : [];
                importedIds.forEach((id) => {
                    if (id) importedEditorFeedIds.add(String(id));
                });

                if (getClipRows().length > 0) {
                    enableStep(2);
                    show("reel-clip-actions");
                }

                const summary = summarizeSourceMoments(sourceMoments).slice(0, 3).join(" · ");
                setSessionInboxStatus(summary ? `${sourceMoments.length} session moments ready. ${summary}.` : `${sourceMoments.length} session moments ready.`);
                await Promise.all(importedIds.map(async (id) => {
                    if (!id || typeof DmAuth.deleteEditorFeed !== "function") return;
                    try {
                        await DmAuth.deleteEditorFeed(id);
                    } catch (error) {
                        // Keep imported ids marked locally even if remote queue cleanup fails.
                    }
                }));
                await loadRecentProjects();
                await loadProjectAssets();
                updateToolbarState();
                if (!quiet) {
                    setOperationRail(importData.message || "Imported mirrored editor feed items into this project.", {
                        source: "Toolkit",
                        tone: "success",
                    });
                }
            }

            return importData;
        } catch (error) {
            if (!quiet) {
                setOperationRail(error?.message || "Failed to sync editor feed.", {
                    source: "Toolkit",
                    tone: "error",
                });
            }
            return null;
        }
    }

    function applyLaunchSessionDetails(params) {
        if ($("reel-session-platform")) $("reel-session-platform").value = "twitch";
        if ($("reel-session-channel") && params.channelName) $("reel-session-channel").value = params.channelName;
        if ($("reel-session-label") && params.sessionLabel) $("reel-session-label").value = params.sessionLabel;
    }

    function selectClipForMoment(moment) {
        const targetRow = getClipRowForMoment({
            end_sec: moment?.endSec ?? moment?.end_sec ?? 0,
            source_kind: moment?.kind || moment?.source_kind || "",
            start_sec: moment?.startSec ?? moment?.start_sec ?? 0,
        });
        if (!targetRow) return false;

        setActiveClip(targetRow.dataset.clipId || "");
        setWorkspace("inbox", { scroll: false, focusPanelId: "inspector" });
        targetRow.scrollIntoView({ behavior: "smooth", block: "center" });
        return true;
    }

    function focusStep(stepNumber) {
        const workspaceByStep = {
            1: "session",
            2: "inbox",
            3: "captions",
            4: "output",
        };
        const panelByStep = {
            1: "session",
            2: "inbox",
            3: "captions",
            4: "output",
        };
        const step = $(`reel-step-${stepNumber}`);
        if (!step) return false;
        const workspaceId = workspaceByStep[stepNumber];
        const panelId = panelByStep[stepNumber];
        if (workspaceId) {
            setWorkspace(workspaceId, { scroll: false, focusPanelId: panelId });
        }
        step.scrollIntoView({ behavior: "smooth", block: "start" });
        return true;
    }

    function setStreamerStatus(message, options = {}) {
        const { error = false } = options;
        const el = $("reel-streamer-status");
        if (!el) return;
        const fallback = "Paste hotkey/marker timestamps after loading the stream VOD to turn them into starter clips.";
        el.textContent = message || fallback;
        el.classList.toggle("error-msg", Boolean(error));
        el.classList.toggle("settings-hint", !error);
        if (message && message !== fallback) {
            setOperationRail(message, {
                source: "Session",
                tone: error ? "error" : "info",
            });
        }
    }

    function setSessionInboxStatus(message, options = {}) {
        const { error = false } = options;
        const el = $("reel-session-inbox-status");
        if (!el) return;
        const fallback = "Load a VOD and import Twitch clips, markers, or chapters to build this session inbox.";
        el.textContent = message || fallback;
        el.classList.toggle("error-msg", Boolean(error));
        el.classList.toggle("settings-hint", !error);
        if (message && message !== fallback) {
            setOperationRail(message, {
                source: "Inbox",
                tone: error ? "error" : "info",
            });
        }
    }

    function renderCaptionRuntimeCard(deps = dependencySnapshot) {
        const heading = $("reel-caption-runtime-heading");
        const summary = $("reel-caption-runtime-summary");
        const installBtn = $("reel-caption-runtime-install-btn");
        const speakerBtn = $("reel-caption-runtime-speaker-btn");
        if (!heading || !summary || !installBtn || !speakerBtn) return;

        dependencySnapshot = deps || dependencySnapshot;
        const captioning = dependencySnapshot?.captioning || {};
        const installState = dependencySnapshot?.captioning_install || {};
        const requiredMissing = Array.isArray(captioning.required_missing) ? captioning.required_missing : [];
        const optionalMissing = Array.isArray(captioning.optional_missing) ? captioning.optional_missing : [];
        const requiredReady = Boolean(captioning.faster_whisper?.installed) && Boolean(captioning.torch?.installed);
        const pyannoteMissing = optionalMissing.includes("pyannote_audio");
        const busy = installState.status === "installing";
        const installMessage = String(installState.message || "");
        const installingPyannote = busy && /pyannote\.audio/i.test(installMessage);

        if (!dependencySnapshot) {
            heading.textContent = "Checking caption runtime...";
            summary.textContent = "The workflow is checking whether the captioning packages are ready.";
            installBtn.disabled = true;
            speakerBtn.disabled = true;
            hide(speakerBtn);
            return;
        }

        if (busy) {
            if (installingPyannote) {
                heading.textContent = "Installing speaker labeling...";
                summary.textContent = installMessage || "Installing pyannote.audio for optional speaker labels. Keep the app open.";
            } else {
                heading.textContent = "Installing caption runtime...";
                summary.textContent = installMessage || "Installing faster-whisper and torch. Keep the app open.";
            }
        } else if (requiredReady) {
            heading.textContent = "Caption runtime is ready";
            summary.textContent = pyannoteMissing
                ? "Transcription is ready. Install pyannote.audio if you want optional speaker labels."
                : "Transcription is ready for this project.";
        } else {
            const missingLabel = requiredMissing.length > 0 ? requiredMissing.join(" + ") : "faster-whisper + torch";
            heading.textContent = "Caption runtime needs setup";
            summary.textContent = `Install ${missingLabel} here before you run the caption pass.`;
        }

        installBtn.disabled = busy;
        installBtn.classList.toggle("hidden", requiredReady);
        installBtn.textContent = busy && !installingPyannote
            ? "Installing caption runtime..."
            : "Install faster-whisper + torch (1-click)";

        speakerBtn.disabled = busy;
        speakerBtn.classList.toggle("hidden", !requiredReady || !pyannoteMissing);
        if (busy && installingPyannote) {
            speakerBtn.textContent = "Installing pyannote.audio...";
        } else if (!busy) {
            speakerBtn.textContent = "Install pyannote.audio (optional)";
        }
    }

    function renderLongformBuildUI() {
        const summary = $("reel-longform-build-summary");
        const handoff = $("reel-longform-handoff");
        const handoffHeading = $("reel-longform-handoff-heading");
        const handoffSummary = $("reel-longform-handoff-summary");
        const openBtn = $("reel-open-longform-btn");
        const buildBtn = $("reel-create-longform-btn");
        const preparedShortCount = getPreparedClipRows().length;
        const queuedPreparedShortCount = getQueuedPreparedClipRows().length;

        if (summary) {
            if (projectRole === "longform") {
                const sourceProject = getSourceProjectSummary();
                summary.textContent = sourceProject
                    ? `This is the horizontal derivative for ${sourceProject.title || `project ${sourceProject.project_id}`}. Return to the shortform source project to rebuild the longform cut.`
                    : "This project is already the horizontal derivative. Return to the shortform source project to rebuild the longform cut.";
            } else if (queuedPreparedShortCount > 0) {
                summary.textContent = `Build a separate horizontal project from ${queuedPreparedShortCount} queued prepared short${queuedPreparedShortCount === 1 ? "" : "s"}. This creates another project; it does not export a file yet.`;
            } else if (preparedShortCount > 0) {
                summary.textContent = "Queue at least one prepared short for longform, then build a separate horizontal project from that queue.";
            } else {
                summary.textContent = "Prep shorts in the Session Inbox first, then queue the best ones for a separate longform project.";
            }
        }

        if (buildBtn) {
            buildBtn.disabled = projectRole === "longform" || !projectId || queuedPreparedShortCount === 0;
            buildBtn.title = projectRole === "longform"
                ? "This project is already a longform derivative."
                : (queuedPreparedShortCount > 0
                    ? `Build a horizontal project from ${queuedPreparedShortCount} queued prepared short${queuedPreparedShortCount === 1 ? "" : "s"}.`
                    : (preparedShortCount > 0
                        ? "Queue at least one prepared short for longform first."
                        : "Prepare at least one clip as a short first."));
        }

        if (!handoff || !handoffHeading || !handoffSummary || !openBtn) return;
        if (pendingLongformProject && pendingLongformProject.sourceProjectId === projectId) {
            handoffHeading.textContent = `Longform project ${pendingLongformProject.projectId} is ready`;
            handoffSummary.textContent = pendingLongformProject.message
                || "Open the derived project when you are ready to edit the horizontal cut.";
            openBtn.disabled = false;
            show(handoff);
            return;
        }

        hide(handoff);
    }

    function dismissLongformHandoff() {
        pendingLongformProject = null;
        renderLongformBuildUI();
    }

    async function openPendingLongformProject() {
        if (!pendingLongformProject?.projectId) return;
        const targetProjectId = pendingLongformProject.projectId;
        pendingLongformProject = null;
        renderLongformBuildUI();
        await resumeProject(targetProjectId);
    }

    function getShortPresetConfig(preset = shortformPreset) {
        return SHORTFORM_PRESETS[preset] || SHORTFORM_PRESETS[DEFAULT_SHORTFORM_PRESET];
    }

    function toTitleCase(value) {
        return String(value || "")
            .replace(/[_-]+/g, " ")
            .replace(/\b\w/g, (char) => char.toUpperCase());
    }

    function getCompositionProfileConfig(profileKey) {
        const matchedPreset = Object.values(SHORTFORM_PRESETS).find((preset) => preset.layoutMode === profileKey);
        if (matchedPreset) {
            return {
                key: profileKey,
                label: matchedPreset.label,
                description: matchedPreset.description,
            };
        }

        const fallbackKey = profileKey || getShortPresetConfig().layoutMode || DEFAULT_SHORTFORM_PRESET;
        return {
            key: fallbackKey,
            label: toTitleCase(fallbackKey || "gameplay_focus"),
            description: "",
        };
    }

    function getEffectiveCompositionState() {
        const defaultProfile = getShortPresetConfig().layoutMode || DEFAULT_SHORTFORM_PRESET;
        const buckets = new Map();

        getClipRows()
            .filter((row) => Boolean((row.dataset.shortPreset || row.dataset.compositionProfile || "").trim()))
            .forEach((row) => {
                const profileKey = row.dataset.compositionProfile
                    || SHORTFORM_PRESETS[row.dataset.shortPreset]?.layoutMode
                    || defaultProfile;
                if (!profileKey) return;

                const start = parseTimestamp(row.querySelector(".clip-start")?.value || "0");
                const end = parseTimestamp(row.querySelector(".clip-end")?.value || "0");
                const duration = Math.max(0.25, end - start);
                const current = buckets.get(profileKey) || { count: 0, duration: 0 };
                current.count += 1;
                current.duration += duration;
                buckets.set(profileKey, current);
            });

        let selectedProfile = defaultProfile;
        if (buckets.size) {
            selectedProfile = [...buckets.entries()].sort((left, right) => {
                const countDiff = right[1].count - left[1].count;
                if (countDiff !== 0) return countDiff;
                const durationDiff = right[1].duration - left[1].duration;
                if (Math.abs(durationDiff) > 0.001) return durationDiff;
                if (left[0] === defaultProfile) return -1;
                if (right[0] === defaultProfile) return 1;
                return left[0].localeCompare(right[0]);
            })[0][0];
        }

        const profile = getCompositionProfileConfig(selectedProfile);
        const layoutMode = exportFormatPreset === "landscape"
            ? "landscape recut"
            : (exportFormatPreset === "square" ? "square framing" : "vertical framing");
        const usingFacecamGuide = profile.key === "facecam_top" && facecamLayout.enabled;

        return {
            key: profile.key,
            label: profile.label,
            summary: `${profile.label} ${layoutMode}${usingFacecamGuide ? " · saved facecam guide" : ""}`,
            detail: exportFormatPreset === "landscape"
                ? `${profile.label} drives the horizontal recut, carrying over shortform pacing while dropping vertical-only framing.`
                : usingFacecamGuide
                    ? `${profile.label} drives the export framing with your saved facecam guide and updated caption safe area.`
                    : `${profile.label} drives the export framing and burned caption placement for this render.`,
        };
    }

    function updateExportCompositionUI() {
        const composition = getEffectiveCompositionState();
        ["reel-export-composition-summary", "reel-delivery-composition-summary"].forEach((id) => {
            const summaryEl = $(id);
            if (summaryEl) {
                summaryEl.textContent = composition.summary;
                summaryEl.title = composition.detail;
            }
        });
        const hintEl = $("reel-export-composition-hint");
        if (hintEl) {
            hintEl.textContent = composition.detail;
        }
        renderPreviewCompositionBadge();
    }

    function syncBurnCaptionsSummary() {
        const burnCaptions = $("reel-burn-captions")?.checked !== false;
        const label = burnCaptions ? "Captions will burn in" : "Clean video + subtitle files";
        ["reel-export-burn-summary", "reel-delivery-burn-summary"].forEach((id) => {
            const el = $(id);
            if (el) {
                el.textContent = label;
            }
        });
    }

    function getPreparedClipRows() {
        return getClipRows().filter((row) => Boolean((row.dataset.shortPreset || "").trim()));
    }

    function getQueuedPreparedClipRows() {
        return getPreparedClipRows().filter((row) => row.dataset.includeInLongform !== "false");
    }

    function getCurrentProjectSummary() {
        return recentProjects.find((item) => item.project_id === projectId) || null;
    }

    function getLinkedLongformSummary() {
        if (projectRole === "longform") {
            return getCurrentProjectSummary();
        }
        return recentProjects.find((item) => item.project_role === "longform" && item.derived_from_project_id === projectId) || null;
    }

    function getSourceProjectSummary() {
        if (projectRole === "longform" && derivedFromProjectId) {
            return recentProjects.find((item) => item.project_id === derivedFromProjectId) || null;
        }
        return getCurrentProjectSummary();
    }

    function buildBetaWorkflowItems() {
        const authState = typeof DmAuth !== "undefined" && typeof DmAuth.getState === "function"
            ? (DmAuth.getState() || {})
            : {};
        const authUser = authState?.user || null;
        const session = collectSessionDetails();
        const currentSummary = getCurrentProjectSummary();
        const sourceSummary = getSourceProjectSummary();
        const linkedLongform = getLinkedLongformSummary();
        const preparedCount = getPreparedClipRows().length;
        const queuedPreparedCount = getQueuedPreparedClipRows().length;
        const momentCounts = sourceMoments.reduce((acc, moment) => {
            const kind = String(moment?.kind || "").trim().toLowerCase() || "source_moment";
            acc[kind] = (acc[kind] || 0) + 1;
            return acc;
        }, {});
        const markerCount = Number(momentCounts.stream_marker || 0);
        const twitchClipCount = Number(momentCounts.twitch_clip || 0);
        const captionsReady = hasCaptionData();
        const sourceExportDone = projectRole === "longform"
            ? Boolean(sourceSummary?.has_export)
            : exportedReelReady;
        const longformBuilt = projectRole === "longform" || Boolean(linkedLongform);
        const longformExportDone = projectRole === "longform"
            ? exportedReelReady
            : Boolean(linkedLongform?.has_export);
        const currentFormat = getExportFormatConfig().label;

        return [
            {
                key: "auth",
                title: "Connect Twitch",
                state: authUser?.login ? "done" : "ready",
                summary: authUser?.login
                    ? `Connected as @${authUser.login}.`
                    : "Connect Twitch to pull VODs, markers, and clips automatically.",
            },
            {
                key: "source",
                title: "Load source VOD",
                state: projectId && vodDuration > 0 && (vodUrl || localVodUploaded) ? "done" : "blocked",
                summary: projectId && vodDuration > 0 && (vodUrl || localVodUploaded)
                    ? `${sourceType === "file" ? "Local source" : "Remote VOD"} loaded at ${formatTime(vodDuration)}.`
                    : "Load a Twitch VOD or local recording for this project.",
            },
            {
                key: "session",
                title: "Save session details",
                state: session.channel_name || session.game_title || session.session_label ? "done" : "ready",
                summary: session.channel_name || session.game_title || session.session_label
                    ? [session.channel_name && `@${session.channel_name}`, session.game_title, session.session_label].filter(Boolean).join(" · ")
                    : "Save the channel, game, and session details for this stream.",
            },
            {
                key: "inbox",
                title: "Fill session inbox",
                state: sourceMoments.length > 0 ? "done" : "blocked",
                summary: sourceMoments.length > 0
                    ? `${sourceMoments.length} surfaced moments. ${markerCount} markers, ${twitchClipCount} Twitch clips.`
                    : "Import Twitch markers, Twitch clips, or manual timestamps into the inbox.",
            },
            {
                key: "shorts",
                title: "Prep shorts",
                state: preparedCount > 0 ? "done" : (sourceMoments.length > 0 ? "ready" : "blocked"),
                summary: preparedCount > 0
                    ? `${preparedCount} short-ready clip${preparedCount === 1 ? "" : "s"} using ${getShortPresetConfig().label}.`
                    : "Prep at least one inbox moment as a short.",
            },
            {
                key: "sequence",
                title: "Build short sequence",
                state: concatReady ? "done" : (getClipRows().length > 0 ? "ready" : "blocked"),
                summary: concatReady
                    ? "Clips are downloaded and stitched into a preview sequence."
                    : "Download and stitch the current clip stack.",
            },
            {
                key: "captions",
                title: "Run caption pass",
                state: captionsReady ? "done" : (concatReady ? "ready" : "blocked"),
                summary: captionsReady
                    ? "Transcription and caption styling are ready."
                    : "Transcribe the stitched sequence and tune the caption style.",
            },
            {
                key: "short_export",
                title: "Deliver current cut",
                state: sourceExportDone ? "done" : (concatReady ? "ready" : "blocked"),
                summary: sourceExportDone
                    ? `${projectRole === "longform" ? "Source shortform project already has a render." : `Rendered for ${currentFormat}.`}`
                    : `Render the shortform cut in ${currentFormat}.`,
            },
            {
                key: "longform_queue",
                title: "Queue longform picks",
                state: queuedPreparedCount > 0 ? "done" : (preparedCount > 0 ? "ready" : "blocked"),
                summary: queuedPreparedCount > 0
                    ? `${queuedPreparedCount} prepared short${queuedPreparedCount === 1 ? "" : "s"} queued for the horizontal cut.`
                    : "Keep at least one prepared short in the longform queue.",
            },
            {
                key: "longform_build",
                title: "Build or deliver longform",
                state: longformExportDone ? "done" : (longformBuilt ? "ready" : (queuedPreparedCount > 0 ? "ready" : "blocked")),
                summary: longformExportDone
                    ? "A horizontal longform project has been built and rendered."
                    : longformBuilt
                        ? "Longform project exists. Open it and render the YouTube cut."
                        : "Build the longform derivative from queued prepared shorts.",
            },
        ];
    }

    function renderBetaFlowStatus() {
        const list = $("reel-beta-flow-list");
        const progress = $("reel-beta-flow-progress");
        const next = $("reel-beta-flow-next");
        if (!list || !progress || !next) return;

        const items = buildBetaWorkflowItems();
        list.innerHTML = "";

        const completedCount = items.filter((item) => item.state === "done").length;
        progress.textContent = `${completedCount} / ${items.length} complete`;

        const nextBlocked = items.find((item) => item.state === "blocked");
        const nextReady = items.find((item) => item.state === "ready");
        if (nextBlocked) {
            next.textContent = `Next blocker: ${nextBlocked.title}. ${nextBlocked.summary}`;
        } else if (nextReady) {
            next.textContent = `Next action: ${nextReady.title}. ${nextReady.summary}`;
        } else {
            next.textContent = "Full streamer beta flow is ready to test end to end, including linked shortform and longform output.";
        }

        items.forEach((item, index) => {
            const row = document.createElement("div");
            row.className = `reel-beta-flow-item ${item.state}`;
            row.innerHTML = `
                <span class="reel-beta-flow-step">${index + 1}</span>
                <div class="reel-beta-flow-copy">
                    <div class="reel-beta-flow-title-row">
                        <span class="reel-beta-flow-title">${escapeHtml(item.title)}</span>
                        <span class="reel-beta-flow-state ${item.state}">${escapeHtml(item.state === "done" ? "Done" : (item.state === "ready" ? "In Progress" : "Blocked"))}</span>
                    </div>
                    <div class="reel-beta-flow-summary">${escapeHtml(item.summary)}</div>
                </div>
            `;
            list.appendChild(row);
        });
    }

    function normalizeFacecamLayout(layout = null) {
        const base = { ...DEFAULT_FACECAM_LAYOUT };
        if (layout && typeof layout === "object") {
            base.enabled = Boolean(layout.enabled ?? base.enabled);
            ["x", "y", "width", "height"].forEach((key) => {
                const value = Number(layout[key]);
                if (Number.isFinite(value)) {
                    base[key] = value;
                }
            });
            if (layout.anchor) {
                base.anchor = String(layout.anchor).trim().toLowerCase() || base.anchor;
            }
        }

        base.width = Math.min(0.7, Math.max(0.08, base.width));
        base.height = Math.min(0.7, Math.max(0.08, base.height));
        base.x = Math.min(1 - base.width, Math.max(0, base.x));
        base.y = Math.min(1 - base.height, Math.max(0, base.y));
        return base;
    }

    function cloneFacecamLayout(layout = facecamLayout) {
        return normalizeFacecamLayout(layout);
    }

    function readStoredFacecamLayouts() {
        try {
            const raw = localStorage.getItem(FACECAM_LAYOUT_STORAGE_KEY);
            const parsed = raw ? JSON.parse(raw) : {};
            return parsed && typeof parsed === "object" ? parsed : {};
        } catch (e) {
            return {};
        }
    }

    function writeStoredFacecamLayouts(layouts) {
        try {
            localStorage.setItem(FACECAM_LAYOUT_STORAGE_KEY, JSON.stringify(layouts || {}));
        } catch (e) {
            // Ignore storage failures.
        }
    }

    function rememberFacecamLayoutForChannel(channel, layout = facecamLayout) {
        const key = String(channel || "").trim().toLowerCase();
        if (!key) return;
        const layouts = readStoredFacecamLayouts();
        layouts[key] = cloneFacecamLayout(layout);
        writeStoredFacecamLayouts(layouts);
    }

    function getRememberedFacecamLayoutForChannel(channel) {
        const key = String(channel || "").trim().toLowerCase();
        if (!key) return null;
        const layouts = readStoredFacecamLayouts();
        return layouts[key] ? normalizeFacecamLayout(layouts[key]) : null;
    }

    function getPreviewVideoMetrics() {
        const container = $("reel-preview-container");
        const video = $("reel-preview-video");
        if (!container || !video) return null;

        const containerRect = container.getBoundingClientRect();
        const videoRect = video.getBoundingClientRect();
        if (!containerRect.width || !containerRect.height || !videoRect.width || !videoRect.height) {
            return null;
        }

        return {
            width: videoRect.width,
            height: videoRect.height,
            left: videoRect.left - containerRect.left,
            top: videoRect.top - containerRect.top,
        };
    }

    function updateFacecamLayoutSummary() {
        const summary = $("reel-facecam-summary");
        if (!summary) return;
        if (!facecamLayout.enabled) {
            summary.textContent = "Enable a saved facecam guide if your stream layout has a consistent camera box. Drag the guide in Clip Preview to place it and reuse that framing in exports.";
            return;
        }

        const x = Math.round(facecamLayout.x * 100);
        const y = Math.round(facecamLayout.y * 100);
        const width = Math.round(facecamLayout.width * 100);
        const height = Math.round(facecamLayout.height * 100);
        summary.textContent = `Saved facecam zone: ${width}% × ${height}% at ${x}% / ${y}% of the source frame. Facecam Top exports will reuse this placement.`;
    }

    function syncFacecamLayoutInputs() {
        if ($("reel-facecam-enabled")) $("reel-facecam-enabled").checked = Boolean(facecamLayout.enabled);
        if ($("reel-facecam-x")) $("reel-facecam-x").value = String(Math.round(facecamLayout.x * 100));
        if ($("reel-facecam-y")) $("reel-facecam-y").value = String(Math.round(facecamLayout.y * 100));
        if ($("reel-facecam-width")) $("reel-facecam-width").value = String(Math.round(facecamLayout.width * 100));
        if ($("reel-facecam-height")) $("reel-facecam-height").value = String(Math.round(facecamLayout.height * 100));
        if ($("reel-facecam-x-value")) $("reel-facecam-x-value").textContent = `${Math.round(facecamLayout.x * 100)}%`;
        if ($("reel-facecam-y-value")) $("reel-facecam-y-value").textContent = `${Math.round(facecamLayout.y * 100)}%`;
        if ($("reel-facecam-width-value")) $("reel-facecam-width-value").textContent = `${Math.round(facecamLayout.width * 100)}%`;
        if ($("reel-facecam-height-value")) $("reel-facecam-height-value").textContent = `${Math.round(facecamLayout.height * 100)}%`;
        updateFacecamLayoutSummary();
    }

    function renderFacecamGuide() {
        const guide = $("reel-facecam-guide");
        if (!guide) return;
        const label = guide.querySelector(".reel-facecam-guide-label");
        const handle = $("reel-facecam-guide-handle");

        const metrics = getPreviewVideoMetrics();
        const composition = getEffectiveCompositionState();
        const showInOutput = previewMode === "sequence" && composition.key === "facecam_top";
        const editable = previewMode === "source";
        if (!facecamLayout.enabled || !metrics || (!editable && !showInOutput)) {
            hide(guide);
            return;
        }

        guide.style.left = `${metrics.left + (metrics.width * facecamLayout.x)}px`;
        guide.style.top = `${metrics.top + (metrics.height * facecamLayout.y)}px`;
        guide.style.width = `${metrics.width * facecamLayout.width}px`;
        guide.style.height = `${metrics.height * facecamLayout.height}px`;
        guide.classList.toggle("readonly", !editable);
        if (label) {
            label.textContent = editable ? "Facecam Guide" : "Output Facecam Zone";
        }
        if (handle) {
            handle.classList.toggle("hidden", !editable);
        }
        show(guide);
    }

    function renderPreviewCompositionBadge() {
        const badge = $("reel-preview-composition-badge");
        const title = $("reel-preview-composition-title");
        const detail = $("reel-preview-composition-detail");
        if (!badge || !title || !detail) return;

        if (!projectId) {
            hide(badge);
            return;
        }

        const activePreset = getExportFormatConfig();
        const composition = getEffectiveCompositionState();
        const burnCaptions = $("reel-burn-captions")?.checked !== false;
        title.textContent = previewMode === "sequence"
            ? `${activePreset.label} output preview`
            : `${composition.label} clip framing`;
        detail.textContent = previewMode === "sequence"
            ? `${composition.summary} · ${burnCaptions ? "captions baked in" : "clean export + subtitle files"}`
            : composition.detail;
        show(badge);
    }

    function setFacecamLayout(nextLayout, options = {}) {
        const { persist = false, remember = false, render = true, refreshToolbar = true } = options;
        facecamLayout = normalizeFacecamLayout(nextLayout);
        syncFacecamLayoutInputs();
        if (render) {
            renderFacecamGuide();
            renderPreviewCompositionBadge();
            if (refreshToolbar) {
                updateToolbarState();
            }
        }
        const channel = $("reel-session-channel")?.value.trim() || "";
        if (remember && channel && facecamLayout.enabled) {
            rememberFacecamLayoutForChannel(channel, facecamLayout);
        }
        if (persist && projectId) {
            void saveSessionDetails({ quiet: true });
        }
        return facecamLayout;
    }

    function maybeApplyRememberedFacecamLayout(channel, options = {}) {
        const { persist = false } = options;
        const remembered = getRememberedFacecamLayoutForChannel(channel);
        if (!remembered) return false;
        setFacecamLayout(remembered, { persist, remember: false, render: true });
        return true;
    }

    function setFacecamPreset(anchor = "top_right") {
        const presets = {
            top_left: { x: 0.04, y: 0.04, width: 0.24, height: 0.24, anchor: "top_left" },
            top_right: { x: 0.72, y: 0.04, width: 0.24, height: 0.24, anchor: "top_right" },
            bottom_left: { x: 0.04, y: 0.68, width: 0.24, height: 0.24, anchor: "bottom_left" },
            bottom_right: { x: 0.72, y: 0.68, width: 0.24, height: 0.24, anchor: "bottom_right" },
        };
        const preset = presets[anchor] || presets.top_right;
        setFacecamLayout({
            ...facecamLayout,
            ...preset,
            enabled: true,
        }, { persist: true, remember: true });
    }

    function updateFacecamLayoutFromInputs() {
        setFacecamLayout({
            ...facecamLayout,
            enabled: $("reel-facecam-enabled")?.checked ?? facecamLayout.enabled,
            x: (Number($("reel-facecam-x")?.value) || 0) / 100,
            y: (Number($("reel-facecam-y")?.value) || 0) / 100,
            width: (Number($("reel-facecam-width")?.value) || 0) / 100,
            height: (Number($("reel-facecam-height")?.value) || 0) / 100,
        }, { persist: false, remember: false, render: true });
    }

    function startFacecamGuideDrag(event, mode = "move") {
        if (!facecamLayout.enabled || previewMode !== "source") return;
        const metrics = getPreviewVideoMetrics();
        if (!metrics) return;
        facecamGuideDrag = {
            mode,
            startX: event.clientX,
            startY: event.clientY,
            metrics,
            layout: cloneFacecamLayout(facecamLayout),
        };
        event.preventDefault();
        event.stopPropagation();
    }

    function onFacecamGuideDragMove(event) {
        if (!facecamGuideDrag) return;
        const { metrics, layout, mode, startX, startY } = facecamGuideDrag;
        const dx = (event.clientX - startX) / Math.max(1, metrics.width);
        const dy = (event.clientY - startY) / Math.max(1, metrics.height);

        if (mode === "resize") {
            setFacecamLayout({
                ...layout,
                width: layout.width + dx,
                height: layout.height + dy,
            }, { persist: false, remember: false, render: true, refreshToolbar: false });
            return;
        }

        setFacecamLayout({
            ...layout,
            x: layout.x + dx,
            y: layout.y + dy,
        }, { persist: false, remember: false, render: true, refreshToolbar: false });
    }

    function onFacecamGuideDragEnd() {
        if (!facecamGuideDrag) return;
        facecamGuideDrag = null;
        updateToolbarState();
        const channel = $("reel-session-channel")?.value.trim() || "";
        if (channel && facecamLayout.enabled) {
            rememberFacecamLayoutForChannel(channel, facecamLayout);
        }
        if (projectId) {
            void saveSessionDetails({ quiet: true });
        }
    }

    function humanizeSourceKind(kind) {
        const normalized = String(kind || "").trim().toLowerCase();
        if (!normalized) return "Source moment";
        if (normalized === "twitch_clip") return "Twitch clip";
        if (normalized === "stream_marker") return "Marker";
        return normalized.replace(/[_-]+/g, " ");
    }

    function buildMomentKey(item, kindOverride = null) {
        const kind = String(kindOverride ?? item?.kind ?? item?.source_kind ?? "").trim().toLowerCase();
        const start = Math.round((Number(item?.start_sec) || 0) * 10) / 10;
        const end = Math.round((Number(item?.end_sec) || 0) * 10) / 10;
        return `${kind}|${start.toFixed(1)}|${end.toFixed(1)}`;
    }

    function getClipRowForMoment(moment) {
        const targetKey = buildMomentKey(moment);
        return getClipRows().find((row) => buildMomentKey({
            start_sec: parseTimestamp(row.querySelector(".clip-start")?.value || "0"),
            end_sec: parseTimestamp(row.querySelector(".clip-end")?.value || "0"),
            source_kind: row.dataset.sourceKind || "",
        }) === targetKey) || null;
    }

    function summarizeSourceMoments(moments = []) {
        const counts = moments.reduce((acc, moment) => {
            const kind = String(moment?.kind || "").trim().toLowerCase() || "source_moment";
            acc[kind] = (acc[kind] || 0) + 1;
            return acc;
        }, {});
        return Object.entries(counts)
            .sort((left, right) => right[1] - left[1])
            .map(([kind, count]) => `${count} ${humanizeSourceKind(kind)}${count === 1 ? "" : "s"}`);
    }

    function updateShortPresetSummary() {
        const config = getShortPresetConfig();
        const el = $("reel-short-preset-summary");
        if (!el) return;
        el.textContent = `${config.label} · ${config.description}`;
    }

    function collectSessionDetails() {
        const parsedPreRoll = Number.parseFloat($("reel-marker-pre-roll")?.value || "8");
        const parsedPostRoll = Number.parseFloat($("reel-marker-post-roll")?.value || "22");
        return {
            platform: $("reel-session-platform")?.value || "twitch",
            channel_name: $("reel-session-channel")?.value.trim() || "",
            game_title: $("reel-session-game")?.value.trim() || "",
            session_label: $("reel-session-label")?.value.trim() || "",
            session_date: $("reel-session-date")?.value || "",
            notes: $("reel-session-notes")?.value.trim() || "",
            pre_roll: Number.isFinite(parsedPreRoll) ? parsedPreRoll : 8,
            post_roll: Number.isFinite(parsedPostRoll) ? parsedPostRoll : 22,
            facecam_layout: cloneFacecamLayout(facecamLayout),
        };
    }

    function populateSessionDetails(project = null) {
        const session = project?.stream_session || {};
        const markerDefaults = project?.marker_defaults || {};
        const shouldPopulateMarkers = !project || Object.prototype.hasOwnProperty.call(project, "stream_markers");
        const markers = shouldPopulateMarkers ? (project?.stream_markers || []) : [];
        if ($("reel-session-platform")) $("reel-session-platform").value = session.platform || "twitch";
        if ($("reel-session-channel")) $("reel-session-channel").value = session.channel_name || "";
        if ($("reel-session-game")) $("reel-session-game").value = session.game_title || "";
        if ($("reel-session-label")) $("reel-session-label").value = session.session_label || "";
        if ($("reel-session-date")) $("reel-session-date").value = session.session_date || "";
        if ($("reel-session-notes")) $("reel-session-notes").value = session.notes || "";
        if ($("reel-marker-pre-roll")) $("reel-marker-pre-roll").value = String(markerDefaults.pre_roll ?? 8);
        if ($("reel-marker-post-roll")) $("reel-marker-post-roll").value = String(markerDefaults.post_roll ?? 22);
        if ($("reel-stream-markers") && shouldPopulateMarkers) {
            $("reel-stream-markers").value = markers.map((marker) => marker.source_text || `${marker.start} ${marker.title || ""}`.trim()).join("\n");
        }
        const explicitLayout = project && Object.prototype.hasOwnProperty.call(project, "facecam_layout")
            ? normalizeFacecamLayout(project?.facecam_layout)
            : null;
        const fallbackLayout = getRememberedFacecamLayoutForChannel(session.channel_name || "");
        facecamLayout = normalizeFacecamLayout(
            explicitLayout?.enabled
                ? explicitLayout
                : (fallbackLayout || explicitLayout || DEFAULT_FACECAM_LAYOUT)
        );
        syncFacecamLayoutInputs();
        renderFacecamGuide();
        setStreamerStatus("");
    }

    function setConnectedVodStatus(message, options = {}) {
        const { error = false } = options;
        const el = $("reel-auth-vod-status");
        if (!el) return;
        const fallback = "Pull recent Twitch archives from the shared auth backend, then import markers from the selected VOD.";
        el.textContent = message || fallback;
        el.classList.toggle("error-msg", Boolean(error));
        el.classList.toggle("settings-hint", !error);
        if (message && message !== fallback) {
            setOperationRail(message, {
                source: "Twitch VODs",
                tone: error ? "error" : "info",
            });
        }
    }

    function setConnectedClipStatus(message, options = {}) {
        const { error = false } = options;
        const el = $("reel-auth-clip-status");
        if (!el) return;
        const fallback = "Fetch Twitch clips that viewers already cut from this stream, then import them as editable starter clips.";
        el.textContent = message || fallback;
        el.classList.toggle("error-msg", Boolean(error));
        el.classList.toggle("settings-hint", !error);
        if (message && message !== fallback) {
            setOperationRail(message, {
                source: "Twitch Clips",
                tone: error ? "error" : "info",
            });
        }
    }

    function formatConnectedVodDate(value) {
        if (!value) return "";
        const ts = new Date(value);
        if (Number.isNaN(ts.getTime())) return "";
        return ts.toLocaleString([], {
            day: "numeric",
            hour: "numeric",
            minute: "2-digit",
            month: "short",
        });
    }

    function formatMarkerTimestamp(seconds) {
        const total = Math.max(0, Math.floor(Number(seconds) || 0));
        const hours = Math.floor(total / 3600);
        const minutes = Math.floor((total % 3600) / 60);
        const secs = total % 60;
        if (hours > 0) {
            return `${hours}:${String(minutes).padStart(2, "0")}:${String(secs).padStart(2, "0")}`;
        }
        return `${minutes}:${String(secs).padStart(2, "0")}`;
    }

    function parseTwitchDurationToSeconds(value) {
        const raw = String(value || "").trim().toLowerCase();
        if (!raw) return 0;
        let total = 0;
        let matched = false;
        raw.replace(/(\d+)(h|m|s)/g, (_, amount, unit) => {
            const parsed = Number(amount) || 0;
            matched = true;
            if (unit === "h") total += parsed * 3600;
            if (unit === "m") total += parsed * 60;
            if (unit === "s") total += parsed;
            return "";
        });
        return matched ? total : 0;
    }

    function buildConnectedVodWindow(video, fallbackUserId = "") {
        const createdAt = new Date(video?.created_at || video?.published_at || "");
        const durationSeconds = parseTwitchDurationToSeconds(video?.duration);
        const broadcasterId = String(video?.user_id || fallbackUserId || "").trim();
        if (!broadcasterId || Number.isNaN(createdAt.getTime()) || durationSeconds <= 0) {
            return null;
        }
        return {
            broadcaster_id: broadcasterId,
            started_at: createdAt.toISOString(),
            ended_at: new Date(createdAt.getTime() + (durationSeconds * 1000)).toISOString(),
        };
    }

    function buildConnectedVodLabel(video, index) {
        const title = String(video?.title || `Twitch VOD ${index + 1}`).trim();
        const dateLabel = formatConnectedVodDate(video?.created_at || video?.published_at);
        const durationLabel = String(video?.duration || "").trim();
        return [title, dateLabel, durationLabel].filter(Boolean).join(" · ");
    }

    function renderConnectedTwitchVideos(videos = []) {
        const select = $("reel-auth-vod-select");
        if (!select) return;

        const currentValue = select.value;
        select.innerHTML = "";

        if (!videos.length) {
            const option = document.createElement("option");
            option.value = "";
            option.textContent = "No Twitch VODs loaded";
            select.appendChild(option);
            select.disabled = true;
            return;
        }

        videos.forEach((video, index) => {
            const option = document.createElement("option");
            option.value = String(video?.id || "");
            option.textContent = buildConnectedVodLabel(video, index);
            select.appendChild(option);
        });
        select.disabled = false;

        if (videos.some((video) => String(video?.id || "") === currentValue)) {
            select.value = currentValue;
        }
    }

    function buildConnectedClipMeta(clip, selectedVod = null) {
        const parts = [];
        const vodOffset = Number(clip?.vod_offset);
        const hasVodOffset = Number.isFinite(vodOffset);
        const durationSeconds = Number(clip?.duration);
        const derivedDuration = Number(clip?.end_sec) - Number(clip?.start_sec);
        const runtime = Number.isFinite(durationSeconds) && durationSeconds > 0
            ? durationSeconds
            : (Number.isFinite(derivedDuration) && derivedDuration > 0 ? derivedDuration : 0);
        const creator = String(clip?.creator_name || clip?.creator_login || "").trim();
        const viewCount = Number(clip?.view_count);
        const clipVideoId = String(clip?.video_id || "").trim();
        const selectedVodId = String(selectedVod?.id || "").trim();

        if (hasVodOffset) {
            parts.push(`Starts at ${formatMarkerTimestamp(vodOffset)}`);
        } else {
            parts.push("Waiting for Twitch VOD offset");
        }
        if (runtime > 0) {
            parts.push(`${formatMarkerTimestamp(runtime)} clip`);
        }
        if (creator) {
            parts.push(`by ${creator}`);
        }
        if (Number.isFinite(viewCount) && viewCount > 0) {
            parts.push(`${viewCount.toLocaleString()} views`);
        }
        if (clipVideoId && selectedVodId && clipVideoId !== selectedVodId) {
            parts.push("Different VOD");
        }

        return parts.join(" · ");
    }

    function renderConnectedTwitchClips(clips = []) {
        const container = $("reel-auth-clip-list");
        if (!container) return;

        container.innerHTML = "";
        if (!clips.length) {
            const empty = document.createElement("div");
            empty.className = "reel-auth-clip-empty";
            empty.textContent = "Load Twitch clips from the selected VOD to surface them here before importing.";
            container.appendChild(empty);
            return;
        }

        const selectedVod = getSelectedConnectedTwitchVod();
        clips.forEach((clip, index) => {
            const item = document.createElement("article");
            item.className = "reel-auth-clip-item";

            const thumb = document.createElement("div");
            thumb.className = "reel-auth-clip-thumb";
            if (clip?.thumbnail_url) {
                thumb.innerHTML = `<img src="${escapeHtml(clip.thumbnail_url)}" alt="${escapeHtml(clip.title || `Twitch clip ${index + 1}`)}" loading="lazy" referrerpolicy="no-referrer">`;
            }

            const body = document.createElement("div");
            body.className = "reel-auth-clip-body";
            body.innerHTML = `
                <div class="reel-auth-clip-title">${escapeHtml(clip?.title || `Twitch Clip ${index + 1}`)}</div>
                <div class="reel-auth-clip-meta">${escapeHtml(buildConnectedClipMeta(clip, selectedVod))}</div>
            `;

            item.appendChild(thumb);
            item.appendChild(body);
            container.appendChild(item);
        });
    }

    function isImportableConnectedTwitchClip(clip, selectedVod = null) {
        const vodOffset = Number(clip?.vod_offset);
        if (!Number.isFinite(vodOffset)) {
            return false;
        }
        const clipVideoId = String(clip?.video_id || "").trim();
        const selectedVodId = String(selectedVod?.id || "").trim();
        return !clipVideoId || !selectedVodId || clipVideoId === selectedVodId;
    }

    function getImportableConnectedTwitchClips(selectedVod = null) {
        return connectedTwitchClips.filter((clip) => isImportableConnectedTwitchClip(clip, selectedVod));
    }

    function updateConnectedTwitchControls(authState = null) {
        const state = authState || (typeof DmAuth !== "undefined" && typeof DmAuth.getState === "function"
            ? DmAuth.getState()
            : { error: null, isLoading: false, user: null });
        const hasUser = Boolean(state?.user);
        const hasVideos = connectedTwitchVideos.length > 0;
        const hasImportableClips = getImportableConnectedTwitchClips(getSelectedConnectedTwitchVod()).length > 0;
        const loading = Boolean(state?.isLoading);
        const select = $("reel-auth-vod-select");
        const loadBtn = $("reel-auth-load-vods-btn");
        const openBtn = $("reel-auth-open-vod-btn");
        const importBtn = $("reel-auth-import-twitch-markers-btn");
        const loadClipsBtn = $("reel-auth-load-twitch-clips-btn");
        const importClipsBtn = $("reel-auth-import-twitch-clips-btn");

        if (select && !hasVideos) {
            select.disabled = true;
            if (!loading) {
                select.innerHTML = "";
                const option = document.createElement("option");
                option.value = "";
                option.textContent = hasUser ? "Refresh to load recent Twitch VODs" : "Sign in to list recent Twitch VODs";
                select.appendChild(option);
            }
        }

        if (loadBtn) loadBtn.disabled = loading || !hasUser;
        if (openBtn) openBtn.disabled = loading || !hasUser || !hasVideos;
        if (importBtn) importBtn.disabled = loading || !hasUser || !hasVideos;
        if (loadClipsBtn) loadClipsBtn.disabled = loading || !hasUser || !hasVideos;
        if (importClipsBtn) importClipsBtn.disabled = loading || !hasUser || !hasVideos || !hasImportableClips;

        if (!loading && !hasUser) {
            connectedTwitchVideos = [];
            connectedTwitchClips = [];
            renderConnectedTwitchVideos([]);
            renderConnectedTwitchClips([]);
            setConnectedVodStatus(state?.error || "Sign in with Twitch to pull your recent VODs and markers.");
            setConnectedClipStatus("Sign in with Twitch to find clips viewers already cut from your stream.");
        } else if (!loading && hasUser && !hasVideos) {
            setConnectedVodStatus("Connected. Refresh Twitch VODs to pick a recent stream archive.");
            if (!connectedTwitchClips.length) {
                renderConnectedTwitchClips([]);
                setConnectedClipStatus("Load a Twitch VOD first to find clips tied to that stream.");
            }
        }
        renderBetaFlowStatus();
    }

    function getSelectedConnectedTwitchVod() {
        const select = $("reel-auth-vod-select");
        const selectedId = String(select?.value || "");
        if (!selectedId) return connectedTwitchVideos[0] || null;
        return connectedTwitchVideos.find((video) => String(video?.id || "") === selectedId) || null;
    }

    async function loadConnectedTwitchVideos(options = {}) {
        const { quiet = false } = options;
        const authState = typeof DmAuth !== "undefined" && typeof DmAuth.getState === "function"
            ? DmAuth.getState()
            : null;
        if (!authState?.user) {
            setConnectedVodStatus("Sign in with Twitch first.", { error: true });
            updateConnectedTwitchControls(authState);
            return [];
        }

        const btn = $("reel-auth-load-vods-btn");
        const previous = btn?.textContent || "Refresh Twitch VODs";
        if (btn) {
            btn.disabled = true;
            btn.textContent = "Loading...";
        }
        setWorkspace("session", { scroll: false, focusPanelId: "session" });
        setActiveTask("Refreshing recent Twitch VODs...", { source: "Ingest" });

        try {
            const data = await DmAuth.fetchTwitchVideos({
                first: 12,
                sort: "time",
                type: "archive",
            });
            connectedTwitchVideos = Array.isArray(data?.videos) ? data.videos : [];
            connectedTwitchClips = [];
            renderConnectedTwitchVideos(connectedTwitchVideos);
            renderConnectedTwitchClips([]);
            updateConnectedTwitchControls({
                ...authState,
                isLoading: false,
            });
            clearActiveTask();
            if (connectedTwitchVideos.length > 0) {
                if (!quiet) {
                    setConnectedVodStatus(`Loaded ${connectedTwitchVideos.length} recent Twitch VOD${connectedTwitchVideos.length === 1 ? "" : "s"}.`);
                }
                setConnectedClipStatus("Select a VOD, then load Twitch clips cut from that stream.");
            } else {
                setConnectedVodStatus("No recent Twitch archives were returned for this account.");
                setConnectedClipStatus("No Twitch clips can be loaded until a stream archive is available.");
            }
            return connectedTwitchVideos;
        } catch (e) {
            connectedTwitchVideos = [];
            connectedTwitchClips = [];
            renderConnectedTwitchVideos([]);
            renderConnectedTwitchClips([]);
            updateConnectedTwitchControls(authState);
            clearActiveTask();
            setConnectedVodStatus(e.message || "Failed to load Twitch VODs.", { error: true });
            setConnectedClipStatus("Failed to load Twitch clips because the VOD list did not load.", { error: true });
            return [];
        } finally {
            if (btn) {
                btn.disabled = false;
                btn.textContent = previous;
            }
            updateConnectedTwitchControls(authState);
        }
    }

    async function loadSelectedConnectedTwitchVod(options = {}) {
        const { quiet = false, loadClips = !quiet } = options;
        let selected = getSelectedConnectedTwitchVod();
        if (!selected) {
            const videos = await loadConnectedTwitchVideos({ quiet: true });
            selected = videos[0] || null;
        }
        if (!selected) {
            if (!quiet) setConnectedVodStatus("Load your Twitch VOD list first.", { error: true });
            return null;
        }

        setWorkspace("session", { scroll: false, focusPanelId: "session" });
        setActiveTask("Loading the selected Twitch VOD into the session...", { source: "Ingest" });
        const vodLink = String(selected.url || "").trim() || `https://www.twitch.tv/videos/${selected.id}`;
        setSourceType("url");
        if ($("reel-url-input")) $("reel-url-input").value = vodLink;

        const authState = typeof DmAuth !== "undefined" && typeof DmAuth.getState === "function"
            ? DmAuth.getState()
            : null;
        if (authState?.user) {
            applyAuthProfile(authState.user, { overwrite: false, persist: Boolean(projectId) });
        }

        const loaded = await validateUrl();
        if (loaded) {
            clearActiveTask();
        }
        if (loaded && !quiet) {
            setConnectedVodStatus(`Loaded Twitch VOD: ${selected.title || `Video ${selected.id}`}.`);
        } else if (!loaded) {
            clearActiveTask();
        }
        if (loaded && loadClips) {
            await loadConnectedTwitchClips({ quiet, selectedVod: selected });
        }
        return loaded ? selected : null;
    }

    async function loadConnectedTwitchClips(options = {}) {
        const { quiet = false, selectedVod = null } = options;
        const authState = typeof DmAuth !== "undefined" && typeof DmAuth.getState === "function"
            ? DmAuth.getState()
            : null;
        if (!authState?.user) {
            setConnectedClipStatus("Sign in with Twitch first.", { error: true });
            updateConnectedTwitchControls(authState);
            return [];
        }

        let targetVod = selectedVod || getSelectedConnectedTwitchVod();
        if (!targetVod) {
            const videos = await loadConnectedTwitchVideos({ quiet: true });
            targetVod = videos[0] || null;
        }
        if (!targetVod) {
            setConnectedClipStatus("Load your Twitch VOD list first.", { error: true });
            return [];
        }

        const clipWindow = buildConnectedVodWindow(targetVod, authState.user.id);
        if (!clipWindow) {
            setConnectedClipStatus("The selected Twitch VOD is missing timing data, so clips cannot be mapped to it yet.", { error: true });
            return [];
        }

        const btn = $("reel-auth-load-twitch-clips-btn");
        const previous = btn?.textContent || "Load Twitch Clips";
        if (btn) {
            btn.disabled = true;
            btn.textContent = "Loading...";
        }
        setWorkspace("session", { scroll: false, focusPanelId: "session" });
        setActiveTask("Fetching Twitch clips cut from this stream...", { source: "Inbox" });

        try {
            const data = await DmAuth.fetchTwitchClips({
                ...clipWindow,
                first: 40,
            });
            connectedTwitchClips = Array.isArray(data?.clips) ? data.clips : [];
            renderConnectedTwitchClips(connectedTwitchClips);
            const importableCount = getImportableConnectedTwitchClips(targetVod).length;
            clearActiveTask();
            if (!connectedTwitchClips.length) {
                setConnectedClipStatus("No Twitch clips were found for the selected VOD.");
            } else if (!quiet) {
                if (importableCount === connectedTwitchClips.length) {
                    setConnectedClipStatus(`Loaded ${connectedTwitchClips.length} Twitch clip${connectedTwitchClips.length === 1 ? "" : "s"} from this stream.`);
                } else {
                    setConnectedClipStatus(`Loaded ${connectedTwitchClips.length} Twitch clip${connectedTwitchClips.length === 1 ? "" : "s"}. ${importableCount} ${importableCount === 1 ? "is" : "are"} ready to import once VOD offsets are available.`);
                }
            }
            return connectedTwitchClips;
        } catch (e) {
            connectedTwitchClips = [];
            renderConnectedTwitchClips([]);
            clearActiveTask();
            setConnectedClipStatus(e.message || "Failed to load Twitch clips.", { error: true });
            return [];
        } finally {
            if (btn) {
                btn.disabled = false;
                btn.textContent = previous;
            }
            updateConnectedTwitchControls(authState);
        }
    }

    function buildMarkerTextFromTwitchMarkers(markers = []) {
        return markers.map((marker, index) => {
            const title = String(marker?.description || "").trim() || `Marker ${index + 1}`;
            return `${formatMarkerTimestamp(marker?.position_seconds)} ${title}`;
        }).join("\n");
    }

    async function importMarkersFromConnectedVod() {
        let selected = getSelectedConnectedTwitchVod();
        if (!selected) {
            const videos = await loadConnectedTwitchVideos({ quiet: true });
            selected = videos[0] || null;
        }
        if (!selected) {
            setConnectedVodStatus("Load your Twitch VOD list first.", { error: true });
            return [];
        }

        const btn = $("reel-auth-import-twitch-markers-btn");
        const previous = btn?.textContent || "Import Twitch Markers";
        if (btn) {
            btn.disabled = true;
            btn.textContent = "Importing...";
        }
        setWorkspace("session", { scroll: false, focusPanelId: "session" });
        setActiveTask("Importing Twitch markers into the session inbox...", { source: "Ingest" });

        try {
            if (!vodUrl || !String(vodUrl).includes(String(selected.id))) {
                setConnectedVodStatus("Loading the selected VOD before importing markers...");
                const loadedVod = await loadSelectedConnectedTwitchVod({ quiet: true });
                if (!loadedVod) {
                    clearActiveTask();
                    setConnectedVodStatus("The selected Twitch VOD could not be loaded into the editor.", { error: true });
                    return [];
                }
            }

            const data = await DmAuth.fetchTwitchMarkers(selected.id, { first: 100 });
            const markers = Array.isArray(data?.markers) ? data.markers : [];
            if (!markers.length) {
                clearActiveTask();
                setConnectedVodStatus("No Twitch markers were found on the selected VOD.");
                return [];
            }

            if ($("reel-stream-markers")) {
                $("reel-stream-markers").value = buildMarkerTextFromTwitchMarkers(markers);
            }
            clearActiveTask();
            setConnectedVodStatus(`Loaded ${markers.length} Twitch marker${markers.length === 1 ? "" : "s"} from the selected VOD. Importing them into clips...`);
            await importStreamMarkers();
            setConnectedVodStatus(`Imported ${markers.length} Twitch marker${markers.length === 1 ? "" : "s"} into this project.`);
            return markers;
        } catch (e) {
            clearActiveTask();
            setConnectedVodStatus(e.message || "Failed to import Twitch markers.", { error: true });
            return [];
        } finally {
            if (btn) {
                btn.disabled = false;
                btn.textContent = previous;
            }
            updateConnectedTwitchControls();
        }
    }

    async function importClipsFromConnectedVod() {
        let selected = getSelectedConnectedTwitchVod();
        if (!selected) {
            const videos = await loadConnectedTwitchVideos({ quiet: true });
            selected = videos[0] || null;
        }
        if (!selected) {
            setConnectedClipStatus("Load your Twitch VOD list first.", { error: true });
            return [];
        }

        const btn = $("reel-auth-import-twitch-clips-btn");
        const previous = btn?.textContent || "Import Twitch Clips";
        if (btn) {
            btn.disabled = true;
            btn.textContent = "Importing...";
        }
        setWorkspace("session", { scroll: false, focusPanelId: "session" });
        setActiveTask("Importing Twitch clips into the session inbox...", { source: "Inbox" });

        try {
            if (!connectedTwitchClips.length) {
                setConnectedClipStatus("Loading Twitch clips from the selected VOD...");
                await loadConnectedTwitchClips({ quiet: true, selectedVod: selected });
            }

            const importableClips = getImportableConnectedTwitchClips(selected);
            if (!importableClips.length) {
                clearActiveTask();
                setConnectedClipStatus("No Twitch clips with VOD offsets are ready to import for this stream yet.", { error: true });
                return [];
            }

            if (!vodUrl || !String(vodUrl).includes(String(selected.id))) {
                setConnectedVodStatus("Loading the selected VOD before importing Twitch clips...");
                const loadedVod = await loadSelectedConnectedTwitchVod({ quiet: true, loadClips: false });
                if (!loadedVod) {
                    clearActiveTask();
                    setConnectedClipStatus("The selected Twitch VOD could not be loaded into the editor.", { error: true });
                    return [];
                }
            }

            await ensureProject();
            const data = await api("/api/reel/import-twitch-clips", {
                method: "POST",
                body: JSON.stringify({
                    project_id: projectId,
                    clips: importableClips,
                }),
            });
            if (data.error) {
                setConnectedClipStatus(data.error, { error: true });
                return [];
            }

            sourceMoments = data.moments || [];
            if (Array.isArray(data.twitch_clips) && data.twitch_clips.length) {
                connectedTwitchClips = data.twitch_clips;
                renderConnectedTwitchClips(connectedTwitchClips);
            }

            const currentIds = new Set(getClipRows().map((row) => row.dataset.clipId));
            (data.imported_clips || []).forEach((clip) => {
                if (!currentIds.has(clip.id)) {
                    renderClipItem(clip);
                    currentIds.add(clip.id);
                }
            });

            if (getClipRows().length > 0) {
                enableStep(2);
                show("reel-clip-actions");
            }

            clearActiveTask();
            if ((data.imported_count || 0) > 0) {
                setSourceMomentStatus(data.message || `Imported ${data.imported_count} Twitch clip(s).`);
                setConnectedClipStatus(`Imported ${data.imported_count} Twitch clip${data.imported_count === 1 ? "" : "s"} into this project.`);
            } else {
                setSourceMomentStatus("Those Twitch clips are already present in this project.");
                setConnectedClipStatus("Those Twitch clips were already imported into this project.");
            }
            const summary = summarizeSourceMoments(sourceMoments).slice(0, 3).join(" · ");
            setSessionInboxStatus(summary ? `${sourceMoments.length} session moments ready. ${summary}.` : "Twitch clips imported into the session inbox.");

            await loadRecentProjects();
            await loadProjectAssets();
            updateToolbarState();
            return data.imported_clips || [];
        } catch (e) {
            clearActiveTask();
            setConnectedClipStatus(e.message || "Failed to import Twitch clips.", { error: true });
            return [];
        } finally {
            if (btn) {
                btn.disabled = false;
                btn.textContent = previous;
            }
            updateConnectedTwitchControls();
        }
    }

    function applyAuthProfile(user, options = {}) {
        if (!user || !user.login) return false;

        const { overwrite = false, persist = false } = options;
        const platformField = $("reel-session-platform");
        const channelField = $("reel-session-channel");
        if (!channelField) return false;

        const currentChannel = channelField.value.trim();
        const normalizedLogin = String(user.login).trim().toLowerCase();
        const matchesExisting = currentChannel.toLowerCase() === normalizedLogin;

        if (matchesExisting && !overwrite) {
            if (platformField && !platformField.value) {
                platformField.value = "twitch";
            }
            if (persist && projectId) {
                saveSessionDetails({ quiet: true });
            }
            renderBetaFlowStatus();
            return true;
        }

        if (currentChannel && !overwrite && !matchesExisting) {
            setStreamerStatus(`Connected Twitch account @${user.login} is available. Click "Use Connected Twitch" to replace the current channel.`);
            renderBetaFlowStatus();
            return false;
        }

        if (platformField && (!platformField.value || platformField.value === "twitch" || overwrite)) {
            platformField.value = "twitch";
        }
        channelField.value = user.login;
        channelField.dataset.authLogin = user.login;
        if (!facecamLayout.enabled) {
            maybeApplyRememberedFacecamLayout(user.login, { persist: false });
        }
        setStreamerStatus(`Connected Twitch account @${user.login} is ready for this video project.`);

        if (persist && projectId) {
            saveSessionDetails({ quiet: true });
        }

        renderBetaFlowStatus();
        return true;
    }

    function setShortformPreset(preset, options = {}) {
        const { render = true } = options;
        shortformPreset = SHORTFORM_PRESETS[preset] ? preset : DEFAULT_SHORTFORM_PRESET;
        if ($("reel-short-preset-select")) {
            $("reel-short-preset-select").value = shortformPreset;
        }
        updateShortPresetSummary();
        if (render) {
            renderSessionInbox();
        }
    }

    function updateClipRowFromData(row, clip) {
        if (!row || !clip) return;
        row.dataset.note = clip.note || "";
        row.dataset.sourceKind = clip.source_kind || "";
        row.dataset.fadeIn = clip.fade_in || 0;
        row.dataset.fadeOut = clip.fade_out || 0;
        row.dataset.shortPreset = clip.short_preset || "";
        row.dataset.compositionProfile = clip.composition_profile || "";
        row.dataset.includeInLongform = String(clip.include_in_longform !== false);
        row.title = clip.title || "";

        const titleInput = row.querySelector(".clip-title");
        if (titleInput) titleInput.value = clip.title || "";
        const startInput = row.querySelector(".clip-start");
        if (startInput) startInput.value = clip.start || "0:00";
        const endInput = row.querySelector(".clip-end");
        if (endInput) endInput.value = clip.end || "0:00";
        const noteBtn = row.querySelector(".clip-note-btn");
        if (noteBtn) {
            noteBtn.classList.toggle("has-note", Boolean((clip.note || "").trim()));
            noteBtn.textContent = (clip.note || "").trim() ? "Note*" : "Note";
        }
    }

    async function toggleClipLongformQueue(clipId, includeInLongform) {
        if (!projectId || !clipId) return null;
        const data = await api("/api/reel/update-clip", {
            method: "POST",
            body: JSON.stringify({
                project_id: projectId,
                clip_id: clipId,
                include_in_longform: includeInLongform,
            }),
        });
        if (data.error) {
            setSessionInboxStatus(data.error, { error: true });
            return null;
        }

        const row = getClipRow(clipId);
        if (row && data.clip) {
            updateClipRowFromData(row, data.clip);
        }
        await loadRecentProjects();
        await loadProjectAssets();
        const queuedCount = getClipRows().filter((item) => Boolean((item.dataset.shortPreset || "").trim()) && item.dataset.includeInLongform !== "false").length;
        setSessionInboxStatus(
            includeInLongform
                ? `Queued this prepared short for longform. ${queuedCount} prepared short${queuedCount === 1 ? "" : "s"} currently included.`
                : `Skipped this prepared short from longform. ${queuedCount} prepared short${queuedCount === 1 ? "" : "s"} still included.`,
        );
        updateToolbarState();
        return data.clip || null;
    }

    async function applyProjectShortRecipe(recipe, options = {}) {
        const { refreshCaptions = true } = options;
        if (recipe?.preset) {
            setShortformPreset(recipe.preset, { render: false });
        } else {
            updateShortPresetSummary();
        }
        if (recipe?.format_preset) {
            setExportFormatPreset(recipe.format_preset, {
                persist: false,
                markDirty: true,
            });
        }
        if (refreshCaptions && projectId) {
            const editor = $("reel-caption-editor");
            if (editor && !editor.classList.contains("hidden") && typeof CaptionEditor !== "undefined" && typeof CaptionEditor.init === "function") {
                await CaptionEditor.init(projectId);
            } else {
                attachCaptionTrack();
            }
        }
        renderSessionInbox();
        renderClipInspector();
    }

    async function prepareSourceMomentAsShort(momentKey) {
        const moment = sourceMoments.find((item) => buildMomentKey(item) === momentKey);
        if (!moment) {
            setSessionInboxStatus("That session moment could not be found anymore.", { error: true });
            return null;
        }

        await ensureProject();
        const data = await api("/api/reel/prepare-short", {
            method: "POST",
            body: JSON.stringify({
                project_id: projectId,
                preset: shortformPreset,
                moment,
            }),
        });
        if (data.error) {
            setSessionInboxStatus(data.error, { error: true });
            return null;
        }

        sourceMoments = Array.isArray(data.moments) ? data.moments : sourceMoments;
        const existingRow = data.clip?.id ? getClipRow(data.clip.id) : getClipRowForMoment(moment);
        if (existingRow && data.clip) {
            updateClipRowFromData(existingRow, data.clip);
        } else if (data.clip) {
            renderClipItem(data.clip);
        }

        if (data.clip?.id) {
            setActiveClip(data.clip.id);
        }

        await applyProjectShortRecipe(data.shortform_recipe);
        await loadRecentProjects();
        await loadProjectAssets();
        show("reel-clip-actions");
        enableStep(2);
        setSessionInboxStatus(
            data.created_clip
                ? `Prepared "${data.clip?.title || "clip"}" as a ${getShortPresetConfig().label} short and added it to the timeline.`
                : `Prepared "${data.clip?.title || "clip"}" as a ${getShortPresetConfig().label} short.`,
        );
        updateToolbarState();
        return data.clip || null;
    }

    async function prepareActiveClipAsShort() {
        const row = getActiveClipRow();
        if (!row || !projectId) {
            setSessionInboxStatus("Select or create a clip first, then prepare it as a short.", { error: true });
            return null;
        }

        const data = await api("/api/reel/prepare-short", {
            method: "POST",
            body: JSON.stringify({
                project_id: projectId,
                preset: shortformPreset,
                clip_id: row.dataset.clipId || "",
            }),
        });
        if (data.error) {
            setSessionInboxStatus(data.error, { error: true });
            return null;
        }

        sourceMoments = Array.isArray(data.moments) ? data.moments : sourceMoments;
        if (data.clip) {
            updateClipRowFromData(row, data.clip);
        }
        await applyProjectShortRecipe(data.shortform_recipe);
        await loadRecentProjects();
        await loadProjectAssets();
        setSessionInboxStatus(`Prepared the active clip as a ${getShortPresetConfig().label} short.`);
        updateToolbarState();
        return data.clip || null;
    }

    async function bulkPrepareShorts(mode = "all") {
        hideError("reel-step2-error");
        if (!sourceMoments.length) {
            setSessionInboxStatus("Import Twitch clips, markers, or manual timestamps before running bulk short prep.", { error: true });
            return null;
        }

        await ensureProject();
        await saveSessionDetails({ quiet: true });
        const buttonMap = {
            all: $("reel-bulk-prepare-all-btn"),
            twitch_clips: $("reel-bulk-prepare-twitch-btn"),
        };
        const btn = buttonMap[mode] || null;
        const previous = btn?.textContent || "";
        if (btn) {
            btn.disabled = true;
            btn.textContent = "Preparing...";
        }

        try {
            const data = await api("/api/reel/bulk-prepare-shorts", {
                method: "POST",
                body: JSON.stringify({
                    project_id: projectId,
                    preset: shortformPreset,
                    mode,
                    only_unprepared: mode !== "all" ? true : false,
                }),
            });
            if (data.error) {
                setSessionInboxStatus(data.error, { error: true });
                return null;
            }

            await resumeProject(projectId, { silent: true });
            setSessionInboxStatus(
                `${data.message || "Prepared session moments as shorts."} ${data.created_count || 0} new clip${Number(data.created_count || 0) === 1 ? "" : "s"}, ${data.updated_count || 0} updated, ${data.skipped_count || 0} skipped.`,
            );
            return data;
        } catch (e) {
            setSessionInboxStatus(e.message || "Bulk short prep failed.", { error: true });
            return null;
        } finally {
            if (btn) {
                btn.disabled = false;
                btn.textContent = previous;
            }
        }
    }

    async function setLongformQueueForPrepared(includeInLongform) {
        hideError("reel-step4-error");
        if (!projectId) {
            setSessionInboxStatus("Open a project first before changing the longform queue.", { error: true });
            return null;
        }

        const btn = includeInLongform ? $("reel-bulk-queue-all-btn") : $("reel-bulk-skip-all-btn");
        const previous = btn?.textContent || "";
        if (btn) {
            btn.disabled = true;
            btn.textContent = includeInLongform ? "Queueing..." : "Skipping...";
        }

        try {
            const data = await api("/api/reel/bulk-set-longform-queue", {
                method: "POST",
                body: JSON.stringify({
                    project_id: projectId,
                    include_in_longform: includeInLongform,
                    target: "prepared",
                }),
            });
            if (data.error) {
                setSessionInboxStatus(data.error, { error: true });
                return null;
            }

            await resumeProject(projectId, { silent: true });
            setSessionInboxStatus(
                includeInLongform
                    ? `Queued ${data.updated_count || 0} prepared short${Number(data.updated_count || 0) === 1 ? "" : "s"} for longform. ${data.queued_prepared_count || 0} currently included.`
                    : `Skipped ${data.updated_count || 0} prepared short${Number(data.updated_count || 0) === 1 ? "" : "s"} from longform. ${data.queued_prepared_count || 0} still included.`,
            );
            return data;
        } catch (e) {
            setSessionInboxStatus(e.message || "Failed to update the longform queue.", { error: true });
            return null;
        } finally {
            if (btn) {
                btn.disabled = false;
                btn.textContent = previous;
            }
        }
    }

    function renderSessionInbox() {
        const container = $("reel-session-inbox-list");
        if (!container) return;

        container.innerHTML = "";
        const moments = [...sourceMoments].sort((left, right) => {
            const startDiff = (Number(left?.start_sec) || 0) - (Number(right?.start_sec) || 0);
            if (Math.abs(startDiff) > 0.001) return startDiff;
            return String(left?.title || "").localeCompare(String(right?.title || ""));
        });

        if (!moments.length) {
            const empty = document.createElement("div");
            empty.className = "reel-session-inbox-empty";
            empty.textContent = "Imported Twitch clips, markers, and detected highlights will appear here.";
            container.appendChild(empty);
            return;
        }

        const activeRow = getActiveClipRow();
        const activeKey = activeRow ? buildMomentKey({
            start_sec: parseTimestamp(activeRow.querySelector(".clip-start")?.value || "0"),
            end_sec: parseTimestamp(activeRow.querySelector(".clip-end")?.value || "0"),
            source_kind: activeRow.dataset.sourceKind || "",
        }) : "";

        moments.forEach((moment) => {
            const clipRow = getClipRowForMoment(moment);
            const appliedPresetKey = clipRow?.dataset.shortPreset || "";
            const presetLabel = appliedPresetKey ? (SHORTFORM_PRESETS[appliedPresetKey]?.label || appliedPresetKey) : "";
            const queuedForLongform = clipRow ? clipRow.dataset.includeInLongform !== "false" : false;
            const item = document.createElement("article");
            item.className = `reel-session-inbox-item${buildMomentKey(moment) === activeKey ? " active" : ""}`;

            const meta = [
                `${moment.start || formatTimestamp(Number(moment.start_sec) || 0)} - ${moment.end || formatTimestamp(Number(moment.end_sec) || 0)}`,
            ];
            if (moment.score) meta.push(`Score ${Math.round(Number(moment.score))}`);
            if (moment.view_count) meta.push(`${Number(moment.view_count).toLocaleString()} views`);
            if (clipRow) meta.push("On timeline");
            if (presetLabel) meta.push(presetLabel);
            if (presetLabel) meta.push(queuedForLongform ? "Queued for longform" : "Skipped in longform");

            item.innerHTML = `
                <div class="reel-session-inbox-main">
                    <div class="reel-session-inbox-copy">
                        <div class="reel-session-inbox-badges">
                            <span class="reel-session-kind-badge">${escapeHtml(humanizeSourceKind(moment.kind))}</span>
                            <span class="reel-session-state-badge ${presetLabel ? "ready" : (clipRow ? "pending" : "")}">${escapeHtml(presetLabel ? "Short ready" : (clipRow ? "Timeline only" : "Inbox"))}</span>
                            ${presetLabel ? `<span class="reel-session-queue-badge ${queuedForLongform ? "queued" : "skipped"}">${escapeHtml(queuedForLongform ? "In Longform" : "Skipped")}</span>` : ""}
                        </div>
                        <div class="reel-session-inbox-title">${escapeHtml(moment.title || "Source Moment")}</div>
                        <div class="reel-session-inbox-meta">${escapeHtml(meta.join(" · "))}</div>
                    </div>
                    <div class="reel-session-inbox-row-actions"></div>
                </div>
            `;

            item.addEventListener("click", (event) => {
                if (event.target.closest("button")) return;
                if (clipRow?.dataset.clipId) {
                    setActiveClip(clipRow.dataset.clipId);
                } else {
                    const video = $("reel-preview-video");
                    if (video && Number.isFinite(Number(moment.start_sec))) {
                        video.currentTime = Number(moment.start_sec) || 0;
                    }
                }
            });

            const actions = item.querySelector(".reel-session-inbox-row-actions");
            const prepBtn = document.createElement("button");
            prepBtn.type = "button";
            prepBtn.className = "secondary-btn";
            prepBtn.textContent = presetLabel ? "Reapply Preset" : (clipRow ? "Prep Short" : "Create + Prep");
            prepBtn.addEventListener("click", () => {
                void prepareSourceMomentAsShort(buildMomentKey(moment));
            });
            actions.appendChild(prepBtn);

            if (clipRow?.dataset.clipId) {
                const selectBtn = document.createElement("button");
                selectBtn.type = "button";
                selectBtn.className = "secondary-btn";
                selectBtn.textContent = "Select Clip";
                selectBtn.addEventListener("click", () => {
                    setActiveClip(clipRow.dataset.clipId);
                });
                actions.appendChild(selectBtn);
            }

            if (clipRow?.dataset.clipId && presetLabel) {
                const queueBtn = document.createElement("button");
                queueBtn.type = "button";
                queueBtn.className = "secondary-btn";
                queueBtn.textContent = queuedForLongform ? "Skip in Longform" : "Keep in Longform";
                queueBtn.addEventListener("click", () => {
                    void toggleClipLongformQueue(clipRow.dataset.clipId, !queuedForLongform);
                });
                actions.appendChild(queueBtn);
            }

            if (moment.clip_url) {
                const openBtn = document.createElement("button");
                openBtn.type = "button";
                openBtn.className = "secondary-btn";
                openBtn.textContent = "Open Clip";
                openBtn.addEventListener("click", () => openExternal(moment.clip_url));
                actions.appendChild(openBtn);
            }

            container.appendChild(item);
        });
    }

    function renderProjectChrome(project = null) {
        const badge = $("reel-project-badge");
        const meta = $("reel-project-meta");
        if (!badge || !meta) return;

        const summary = project || recentProjects.find((item) => item.project_id === projectId);
        if (!projectId) {
            hide(badge);
            meta.textContent = "Video projects autosave locally and can be resumed later.";
            updateWorkspaceChrome();
            return;
        }

        badge.textContent = `Project ${projectId}`;
        show(badge);
        if (summary) {
            const sourceProject = summary.project_role === "longform" && summary.derived_from_project_id
                ? recentProjects.find((item) => item.project_id === summary.derived_from_project_id) || null
                : null;
            const linkedLongform = summary.project_role !== "longform"
                ? recentProjects.find((item) => item.derived_from_project_id === summary.project_id) || null
                : null;
            const contextLabel = sourceProject
                ? `Derived from ${sourceProject.title || `project ${sourceProject.project_id}`}`
                : (linkedLongform ? `Longform project ${linkedLongform.project_id} linked` : "");
            meta.textContent = `${summary.title || "Untitled video project"} · ${buildProjectMeta(summary)}${contextLabel ? ` · ${contextLabel}` : ""}`;
        } else {
            meta.textContent = `Autosaving locally as video project ${projectId}.`;
        }
        updateWorkspaceChrome(summary);
    }

    function updateWorkspaceChrome(project = null) {
        const summary = project || recentProjects.find((item) => item.project_id === projectId) || null;
        const activePreset = getExportFormatConfig();
        const currentTitle = summary?.title || (projectId ? `Project ${projectId}` : "Streamer Workflow Workspace");
        const totalDuration = getTotalClipDuration();
        const clipCount = getClipRows().length;
        const row = getActiveClipRow();
        const workspaceLabel = getWorkspaceLabel();
        const sourceChip = $("reel-monitor-source-chip");
        const sequenceChip = $("reel-monitor-sequence-chip");
        const canPreviewSource = sourceType === "file"
            ? Boolean(localVodObjectUrl || localVodUploaded)
            : Boolean(projectId && vodUrl);

        if ($("reel-window-project-label")) {
            $("reel-window-project-label").textContent = currentTitle;
        }

        if ($("reel-toolbar-project-summary")) {
            if (!projectId) {
                $("reel-toolbar-project-summary").textContent = "No project loaded";
            } else {
                const roleLabel = summary?.project_role === "longform" ? "Longform" : "Shortform";
                $("reel-toolbar-project-summary").textContent = `${currentTitle} · ${clipCount} clip${clipCount === 1 ? "" : "s"} · ${roleLabel}`;
            }
        }
        if ($("reel-toolbar-workspace-summary")) {
            $("reel-toolbar-workspace-summary").textContent = `Workspace: ${workspaceLabel}`;
        }

        if ($("reel-monitor-title")) {
            $("reel-monitor-title").textContent = previewMode === "sequence" ? "Project Preview" : "Clip Preview";
        }

        if (sourceChip) {
            sourceChip.classList.toggle("active", previewMode === "source");
            sourceChip.disabled = !canPreviewSource;
        }
        if (sequenceChip) {
            sequenceChip.classList.toggle("active", previewMode === "sequence");
            sequenceChip.disabled = !concatReady;
        }

        if ($("reel-monitor-subtitle")) {
            if (!projectId) {
                $("reel-monitor-subtitle").textContent = "Load a source video to start building the timeline.";
            } else if (previewMode === "sequence" && concatReady) {
                $("reel-monitor-subtitle").textContent = hasCaptionData()
                    ? "Preview the stitched program output with the live caption track."
                    : "Preview the stitched program output before the final render.";
            } else if (row) {
                const start = parseTimestamp(row.querySelector(".clip-start")?.value || "0");
                const end = parseTimestamp(row.querySelector(".clip-end")?.value || "0");
                const clipName = row.querySelector(".clip-title")?.value?.trim() || row.querySelector(".clip-number")?.textContent || "Clip";
                $("reel-monitor-subtitle").textContent = `${clipName} · ${formatTimestamp(start)} to ${formatTimestamp(end)}. Drag clip edges in Clip Review or use I / O to trim.`;
            } else {
                $("reel-monitor-subtitle").textContent = "Use Clip Review to mark ranges, then switch to Project Preview to inspect the stitched output.";
            }
        }

        const activeClipPill = $("reel-active-clip-pill");
        if (activeClipPill) {
            if (row) {
                const start = parseTimestamp(row.querySelector(".clip-start")?.value || "0");
                const end = parseTimestamp(row.querySelector(".clip-end")?.value || "0");
                activeClipPill.textContent = `${row.querySelector(".clip-number")?.textContent || "Clip"} · ${formatTimestamp(start)} - ${formatTimestamp(end)}`;
                show(activeClipPill);
            } else {
                hide(activeClipPill);
            }
        }

        if ($("reel-status-project")) {
            $("reel-status-project").textContent = projectId ? currentTitle : "No project loaded";
        }
        if ($("reel-status-workspace")) {
            $("reel-status-workspace").textContent = `${workspaceLabel} workspace`;
        }
        if ($("reel-status-selection")) {
            if (row) {
                const start = parseTimestamp(row.querySelector(".clip-start")?.value || "0");
                const end = parseTimestamp(row.querySelector(".clip-end")?.value || "0");
                $("reel-status-selection").textContent = `${row.querySelector(".clip-number")?.textContent || "Clip"} · ${formatTimestamp(start)} - ${formatTimestamp(end)}`;
            } else {
                $("reel-status-selection").textContent = "No clip selected";
            }
        }
        if ($("reel-status-preview")) {
            $("reel-status-preview").textContent = previewMode === "sequence"
                ? "Project preview"
                : (sourceType === "file" ? "Clip preview · local" : "Clip preview · remote");
        }
        if ($("reel-status-format")) {
            const composition = getEffectiveCompositionState();
            $("reel-status-format").textContent = `${activePreset.label} · ${composition.label}`;
        }
        if ($("reel-status-runtime")) {
            $("reel-status-runtime").textContent = clipCount > 0
                ? `${clipCount} clip${clipCount === 1 ? "" : "s"} · ${formatTimestamp(totalDuration)} total`
                : "0 clips";
        }
        renderWorkspaceControls();
        renderActivityStatus();
        renderFacecamGuide();
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
        const shortPresetInput = $("reel-inspector-short-preset");
        if (!card || !heading || !titleInput || !noteInput || !rangeInput || !durationInput || !sourceKind || !shortPresetInput) return;

        const row = getActiveClipRow();
        if (!row) {
            hide(card);
            heading.textContent = "No clip selected";
            titleInput.value = "";
            noteInput.value = "";
            rangeInput.value = "";
            durationInput.value = "";
            shortPresetInput.value = "";
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
        const presetConfig = SHORTFORM_PRESETS[row.dataset.shortPreset || ""];
        shortPresetInput.value = presetConfig
            ? `${presetConfig.label} · ${row.dataset.compositionProfile || presetConfig.layoutMode}`
            : "";
        const fadeInEl = $("reel-inspector-fade-in");
        const fadeOutEl = $("reel-inspector-fade-out");
        if (fadeInEl) fadeInEl.value = row.dataset.fadeIn || "0";
        if (fadeOutEl) fadeOutEl.value = row.dataset.fadeOut || "0";

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
        const preparedShortCount = getPreparedClipRows().length;
        syncBurnCaptionsSummary();
        updateExportCompositionUI();
        renderBetaFlowStatus();
        renderLongformBuildUI();
        if ($("reel-toolbar-source-summary")) {
            const sourceLabel = isUrlSource ? "URL" : "Local File";
            const previewLabel = previewMode === "sequence" ? "Project Preview" : "Clip Preview";
            $("reel-toolbar-source-summary").textContent = `Source: ${sourceLabel} · ${previewLabel}`;
        }
        if ($("reel-url-input")) $("reel-url-input").disabled = !isUrlSource;
        if ($("reel-validate-btn")) $("reel-validate-btn").disabled = !isUrlSource;
        if ($("reel-import-moments-btn")) {
            $("reel-import-moments-btn").disabled = !isUrlSource || !projectId || !vodUrl;
        }
        if ($("reel-add-clip-btn")) $("reel-add-clip-btn").disabled = !projectId || !vodDuration;
        if ($("reel-download-clips-btn")) $("reel-download-clips-btn").disabled = !projectId || getClipRows().length === 0;
        if ($("reel-transcribe-btn")) $("reel-transcribe-btn").disabled = !concatReady;
        if ($("reel-export-btn")) $("reel-export-btn").disabled = !concatReady;
        if ($("reel-bulk-prepare-all-btn")) $("reel-bulk-prepare-all-btn").disabled = !projectId || sourceMoments.length === 0;
        if ($("reel-bulk-prepare-twitch-btn")) {
            $("reel-bulk-prepare-twitch-btn").disabled = !projectId || !sourceMoments.some((moment) => String(moment?.kind || "").toLowerCase() === "twitch_clip");
        }
        if ($("reel-bulk-queue-all-btn")) $("reel-bulk-queue-all-btn").disabled = !projectId || preparedShortCount === 0;
        if ($("reel-bulk-skip-all-btn")) $("reel-bulk-skip-all-btn").disabled = !projectId || preparedShortCount === 0;
        if ($("reel-prepare-active-clip-btn")) $("reel-prepare-active-clip-btn").disabled = !projectId || !activeClipId;
        $("reel-preview-source-btn")?.classList.toggle("active", previewMode === "source");
        $("reel-preview-sequence-btn")?.classList.toggle("active", previewMode === "sequence");
        const canPreviewSource = sourceType === "file"
            ? Boolean(localVodObjectUrl || localVodUploaded)
            : Boolean(projectId && vodUrl);
        if ($("reel-preview-source-btn")) $("reel-preview-source-btn").disabled = !canPreviewSource;
        if ($("reel-preview-sequence-btn")) $("reel-preview-sequence-btn").disabled = !concatReady;
        renderPreviewCompositionBadge();

        // Update total video duration from clip ranges
        const totalDurEl = $("reel-total-duration");
        if (totalDurEl) {
            const totalSec = getTotalClipDuration();
            if (totalSec > 0) {
                let warning = "";
                if (totalSec > 180) warning = " ⚠ >3 min";
                else if (totalSec > 90) warning = " ⚠ >90s (exceeds YT Shorts)";
                else if (totalSec > 60) warning = " ⚠ >60s (exceeds TikTok/Reels)";
                totalDurEl.textContent = `~${formatTimestamp(totalSec)}${warning}`;
                totalDurEl.title = warning
                    ? "TikTok/Reels max: 60s. YouTube Shorts max: 60s (some up to 3 min)."
                    : "Total clip duration before export";
            } else {
                totalDurEl.textContent = "";
                totalDurEl.title = "Total reel duration based on clip ranges";
            }
        }

        updateWorkspaceChrome();
        renderSessionInbox();
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
                <span class="reel-project-row-title">${escapeHtml(project.title || "Untitled video project")}</span>
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

            const renameBtn = document.createElement("button");
            renameBtn.type = "button";
            renameBtn.className = "secondary-btn";
            renameBtn.textContent = "Rename";
            renameBtn.addEventListener("click", async () => {
                const current = project.title || "Untitled video project";
                const newTitle = prompt("Project title:", current);
                if (!newTitle || newTitle.trim() === current) return;
                await renameProject(project.project_id, newTitle.trim());
            });

            actions.appendChild(resumeBtn);
            actions.appendChild(renameBtn);
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
        renderProjectChrome();
        renderBetaFlowStatus();
        queueEditorSummaryPublish();
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
        connectedTwitchClips = [];
        previewMode = "source";
        timelineZoom = 1;
        timelineDragState = null;
        exportFormatPreset = DEFAULT_EXPORT_FORMAT_PRESET;
        shortformPreset = DEFAULT_SHORTFORM_PRESET;
        facecamLayout = cloneFacecamLayout(DEFAULT_FACECAM_LAYOUT);
        facecamGuideDrag = null;
        projectRole = "shortform";
        derivedFromProjectId = "";
        pendingLongformProject = null;
        activeTask = null;
        importedEditorFeedIds = new Set();
        if (clipInspectorTimer) {
            clearTimeout(clipInspectorTimer);
            clipInspectorTimer = null;
        }

        if (forgetProject) {
            setProjectIdentity("");
        }

        populateSessionDetails(null);
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
        renderConnectedTwitchClips([]);
        setConnectedClipStatus("");
        setSessionInboxStatus("");
        setShortformPreset(DEFAULT_SHORTFORM_PRESET, { render: false });
        syncFacecamLayoutInputs();
        renderFacecamGuide();
        if ($("reel-sequence-audio-lane")) {
            $("reel-sequence-audio-lane").style.backgroundImage = "";
        }
        if ($("reel-timeline-zoom")) $("reel-timeline-zoom").value = "1";

        hide("reel-vod-info");
        hide("reel-clip-actions");
        hide("reel-download-status");
        hide("reel-transcribe-progress");
        hide("reel-caption-editor");
        hide("reel-local-file-restore");
        clearOperationRail();
        hideError("reel-step1-error");
        hideError("reel-step2-error");
        hideError("reel-step3-error");
        hideError("reel-step4-error");

        disableStep(2);
        disableStep(3);
        disableStep(4);
        $("reel-transcribe-btn").disabled = true;
        resetExportState();
        setExportFormatPreset(DEFAULT_EXPORT_FORMAT_PRESET, { persist: false, markDirty: false });
        setSourceType("url");
        applyTimelineZoom();
        renderTimeline();

        if (typeof CaptionEditor !== "undefined" && typeof CaptionEditor.reset === "function") {
            CaptionEditor.reset();
        }

        renderClipInspector();
        renderAssetBin();
        renderProjectChrome();
        renderLongformBuildUI();
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
        updateWorkspaceChrome();
        updateToolbarState();
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
        renderTimeRuler("reel-sequence-ruler", duration);
        renderGuideLane("reel-sequence-audio-lane", segments, duration, { kind: "audio", sequence: true });
        renderGuideLane("reel-sequence-caption-lane", segments, duration, { kind: "caption", sequence: true });
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
        renderFacecamGuide();
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
        const clipSegments = getClipSegments();
        renderTimeRuler("reel-source-ruler", duration);
        renderGuideLane("reel-source-audio-lane", clipSegments, duration, { kind: "audio", sequence: false });
        renderGuideLane("reel-source-caption-lane", clipSegments, duration, { kind: "caption", sequence: false });
        playhead.style.left = `${(clampedCurrent / duration) * 100}%`;
        clipsEl.innerHTML = "";

        clipSegments.forEach((clip) => {
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

    function focusSourceInput() {
        setWorkspace("session", { scroll: false, focusPanelId: "session" });
        setSourceType("url");
        const input = $("reel-url-input");
        if (input) {
            window.setTimeout(() => {
                input.focus();
                input.select();
            }, 50);
        }
        focusPanel("session", { scroll: true, workspaceId: "session" });
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
        setWorkspace("session", { scroll: false, focusPanelId: "session" });
        setSourceType("file");
        focusPanel("session", { scroll: true, workspaceId: "session" });
        $("reel-video-input")?.click();
    }

    async function importSourceMoments(options = {}) {
        const { auto = false } = options;
        hideError("reel-step2-error");

        if (!projectId || sourceType !== "url" || !vodUrl) {
            if (!auto) {
                showError("reel-step2-error", "Load a remote video URL first.");
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
                setSourceMomentStatus(`Detected ${sourceMoments.length} source moment(s) on this video.`);
            } else {
                setSourceMomentStatus("No source moments were exposed by this video.");
            }
            if (sourceMoments.length > 0) {
                const summary = summarizeSourceMoments(sourceMoments).slice(0, 3).join(" · ");
                setSessionInboxStatus(summary ? `${sourceMoments.length} session moments ready. ${summary}.` : `${sourceMoments.length} session moments ready.`);
            } else {
                setSessionInboxStatus("This source did not expose moments yet. Import Twitch clips or markers to build the session inbox.");
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
        projectRole = project.project_role || "shortform";
        derivedFromProjectId = project.derived_from_project_id || "";
        if (pendingLongformProject?.projectId && pendingLongformProject.projectId === restoredProjectId) {
            pendingLongformProject = null;
        }
        sourceMoments = project.source_moments || [];
        setShortformPreset(project.shortform_recipe?.preset || DEFAULT_SHORTFORM_PRESET, { render: false });
        connectedTwitchClips = sourceMoments.filter((moment) => String(moment?.kind || "").toLowerCase() === "twitch_clip");
        renderConnectedTwitchClips(connectedTwitchClips);
        renderSessionInbox();
        localVodFile = null;
        activeClipId = "";
        concatReady = Boolean(project.concat_file);
        exportedReelReady = Boolean(project.export_file);
        setExportFormatPreset(project.export_format_preset || DEFAULT_EXPORT_FORMAT_PRESET, {
            persist: false,
            markDirty: false,
        });
        populateSessionDetails(project);
        if (typeof DmAuth !== "undefined" && typeof DmAuth.getState === "function") {
            const authState = DmAuth.getState();
            if (authState?.user) {
                applyAuthProfile(authState.user, { overwrite: false, persist: false });
            }
            updateConnectedTwitchControls(authState);
        }

        setSourceType(sourceType);
        $("reel-url-input").value = sourceType === "url" ? vodUrl : "";

        if (project.vod_title || vodDuration) {
            $("reel-vod-title").textContent = project.vod_title || "Untitled Video";
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
            setSourceMomentStatus(`Loaded ${sourceMoments.length} source moment(s) from this video.`);
        }
        if (connectedTwitchClips.length > 0) {
            setConnectedClipStatus(`Loaded ${connectedTwitchClips.length} Twitch clip${connectedTwitchClips.length === 1 ? "" : "s"} from this project.`);
        }
        if (sourceMoments.length > 0) {
            const summary = summarizeSourceMoments(sourceMoments).slice(0, 3).join(" · ");
            setSessionInboxStatus(summary ? `${sourceMoments.length} session moments ready. ${summary}.` : `${sourceMoments.length} session moments ready.`);
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
        renderLongformBuildUI();
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
            await syncEditorFeedIntoProject({ quiet: true });
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

    async function importLaunchTwitchClip(params) {
        if (!projectId || !params?.clipId || params.startSec === null || params.endSec === null) {
            return false;
        }

        const rawClip = {
            created_at: params.createdAt || "",
            duration: Math.max(MIN_CLIP_DURATION, Number(params.endSec) - Number(params.startSec)),
            id: params.clipId,
            title: params.candidateTitle || "Twitch Clip",
            url: params.clipUrl || "",
            video_id: params.videoId || "",
            view_count: params.viewCount ?? null,
            vod_offset: params.vodOffset ?? params.startSec,
        };

        const data = await api("/api/reel/import-twitch-clips", {
            method: "POST",
            body: JSON.stringify({
                project_id: projectId,
                clips: [rawClip],
            }),
        });

        if (data.error) {
            setConnectedClipStatus(data.error, { error: true });
            return false;
        }

        sourceMoments = data.moments || sourceMoments;
        connectedTwitchClips = sourceMoments.filter((moment) => String(moment?.kind || "").toLowerCase() === "twitch_clip");
        renderConnectedTwitchClips(connectedTwitchClips);

        const currentIds = new Set(getClipRows().map((row) => row.dataset.clipId));
        (data.imported_clips || []).forEach((clip) => {
            if (!currentIds.has(clip.id)) {
                renderClipItem(clip);
                currentIds.add(clip.id);
            }
        });

        if (getClipRows().length > 0) {
            enableStep(2);
            show("reel-clip-actions");
        }

        const targetMoment = sourceMoments.find((moment) => String(moment?.clip_id || "") === params.clipId)
            || {
                end_sec: params.endSec,
                kind: "twitch_clip",
                start_sec: params.startSec,
            };

        selectClipForMoment(targetMoment);
        const summary = summarizeSourceMoments(sourceMoments).slice(0, 3).join(" · ");
        setSessionInboxStatus(summary ? `${sourceMoments.length} session moments ready. ${summary}.` : "Twitch clip imported into the session inbox.");
        setConnectedClipStatus(data.message || "Twitch clip is ready in this project.");
        await loadRecentProjects();
        await loadProjectAssets();
        updateToolbarState();
        return true;
    }

    async function consumeOpenNowLaunch() {
        const params = readLaunchParams();
        if (!params.openNow) {
            return false;
        }

        let opened = false;

        try {
            let resumed = false;
            if (params.projectId) {
                resumed = await resumeProject(params.projectId, { silent: true });
            }

            if (!resumed && params.vodUrl) {
                setWorkspace("session", { scroll: false, focusPanelId: "session" });
                setSourceType("url");
                if ($("reel-url-input")) {
                    $("reel-url-input").value = params.vodUrl;
                }
                const loaded = await validateUrl();
                if (!loaded) {
                    return false;
                }
            }

            if (!projectId) {
                await ensureProject();
            }

            applyLaunchSessionDetails(params);
            await saveSessionDetails({ quiet: true });
            await syncEditorFeedIntoProject({ quiet: true });

            if (params.candidateKind === "twitch_clip" && params.clipId && params.startSec !== null && params.endSec !== null) {
                await importLaunchTwitchClip(params);
            } else if (params.startSec !== null && params.endSec !== null) {
                selectClipForMoment({
                    end_sec: params.endSec,
                    kind: params.candidateKind || "source_moment",
                    start_sec: params.startSec,
                });
            }

            if (params.startSec !== null && params.endSec !== null) {
                focusStep(2);
            } else {
                focusStep(1);
            }

            queueEditorSummaryPublish({ force: true, delayMs: 40 });
            opened = true;
            setOperationRail("Opened the mirrored editing handoff from DM Toolkit.", {
                source: "Toolkit",
                tone: "success",
            });
            return true;
        } finally {
            consumeLaunchParams();
            if (!opened) {
                queueEditorSummaryPublish({ force: true, delayMs: 40 });
            }
        }
    }

    async function saveSessionDetails(options = {}) {
        const { quiet = false } = options;
        await ensureProject();
        const payload = collectSessionDetails();
        try {
            const data = await api("/api/reel/set-session", {
                method: "POST",
                body: JSON.stringify({
                    project_id: projectId,
                    ...payload,
                }),
            });
            if (data.error) {
                if (!quiet) setStreamerStatus(data.error, { error: true });
                return null;
            }
            const project = {
                ...(recentProjects.find((item) => item.project_id === projectId) || {}),
                stream_session: data.stream_session || {},
                marker_defaults: data.marker_defaults || {},
                facecam_layout: data.facecam_layout || payload.facecam_layout,
            };
            populateSessionDetails(project);
            if (payload.channel_name && payload.facecam_layout?.enabled) {
                rememberFacecamLayoutForChannel(payload.channel_name, payload.facecam_layout);
            }
            await loadRecentProjects();
            if (!quiet) {
                setStreamerStatus("Session details saved. Import marker timestamps when you are ready.");
            }
            return data;
        } catch (e) {
            if (!quiet) setStreamerStatus(e.message || "Failed to save session details.", { error: true });
            return null;
        }
    }

    async function importStreamMarkers() {
        hideError("reel-step1-error");
        if (!projectId && !($("reel-url-input")?.value.trim() || localVodFile || vodUrl)) {
            showError("reel-step1-error", "Load the stream VOD first, then import marker timestamps.");
            return;
        }
        await ensureProject();
        await saveSessionDetails({ quiet: true });

        const markersText = $("reel-stream-markers")?.value || "";
        if (!markersText.trim()) {
            setStreamerStatus("Paste one marker timestamp per line first.", { error: true });
            return;
        }

        try {
            const sessionDetails = collectSessionDetails();
            const data = await api("/api/reel/import-stream-markers", {
                method: "POST",
                body: JSON.stringify({
                    project_id: projectId,
                    markers_text: markersText,
                    pre_roll: sessionDetails.pre_roll,
                    post_roll: sessionDetails.post_roll,
                }),
            });
            if (data.error) {
                setStreamerStatus(data.error, { error: true });
                return;
            }

            sourceMoments = data.moments || [];
            const currentIds = new Set(getClipRows().map((row) => row.dataset.clipId));
            (data.imported_clips || []).forEach((clip) => {
                if (!currentIds.has(clip.id)) {
                    renderClipItem(clip);
                    currentIds.add(clip.id);
                }
            });
            if (Array.isArray(data.stream_markers)) {
                populateSessionDetails({
                    stream_session: {
                        platform: sessionDetails.platform,
                        channel_name: sessionDetails.channel_name,
                        game_title: sessionDetails.game_title,
                        session_label: sessionDetails.session_label,
                        session_date: sessionDetails.session_date,
                        notes: sessionDetails.notes,
                    },
                    marker_defaults: {
                        pre_roll: sessionDetails.pre_roll,
                        post_roll: sessionDetails.post_roll,
                    },
                    stream_markers: data.stream_markers,
                });
            }

            if ((data.imported_count || 0) > 0) {
                enableStep(2);
                show("reel-clip-actions");
            }
            setSourceMomentStatus(data.message || `Imported ${(data.imported_count || 0)} stream marker clip(s).`);
            setStreamerStatus(data.message || "Stream marker clips imported.");
            const summary = summarizeSourceMoments(sourceMoments).slice(0, 3).join(" · ");
            setSessionInboxStatus(summary ? `${sourceMoments.length} session moments ready. ${summary}.` : "Stream markers imported into the session inbox.");
            await loadRecentProjects();
            await loadProjectAssets();
            updateToolbarState();
        } catch (e) {
            setStreamerStatus(e.message || "Failed to import stream markers.", { error: true });
        }
    }

    async function createLongformVersion() {
        hideError("reel-step4-error");
        if (!projectId) {
            showError("reel-step4-error", "Open a project first.");
            return;
        }
        if (projectRole === "longform") {
            showError("reel-step4-error", "This project is already the longform derivative. Reopen the shortform source project to rebuild it.");
            return;
        }

        const btn = $("reel-create-longform-btn");
        const previous = btn?.textContent || "Build Longform Project";
        if (btn) {
            btn.disabled = true;
            btn.textContent = "Creating...";
        }
        setWorkspace("output", { scroll: false, focusPanelId: "output" });
        setActiveTask("Building the linked longform project...", { source: "Output" });

        try {
            await saveSessionDetails({ quiet: true });
            const sourceProjectId = projectId;
            const data = await api("/api/reel/create-longform-version", {
                method: "POST",
                body: JSON.stringify({ project_id: projectId }),
            });
            if (data.error) {
                showError("reel-step4-error", data.error);
                return;
            }
            await loadRecentProjects();
            const selectedCount = Number(data.selected_clip_count || 0);
            const sourceCount = Number(data.source_clip_count || 0);
            pendingLongformProject = {
                projectId: data.project_id,
                sourceProjectId,
                message: selectedCount > 0
                    ? `Longform project ${data.project_id} was built from ${selectedCount} queued short${selectedCount === 1 ? "" : "s"} out of ${sourceCount || selectedCount} source clip${(sourceCount || selectedCount) === 1 ? "" : "s"}. Open it when you are ready to continue the horizontal edit.`
                    : "Longform project is ready. Open it when you are ready to continue the horizontal edit.",
            };
            renderLongformBuildUI();
            clearActiveTask();
            setStreamerStatus(
                selectedCount > 0
                    ? `Built longform project ${data.project_id} from ${selectedCount} queued prepared short${selectedCount === 1 ? "" : "s"} out of ${sourceCount || selectedCount} source clip${(sourceCount || selectedCount) === 1 ? "" : "s"}.`
                    : `Built longform project ${data.project_id} from the prepared shorts in this session.`,
            );
        } catch (e) {
            clearActiveTask();
            showError("reel-step4-error", e.message || "Failed to create longform version.");
        } finally {
            if (btn) {
                btn.disabled = false;
                btn.textContent = previous;
            }
        }
    }

    // ── Create project ────────────────────────────────────────────

    async function ensureProject() {
        if (projectId) return projectId;
        const data = await api("/api/reel/create-project", { method: "POST" });
        setProjectIdentity(data.project_id);
        await persistExportFormatPreset();
        await loadRecentProjects();
        return projectId;
    }

    // ── Validate video URL ────────────────────────────────────────

    async function validateUrl() {
        hideError("reel-step1-error");
        const url = $("reel-url-input").value.trim();
        if (!url) {
            showError("reel-step1-error", "Please enter a URL.");
            return false;
        }

        const btn = $("reel-validate-btn");
        btn.disabled = true;
        btn.textContent = "Loading...";
        setWorkspace("session", { scroll: false, focusPanelId: "session" });
        setActiveTask("Inspecting the source URL and loading stream metadata...", { source: "Ingest" });

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
                clearActiveTask();
                showError("reel-step1-error", data.error || "Invalid URL");
                return false;
            }

            vodUrl = url;
            vodDuration = Number(data.duration || 0);

            // Store video info in project
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
            $("reel-vod-duration").textContent = vodDuration > 0 ? formatTime(vodDuration) : "Unknown";
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
            clearActiveTask({
                message: `Loaded ${data.title || "source video"} into the session.`,
                source: "Ingest",
                tone: "success",
            });
            return true;
        } catch (e) {
            clearActiveTask();
            showError("reel-step1-error", e.message || "Validation failed");
            return false;
        } finally {
            btn.disabled = false;
            btn.textContent = "Load Video";
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
        setWorkspace("session", { scroll: false, focusPanelId: "session" });
        setActiveTask(`Loading local file ${file.name}...`, { source: "Ingest" });
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
            setSourceMomentStatus("Local video loaded. Source moment import is available for remote video URLs only.");

            if ($("reel-clip-list").children.length === 0) {
                await addClip();
            }
            clearActiveTask({
                message: `${file.name || "Local video"} is ready in the session workspace.`,
                source: "Ingest",
                tone: "success",
            });
        } catch (e) {
            clearActiveTask();
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
            showError("reel-step1-error", "Choose a local video file first.");
            return false;
        }

        await ensureProject();
        setActiveTask("Uploading the local source video for stitching and render...", { source: "Ingest" });
        const formData = new FormData();
        formData.append("project_id", projectId);
        formData.append("video", localVodFile);
        try {
            const data = await api("/api/reel/upload-vod", {
                method: "POST",
                body: formData,
            });

            if (data.error) {
                clearActiveTask();
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
            clearActiveTask({
                message: `${data.filename || localVodFile.name || "Local video"} uploaded and ready for stitching.`,
                source: "Ingest",
                tone: "success",
            });
            updateToolbarState();
            return true;
        } catch (e) {
            clearActiveTask();
            showError("reel-step1-error", e.message || "Failed to upload the local video.");
            return false;
        }
    }

    function renderClipItem(clip) {
        const container = $("reel-clip-list");
        const idx = container.children.length + 1;

        const div = document.createElement("div");
        div.className = "clip-item";
        div.dataset.clipId = clip.id;
        div.dataset.note = clip.note || "";
        div.dataset.sourceKind = clip.source_kind || "";
        div.dataset.fadeIn = clip.fade_in || 0;
        div.dataset.fadeOut = clip.fade_out || 0;
        div.dataset.shortPreset = clip.short_preset || "";
        div.dataset.compositionProfile = clip.composition_profile || "";
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
            <button class="clip-dupe-btn" onclick="ReelMaker.duplicateClip('${clip.id}')" title="Duplicate clip">⧉</button>
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

    async function renameProject(renameProjectId, newTitle) {
        const data = await api("/api/reel/rename", {
            method: "POST",
            body: JSON.stringify({ project_id: renameProjectId, title: newTitle }),
        });
        if (data.error) { alert(data.error); return; }
        await loadRecentProjects();
        if (renameProjectId === projectId) renderProjectChrome();
    }

    async function duplicateClip(clipId) {
        if (!projectId) return;
        const row = getClipRow(clipId);
        if (!row) return;
        const start = row.querySelector(".clip-start")?.value || "0:00";
        const end = row.querySelector(".clip-end")?.value || "0:10";
        const title = (row.querySelector(".clip-title")?.value || "").trim();
        const note = row.dataset.note || "";
        const data = await api("/api/reel/add-clip", {
            method: "POST",
            body: JSON.stringify({
                project_id: projectId,
                start,
                end,
                title: title ? `${title} (copy)` : "",
                note,
            }),
        });
        if (data.clip) {
            renderClipItem(data.clip);
        }
    }

    // ── Download & stitch ─────────────────────────────────────────

    async function downloadAllClips() {
        hideError("reel-step2-error");
        if (!projectId) return;
        setWorkspace("inbox", { scroll: false, focusPanelId: "inbox" });

        if (!(await ensureLocalVodUploaded())) {
            return;
        }

        const btn = $("reel-download-clips-btn");
        btn.disabled = true;
        show("reel-clip-actions");
        show("reel-download-status");
        resetExportState();
        setActiveTask("Preparing the current short stack for download and stitch...", { source: "Inbox" });

        try {
            const data = await api("/api/reel/download-clips", {
                method: "POST",
                body: JSON.stringify({ project_id: projectId }),
            });

            if (data.error) {
                clearActiveTask();
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
                    const progress = Math.round(Number(status.progress || 0));

                    $("reel-download-bar").style.width = `${status.progress || 0}%`;
                    $("reel-download-status-text").textContent = status.stage || "Processing...";
                    setActiveTask(
                        `${status.stage || "Processing clips..."}${progress > 0 ? ` (${progress}%)` : ""}`,
                        { source: "Inbox" },
                    );

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
                        clearActiveTask({
                            message: `Short sequence is ready. ${status.clips_downloaded} clip${Number(status.clips_downloaded || 0) === 1 ? "" : "s"} stitched${status.clips_failed > 0 ? `, ${status.clips_failed} failed` : ""}.`,
                            source: "Inbox",
                            tone: "success",
                        });

                        // Load preview
                        loadConcatPreview();
                        loadRecentProjects();
                        loadProjectAssets();
                        updateToolbarState();

                        // Auto-transcribe if checkbox is set
                        if ($("reel-auto-transcribe")?.checked) {
                            transcribe();
                        }
                    } else if (status.status === "error") {
                        clearInterval(pollTimer);
                        pollTimer = null;
                        btn.disabled = false;
                        clearActiveTask();
                        showError("reel-step2-error", status.error || "Download failed");
                    }
                } catch (e) {
                    // Polling error, keep trying
                }
            }, 1000);
        } catch (e) {
            clearActiveTask();
            showError("reel-step2-error", e.message || "Download failed");
            btn.disabled = false;
            hide("reel-download-status");
        }
    }

    // ── Preview ───────────────────────────────────────────────────

    function loadConcatPreview() {
        if (!projectId) return;
        loadPreview(`/api/reel/serve-concat/${projectId}`, "sequence");
        attachCaptionTrack();
        loadReelWaveform();
    }

    function loadReelWaveform() {
        if (!projectId) return;
        const lane = $("reel-sequence-audio-lane");
        if (!lane) return;
        const imgUrl = `/api/reel/waveform/${projectId}?t=${Date.now()}`;
        const img = new Image();
        img.onload = () => {
            lane.style.backgroundImage = `url('${imgUrl}')`;
            lane.style.backgroundSize = "100% 100%";
            lane.style.backgroundRepeat = "no-repeat";
            lane.style.backgroundPosition = "0 center";
        };
        img.onerror = () => {};
        img.src = imgUrl;

        // Click on waveform area to seek preview video
        if (!lane._waveformSeekBound) {
            lane._waveformSeekBound = true;
            lane.addEventListener("click", (e) => {
                // Ignore clicks on child elements (timeline segments/handles)
                if (e.target !== lane) return;
                const rect = lane.getBoundingClientRect();
                const pct = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width));
                const video = $("reel-preview-video");
                if (video && Number.isFinite(video.duration) && video.duration > 0) {
                    video.currentTime = pct * video.duration;
                }
            });
        }
    }

    function attachCaptionTrack() {
        const video = $("reel-preview-video");
        if (!video || !projectId) return;
        // Remove any existing caption tracks we added
        const existing = video.querySelector("track[data-caption-preview]");
        if (existing) existing.remove();
        const track = document.createElement("track");
        track.kind = "subtitles";
        track.label = "Captions";
        track.srclang = "en";
        track.src = `/api/reel/captions/${projectId}/vtt?t=${Date.now()}`;
        track.dataset.captionPreview = "1";
        track.default = true;
        video.appendChild(track);
        // Show subtitles once metadata loads (avoids race condition)
        video.addEventListener("loadedmetadata", () => {
            for (const t of video.textTracks) {
                if (t.label === "Captions") t.mode = "showing";
            }
        }, { once: true });
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
        renderPreviewCompositionBadge();
        renderFacecamGuide();
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
        setWorkspace("captions", { scroll: false, focusPanelId: "captions" });
        markExportDirty();

        const languageRaw = $("reel-caption-language").value;
        const language = languageRaw || null;  // "" → null triggers Whisper auto-detection
        const model = $("reel-whisper-model").value;
        const hfToken = $("reel-hf-token").value.trim();

        $("reel-transcribe-btn").disabled = true;
        show("reel-transcribe-progress");
        setActiveTask("Starting the caption pass on the stitched sequence...", { source: "Captions" });

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
                clearActiveTask();
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
                    const progress = Math.round(Number(status.progress || 0));
                    $("reel-transcribe-bar").style.width = `${status.progress || 0}%`;
                    $("reel-transcribe-status").textContent = status.stage || "Processing...";
                    setActiveTask(
                        `${status.stage || "Running caption pass..."}${progress > 0 ? ` (${progress}%)` : ""}`,
                        { source: "Captions" },
                    );

                    if (status.status === "complete") {
                        clearInterval(timer);
                        $("reel-transcribe-btn").disabled = false;
                        $("reel-transcribe-status").textContent =
                            `Done! ${status.word_count || 0} words, ${status.speaker_count || 1} speaker(s) detected.`;
                        clearActiveTask({
                            message: `Caption pass complete. ${status.word_count || 0} words and ${status.speaker_count || 1} speaker track${Number(status.speaker_count || 1) === 1 ? "" : "s"} ready.`,
                            source: "Captions",
                            tone: "success",
                        });

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
                        clearActiveTask();
                        showError("reel-step3-error", status.error || "Transcription failed");
                    }
                } catch (e) {
                    // Keep polling
                }
            }, 1000);
        } catch (e) {
            clearActiveTask();
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
        setWorkspace("output", { scroll: false, focusPanelId: "output" });

        const burnCaptions = $("reel-burn-captions")?.checked !== false;
        const btn = $("reel-export-btn");
        btn.disabled = true;
        show("reel-export-progress");
        hide("reel-export-download");
        setActiveTask("Queueing the export job for the current project...", { source: "Output" });

        try {
            const data = await api("/api/reel/export", {
                method: "POST",
                body: JSON.stringify({
                    project_id: projectId,
                    format_preset: exportFormatPreset,
                    burn_captions: burnCaptions,
                }),
            });

            if (data.error) {
                clearActiveTask();
                showError("reel-step4-error", data.error);
                btn.disabled = false;
                hide("reel-export-progress");
                return;
            }

            const jobId = data.job_id;
            if (data.composition_label) {
                $("reel-export-status").textContent = `Queued ${data.composition_label} export...`;
            }
            const timer = setInterval(async () => {
                try {
                    const status = await api(`/api/status/${jobId}`);
                    const progress = Math.round(Number(status.progress || 0));
                    $("reel-export-bar").style.width = `${status.progress || 0}%`;
                    $("reel-export-status").textContent = status.stage || "Processing...";
                    setActiveTask(
                        `${status.stage || "Rendering output..."}${progress > 0 ? ` (${progress}%)` : ""}`,
                        { source: "Output" },
                    );

                    if (status.status === "complete") {
                        clearInterval(timer);
                        btn.disabled = false;
                        exportedReelReady = true;
                        $("reel-export-status").textContent = status.stage || "Video export complete!";
                        const downloadDiv = $("reel-export-download");
                        if (downloadDiv && status.file_size_mb) {
                            downloadDiv.innerHTML = `<span class="info-label">File ready: ${status.file_size_mb} MB · ${status.filename || "video.mp4"}</span>`;
                        }
                        show("reel-export-download");
                        show("reel-export-download-btn");
                        clearActiveTask({
                            message: `Render complete. ${status.filename || "video.mp4"} is ready to download.`,
                            source: "Output",
                            tone: "success",
                        });
                        loadRecentProjects();
                        loadProjectAssets();
                        updateToolbarState();
                    } else if (status.status === "error") {
                        clearInterval(timer);
                        btn.disabled = false;
                        clearActiveTask();
                        showError("reel-step4-error", status.error || "Export failed");
                    }
                } catch (e) {
                    // Keep polling
                }
            }, 1000);
        } catch (e) {
            clearActiveTask();
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
        document.addEventListener("dm:auth-session", (event) => {
            const detail = event?.detail || {};
            const user = detail?.user || null;
            if (user) {
                applyAuthProfile(user, { overwrite: false, persist: Boolean(projectId) });
                queueEditorSummaryPublish({ force: true, delayMs: 40 });
                if (projectId) {
                    void syncEditorFeedIntoProject({ quiet: true });
                }
            }
            updateConnectedTwitchControls(detail);
        });
        document.addEventListener("dm:deps-status", (event) => {
            renderCaptionRuntimeCard(event?.detail || null);
        });
        initChromeMenus();
        $("reel-video-input")?.addEventListener("change", onLocalVideoSelect);
        $("reel-url-input")?.addEventListener("keydown", (event) => {
            if (event.key === "Enter") validateUrl();
        });
        $("reel-short-preset-select")?.addEventListener("change", (event) => {
            setShortformPreset(event.target.value);
            if (projectId) {
                setSessionInboxStatus(`Default short preset set to ${getShortPresetConfig().label}. Use Prep Short on any session moment to apply it.`);
            }
        });
        $("reel-burn-captions")?.addEventListener("change", () => {
            syncBurnCaptionsSummary();
            renderPreviewCompositionBadge();
            markExportDirty();
        });
        const channelField = $("reel-session-channel");
        channelField?.addEventListener("change", () => {
            if (!facecamLayout.enabled) {
                maybeApplyRememberedFacecamLayout(channelField.value, { persist: false });
            }
            renderBetaFlowStatus();
        });
        $("reel-facecam-enabled")?.addEventListener("change", () => {
            updateFacecamLayoutFromInputs();
            const channel = $("reel-session-channel")?.value.trim() || "";
            if (channel && facecamLayout.enabled) {
                rememberFacecamLayoutForChannel(channel, facecamLayout);
            }
            if (projectId) {
                void saveSessionDetails({ quiet: true });
            }
        });
        ["x", "y", "width", "height"].forEach((field) => {
            $(`reel-facecam-${field}`)?.addEventListener("input", updateFacecamLayoutFromInputs);
            $(`reel-facecam-${field}`)?.addEventListener("change", () => {
                updateFacecamLayoutFromInputs();
                const channel = $("reel-session-channel")?.value.trim() || "";
                if (channel && facecamLayout.enabled) {
                    rememberFacecamLayoutForChannel(channel, facecamLayout);
                }
                if (projectId) {
                    void saveSessionDetails({ quiet: true });
                }
            });
        });
        $("reel-auth-vod-select")?.addEventListener("change", () => {
            connectedTwitchClips = [];
            renderConnectedTwitchClips([]);
            const authState = typeof DmAuth !== "undefined" && typeof DmAuth.getState === "function"
                ? DmAuth.getState()
                : null;
            if (authState?.user) {
                setConnectedClipStatus("Selected VOD changed. Load Twitch clips for this archive.");
            }
            updateConnectedTwitchControls(authState);
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
        $("reel-copy-timecode-btn")?.addEventListener("click", () => {
            const range = $("reel-inspector-range")?.value || "";
            if (range) {
                navigator.clipboard?.writeText(range).catch(() => {});
                const btn = $("reel-copy-timecode-btn");
                if (btn) { btn.textContent = "Copied!"; setTimeout(() => { btn.textContent = "Copy"; }, 1200); }
            }
        });
        $("reel-inspector-jump-btn")?.addEventListener("click", jumpToActiveClip);
        $("reel-inspector-start-btn")?.addEventListener("click", async () => {
            if (activeClipId) await capturePreviewTime(activeClipId, "start");
        });
        $("reel-inspector-end-btn")?.addEventListener("click", async () => {
            if (activeClipId) await capturePreviewTime(activeClipId, "end");
        });

        const saveFadeField = (field) => {
            const row = getActiveClipRow();
            if (!row || !activeClipId) return;
            const apiKey = field === "in" ? "fade_in" : "fade_out";
            const dataKey = field === "in" ? "fadeIn" : "fadeOut";
            const value = parseFloat($(`reel-inspector-fade-${field}`)?.value || "0") || 0;
            row.dataset[dataKey] = value;
            api("/api/reel/update-clip", {
                method: "POST",
                body: JSON.stringify({ project_id: projectId, clip_id: activeClipId, [apiKey]: value }),
            });
        };
        $("reel-inspector-fade-in")?.addEventListener("change", () => saveFadeField("in"));
        $("reel-inspector-fade-out")?.addEventListener("change", () => saveFadeField("out"));
        $("reel-preview-video")?.addEventListener("loadedmetadata", renderTimeline);
        $("reel-preview-video")?.addEventListener("loadedmetadata", renderSequenceTimeline);
        $("reel-preview-video")?.addEventListener("loadedmetadata", renderFacecamGuide);
        $("reel-preview-video")?.addEventListener("timeupdate", () => {
            renderPreviewTimelines();
            const video = $("reel-preview-video");
            const tc = $("reel-preview-timecode");
            if (tc && video) {
                tc.textContent = formatTimestamp(video.currentTime);
                tc.classList.remove("hidden");
            }
        });
        $("reel-preview-video")?.addEventListener("seeked", renderPreviewTimelines);
        $("reel-preview-video")?.addEventListener("seeked", renderFacecamGuide);
        $("reel-facecam-guide")?.addEventListener("pointerdown", (event) => {
            if (event.target?.id === "reel-facecam-guide-handle") return;
            startFacecamGuideDrag(event, "move");
        });
        $("reel-facecam-guide-handle")?.addEventListener("pointerdown", (event) => startFacecamGuideDrag(event, "resize"));
        $("reel-timeline-track")?.addEventListener("click", onTimelinePointer);
        $("reel-sequence-track")?.addEventListener("click", onSequencePointer);
        window.addEventListener("pointermove", onTimelineDragMove);
        window.addEventListener("pointerup", onTimelineDragEnd);
        window.addEventListener("pointercancel", onTimelineDragEnd);
        window.addEventListener("pointermove", onFacecamGuideDragMove);
        window.addEventListener("pointerup", onFacecamGuideDragEnd);
        window.addEventListener("pointercancel", onFacecamGuideDragEnd);
        window.addEventListener("resize", renderFacecamGuide);
        $("reel-timeline-zoom")?.addEventListener("input", (event) => {
            setTimelineZoom(event.target.value);
        });
        document.addEventListener("keydown", handlePreviewShortcut);
        setupFileDropZone();
        updateExportFormatUI();
        updateShortPresetSummary();
        if (typeof App !== "undefined" && typeof App.getDependencySnapshot === "function") {
            renderCaptionRuntimeCard(App.getDependencySnapshot());
        } else {
            renderCaptionRuntimeCard(null);
        }
        resetEditorState({ forgetProject: false });
        await loadRecentProjects();
        await restoreLastProject();
        await consumeOpenNowLaunch();
        if (typeof DmAuth !== "undefined" && typeof DmAuth.getState === "function") {
            const authState = DmAuth.getState();
            if (authState?.user) {
                applyAuthProfile(authState.user, { overwrite: false, persist: false });
            }
            updateConnectedTwitchControls(authState);
        }
        updateToolbarState();
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", init);
    } else {
        init();
    }

    // ── Detect moments ────────────────────────────────────────────

    async function detectMoments() {
        if (!projectId) return;
        setWorkspace("inbox", { scroll: false, focusPanelId: "inbox" });
        const statusEl = $("reel-detect-moments-status");
        const btn = $("reel-detect-moments-btn");
        show("reel-clip-actions");
        if (statusEl) { statusEl.textContent = "Detecting..."; statusEl.classList.remove("hidden"); }
        if (btn) btn.disabled = true;
        setActiveTask("Scanning the source for highlight candidates...", { source: "Inbox" });

        try {
            const data = await api("/api/reel/detect-moments", {
                method: "POST",
                body: JSON.stringify({ project_id: projectId }),
            });
            if (data.error) {
                clearActiveTask();
                if (statusEl) statusEl.textContent = data.error;
                if (btn) btn.disabled = false;
                return;
            }
            const jobId = data.job_id;
            const poll = setInterval(async () => {
                const status = await api(`/api/status/${jobId}`);
                const progress = Math.round(Number(status.progress || 0));
                setActiveTask(
                    `${status.stage || "Detecting moments..."}${progress > 0 ? ` (${progress}%)` : ""}`,
                    { source: "Inbox" },
                );
                if (statusEl) {
                    statusEl.textContent = status.stage || "Detecting...";
                }
                if (status.status === "complete") {
                    clearInterval(poll);
                    if (btn) btn.disabled = false;
                    const moments = status.moments || [];
                    if (moments.length === 0) {
                        clearActiveTask({
                            message: "No distinct moments were detected in this source.",
                            source: "Inbox",
                            tone: "info",
                        });
                        if (statusEl) statusEl.textContent = "No distinct moments detected.";
                        return;
                    }
                    // Import moments as clips
                    for (const m of moments) {
                        const start = m.start;
                        const end = m.end;
                        const newClipData = await api("/api/reel/add-clip", {
                            method: "POST",
                            body: JSON.stringify({
                                project_id: projectId,
                                start: formatTimestamp(start),
                                end: formatTimestamp(end),
                                title: `Auto ${formatTimestamp(start)}`,
                            }),
                        });
                        if (newClipData && newClipData.clip) {
                            renderClipItem(newClipData.clip);
                        }
                    }
                    clearActiveTask({
                        message: `Added ${moments.length} detected moment${moments.length === 1 ? "" : "s"} into the inbox.`,
                        source: "Inbox",
                        tone: "success",
                    });
                    if (statusEl) statusEl.textContent = `Added ${moments.length} moment(s) as clips.`;
                    updateToolbarState();
                } else if (status.status === "error") {
                    clearInterval(poll);
                    if (btn) btn.disabled = false;
                    clearActiveTask();
                    if (statusEl) statusEl.textContent = `Error: ${status.error}`;
                }
            }, 1000);
        } catch (e) {
            clearActiveTask();
            if (statusEl) statusEl.textContent = `Error: ${e.message}`;
            if (btn) btn.disabled = false;
        }
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
        setExportFormatPreset,
        markExportDirty,
        capturePreviewTime,
        resumeProject,
        reloadRecentProjects: loadRecentProjects,
        refreshAssets: loadProjectAssets,
        startNewProject,
        openLocalVodPicker,
        getProjectId: () => projectId,
        saveSessionDetails,
        loadConnectedTwitchVideos,
        loadSelectedConnectedTwitchVod,
        loadConnectedTwitchClips,
        importStreamMarkers,
        importMarkersFromConnectedVod,
        importClipsFromConnectedVod,
        prepareSourceMomentAsShort,
        prepareActiveClipAsShort,
        bulkPrepareShorts,
        setLongformQueueForPrepared,
        setFacecamPreset,
        createLongformVersion,
        focusStep,
        setWorkspace,
        focusPanel,
        toggleWorkspacePanel,
        resetWorkspacePanels,
        focusSourceInput,
        openPendingLongformProject,
        dismissLongformHandoff,
        applyAuthProfile,
        detectMoments,
        attachCaptionTrack,
        duplicateClip,
        toggleMenu,
        closeMenus,
    };
})();
