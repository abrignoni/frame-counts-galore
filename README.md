# Forensic Video Processor

A Python CLI tool for forensic video analysis, supporting two operational modes to balance **forensic rigor**, **performance**, and **analyst intent**.

Dependencies for your python environment are listed in requirements.txt

Install them using the below command. Ensure the py part is correct for your environment, eg py, python, or python3, etc.

```
py -m pip install -r requirements.txt
```
or
```
pip3 install -r requirements.txt
```

FFMPEG needs to be installed on your system.

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

| Mode Name               | CLI Argument      | Frame Decode | Pixel Conversion | Image Files Written | Pixel Hashing | Timing (PTS) | Performance |
| ----------------------- | ----------------- | ------------ | ---------------- | ------------------- | ------------- | ------------ | ----------- |
| Full Forensic (Default) | *(none)* or `--full-forensic` | ✅ Yes | ✅ Yes | ✅ Yes | ✅ Yes | ✅ Yes | Slow |
| Decode-Only             | `--no-frames`     | ✅ Yes        | ✅ Yes            | ❌ No                | ✅ Yes         | ✅ Yes        | Medium      |

**Descriptions**

- **Full Forensic (Default)**: Decodes all frames, converts to RGB, saves images, hashes pixels, extracts PTS, FPS, and timing data. Suitable for evidence production and court-ready analysis. Can be invoked explicitly with `--full-forensic` or implicitly by omitting a mode flag entirely; both are identical in behaviour and both record `full-forensic` in the case provenance manifest.
- **Decode-Only (`--no-frames`)**: Decodes frames and hashes pixels but does not save images. Suitable for internal validation or when disk usage should be minimized.

> **Note:** The two mode flags are mutually exclusive. Passing both `--full-forensic` and `--no-frames` will produce an error.

---

## Outputs by Mode

| Output Artifact                 | Full Forensic | Decode-Only |
| ------------------------------- | ------------- | ----------- |
| Frame images (PNG)              | ✅ Yes         | ❌ No        |
| Frame-level CSV                 | ✅ Yes         | ✅ Yes       |
| Cryptographic hashes            | ✅ Yes         | ✅ Yes       |
| Timing / FPS analysis           | ✅ Yes         | ✅ Yes       |
| Case provenance manifest (JSON) | ✅ Yes         | ✅ Yes       |
| Processing log                  | ✅ Yes         | ✅ Yes       |

---

## Choosing the Right Mode

| Objective                          | Recommended Mode                        |
| ---------------------------------- | --------------------------------------- |
| Evidence production                | Full Forensic (`--full-forensic`)       |
| Decode integrity without artifacts | Decode-Only (`--no-frames`)             |

---

## CLI Usage

```
# Basic syntax
python video_processor_cli.py -i <input> -o <output> [options]
  
  Required arguments:
  -i, --input <path>     Input video file or directory
    -o, --output <path>    Output directory for case results
      
      Optional arguments (mutually exclusive):
      --full-forensic        Full forensic mode: decode frames, write PNG images,
      hash pixels (explicit form of the default behaviour)
      --no-frames            Decode-only mode: decode frames and hash pixels,
      but do not write image files
      -h, --help             Show this help message and exit
      
      Notes:
      • If neither --full-forensic nor --no-frames is specified, full forensic
      mode runs by default.
      • --full-forensic and --no-frames are mutually exclusive; passing both
      will produce an error.
      • The selected mode is recorded in the case provenance manifest.
      ```
      
      ---
      
      ## Example Commands
      
      ```
      # Full forensic processing (explicit)
      python video_processor_cli.py -i video.mp4 -o output/ --full-forensic
      
      # Full forensic processing (implicit default — identical to above)
      python video_processor_cli.py -i video.mp4 -o output/
      
      # Decode-only (no image artifacts)
      python video_processor_cli.py -i video.mp4 -o output/ --no-frames
      ```
      
      ---
      
      ## Forensic Notes
      
      - **Decode-Only** mode performs **no image writing**; pixel hashes are still computed and recorded.
      - Both modes generate:
      - Case provenance manifest (JSON)
      - Processing log file
      - Frame-level CSV with per-frame PTS, timestamps, hashes, and FPS data
      - Each mode is **explicitly recorded** in the case manifest to maintain traceability and reproducibility.
      - Frame count is determined by decoded frame output from avcodec (`container.decode()`), not by packet enumeration. This is the only count reported and is consistent across both modes.
      - Analysts should select modes based on **purpose, evidentiary requirements, and disk constraints**.
      
      ---
      
      ## Decision Tree for Analysts
      
      1. **Define purpose**
      - Evidence production → Step 2
      - Internal validation / triage → Step 3
      2. **Evidentiary requirements**
      - Need frame images → Full Forensic (`--full-forensic`)
      - Frame images not required → Decode-Only (`--no-frames`)
      3. **Need pixel integrity verification without producing image files?**
      - Yes → Decode-Only (`--no-frames`)
      - No specific requirement → Full Forensic (`--full-forensic`)
      4. **Record mode in manifest** for traceability
      
      ---
