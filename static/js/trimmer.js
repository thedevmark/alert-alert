/**
 * iPhone-style filmstrip video trimmer.
 * Generates thumbnails from video and provides draggable trim handles.
 */
class FilmstripTrimmer {
    constructor() {
        this.container = document.getElementById("filmstrip-container");
        this.track = document.getElementById("filmstrip-track");
        this.selection = document.getElementById("trim-selection");
        this.handleLeft = document.getElementById("trim-handle-left");
        this.handleRight = document.getElementById("trim-handle-right");
        this.overlayLeft = document.getElementById("trim-overlay-left");
        this.overlayRight = document.getElementById("trim-overlay-right");
        this.playhead = document.getElementById("playhead");

        this.startDisplay = document.getElementById("trim-start-val");
        this.endDisplay = document.getElementById("trim-end-val");
        this.durationDisplay = document.getElementById("trim-duration");

        this.video = null;
        this.duration = 0;
        this.trimStart = 0;  // in seconds
        this.trimEnd = 0;    // in seconds

        this.isDragging = null; // 'left', 'right', or null
        this.containerWidth = 0;

        this._onMouseMove = this._onMouseMove.bind(this);
        this._onMouseUp = this._onMouseUp.bind(this);
        this._onTouchMove = this._onTouchMove.bind(this);
        this._onTouchEnd = this._onTouchEnd.bind(this);
        this._onVideoTimeUpdate = this._onVideoTimeUpdate.bind(this);

        this._setupEvents();
    }

    _setupEvents() {
        // Left handle
        this.handleLeft.addEventListener("mousedown", (e) => {
            e.preventDefault();
            e.stopPropagation();
            this.isDragging = "left";
        });

        // Right handle
        this.handleRight.addEventListener("mousedown", (e) => {
            e.preventDefault();
            e.stopPropagation();
            this.isDragging = "right";
        });

        // Selection window (drag whole crop)
        this.selection.addEventListener("mousedown", (e) => {
            e.preventDefault();
            e.stopPropagation();
            this.isDragging = "move";
            this.dragStartX = e.clientX;
            this.dragStartTrimStart = this.trimStart;
            this.dragStartTrimEnd = this.trimEnd;
        });

        // Touch events
        this.handleLeft.addEventListener("touchstart", (e) => {
            e.preventDefault();
            e.stopPropagation();
            this.isDragging = "left";
        }, { passive: false });

        this.handleRight.addEventListener("touchstart", (e) => {
            e.preventDefault();
            e.stopPropagation();
            this.isDragging = "right";
        }, { passive: false });

        this.selection.addEventListener("touchstart", (e) => {
            e.preventDefault();
            e.stopPropagation();
            this.isDragging = "move";
            this.dragStartX = e.touches[0].clientX;
            this.dragStartTrimStart = this.trimStart;
            this.dragStartTrimEnd = this.trimEnd;
        }, { passive: false });

        // Document-level move/up handlers
        document.addEventListener("mousemove", this._onMouseMove);
        document.addEventListener("mouseup", this._onMouseUp);
        document.addEventListener("touchmove", this._onTouchMove, { passive: false });
        document.addEventListener("touchend", this._onTouchEnd);
    }


    _onMouseMove(e) {
        if (!this.isDragging) return;
        this._handleDrag(e.clientX);
    }

    _onTouchMove(e) {
        if (!this.isDragging) return;
        e.preventDefault();
        this._handleDrag(e.touches[0].clientX);
    }

    _onMouseUp() {
        this.isDragging = null;
    }

    _onTouchEnd() {
        this.isDragging = null;
    }

    _handleDrag(clientX) {
        const rect = this.container.getBoundingClientRect();

        if (this.isDragging === "move") {
            // Calculate delta in seconds
            const deltaX = clientX - this.dragStartX;
            const deltaPct = deltaX / rect.width;
            const deltaTime = deltaPct * this.duration;

            const currentDuration = this.dragStartTrimEnd - this.dragStartTrimStart;

            let newStart = this.dragStartTrimStart + deltaTime;
            let newEnd = this.dragStartTrimEnd + deltaTime;

            // Clamp to bounds
            if (newStart < 0) {
                newStart = 0;
                newEnd = currentDuration;
            }
            if (newEnd > this.duration) {
                newEnd = this.duration;
                newStart = this.duration - currentDuration;
            }

            this.trimStart = newStart;
            this.trimEnd = newEnd;

            // Sync video to start for preview
            if (this.video) {
                this.video.currentTime = this.trimStart;
            }

        } else {
            const x = Math.max(0, Math.min(rect.width, clientX - rect.left));
            const pct = x / rect.width;
            const time = pct * this.duration;

            if (this.isDragging === "left") {
                // Don't let left handle go past right handle - min 0.5s selection
                this.trimStart = Math.min(time, this.trimEnd - 0.5);
                this.trimStart = Math.max(0, this.trimStart);

                // Sync video to show the start frame
                if (this.video) this.video.currentTime = this.trimStart;
            } else if (this.isDragging === "right") {
                // Don't let right handle go before left handle
                this.trimEnd = Math.max(time, this.trimStart + 0.5);
                this.trimEnd = Math.min(this.duration, this.trimEnd);

                // Sync video to show the end frame
                if (this.video) this.video.currentTime = this.trimEnd;
            }
        }

        this._updateUI();
        this._dispatchChange();
    }

    _updateUI() {
        if (!this.duration) return;

        const leftPct = (this.trimStart / this.duration) * 100;
        const rightPct = (this.trimEnd / this.duration) * 100;

        // Update selection box position
        this.selection.style.left = leftPct + "%";
        this.selection.style.right = (100 - rightPct) + "%";

        // Update overlays
        this.overlayLeft.style.width = leftPct + "%";
        this.overlayRight.style.width = (100 - rightPct) + "%";

        // Update time displays
        this.startDisplay.textContent = this._formatTime(this.trimStart);
        this.endDisplay.textContent = this._formatTime(this.trimEnd);
        this.durationDisplay.textContent = "Duration: " + this._formatTime(this.trimEnd - this.trimStart);
    }

    _formatTime(seconds) {
        const m = Math.floor(seconds / 60);
        const s = Math.floor(seconds % 60);
        const ms = Math.floor((seconds % 1) * 10);
        return `${m}:${s.toString().padStart(2, "0")}.${ms}`;
    }

    // Updated to use timeupdate event instead of interval
    _onVideoTimeUpdate() {
        if (!this.video) return;

        const currentTime = this.video.currentTime;

        // Loop within trim region
        if (currentTime >= this.trimEnd) {
            // Only loop if we are playing
            if (!this.video.paused) {
                this.video.currentTime = this.trimStart;
            }
        }

        // Update playhead position
        // Map current time (0..duration) to percentage (0..100)
        let pct = (currentTime / this.duration) * 100;
        pct = Math.max(0, Math.min(100, pct));

        this.playhead.style.left = pct + "%";

        // Ensure playhead is visible even if outside selection (it should be)
    }

    _dispatchChange() {
        // Dispatch custom event for app.js to listen to
        window.dispatchEvent(new CustomEvent("trimchange", {
            detail: {
                start: this.trimStart,
                end: this.trimEnd
            }
        }));
    }

    /**
     * Initialize the trimmer with a video element.
     * @param {HTMLVideoElement} video - The video element to trim
     * @param {number} duration - Video duration in seconds
     */
    initialize(video, duration) {
        this.video = video;
        this.duration = duration;
        this.trimStart = 0;
        this.trimEnd = duration;
        this.containerWidth = this.container.getBoundingClientRect().width;

        // Register time update listener
        this.video.addEventListener("timeupdate", this._onVideoTimeUpdate);

        // Generate thumbnails
        this._generateThumbnails();

        // Reset UI
        this._updateUI();
    }

    /**
     * Generate thumbnail strip from video.
     */
    _generateThumbnails() {
        this.track.innerHTML = "";

        if (!this.video || !this.duration) return;

        const containerWidth = this.container.getBoundingClientRect().width;
        const thumbWidth = 50;
        const numThumbs = Math.ceil(containerWidth / thumbWidth);
        const interval = this.duration / numThumbs;

        // Create a temporary video for seeking
        const tempVideo = document.createElement("video");
        tempVideo.src = this.video.src;
        tempVideo.crossOrigin = "anonymous";
        tempVideo.muted = true;
        tempVideo.preload = "metadata";

        const canvas = document.createElement("canvas");
        const ctx = canvas.getContext("2d");
        canvas.width = thumbWidth;
        canvas.height = 56;

        let generated = 0;

        const generateNext = () => {
            if (generated >= numThumbs) {
                tempVideo.remove();
                return;
            }

            const time = generated * interval;
            tempVideo.currentTime = time;
        };

        tempVideo.addEventListener("seeked", () => {
            // Draw frame to canvas
            ctx.drawImage(tempVideo, 0, 0, canvas.width, canvas.height);

            // Create image from canvas
            const img = document.createElement("img");
            img.src = canvas.toDataURL("image/jpeg", 0.5);
            img.style.width = thumbWidth + "px";
            img.style.height = "56px";
            this.track.appendChild(img);

            generated++;
            generateNext();
        });

        tempVideo.addEventListener("loadedmetadata", () => {
            generateNext();
        });

        tempVideo.addEventListener("error", () => {
            // If thumbnails fail, show solid color
            this.track.style.background = "linear-gradient(90deg, #333 0%, #444 50%, #333 100%)";
        });
    }

    /**
     * Get current trim values.
     */
    getTrimValues() {
        return {
            start: this.trimStart,
            end: this.trimEnd
        };
    }

    /**
     * Reset the trimmer.
     */
    reset() {
        if (this.video) {
            this.video.removeEventListener("timeupdate", this._onVideoTimeUpdate);
        }
        this.video = null;
        this.duration = 0;
        this.trimStart = 0;
        this.trimEnd = 0;
        this.track.innerHTML = "";
    }
}

// Create global instance
const filmstripTrimmer = new FilmstripTrimmer();
