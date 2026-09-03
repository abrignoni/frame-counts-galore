# Forensic Video Processor

A Python tool for forensic video analysis. It decodes every frame of a video, records each frame's presentation timestamp and a hash of its pixels, optionally writes every frame out as a PNG, and produces a case provenance manifest. It comes as a command line tool and a graphical front end that drives the same code.

Dependencies for your python environment are listed in requirements.txt

Install them using the below command. Ensure the py part is correct for your environment, eg py, python, or python3, etc.

```
py -m pip install -r requirements.txt
```
or
```
pip3 install -r requirements.txt
```

FFmpeg needs to be installed on your system. The graphical front end uses tkinter, which ships with the python.org installers for Windows and macOS; on Linux install your distribution's `python3-tk` package.

---

## Table of Contents

1. [Operational Modes](#operational-modes)
2. [Outputs by Mode](#outputs-by-mode)
3. [Choosing the Right Mode](#choosing-the-right-mode)
4. [CLI Usage](#cli-usage)
5. [Example Commands](#example-commands)
6. [Estimating a Run Before Starting It](#estimating-a-run-before-starting-it)
7. [Graphical Front End](#graphical-front-end)
8. [How Frames Are Counted](#how-frames-are-counted)
9. [Forensic Notes](#forensic-notes)
10. [Prebuilt Executables](#prebuilt-executables)
11. [Test Videos](#test-videos)
12. [Decision Tree for Analysts](#decision-tree-for-analysts)

---

## Operational Modes

| Mode Name               | CLI Argument      | Frame Decode | Pixel Conversion | Image Files Written | Pixel Hashing | Timing (PTS) | Performance |
| ----------------------- | ----------------- | ------------ | ---------------- | ------------------- | ------------- | ------------ | ----------- |
| Full Forensic (Default) | *(none)* or `--full-forensic` | ✅ Yes | ✅ Yes | ✅ Yes | ✅ Yes | ✅ Yes | Slow |
| Decode-Only             | `--no-frames`     | ✅ Yes        | ✅ Yes            | ❌ No                | ✅ Yes         | ✅ Yes        | Medium      |

**Descriptions**

- **Full Forensic (Default)**: Decodes all frames, converts to RGB, saves images, hashes pixels, extracts PTS, FPS, and timing data. Suitable for evidence production. Can be invoked explicitly with `--full-forensic` or implicitly by omitting a mode flag entirely; both are identical in behaviour and both record `full-forensic` in the case provenance manifest.
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
python video_processor_cli.py -i <input> -o <output> [options]

Required arguments:
  -i, --input <path>     Input video file or directory
  -o, --output <path>    Output directory for case results
                         (not needed with --estimate-only)

Mode (mutually exclusive):
  --full-forensic        Full forensic mode: decode frames, write PNG images,
                         hash pixels (explicit form of the default behaviour)
  --no-frames            Decode-only mode: decode frames and hash pixels,
                         but do not write image files

Estimating (mutually exclusive):
  --estimate             Print an estimate of frames, run time and disk use
                         before processing, and record it in the manifest
  --estimate-only        Print the estimate and exit without processing

  -h, --help             Show this help message and exit
```

Notes:

- If neither `--full-forensic` nor `--no-frames` is specified, full forensic mode runs by default.
- `--full-forensic` and `--no-frames` are mutually exclusive; passing both will produce an error.
- The selected mode is recorded in the case provenance manifest.
- When the input is a directory, every file in it that FFmpeg can open is processed. Files FFmpeg cannot open are listed in the log and in the manifest under `files_skipped`. Sub-directories are not entered.

---

## Example Commands

```
# Full forensic processing (explicit)
python video_processor_cli.py -i video.mp4 -o output/ --full-forensic

# Full forensic processing (implicit default, identical to above)
python video_processor_cli.py -i video.mp4 -o output/

# Decode-only (no image artifacts)
python video_processor_cli.py -i video.mp4 -o output/ --no-frames

# See how long a full forensic run would take and how much disk it needs
python video_processor_cli.py -i video.mp4 --estimate-only

# Estimate first, then process, with the estimate recorded in the manifest
python video_processor_cli.py -i evidence_folder/ -o output/ --estimate
```

---

## Estimating a Run Before Starting It

`--estimate` and `--estimate-only` (and the Estimate button in the graphical front end) sample the first frames of each video through the same decode, hash and PNG write code a real run uses, measure the time and PNG bytes per frame, and multiply by the frame count the container reports. The result is printed per video and in total:

```
Estimate for 1 video(s), mode full-forensic, 1 parallel worker(s):
  clip.mp4: 1920x1080 hevc, ~5,994 frames (container frame count), ~16m 00s at 160.0 ms/frame over 30 sampled frames, ~8.3 GB of PNGs
  Estimated frames in total: 5,994
  Estimated wall-clock time: 16m 00s
  Estimated disk use for frame images: 8.3 GB
```

Points to keep in mind:

- The frame count used for the estimate comes from the container's metadata, or from duration multiplied by average frame rate when the container does not carry a count (MKV, WebM and MPEG-TS usually do not). It is an estimate. The count reported in the manifest and CSV always comes from decoding the whole file.
- The time estimate assumes the videos are processed in parallel by the worker pool, longest first. Parallel workers share the disk and CPU, so several large videos at once can run slower than the estimate.
- Full forensic mode writes one PNG per frame. A 1080p PNG is typically 1 to 2 MB, so an hour of 30 fps video is roughly 100 GB or more. Run the estimate before starting a full forensic run on long material.
- When `--estimate` is used before a run, the estimate is stored in the manifest under `pre_run_estimate` so it can be compared with the actual result.

---

## Graphical Front End

```
python video_processor_gui.py
```

The window offers the same two modes and calls the same processing code as the command line tool, so the case folder, CSV files, PNG images, log and manifest are identical. It adds:

- File and folder pickers for the input and the output directory.
- An **Estimate** button that shows the per-video and total estimate described above. In full forensic mode, once an estimate has been made in that mode, the Start button asks for confirmation when the estimate comes to 5 GB of PNGs or more. Starting a run without pressing Estimate first does not prompt.
- A progress table with one row per video (status, frames decoded so far, estimated frames, packets rejected by the decoder), an overall progress bar, elapsed time and a remaining-time figure derived from the metadata frame count.
- A live view of the processing log.
- A **Stop** button. Stopping terminates the workers; the manifest is still written and records `"status": "cancelled"`.
- An **Open Output Folder** button once the run has finished.
- A read-only **Workers** figure beside the mode buttons, one less than the number of CPU cores, and never below one. It is the same worker count the command line tool uses, and neither front end lets you change it.

---

## How Frames Are Counted

The frame count is the number of frames the decoder returns, which is what `ffprobe -count_frames` reports as `nb_read_frames`. It is not the packet count and not the count the container claims in its metadata; both can differ from the number of decodable frames.

Decoding is done packet by packet:

1. Every packet the demuxer returns is passed to the decoder, followed by a flush so frames still buffered inside the decoder (B-frame reordering) are drained.
2. If the decoder rejects a packet, the rejection is logged with the packet number and timestamp, counted, and decoding continues with the next packet. This is the behaviour of the ffmpeg and ffprobe command line tools. Stopping at the first rejected packet would silently drop every frame after it, so the count reflects decodable frames and the manifest records how many packets were rejected (`corrupt_packets`) out of how many were read (`packets_read`).
3. If the demuxer itself fails part way through a file, decoding cannot continue. The frames decoded before the failure are kept and the manifest records the error under `demux_error`.
4. A frame that decodes but cannot be converted to rgb24 is logged, counted under `frames_conversion_failed`, and left out of the CSV. `frames_decoded` in the manifest is the count before this step and `frames` the count after it.

The decoder is pinned to a single thread (`decode_thread_type` and `decode_thread_count` are recorded per video). Frame counts, timestamps and the hashes of intact streams do not depend on threading, but two things do: FFmpeg's frame threading reports decode errors late and the PyAV bindings then stop draining the decoder, which loses the last frames of a damaged file, and the pixels of error-concealed frames (frames between a rejected packet and the next keyframe) differ between threaded and single-threaded decoding. Pinning one thread keeps the output of a damaged file reproducible on any machine.

---

## Forensic Notes

- **Decode-Only** mode performs **no image writing**; pixel hashes are still computed and recorded.
- Both modes generate:
  - Case provenance manifest (JSON)
  - Processing log file
  - Frame-level CSV with per-frame PTS, timestamps, hashes, and FPS data
- The manifest records the tool version, PyAV and FFmpeg library versions, platform, mode, decoder threading, worker count, the files skipped because FFmpeg could not open them, the pre-run estimate if one was made, and per video: frames, frames decoded, packets read, packets rejected, codec, pixel format, duration, average FPS and the path of the CSV.
- Each mode is **explicitly recorded** in the case manifest to maintain traceability and reproducibility.
- Each frame carries two SHA-256 hashes, and the manifest's `hash_columns` entry describes them:
  - `native_sha256` is the hash of the decoder's own output in the stream's pixel format (`native_pixel_format`, for example `yuv420p`), plane by plane with line padding removed. Video decoders produce this data bit-exactly on every platform, and it is the same layout `ffmpeg -f framehash -hash sha256` hashes, so it can be compared between machines and against ffmpeg. `native_hash_note` says why it is empty on the rare pixel formats whose layout cannot be derived.
  - `decoded_sha256` is the hash of the frame converted to rgb24, which is what the PNG contains. That conversion goes through libswscale, whose rounding differs between CPU architectures: the same file produced rgb24 values one level apart on an Apple Silicon Mac and an x64 Windows build of the same FFmpeg version. It is reproducible on the same platform, not across platforms. Use `native_sha256` to establish that two machines decoded the same frames.
  - In full forensic mode each PNG is read back after writing and hashed again as `image_sha256`; `hash_verified` is true when it equals `decoded_sha256`.
- Frames following a rejected packet up to the next keyframe are error-concealed by the decoder. Their hashes describe the decoder's concealment output, not data present in the file, and are only reproducible with the same decoder configuration.
- Analysts should select modes based on **purpose, evidentiary requirements, and disk constraints**.

---

## Prebuilt Executables

Each [release](https://github.com/abrignoni/frame-counts-galore/releases) carries one archive per platform with the graphical front end, the command line tool, a README and SHA256 checksums: Windows x64 and ARM64, macOS Apple Silicon and Intel, and Linux x64 and ARM64. Python, PyAV and the FFmpeg libraries are bundled, so nothing needs to be installed and no system FFmpeg is used. The archives are built by the `Build executables` GitHub Actions workflow on a GitHub-hosted runner of each platform, which also runs the command line executable against `test_videos/corrupt` and checks the frame counts and rejected packet counts before packaging.

Things to know before running one:

- **They are not code-signed or notarised.** Windows SmartScreen warns on first launch (More info, then Run anyway). macOS Gatekeeper refuses the app until you right-click it and choose Open, or clear the quarantine attribute with `xattr -dr com.apple.quarantine <file>`. The README inside each archive repeats the exact steps.
- Single-file executables unpack to a temporary folder on every start, so the first launch can take 10 to 20 seconds.
- The Linux builds are made on Ubuntu 22.04 and need glibc 2.35 or newer; the graphical front end needs an X11 or XWayland display.
- Verify the download against `SHA256SUMS.txt` before use. The release page also lists the checksum of every archive.
- The manifest records the platform, Python and FFmpeg library versions the run used, so a report can state which build produced it.

To build the executables yourself, install the requirements plus `pyinstaller` and run the two `pyinstaller` commands from `.github/workflows/build-executables.yml`.

---

## Test Videos

`test_videos/` holds small videos with known frame counts, generated with ffmpeg by `tools/make_test_videos.sh`: `synth/` covers H.264 with and without B-frames, HEVC, VP9, MPEG-2, MJPEG, ProRes, MKV, WebM and MPEG-TS containers, odd dimensions, a single frame, variable frame rate and 1080p, and `corrupt/` holds damaged and truncated copies. The number in each `synth/` filename is the exact frame count, and `test_videos/README.md` lists the expected frames and rejected packets for every damaged file. Point the tool at either folder to check an installation.

---

## Decision Tree for Analysts

1. **Define purpose**
   - Evidence production: go to step 2
   - Internal validation / triage: go to step 3
2. **Evidentiary requirements**
   - Need frame images: Full Forensic (`--full-forensic`)
   - Frame images not required: Decode-Only (`--no-frames`)
3. **Need pixel integrity verification without producing image files?**
   - Yes: Decode-Only (`--no-frames`)
   - No specific requirement: Full Forensic (`--full-forensic`)
4. **Run the estimate** before a full forensic run on long material
5. **Record mode in manifest** for traceability
