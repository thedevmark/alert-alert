# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
