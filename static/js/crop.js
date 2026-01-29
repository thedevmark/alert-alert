/**
 * Interactive crop preview component.
 * Displays a video frame with a draggable, resizable overlay.
 * Supports multiple aspect ratios: 1:1, 16:9, 9:16, 4:3
 * The area outside the crop is darkened.
 * Zoom slider controls the crop size (smaller = more zoomed in).
 */
class CropPreview {
    constructor() {
        this.container = document.getElementById("crop-container");
        this.video = document.getElementById("crop-video");
        this.overlay = document.getElementById("crop-overlay");
        this.coordsDisplay = document.getElementById("crop-coords");
        this.zoomSlider = document.getElementById("zoom-slider");
        this.zoomValue = document.getElementById("zoom-value");
        this.ratioButtons = document.getElementById("ratio-buttons");

        // Controls
        this.playBtn = document.getElementById("preview-play-btn");
        this.muteBtn = document.getElementById("preview-mute-btn");

        this.videoWidth = 0;
        this.videoHeight = 0;
        this.displayWidth = 0;
        this.displayHeight = 0;
        this.scale = 1;

        // Aspect ratio (width:height) - default 1:1
        this.aspectRatio = 1; // width / height
        this.ratioLabel = "1:1";

        // Zoom: 100% = max size that fits, lower = smaller crop (more zoomed)
        this.zoomPct = 100;

        this.isDragging = false;
        this.dragStartX = 0;
        this.dragStartY = 0;
        this.overlayStartX = 0;
        this.overlayStartY = 0;

        this._onMouseMove = this._onMouseMove.bind(this);
        this._onMouseUp = this._onMouseUp.bind(this);
        this._onTouchMove = this._onTouchMove.bind(this);
        this._onTouchEnd = this._onTouchEnd.bind(this);
        this._onResize = this._onResize.bind(this);

        this._setupEvents();
        this._setupMediaControls();

        // Handle window resize
        window.addEventListener("resize", this._onResize);
    }

    _onResize() {
        if (this.videoWidth > 0) {
            // Recalculate display dimensions and reapply zoom
            requestAnimationFrame(() => {
                const rect = this.video.getBoundingClientRect();
                if (rect.width > 0) {
                    this.displayWidth = rect.width;
                    this.displayHeight = rect.height;
                    this.scale = this.displayWidth / this.videoWidth;
                    this._applyZoom();
                }
            });
        }
    }

    _setupMediaControls() {
        if (this.playBtn) {
            this.playBtn.addEventListener("click", () => {
                if (this.video.paused) {
                    this.video.play();
                    this.playBtn.textContent = "⏸ Pause";
                } else {
                    this.video.pause();
                    this.playBtn.textContent = "▶ Play";
                }
            });
        }

        if (this.muteBtn) {
            this.muteBtn.addEventListener("click", () => {
                this.video.muted = !this.video.muted;
                this.muteBtn.textContent = this.video.muted ? "🔇 Unmute" : "🔊 Mute";
            });
        }
    }

    /**
     * Clear preview state
     */
    reset() {
        this.video.classList.add("hidden");
        this.overlay.classList.add("hidden");
        this.video.pause();
        this.video.src = "";
        if (this.playBtn) this.playBtn.textContent = "▶ Play";
    }

    /**
     * Initialize with actual video dimensions.
     * Call after the preview video has loaded.
     */
    initialize(videoWidth, videoHeight) {
        this.videoWidth = videoWidth;
        this.videoHeight = videoHeight;

        // Show video and overlay
        this.video.classList.remove("hidden");
        this.overlay.classList.remove("hidden");

        // Reset controls
        this.video.currentTime = 0;
        this.video.play().catch(() => { }); // Auto-play if possible
        if (this.playBtn) this.playBtn.textContent = "⏸ Pause";

        // Wait for render layout - use double RAF to ensure layout is complete
        requestAnimationFrame(() => {
            requestAnimationFrame(() => {
                const rect = this.video.getBoundingClientRect();
                // Fallback to video dimensions if rect is 0 (hidden)
                this.displayWidth = rect.width || videoWidth;
                this.displayHeight = rect.height || videoHeight;

                if (this.displayWidth === 0) {
                    // Force a layout read if still 0
                    this.displayWidth = this.video.videoWidth;
                    this.displayHeight = this.video.videoHeight;
                }

                this.scale = this.displayWidth / this.videoWidth;

                // Don't set fixed container size - let it be dynamic
                // this.container.style.width = this.displayWidth + "px";
                // this.container.style.height = this.displayHeight + "px";

                // Apply current zoom and ratio
                this._applyZoom();
                // Ensure overlay is visible (hidden class already removed above)
                this.overlay.classList.remove("hidden");
            });
        });
    }

    /**
     * Returns crop parameters in original video pixel coordinates.
     */
    getCropParams() {
        // Recalculate scale based on current video display size
        const rect = this.video.getBoundingClientRect();
        const currentDisplayWidth = rect.width || this.videoWidth;
        const currentDisplayHeight = rect.height || this.videoHeight;
        const currentScale = currentDisplayWidth / this.videoWidth;

        const cropDisplayWidth = parseFloat(this.overlay.style.width);
        const cropDisplayHeight = parseFloat(this.overlay.style.height);
        const displayX = parseFloat(this.overlay.style.left) || 0;
        const displayY = parseFloat(this.overlay.style.top) || 0;

        return {
            x: Math.round(displayX / currentScale),
            y: Math.round(displayY / currentScale),
            width: Math.round(cropDisplayWidth / currentScale),
            height: Math.round(cropDisplayHeight / currentScale),
            ratio: this.ratioLabel,
        };
    }

    /**
     * Set aspect ratio from string like "1:1", "16:9", etc.
     */
    setAspectRatio(ratioStr) {
        const [w, h] = ratioStr.split(":").map(Number);
        this.aspectRatio = w / h;
        this.ratioLabel = ratioStr;

        if (this.displayWidth > 0) {
            this._applyZoom();
        }
    }

    _setupEvents() {
        // Mouse drag
        this.overlay.addEventListener("mousedown", (e) => {
            e.preventDefault();
            this._startDrag(e.clientX, e.clientY);
        });
        document.addEventListener("mousemove", this._onMouseMove);
        document.addEventListener("mouseup", this._onMouseUp);

        // Touch drag
        this.overlay.addEventListener("touchstart", (e) => {
            e.preventDefault();
            const touch = e.touches[0];
            this._startDrag(touch.clientX, touch.clientY);
        }, { passive: false });
        document.addEventListener("touchmove", this._onTouchMove, { passive: false });
        document.addEventListener("touchend", this._onTouchEnd);

        // Zoom slider
        if (this.zoomSlider) {
            this.zoomSlider.addEventListener("input", () => {
                this.zoomPct = parseInt(this.zoomSlider.value);
                if (this.zoomValue) {
                    this.zoomValue.textContent = this.zoomPct + "%";
                }
                if (this.displayWidth > 0) {
                    this._applyZoom();
                }
            });
        }

        // Ratio buttons
        if (this.ratioButtons) {
            this.ratioButtons.addEventListener("click", (e) => {
                if (e.target.classList.contains("ratio-btn")) {
                    // Update active state
                    this.ratioButtons.querySelectorAll(".ratio-btn").forEach(btn => {
                        btn.classList.remove("active");
                    });
                    e.target.classList.add("active");

                    // Apply ratio
                    const ratio = e.target.dataset.ratio;
                    this.setAspectRatio(ratio);
                }
            });
        }
    }

    _applyZoom() {
        // Get current video display dimensions
        const rect = this.video.getBoundingClientRect();
        const displayWidth = rect.width || this.displayWidth;
        const displayHeight = rect.height || this.displayHeight;

        if (displayWidth === 0 || displayHeight === 0) return;

        // Update cached values
        this.displayWidth = displayWidth;
        this.displayHeight = displayHeight;
        this.scale = displayWidth / this.videoWidth;

        // Calculate max crop dimensions that fit within the display area
        // while maintaining the aspect ratio
        let maxCropWidth, maxCropHeight;

        if (this.aspectRatio >= 1) {
            // Wide or square: width is limiting factor
            maxCropWidth = displayWidth;
            maxCropHeight = displayWidth / this.aspectRatio;
            if (maxCropHeight > displayHeight) {
                maxCropHeight = displayHeight;
                maxCropWidth = displayHeight * this.aspectRatio;
            }
        } else {
            // Tall: height is limiting factor
            maxCropHeight = displayHeight;
            maxCropWidth = displayHeight * this.aspectRatio;
            if (maxCropWidth > displayWidth) {
                maxCropWidth = displayWidth;
                maxCropHeight = displayWidth / this.aspectRatio;
            }
        }

        // Min size is 10% of max
        const minCropWidth = maxCropWidth * 0.1;
        const minCropHeight = maxCropHeight * 0.1;

        // Interpolate based on zoom percentage
        const cropWidth = minCropWidth + (maxCropWidth - minCropWidth) * (this.zoomPct / 100);
        const cropHeight = minCropHeight + (maxCropHeight - minCropHeight) * (this.zoomPct / 100);

        // Get old position to maintain center (as percentage of display)
        const oldWidth = parseFloat(this.overlay.style.width) || cropWidth;
        const oldHeight = parseFloat(this.overlay.style.height) || cropHeight;
        const oldX = parseFloat(this.overlay.style.left) || 0;
        const oldY = parseFloat(this.overlay.style.top) || 0;

        // Calculate center as percentage for more stable positioning
        const oldCenterX = oldX + oldWidth / 2;
        const oldCenterY = oldY + oldHeight / 2;

        let newX = oldCenterX - cropWidth / 2;
        let newY = oldCenterY - cropHeight / 2;

        // Clamp to bounds
        const maxX = displayWidth - cropWidth;
        const maxY = displayHeight - cropHeight;
        newX = Math.max(0, Math.min(maxX, newX));
        newY = Math.max(0, Math.min(maxY, newY));

        this.overlay.style.width = cropWidth + "px";
        this.overlay.style.height = cropHeight + "px";
        this.overlay.style.left = newX + "px";
        this.overlay.style.top = newY + "px";

        this._updateCoords();
    }

    _startDrag(clientX, clientY) {
        this.isDragging = true;
        this.dragStartX = clientX;
        this.dragStartY = clientY;
        this.overlayStartX = parseFloat(this.overlay.style.left) || 0;
        this.overlayStartY = parseFloat(this.overlay.style.top) || 0;
        this.overlay.classList.add("dragging");
    }

    _onMouseMove(e) {
        if (!this.isDragging) return;
        this._moveDrag(e.clientX, e.clientY);
    }

    _onMouseUp() {
        this._endDrag();
    }

    _onTouchMove(e) {
        if (!this.isDragging) return;
        e.preventDefault();
        const touch = e.touches[0];
        this._moveDrag(touch.clientX, touch.clientY);
    }

    _onTouchEnd() {
        this._endDrag();
    }

    _moveDrag(clientX, clientY) {
        const dx = clientX - this.dragStartX;
        const dy = clientY - this.dragStartY;

        // Get current video display dimensions
        const rect = this.video.getBoundingClientRect();
        const displayWidth = rect.width || this.displayWidth;
        const displayHeight = rect.height || this.displayHeight;

        const cropWidth = parseFloat(this.overlay.style.width);
        const cropHeight = parseFloat(this.overlay.style.height);
        const maxX = displayWidth - cropWidth;
        const maxY = displayHeight - cropHeight;

        const newX = Math.max(0, Math.min(maxX, this.overlayStartX + dx));
        const newY = Math.max(0, Math.min(maxY, this.overlayStartY + dy));

        this.overlay.style.left = newX + "px";
        this.overlay.style.top = newY + "px";

        this._updateCoords();
    }

    _endDrag() {
        if (!this.isDragging) return;
        this.isDragging = false;
        this.overlay.classList.remove("dragging");
    }

    _updateCoords() {
        const params = this.getCropParams();
        if (this.coordsDisplay) {
            this.coordsDisplay.textContent = `Crop: ${params.width}×${params.height} @ (${params.x}, ${params.y})`;
        }
    }
}
