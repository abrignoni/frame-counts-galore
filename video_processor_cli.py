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
    qh = QueueHandler(log_queue)
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)
    logger.addHandler(qh)

# ---------------- WORKER ----------------
def process_video(args):
    (
        video_path,
        case_dir,
        log_queue,
        no_frames,
        pts_only
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
    # PTS-ONLY MODE
    # ======================================================
    if pts_only:
        packets = []

        for packet in container.demux(stream):
            if packet.pts is None:
                continue

            packets.append({
                "packet_index": len(packets),
                "pts": packet.pts,
                "dts": packet.dts,
                "duration": packet.duration,
                "time_base": str(packet.time_base),
                "timestamp_seconds": float(packet.pts * packet.time_base),
                "is_keyframe": packet.is_keyframe
            })

        container.close()

        if not packets:
            logger.warning(f"[{video_name}] No packets with PTS found")
            return None

        csv_path = os.path.join(video_dir, f"{video_name}_pts_only.csv")
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=packets[0].keys())
            writer.writeheader()
            writer.writerows(packets)

        logger.info(f"[{video_name}] PTS-only mode complete | Packets: {len(packets)}")

        return {
            "video": video_path,
            "mode": "pts-only",
            "packets": len(packets),
            "codec": codec.name,
            "codec_long": codec.long_name,
            "time_base": str(stream.time_base),
            "duration": float(stream.duration * stream.time_base)
            if stream.duration else None
        }

    # ======================================================
    # DECODE MODE
    # ======================================================
    if not no_frames:
        os.makedirs(frames_dir, exist_ok=True)

    frames = []

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
        except Exception:
            continue

        decoded_hash = hashlib.sha256(rgb.tobytes()).hexdigest()

        if not no_frames:
            pts_label = pts_value if pts_value is not None else "no_pts"
            image_filename = f"frame_{frame_index:06d}_pts_{pts_label}.png"
            image_path = os.path.join(frames_dir, image_filename)

            Image.fromarray(rgb).save(image_path)
            reloaded = np.array(Image.open(image_path))
            image_hash = hashlib.sha256(reloaded.tobytes()).hexdigest()
            hash_verified = (decoded_hash == image_hash)

        frames.append({
            "frame_index": frame_index,
            "pts": pts_value,
            "time_base": str(frame.time_base) if frame.time_base else None,
            "timestamp_seconds": timestamp_seconds,
            "frame_duration": None,
            "fps": None,
            "key_frame": bool(frame.key_frame),
            "decoded_sha256": decoded_hash,
            "image_sha256": image_hash,
            "hash_verified": hash_verified,
            "image_file": image_filename,
            "decode_method": DECODE_METHOD,
            "decode_hwaccel": DECODE_DETAILS["hwaccel"]
        })

    container.close()

    if not frames:
        logger.warning(f"[{video_name}] No frames decoded")
        return None

    # ---------------- TIMING CALCULATIONS ----------------
    for i in range(len(frames) - 1):
        t0 = frames[i]["timestamp_seconds"]
        t1 = frames[i + 1]["timestamp_seconds"]
        if t0 is not None and t1 is not None and t1 > t0:
            duration = t1 - t0
            frames[i]["frame_duration"] = duration
            frames[i]["fps"] = 1.0 / duration

    valid_ts = [f["timestamp_seconds"] for f in frames if f["timestamp_seconds"] is not None]
    if len(valid_ts) >= 2:
        total_duration = valid_ts[-1] - valid_ts[0]
        average_fps = (len(valid_ts) - 1) / total_duration
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
        "mode": "decode",
        "frames": len(frames),
        "codec": codec.name,
        "codec_long": codec.long_name,
        "pixel_format": stream.codec_context.format.name if stream.codec_context.format else None,
        "total_duration": total_duration,
        "average_fps": average_fps
    }

# ---------------- MAIN ----------------
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Forensic Video Processor CLI")

    parser.add_argument("-i", "--input", required=True)
    parser.add_argument("-o", "--output", required=True)
    parser.add_argument("--no-frames", action="store_true")
    parser.add_argument("--pts-only", action="store_true")

    args = parser.parse_args()

    input_path = args.input
    output_root = args.output

    case_id = f"case_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
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

    listener = QueueListener(log_queue, file_handler, stream_handler)
    listener.start()

    main_qh = QueueHandler(log_queue)
    main_logger = logging.getLogger()
    main_logger.setLevel(logging.INFO)
    main_logger.addHandler(main_qh)

    main_logger.info(f"Case ID: {case_id}")

    video_files = get_video_files(input_path)
    if not video_files:
        main_logger.error("No supported video files found")
        listener.stop()
        sys.exit(1)

    worker_args = [
        (vp, case_dir, log_queue, args.no_frames, args.pts_only)
        for vp in video_files
    ]

    with Pool(processes=MAX_WORKERS) as pool:
        results = pool.map(process_video, worker_args)

    
    manifest = {
        "case_id": case_id,
        "case_start_utc": datetime.now(timezone.utc).isoformat(),
        "case_end_utc": datetime.now(timezone.utc).isoformat(),
        "platform": platform.platform(),
        "python_version": platform.python_version(),
        "pyav_version": av.__version__,
        "ffmpeg_libraries": av.library_versions,
        "processing_modes": {
            "pts_only": args.pts_only,
            "no_frames": args.no_frames
        },
        "input_path": input_path,
        "videos_processed": [r for r in results if r],
        "log_file": log_path
    }

    manifest_path = os.path.join(case_dir, "case_provenance_manifest.json")
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)

    main_logger.info("All videos processed")
    main_logger.info(f"Case provenance manifest written to: {manifest_path}")

    listener.stop()
    