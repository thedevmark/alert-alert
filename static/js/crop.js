/**
 * Interactive crop preview component.
 * Displays a video frame with a draggable, resizable overlay.
 * Supports multiple aspect ratios: original, 1:1, 16:9, 9:16, 4:3
 * The area outside the crop is darkened.
 * Zoom slider controls the crop size (smaller = more zoomed in).
 */
class CropPreview {
    constructor() {
        this.container = document.getElementById("crop-container");
        this.video = document.getElementById("crop-video");
        this.image = document.getElementById("crop-image");
        this.overlay = document.getElementById("crop-overlay");
        this.coordsDisplay = document.getElementById("crop-coords");
        this.zoomSlider = document.getElementById("zoom-slider");
        this.zoomValue = document.getElementById("zoom-value");
        this.ratioButtons = document.getElementById("ratio-buttons");
        this.activeMedia = this.video;
        this.resizeLocked = false;
        this.lockedCropParams = null;
        this.resizeObserver = null;

        // Controls
        this.playBtn = document.getElementById("preview-play-btn");
        this.muteBtn = document.getElementById("preview-mute-btn");

        this.videoWidth = 0;
        this.videoHeight = 0;
        this.displayWidth = 0;
        this.displayHeight = 0;
        this.displayOffsetX = 0;
        this.displayOffsetY = 0;
        this.scale = 1;

        // Aspect ratio (width:height) - default original source ratio
        this.aspectRatio = 1; // width / height
        this.ratioLabel = "original";

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
        this._onObservedResize = this._onObservedResize.bind(this);

        this._setupEvents();
        this._setupMediaControls();

        // Handle window resize
        window.addEventListener("resize", this._onResize);

        // Handle layout shifts (scrollbar appearance/content expansion) that can move media
        // without triggering a window resize.
        if (typeof ResizeObserver !== "undefined") {
            this.resizeObserver = new ResizeObserver(this._onObservedResize);
            this.resizeObserver.observe(this.container);
            this.resizeObserver.observe(this.video);
            if (this.image) this.resizeObserver.observe(this.image);
        }
    }

    _onResize() {
        if (this.videoWidth <= 0) return;
        requestAnimationFrame(() => {
            const metrics = this._syncDisplayMetrics();
            if (metrics.width <= 0) return;
            if (this.resizeLocked) {
                this._applyLockedCropToDisplay();
                return;
            }
            this._applyZoom();
        });
    }

    _onObservedResize() {
        if (this.videoWidth <= 0) return;
        if (this.videoWidth > 0) {
            requestAnimationFrame(() => {
                const metrics = this._syncDisplayMetrics();
                if (metrics.width <= 0) return;
                if (this.resizeLocked) {
                    this._applyLockedCropToDisplay();
                    return;
                }
                this._applyZoom();
            });
        }
    }

    setResizeLock(locked) {
        this.resizeLocked = !!locked;
        if (this.resizeLocked) {
            this.lockedCropParams = this.getCropParams();
            this._applyLockedCropToDisplay();
        } else {
            this.lockedCropParams = null;
        }
    }

    realignLockedOverlay() {
        if (!this.resizeLocked || !this.lockedCropParams) return;
        this._applyLockedCropToDisplay();
    }

    _applyLockedCropToDisplay() {
        if (!this.lockedCropParams) return;

        const metrics = this._syncDisplayMetrics();
        const scale = metrics.scale || 1;
        if (metrics.width <= 0 || metrics.height <= 0 || scale <= 0) return;

        const cropWidth = this.lockedCropParams.width * scale;
        const cropHeight = this.lockedCropParams.height * scale;

        const minX = metrics.offsetX;
        const minY = metrics.offsetY;
        const maxX = metrics.offsetX + metrics.width - cropWidth;
        const maxY = metrics.offsetY + metrics.height - cropHeight;
        const desiredX = metrics.offsetX + (this.lockedCropParams.x * scale);
        const desiredY = metrics.offsetY + (this.lockedCropParams.y * scale);
        const clampedX = Math.max(minX, Math.min(maxX, desiredX));
        const clampedY = Math.max(minY, Math.min(maxY, desiredY));

        this.overlay.style.width = cropWidth + "px";
        this.overlay.style.height = cropHeight + "px";
        this.overlay.style.left = clampedX + "px";
        this.overlay.style.top = clampedY + "px";
        this._updateCoords();
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
        if (this.image) {
            this.image.classList.add("hidden");
            this.image.src = "";
        }
        this.overlay.classList.add("hidden");
        this.video.pause();
        this.video.src = "";
        this.activeMedia = this.video;
        if (this.playBtn) this.playBtn.textContent = "▶ Play";
    }

    /**
     * Initialize with actual video dimensions.
     * Call after the preview video has loaded.
     */
    initialize(videoWidth, videoHeight) {
        this.activeMedia = this.video;
        this.videoWidth = videoWidth;
        this.videoHeight = videoHeight;
        if (this.ratioLabel === "original") {
            this.aspectRatio = this._resolveOriginalAspectRatio();
        }

        // Show video and overlay
        this.video.classList.remove("hidden");
        if (this.image) this.image.classList.add("hidden");
        this.overlay.classList.remove("hidden");

        // Reset controls
        this.video.currentTime = 0;
        this.video.play().catch(() => { }); // Auto-play if possible
        if (this.playBtn) this.playBtn.textContent = "⏸ Pause";

        // Wait for render layout - use double RAF to ensure layout is complete
        requestAnimationFrame(() => {
            requestAnimationFrame(() => {
                this._syncDisplayMetrics();

                // Apply current zoom and ratio
                this._applyZoom();
                // Ensure overlay is visible (hidden class already removed above)
                this.overlay.classList.remove("hidden");
            });
        });
    }

    /**
     * Initialize crop preview using a static image.
     */
    initializeImage(imageUrl, imageWidth = 0, imageHeight = 0) {
        if (!this.image) return;

        this.activeMedia = this.image;
        this.video.pause();
        this.video.classList.add("hidden");

        this.image.classList.remove("hidden");
        this.overlay.classList.remove("hidden");

        const finalize = () => {
            this.videoWidth = imageWidth || this.image.naturalWidth || 1;
            this.videoHeight = imageHeight || this.image.naturalHeight || 1;
            if (this.ratioLabel === "original") {
                this.aspectRatio = this._resolveOriginalAspectRatio();
            }
            this._syncDisplayMetrics();
            this._applyZoom();
            this.overlay.classList.remove("hidden");
        };

        if (this.image.src !== imageUrl) {
            this.image.onload = () => {
                finalize();
            };
            this.image.src = imageUrl;
        } else {
            finalize();
        }
    }

    /**
     * Measure the rendered video area inside the container.
     * Needed because object-fit/centering introduces letterbox offsets.
     */
    _syncDisplayMetrics() {
        const media = this.activeMedia || this.video;
        const mediaRect = media.getBoundingClientRect();
        const containerRect = this.container.getBoundingClientRect();

        const width = mediaRect.width || this.displayWidth || this.videoWidth;
        const height = mediaRect.height || this.displayHeight || this.videoHeight;

        const rawOffsetX = mediaRect.left - containerRect.left;
        const rawOffsetY = mediaRect.top - containerRect.top;
        const offsetX = Number.isFinite(rawOffsetX) ? rawOffsetX : this.displayOffsetX;
        const offsetY = Number.isFinite(rawOffsetY) ? rawOffsetY : this.displayOffsetY;

        if (width > 0) this.displayWidth = width;
        if (height > 0) this.displayHeight = height;
        this.displayOffsetX = offsetX || 0;
        this.displayOffsetY = offsetY || 0;
        this.scale = this.videoWidth > 0 ? (this.displayWidth / this.videoWidth) : 1;

        return {
            width: this.displayWidth,
            height: this.displayHeight,
            offsetX: this.displayOffsetX,
            offsetY: this.displayOffsetY,
            scale: this.scale,
        };
    }

    /**
     * Returns crop parameters in original video pixel coordinates.
     */
    getCropParams() {
        if (this.resizeLocked && this.lockedCropParams) {
            return {
                x: this.lockedCropParams.x,
                y: this.lockedCropParams.y,
                width: this.lockedCropParams.width,
                height: this.lockedCropParams.height,
                ratio: this.lockedCropParams.ratio || this.ratioLabel,
            };
        }

        const metrics = this._syncDisplayMetrics();
        const currentScale = metrics.scale || 1;
        const rawCropDisplayWidth = parseFloat(this.overlay.style.width);
        const rawCropDisplayHeight = parseFloat(this.overlay.style.height);
        const rawDisplayX = parseFloat(this.overlay.style.left);
        const rawDisplayY = parseFloat(this.overlay.style.top);

        const cropDisplayWidth = Number.isFinite(rawCropDisplayWidth) ? rawCropDisplayWidth : 0;
        const cropDisplayHeight = Number.isFinite(rawCropDisplayHeight) ? rawCropDisplayHeight : 0;
        const displayX = (Number.isFinite(rawDisplayX) ? rawDisplayX : metrics.offsetX) - metrics.offsetX;
        const displayY = (Number.isFinite(rawDisplayY) ? rawDisplayY : metrics.offsetY) - metrics.offsetY;

        return {
            x: Math.round(Math.max(0, displayX) / currentScale),
            y: Math.round(Math.max(0, displayY) / currentScale),
            width: Math.round(cropDisplayWidth / currentScale),
            height: Math.round(cropDisplayHeight / currentScale),
            ratio: this.ratioLabel,
        };
    }

    /**
     * Set aspect ratio from string like "1:1", "16:9", etc.
     */
    setAspectRatio(ratioStr) {
        if (ratioStr === "original") {
            this.aspectRatio = this._resolveOriginalAspectRatio();
            this.ratioLabel = "original";
            if (this.displayWidth > 0) {
                this._applyZoom();
            }
            return;
        }

        const [w, h] = ratioStr.split(":").map(Number);
        if (!w || !h) return;
        this.aspectRatio = w / h;
        this.ratioLabel = ratioStr;

        if (this.displayWidth > 0) {
            this._applyZoom();
        }
    }

    _resolveOriginalAspectRatio() {
        const w = Number(this.videoWidth) || 1;
        const h = Number(this.videoHeight) || 1;
        if (w <= 0 || h <= 0) return 1;
        return w / h;
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
        // Keep prior display metrics so center can be preserved proportionally
        // when the preview area resizes (instead of drifting toward top-left).
        const prevDisplayWidth = this.displayWidth;
        const prevDisplayHeight = this.displayHeight;
        const prevOffsetX = this.displayOffsetX;
        const prevOffsetY = this.displayOffsetY;
        const rawOldWidth = parseFloat(this.overlay.style.width);
        const rawOldHeight = parseFloat(this.overlay.style.height);
        const rawOldX = parseFloat(this.overlay.style.left);
        const rawOldY = parseFloat(this.overlay.style.top);

        const metrics = this._syncDisplayMetrics();
        const displayWidth = metrics.width;
        const displayHeight = metrics.height;
        const offsetX = metrics.offsetX;
        const offsetY = metrics.offsetY;

        if (displayWidth === 0 || displayHeight === 0) return;

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

        // Get old position to maintain center (as percentage of previous display)
        const oldWidth = Number.isFinite(rawOldWidth) ? rawOldWidth : cropWidth;
        const oldHeight = Number.isFinite(rawOldHeight) ? rawOldHeight : cropHeight;
        let centerXPct = 0.5;
        let centerYPct = 0.5;

        if (Number.isFinite(rawOldX) && Number.isFinite(rawOldY) && prevDisplayWidth > 0 && prevDisplayHeight > 0) {
            const oldXPrev = rawOldX - prevOffsetX;
            const oldYPrev = rawOldY - prevOffsetY;
            centerXPct = (oldXPrev + oldWidth / 2) / prevDisplayWidth;
            centerYPct = (oldYPrev + oldHeight / 2) / prevDisplayHeight;
        } else if (Number.isFinite(rawOldX) && Number.isFinite(rawOldY) && displayWidth > 0 && displayHeight > 0) {
            const oldX = rawOldX - offsetX;
            const oldY = rawOldY - offsetY;
            centerXPct = (oldX + oldWidth / 2) / displayWidth;
            centerYPct = (oldY + oldHeight / 2) / displayHeight;
        }

        centerXPct = Math.max(0, Math.min(1, centerXPct));
        centerYPct = Math.max(0, Math.min(1, centerYPct));

        const newCenterX = centerXPct * displayWidth;
        const newCenterY = centerYPct * displayHeight;
        let newX = newCenterX - cropWidth / 2;
        let newY = newCenterY - cropHeight / 2;

        // Clamp to bounds
        const maxX = displayWidth - cropWidth;
        const maxY = displayHeight - cropHeight;
        newX = Math.max(0, Math.min(maxX, newX));
        newY = Math.max(0, Math.min(maxY, newY));

        this.overlay.style.width = cropWidth + "px";
        this.overlay.style.height = cropHeight + "px";
        this.overlay.style.left = (offsetX + newX) + "px";
        this.overlay.style.top = (offsetY + newY) + "px";

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

        const metrics = this._syncDisplayMetrics();
        const displayWidth = metrics.width;
        const displayHeight = metrics.height;
        const offsetX = metrics.offsetX;
        const offsetY = metrics.offsetY;

        const cropWidth = parseFloat(this.overlay.style.width);
        const cropHeight = parseFloat(this.overlay.style.height);
        const minX = offsetX;
        const minY = offsetY;
        const maxX = offsetX + displayWidth - cropWidth;
        const maxY = offsetY + displayHeight - cropHeight;

        const newX = Math.max(minX, Math.min(maxX, this.overlayStartX + dx));
        const newY = Math.max(minY, Math.min(maxY, this.overlayStartY + dy));

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
