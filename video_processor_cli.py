#!/usr/bin/env python3

import av
import csv
import hashlib
import os
import sys
import json
import platform
import logging
from logging.handlers import QueueHandler, QueueListener
from datetime import datetime, timezone
from multiprocessing import Pool, cpu_count, Manager
from PIL import Image
import numpy as np
import argparse

# ---------------- CONFIG ----------------
MAX_WORKERS = max(cpu_count() - 1, 1)
HASH_ALGORITHM = "sha256"
DECODE_METHOD = "cpu"
DECODE_DETAILS = {"requested": "cpu", "actual": "cpu", "hwaccel": None}

# ---------------- HELPER FUNCTIONS ----------------
def get_video_files(path):
    if os.path.isfile(path):
        return [path]
    files = []
    for entry in os.scandir(path):
        if entry.is_file():
            try:
                av.open(entry.path).close()
                files.append(entry.path)
            except Exception:
                pass
    return files

def setup_worker_logging(log_queue):
    logger = logging.getLogger()
    logger.handlers.clear()  # Prevent handler accumulation across calls
    qh = QueueHandler(log_queue)
    logger.setLevel(logging.INFO)
    logger.addHandler(qh)

# ---------------- WORKER ----------------
def process_video(args):
    (
        video_path,
        case_dir,
        log_queue,
        no_frames,
        resolved_mode
    ) = args

    setup_worker_logging(log_queue)
    logger = logging.getLogger(__name__)

    video_name = os.path.splitext(os.path.basename(video_path))[0]
    video_dir = os.path.join(case_dir, video_name)
    frames_dir = os.path.join(video_dir, "frames")
    os.makedirs(video_dir, exist_ok=True)

    logger.info(f"[{video_name}] Processing video: {video_path}")

    try:
        container = av.open(video_path)
    except Exception as e:
        logger.error(f"[{video_name}] Failed to open: {e}")
        return None

    video_streams = [s for s in container.streams if s.type == "video"]
    if not video_streams:
        logger.warning(f"[{video_name}] No video stream found")
        container.close()
        return None

    stream = video_streams[0]
    codec = stream.codec_context.codec

    # ======================================================
    # DECODE MODE
    # ======================================================
    if not no_frames:
        os.makedirs(frames_dir, exist_ok=True)

    frames = []
    corrupt_packets = 0

    try:
        for frame_index, frame in enumerate(container.decode(stream)):

            if frame.pts is not None and frame.time_base:
                timestamp_seconds = float(frame.pts * frame.time_base)
                pts_value = frame.pts
            else:
                timestamp_seconds = None
                pts_value = None

            decoded_hash = None
            image_hash = None
            image_filename = None
            hash_verified = None

            try:
                rgb = frame.to_ndarray(format="rgb24")
            except Exception as e:
                logger.warning(f"[{video_name}] Frame {frame_index}: could not convert to rgb24 — {e}. Skipping.")
                continue

            decoded_hash = hashlib.new(HASH_ALGORITHM, rgb.tobytes()).hexdigest()

            if not no_frames:
                pts_label = pts_value if pts_value is not None else "no_pts"
                image_filename = f"frame_{frame_index:06d}_pts_{pts_label}.png"
                image_path = os.path.join(frames_dir, image_filename)

                Image.fromarray(rgb).save(image_path)
                reloaded = np.array(Image.open(image_path))
                image_hash = hashlib.new(HASH_ALGORITHM, reloaded.tobytes()).hexdigest()
                hash_verified = (decoded_hash == image_hash)

            frames.append({
                "frame_index": frame_index,
                "pts": pts_value,
                "time_base": str(frame.time_base) if frame.time_base else None,
                "timestamp_seconds": timestamp_seconds,
                "frame_duration": None,
                "fps": None,
                "key_frame": bool(frame.key_frame),
                f"decoded_{HASH_ALGORITHM}": decoded_hash,
                f"image_{HASH_ALGORITHM}": image_hash,
                "hash_verified": hash_verified,
                "image_file": image_filename,
                "decode_method": DECODE_METHOD,
                "decode_hwaccel": DECODE_DETAILS["hwaccel"]
            })

    except av.error.InvalidDataError as e:
        # A corrupt or undecodable packet was encountered mid-stream.
        # avcodec_send_packet() raised this; we log it and preserve all
        # frames successfully decoded before the error occurred.
        corrupt_packets += 1
        logger.warning(
            f"[{video_name}] InvalidDataError at frame {len(frames)} "
            f"(avcodec_send_packet) — {e}. "
            f"Recovered {len(frames)} frames before corrupt packet."
        )
    except av.AVError as e:
        logger.error(f"[{video_name}] AVError during decode: {e}")
    finally:
        container.close()

    if not frames:
        logger.warning(f"[{video_name}] No frames decoded")
        return None

    if corrupt_packets:
        logger.warning(f"[{video_name}] Total corrupt packet errors encountered: {corrupt_packets}")

    # ---------------- TIMING CALCULATIONS ----------------
    # Per-frame duration and FPS are computed from successive PTS differences.
    # The final frame has no successor, so its duration and FPS remain None.
    for i in range(len(frames) - 1):
        t0 = frames[i]["timestamp_seconds"]
        t1 = frames[i + 1]["timestamp_seconds"]
        if t0 is not None and t1 is not None and t1 > t0:
            duration = t1 - t0
            frames[i]["frame_duration"] = duration
            frames[i]["fps"] = 1.0 / duration

    valid_ts = [f["timestamp_seconds"] for f in frames if f["timestamp_seconds"] is not None]
    if len(valid_ts) >= 2:
        # Compute the mean inter-frame interval from all frames that have a
        # measured duration (i.e. all but the last).
        measured_durations = [
            f["frame_duration"] for f in frames if f["frame_duration"] is not None
        ]
        if measured_durations:
            mean_frame_duration = sum(measured_durations) / len(measured_durations)
        else:
            mean_frame_duration = 0

        # True stream duration includes the final frame's display period.
        # Use the mean frame duration as the best available estimate for that
        # period, since no closing PTS boundary exists for the last frame.
        pts_span = valid_ts[-1] - valid_ts[0]
        total_duration = pts_span + mean_frame_duration

        # average_fps is derived from total frame count and true duration so
        # that average_fps * total_duration == len(frames) (within float precision).
        average_fps = len(frames) / total_duration if total_duration > 0 else 0
    else:
        total_duration = 0
        average_fps = 0

    csv_path = os.path.join(video_dir, f"{video_name}_frames.csv")
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=frames[0].keys())
        writer.writeheader()
        writer.writerows(frames)

    logger.info(
        f"[{video_name}] Completed | "
        f"Frames: {len(frames)} | "
        f"Duration: {total_duration:.6f} sec | "
        f"Average FPS: {average_fps:.6f}"
    )

    return {
        "video": video_path,
        "mode": resolved_mode,
        "frames": len(frames),
        "corrupt_packets": corrupt_packets,
        "codec": codec.name,
        "codec_long": codec.long_name,
        "pixel_format": stream.codec_context.format.name if stream.codec_context.format else None,
        "total_duration": total_duration,
        "average_fps": average_fps
    }

# ---------------- MAIN ----------------
if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Forensic Video Processor CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Modes (mutually exclusive):\n"
            "  (default)        Full Forensic: decode all frames, write PNG images,\n"
            "                   compute pixel hashes, verify round-trip integrity.\n"
            "  --full-forensic  Explicit form of the default. Identical behaviour;\n"
            "                   use when you want the mode recorded unambiguously.\n"
            "  --no-frames      Decode-Only: decode frames and hash pixels, but do\n"
            "                   not write image files. Saves disk space.\n"
        )
    )

    parser.add_argument("-i", "--input", required=True, help="Input video file or directory")
    parser.add_argument("-o", "--output", required=True, help="Output directory for case results")

    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument(
        "--full-forensic",
        action="store_true",
        help="Full forensic mode: decode frames, write PNG images, hash pixels (default behaviour)"
    )
    mode_group.add_argument(
        "--no-frames",
        action="store_true",
        help="Decode-only mode: decode frames and hash pixels, but do not write image files"
    )

    args = parser.parse_args()

    input_path = args.input
    output_root = args.output

    case_start_utc = datetime.now(timezone.utc)
    case_id = f"case_{case_start_utc.strftime('%Y%m%dT%H%M%SZ')}"
    case_dir = os.path.join(output_root, case_id)
    os.makedirs(case_dir, exist_ok=True)

    log_path = os.path.join(case_dir, "case_processing.log")
    manager = Manager()
    log_queue = manager.Queue()

    file_handler = logging.FileHandler(log_path, mode="w", encoding="utf-8")
    formatter = logging.Formatter("%(asctime)s [%(processName)s] %(levelname)s: %(message)s")
    file_handler.setFormatter(formatter)

    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(formatter)

    # QueueListener is the single point that dispatches to both handlers,
    # so the root logger must only have the QueueHandler — nothing else.
    listener = QueueListener(log_queue, file_handler, stream_handler, respect_handler_level=True)
    listener.start()

    main_qh = QueueHandler(log_queue)
    main_logger = logging.getLogger()
    main_logger.handlers.clear()  # Ensure no pre-existing handlers cause duplicate output
    main_logger.setLevel(logging.INFO)
    main_logger.addHandler(main_qh)

    main_logger.info(f"Case ID: {case_id}")

    video_files = get_video_files(input_path)
    if not video_files:
        main_logger.error("No supported video files found")
        listener.stop()
        sys.exit(1)

    resolved_mode = "decode-only" if args.no_frames else "full-forensic"

    worker_args = [
        (vp, case_dir, log_queue, args.no_frames, resolved_mode)
        for vp in video_files
    ]

    try:
        try:
            with Pool(processes=MAX_WORKERS) as pool:
                results = pool.map(process_video, worker_args)
        except Exception as e:
            main_logger.error(f"Multiprocessing pool error: {e}")
            results = []

        manifest = {
            "case_id": case_id,
            "case_start_utc": case_start_utc.isoformat(),
            "case_end_utc": datetime.now(timezone.utc).isoformat(),
            "platform": platform.platform(),
            "python_version": platform.python_version(),
            "pyav_version": av.__version__,
            "ffmpeg_libraries": av.library_versions,
            "processing_modes": {
                "mode": resolved_mode
            },
            "hash_algorithm": HASH_ALGORITHM,
            "input_path": input_path,
            "videos_processed": [r for r in results if r],
            "log_file": log_path
        }

        manifest_path = os.path.join(case_dir, "case_provenance_manifest.json")
        with open(manifest_path, "w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=2)

        main_logger.info("All videos processed")
        main_logger.info(f"Case provenance manifest written to: {manifest_path}")

    finally:
        # Always stop the listener last — after every log message has been
        # emitted — so nothing queued after pool completion is silently dropped.
        listener.stop()