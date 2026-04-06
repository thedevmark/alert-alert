# Photo Filter Reference Analysis

Scanned on 2026-04-05 from `D:\OneDrive\_Photo Library`.

## Matching Method

- I treated each top-level folder whose name starts with a date-like prefix (`YYYY-M-D`) as a candidate shoot.
- I looked for an immediate child folder named `RAW`, `raw`, `Raw`, or `unedited`.
- I treated root-level image files inside the shoot folder as edited candidates.
- I matched edited/root files to raw files by filename stem, ignoring case and extension.
- Supporting artifacts:
  - `temp/photo_filter_inventory.tsv`
  - `temp/photo_filter_metrics.json`
  - `temp/photo_filter_board.html`
  - `temp/photo_filter_server.js`

## Dataset Summary

- Qualifying folders with both root-level exports and a raw-like subfolder: `45`
- Root-level candidate edited images: `2,671`
- Files inside those raw-like folders: `3,302`
- Filename-stem matches found: `2,602`

### Year Breakdown

| Year | Folders | Root Images | Raw Files | Matches |
| --- | ---: | ---: | ---: | ---: |
| 2023 | 1 | 38 | 38 | 38 |
| 2024 | 20 | 1,121 | 1,364 | 1,073 |
| 2025 | 21 | 1,407 | 1,770 | 1,388 |
| 2026 | 3 | 105 | 130 | 103 |

### Largest Usable Matched Sets

| Folder | Matches | Root | Raw |
| --- | ---: | ---: | ---: |
| `2025-10-26 - Myrtle Towers` | 159 | 159 | 180 |
| `2025-07-09 - Myrtle Towers Party` | 159 | 159 | 246 |
| `2025-07-17 - Emma and James Wedding` | 154 | 154 | 201 |
| `2025-08-03 - August Bender` | 141 | 141 | 143 |
| `2024-06-07 - GOV BALL 2024` | 141 | 141 | 143 |
| `2024-07-22 - Brat Boat Weekend` | 130 | 130 | 154 |
| `2025-08-17 - Silo Alex` | 123 | 123 | 123 |
| `2025-08-24 - Church Basement` | 105 | 106 | 115 |
| `2024-05-26 - Miller High Life Night` | 99 | 102 | 104 |

### Pairing Caveats

- `2024-05-18 - NYC Kim Hoboken` matched well by filename, but spot-checks were byte-identical copies rather than meaningful edits. Do not use it as a primary look reference.
- `2024-09-07 - DJ Set NYC rooftop` has only `1` real match from `13` root images, so it is not reliable for pair analysis.
- `2024-10-26 - Gay Halloween` and `2025-07-20 - Rooftop Johnson Street` appear to mix multiple naming systems / sources; they are usable selectively, not as clean full-folder pair sets.

## Tooling Clue

- Sample 2024 edited exports report `software=DxO FilmPack 6`.
- Sample 2025-2026 edited exports report `software=DxO FilmPack 7`.
- This strongly suggests the look is coming from DxO FilmPack-style film rendering plus per-image tone work, not a single fixed LUT applied unchanged across every scene.

## Core Read On The Look

The look is not one rigid preset. It is a house taste.

- It consistently tries to make digital point-and-shoot images feel denser, less clinically neutral, and more memory-like.
- The edits are usually restrained. The vibe comes from small moves repeated consistently, not giant swings.
- The common thread is mood compression:
  - blacks get denser
  - whites lose some sterile digital neutrality
  - skin drifts peach/cream instead of flat neutral
  - practical lights and neon stay emotionally loud
  - scenes feel slightly less “accurate” and more “lived in”

## Measured Aggregate Delta

Across 9 representative before/after pairs:

- Average luminance delta: `-1.32`
- Average contrast delta: `+1.96`
- Average saturation delta: `+2.28` percentage points
- Average red delta: `-0.68`
- Average green delta: `-1.43`
- Average blue delta: `-2.14`
- Average shadow clip delta: `+1.94` percentage points
- Average highlight clip delta: `+0.24` percentage points

Interpretation:

- The edited images are, on average, a little darker.
- Contrast usually rises, but not always via aggressive global crunch.
- Blue tends to be reduced more than red, which creates a mild warm shift overall.
- Shadow clipping rises more than highlight clipping, which means the look is more comfortable crushing blacks than flattening highlights.

## Scene Families

### 1. Ambient / Daylight Film

This is the strongest recurring behavior in the 2024 ambient/daylight sets.

- Exposure generally comes down.
- Saturation often rises a little.
- Highlights are usually reined in.
- Blacks get denser and moodier.
- Blue skies and cool ambient light are slightly tamed so the image feels less digitally crisp.

Representative examples:

| Example | Luminance | Contrast | Saturation | Shadow Clip | Highlight Clip | Warmth Shift |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `2024-06-07 - GOV BALL 2024 / P1120444` | `-12.71` | `-3.20` | `+8.83` | `+5.65` | `-0.13` | `+3.89` |
| `2024-07-22 - Brat Boat Weekend / P1120888` | `-9.05` | `-1.56` | `+1.43` | `+0.06` | `-0.38` | `+3.93` |
| `2024-08-05 - Weeknight in Manhattan / f2719040` | `-6.30` | `-0.54` | `+2.99` | `+1.43` | `-0.01` | `+1.07` |

Visual read:

- The edit is not trying to “brighten the image into correctness.”
- It keeps the air in the shadows.
- It lets bright color accents carry the scene.

### 2. Flash / Event Lift

This is the recurring behavior in several 2025-2026 flash/event examples.

- Exposure often rises instead of falls.
- Contrast jumps harder.
- Saturation may hold steady or even come down.
- Specular flash highlights are allowed to pop more.
- The edit protects subject presence first, then adds taste.

Representative examples:

| Example | Luminance | Contrast | Saturation | Shadow Clip | Highlight Clip | Warmth Shift |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `2025-07-17 - Emma and James Wedding / P1140200` | `+11.87` | `+13.10` | `-2.31` | `-0.14` | `+2.71` | `+1.25` |
| `2025-08-17 - Silo Alex / P1140610` | `+13.82` | `+12.27` | `+3.31` | `-0.17` | `+0.72` | `-4.37` |
| `2026-01-01 - NYE in NY / P1150360` | `+3.92` | `+1.34` | `-5.00` | `-1.09` | `+0.00` | `+2.73` |

Visual read:

- This version does not force everything into warm nostalgia.
- It is happy to keep club light, flash haze, and colder nightlife color if that is what makes the frame feel real.
- The unifying trait is still “film mood,” but the route is subject separation and flash pop rather than underexposed ambient density.

## What The Filter Seems To Do

If I reduce it to the moves that keep showing up:

- Slight black-floor compression.
- Gentle shoulder / highlight softening in ambient scenes.
- Mild warm bias overall, usually by reducing blue more than red.
- Slight saturation bump for ambient scenes, especially where color accents matter.
- Willingness to lower global exposure rather than rescue every shadow.
- For flash-heavy images, more midtone lift and contrast, with saturation held back if skin or highlights would go plastic.
- Orientation / export cleanup is part of the pipeline. Some raw previews present rotated while the edited exports are corrected.

## What It Does *Not* Do

- It is not a heavy orange-teal grade.
- It is not a flat matte wash.
- It is not a crunchy HDR contrast pass.
- It is not a one-size-fits-all LUT.
- It does not erase flash haze, softness, bloom, or imperfect point-and-shoot texture. Those imperfections are part of the charm.

## Build Guidance For Alert Alert

Do not model this as one slider stack only. Build at least two scene-aware variants:

- `Ambient Film`
- `Flash Film`

### Ambient Film v1

- Small negative exposure bias.
- Mild contrast shaping through a soft S-curve.
- Slightly crushed blacks.
- Small saturation / vibrance increase.
- Slight warm-midtone bias.
- Slight blue reduction.
- Optional tiny grain pass after color.

### Flash Film v1

- Small positive exposure lift.
- Stronger contrast than the ambient preset.
- Less saturation boost than the ambient preset.
- Keep specular highlights alive.
- Preserve haze / bloom instead of “cleaning” it out.
- Allow neutral-to-cool nightlife color when the scene already has it.

## Practical Next Step

For implementation, the safest path is:

1. Start with a shared tone engine:
   - black compression
   - mild highlight shoulder
   - optional subtle grain
2. Fork the color behavior into two presets:
   - ambient darker / warmer / denser
   - flash brighter / punchier / more neutral
3. Add a scene toggle later if you want one-click mode selection.

This reference set is strong enough to build a first-pass Alert Alert photo filter without guessing from generic “film look” clichés.
