# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.3.1] - 2026-02-27

### Added
- **Dependency Download Consent Gate**: First-run dependency downloads now require explicit user permission.
  - Clear disclosure of install location and download sources before any auto-download begins.
  - New manual-only path with opt-in retry via **Auto Install Missing**.
- **Alternate Visual Source Support**: The visual override path now supports either an image or a video file.

### Changed
- **Dependency Setup UX**: Moved dependency setup into a top-right navbar dropdown for faster access.
- **Runtime Installer Behavior**: On Windows, dependency bootstrap now attempts optional `deno` by default (after consent) while keeping app functionality intact if `deno` install fails.
- **Audio/Visual Option Labels**:
  - `Use separate audio source` -> `Use different audio with this video`
  - `Use static image with audio only` -> `Use a different image or video with this audio`
- **Audio Controls Layout**: `Normalize` and `Fade Length` are now grouped with `Audio Fade` in Step 3.

### Fixed
- Corrected dependency guidance text to reference **Dependency Setup (top-right)** instead of Step 1.
- Improved processing validation and error messaging when alternate visual media is enabled but missing or unsupported.

## [1.2.0] - 2026-01-27

### Added
- **Draggable Trimmer Selection**: The trim selection window can now be dragged left and right to adjust the cropped segment's time position without changing its duration.
- **Playhead Sync**: The white playhead line now accurately follows the video playback in the trimmer interface.

### Changed
- Improved trimmer interaction for better precision.

## [1.1.0] - 2026-01-27

### Added
- **Separate Audio Source**: Optional feature to use audio from a different YouTube video
  - Toggle checkbox to enable/disable
  - Separate URL validation for audio source
  - Independent timestamp selection for audio
- Audio clip duration display

### Changed
- **Improved Audio Quality**: Complete rewrite of audio processing pipeline
  - Audio now extracted to lossless PCM WAV for all intermediate processing
  - Loudness normalization applied on PCM (no generation loss)
  - Audio encoded to AAC only once at final output stage
  - Increased final audio bitrate from 128kbps to 192kbps
- Restructured processing stages for cleaner pipeline

### Fixed
- Audio quality degradation from multiple lossy AAC re-encodings

## [1.0.0] - 2026-01-26

### Added
- Initial release
- YouTube video downloading with timestamp selection
- Interactive square crop preview with drag-to-position
- Zoom slider for crop size adjustment
- Frame scrubber for preview navigation
- EBU R128 loudness normalization (-16 LUFS)
- Automatic 2-second still frame buffer at end
- Progress tracking with stage indicators
- Dependency checking with helpful installation instructions
- Dark theme UI
