# frame-counts-galore
Python wrapper for FFMPEG that extracts frames from media.

Dependencies for your python environment are listed in requirements.txt

Install them using the below command. Ensure the py part is correct for your environment, eg py, python, or python3, etc.

py -m pip install -r requirements.txt\
or\
pip3 install -r requirements.txt

FFMPEG needs to be installed on your system.

# Forensic Video Processor

A Python CLI tool for forensic video analysis, supporting multiple operational modes to balance **forensic rigor**, **performance**, and **analyst intent**.

---

## Table of Contents

1. [Operational Modes](#operational-modes)
2. [Outputs by Mode](#outputs-by-mode)
3. [Choosing the Right Mode](#choosing-the-right-mode)
4. [CLI Usage](#cli-usage)
5. [Example Commands](#example-commands)
6. [Example Screens](#example-screens)

---

## Operational Modes

| Mode Name | CLI Argument | Frame Decode | Pixel Conversion | Image Files Written | Pixel Hashing | Timing (PTS) | Performance |
|-----------|--------------|-------------|-----------------|-------------------|---------------|--------------|------------|
| Full Forensic (Default) | *(none)* | ✅ Yes | ✅ Yes | ✅ Yes | ✅ Yes | ✅ Yes | Slow |
| Decode-Only | `--no-frames` | ✅ Yes | ✅ Yes | ❌ No | ✅ Yes | ✅ Yes | Medium |
| PTS-Only / Metadata-Only | `--pts-only` | ❌ No | ❌ No | ❌ No | ❌ No | ✅ Yes | Fast |

**Descriptions**

- **Full Forensic (Default)**: Decodes all frames, converts to RGB, saves images, hashes pixels, extracts PTS, FPS, and timing data. Suitable for evidence production and court-ready analysis.
- **Decode-Only (`--no-frames`)**: Decodes frames and hashes pixels but does not save images. Suitable for internal validation or when disk usage should be minimized.
- **PTS-Only / Metadata-Only (`--pts-only`)**: Extracts PTS, DTS, frame duration, and keyframe metadata only. No decoding or image writing. Ideal for rapid triage or large datasets.

---

## Outputs by Mode

| Output Artifact | Full Forensic | Decode-Only | PTS-Only |
|-----------------|---------------|-------------|----------|
| Frame images (PNG) | ✅ Yes | ❌ No | ❌ No |
| Frame-level CSV | ✅ Yes | ✅ Yes | ❌ No |
| Packet-level CSV | ❌ No | ❌ No | ✅ Yes |
| Cryptographic hashes | ✅ Yes | ✅ Yes | ❌ No |
| Timing / FPS analysis | ✅ Yes | ✅ Yes | ✅ Yes |
| Case provenance manifest (JSON) | ✅ Yes | ✅ Yes | ✅ Yes |
| Processing log | ✅ Yes | ✅ Yes | ✅ Yes |

---

## Choosing the Right Mode

| Objective | Recommended Mode |
|-----------|-----------------|
| Evidence production | Full Forensic (Default) |
| Decode integrity without artifacts | Decode-Only (`--no-frames`) |
| Rapid triage / timestamp analysis | PTS-Only (`--pts-only`) |

---

## CLI Usage

```bash
# Basic syntax
python forensic_video.py -i <input> -o <output> [options]

Required arguments:
  -i, --input <path>     Input video file or directory
  -o, --output <path>    Output directory for case results

Optional arguments:
  --no-frames            Decode frames and compute hashes, but do not write image files
  --pts-only             Extract PTS and metadata only (no frame decode)
  -h, --help             Show this help message and exit

Notes:
  • If --pts-only is specified, no frame decoding or image processing occurs
  • If --no-frames is specified without --pts-only, frames are decoded but not saved
  • The selected mode is recorded in the case provenance manifest
```
## Example commands
```bash
# Full forensic processing (default)
python forensic_video.py -i video.mp4 -o output/

# Decode-only (no image artifacts)
python forensic_video.py -i video.mp4 -o output/ --no-frames

# PTS-only / metadata-only (no decode)
python forensic_video.py -i video.mp4 -o output/ --pts-only
```
## Forensic Notes

- **PTS-Only** mode performs **no decoding**.
- **Decode-Only** mode performs **no image writing**.
- All modes generate:
  - Case provenance manifest (JSON)
  - Processing log file
  - Timing metadata (mode-dependent detail)
- Each mode is **explicitly recorded** in the case manifest to maintain traceability and reproducibility.
- Analysts should select modes based on **purpose, dataset size, and evidentiary requirements**.

---

## Decision Tree for Analysts

1. **Define purpose**
   - Triage/intake → Step 2
   - Evidence production → Step 4
2. **Dataset size / urgency**
   - Large/urgent → PTS-Only
   - Small → Step 3
3. **Need pixel integrity?**
   - Yes → Decode-Only
   - No → PTS-Only
4. **Evidentiary requirements**
   - Need frame images → Full Forensic
   - No frame images → Decode-Only
5. **Record mode in manifest** for traceability

---


## Example screens
<img width="1517" height="400" alt="Screenshot 2025-12-23 at 7 12 57 PM" src="https://github.com/user-attachments/assets/71730bf7-f7e6-4a69-85aa-d0be40b82b96" />

Sample output folder structure for 3 processed files\
<img width="464" height="171" alt="Screenshot 2025-12-23 at 9 09 53 PM" src="https://github.com/user-attachments/assets/f6793348-b65a-477d-85f6-64f7294bd6b1" />

Sample spreadsheet
<img width="1363" height="385" alt="Screenshot 2025-12-23 at 9 12 00 PM" src="https://github.com/user-attachments/assets/2a6bdf70-9758-4288-8e3e-ef88e65779cc" />

Sample frames directory with files named by index and pts\
<img width="326" height="259" alt="Screenshot 2025-12-23 at 9 12 46 PM" src="https://github.com/user-attachments/assets/25f2778e-faec-49fd-9c1d-bd72fd7ae157" />
