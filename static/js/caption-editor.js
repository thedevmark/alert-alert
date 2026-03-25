/**
 * Caption Editor — interactive UI for editing auto-generated captions.
 * Allows toggling lines, editing text, reassigning speakers, and changing colors.
 */
const CaptionEditor = (() => {
    let projectId = "";
    let words = [];
    let lines = [];
    let speakers = {};
    let dirty = false;

    const $ = (id) => document.getElementById(id);

    function escapeHtml(str) {
        const div = document.createElement("div");
        div.textContent = str;
        return div.innerHTML;
    }

    function formatTime(seconds) {
        if (!seconds || seconds < 0) return "0:00";
        const m = Math.floor(seconds / 60);
        const s = Math.floor(seconds % 60);
        return `${m}:${s.toString().padStart(2, "0")}`;
    }

    // ── Init ──────────────────────────────────────────────────────

    async function init(pId) {
        projectId = pId;
        dirty = false;
        await loadCaptions();
    }

    async function loadCaptions() {
        try {
            const resp = await fetch(`/api/reel/captions/${projectId}`);
            const data = await resp.json();
            if (data.error) {
                console.error("Failed to load captions:", data.error);
                return;
            }
            words = data.words || [];
            speakers = data.speakers || {};
            lines = data.lines || groupWordsIntoLines(words);
            renderSpeakerPanel();
            renderLineList();
        } catch (e) {
            console.error("Failed to load captions:", e);
        }
    }

    // ── Group words into lines (mirror backend logic) ─────────────

    function groupWordsIntoLines(words, maxWords = 6) {
        const result = [];
        let current = [];

        const flushCurrent = () => {
            if (current.length === 0) return;
            result.push({
                words: [...current],
                speaker: current[0].speaker || "SPEAKER_0",
                start: current[0].start,
                end: current[current.length - 1].end,
                enabled: current[0].enabled !== false,
            });
            current = [];
        };

        for (const word of words) {
            if (current.length > 0) {
                const currentEnabled = current[0].enabled !== false;
                const wordEnabled = word.enabled !== false;
                const currentSpeaker = current[0].speaker || "SPEAKER_0";
                const wordSpeaker = word.speaker || "SPEAKER_0";
                if (currentEnabled !== wordEnabled || currentSpeaker !== wordSpeaker) {
                    flushCurrent();
                }
            }

            current.push(word);
            const text = word.text.trim();
            const isSentenceEnd = text.endsWith(".") || text.endsWith("!") || text.endsWith("?") || text.endsWith(",");
            if (current.length >= maxWords || isSentenceEnd) {
                flushCurrent();
            }
        }
        flushCurrent();
        return result;
    }

    // ── Render speaker panel ──────────────────────────────────────

    function renderSpeakerPanel() {
        const container = $("reel-speaker-panel");
        if (!container) return;
        container.innerHTML = "";

        for (const [id, data] of Object.entries(speakers)) {
            const badge = document.createElement("div");
            badge.className = "speaker-badge";
            badge.innerHTML = `
                <input type="color" value="${data.color}"
                       onchange="CaptionEditor.updateSpeakerColor('${escapeHtml(id)}', this.value)">
                <span class="speaker-color-dot" style="background:${data.color}" data-speaker="${escapeHtml(id)}"></span>
                <input type="text" value="${escapeHtml(data.name)}"
                       onchange="CaptionEditor.updateSpeakerName('${escapeHtml(id)}', this.value)">
            `;
            container.appendChild(badge);
        }
    }

    // ── Render caption lines ──────────────────────────────────────

    function renderLineList() {
        const container = $("reel-caption-lines");
        if (!container) return;
        container.innerHTML = "";

        lines.forEach((line, i) => {
            const el = document.createElement("div");
            el.className = `caption-line${line.enabled ? "" : " disabled"}`;
            el.dataset.lineIndex = i;

            const speakerColor = speakers[line.speaker]?.color || "#fff";

            // Build speaker dropdown
            const speakerOptions = Object.entries(speakers)
                .map(([id, s]) =>
                    `<option value="${escapeHtml(id)}" ${id === line.speaker ? "selected" : ""}>${escapeHtml(s.name)}</option>`
                )
                .join("");

            const lineText = line.words.map((w) => w.text).join(" ");

            el.innerHTML = `
                <input type="checkbox" ${line.enabled ? "checked" : ""}
                       onchange="CaptionEditor.toggleLine(${i})">
                <span class="line-time">${formatTime(line.start)}</span>
                <input type="text" class="line-text" value="${escapeHtml(lineText)}"
                       onchange="CaptionEditor.editLineText(${i}, this.value)">
                <select class="line-speaker" onchange="CaptionEditor.assignSpeaker(${i}, this.value)">
                    ${speakerOptions}
                </select>
                <span class="speaker-color-dot" style="background:${speakerColor}"></span>
            `;
            container.appendChild(el);
        });
    }

    // ── Actions ───────────────────────────────────────────────────

    function toggleLine(index) {
        if (index < 0 || index >= lines.length) return;
        lines[index].enabled = !lines[index].enabled;
        // Update the words in this line
        for (const word of lines[index].words) {
            word.enabled = lines[index].enabled;
        }
        renderLineList();
        markDirty();
    }

    function editLineText(index, newText) {
        if (index < 0 || index >= lines.length) return;
        const line = lines[index];
        const newWords = newText.split(/\s+/).filter(Boolean);
        if (newWords.length === 0) return;

        const totalDuration = line.end - line.start;
        const wordDuration = totalDuration / newWords.length;

        line.words = newWords.map((text, j) => ({
            text,
            start: line.start + j * wordDuration,
            end: line.start + (j + 1) * wordDuration,
            speaker: line.speaker,
            confidence: 1.0,
            enabled: line.enabled,
        }));
        markDirty();
    }

    function assignSpeaker(index, speakerId) {
        if (index < 0 || index >= lines.length) return;
        lines[index].speaker = speakerId;
        for (const word of lines[index].words) {
            word.speaker = speakerId;
        }
        renderLineList();
        markDirty();
    }

    function updateSpeakerColor(speakerId, color) {
        if (speakers[speakerId]) {
            speakers[speakerId].color = color;
            renderSpeakerPanel();
            renderLineList();
            markDirty();
        }
    }

    function updateSpeakerName(speakerId, name) {
        if (speakers[speakerId]) {
            speakers[speakerId].name = name;
            markDirty();
        }
    }

    function enableAll() {
        lines.forEach((line) => {
            line.enabled = true;
            line.words.forEach((w) => { w.enabled = true; });
        });
        renderLineList();
        markDirty();
    }

    function disableAll() {
        lines.forEach((line) => {
            line.enabled = false;
            line.words.forEach((w) => { w.enabled = false; });
        });
        renderLineList();
        markDirty();
    }

    function markDirty() {
        dirty = true;
    }

    // ── Save ──────────────────────────────────────────────────────

    async function save() {
        if (!projectId) return;

        // Flatten lines back to words
        const allWords = [];
        for (const line of lines) {
            for (const word of line.words) {
                allWords.push({
                    ...word,
                    enabled: line.enabled,
                });
            }
        }

        try {
            const resp = await fetch(`/api/reel/captions/${projectId}`, {
                method: "PUT",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ words: allWords, speakers }),
            });
            const data = await resp.json();
            if (data.error) {
                alert("Failed to save captions: " + data.error);
            } else {
                dirty = false;
            }
        } catch (e) {
            alert("Failed to save captions: " + e.message);
        }
    }

    // ── Public API ────────────────────────────────────────────────

    return {
        init,
        toggleLine,
        editLineText,
        assignSpeaker,
        updateSpeakerColor,
        updateSpeakerName,
        enableAll,
        disableAll,
        save,
        isDirty: () => dirty,
    };
})();
