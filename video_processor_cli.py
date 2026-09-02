#!/usr/bin/env python3

import av
import csv
import hashlib
import os
import sys
import json
import platform
import logging
import shutil
import tempfile
import threading
import time
from logging.handlers import QueueHandler, QueueListener
from datetime import datetime, timezone
from multiprocessing import Pool, cpu_count, Manager
from PIL import Image
import numpy as np
import argparse

# ---------------- CONFIG ----------------
TOOL_VERSION = "1.2.0"
MAX_WORKERS = max(cpu_count() - 1, 1)
HASH_ALGORITHM = "sha256"
DECODE_METHOD = "cpu"
DECODE_DETAILS = {"requested": "cpu", "actual": "cpu", "hwaccel": None}
# Decoder threading. The decoder is pinned to one thread so that decoding
# does not depend on the machine's core count. Measured on H.264 samples:
#  - Frame counts, timestamps and hashes of intact streams are identical
#    for every threading configuration, and single-slice streams decode no
#    faster with slice threads.
#  - Frame threading ("AUTO"/"FRAME") reports a decode error late, on a
#    later receive call, and PyAV stops draining the decoder when that
#    happens: a truncated file returned 94 frames and no error instead of
#    the 96 frames plus the error that ffprobe and single-threaded decoding
#    return.
#  - The pixels of error-concealed frames (after a rejected packet, before
#    the next keyframe) differ between threaded and single-threaded
#    decoding, so their hashes are only reproducible under one fixed
#    configuration.
# The thread type and count in effect are recorded per video in the manifest.
DECODE_THREAD_TYPE = "NONE"
DECODE_THREAD_COUNT = 1
# How often a worker reports progress (seconds) when a progress queue is given.
PROGRESS_INTERVAL_SECONDS = 0.5
# Estimator sampling: decode at least this many frames, and keep going until
# this much wall-clock time has passed, capped at the maximum.
ESTIMATE_MIN_FRAMES = 30
ESTIMATE_MAX_FRAMES = 300
ESTIMATE_TIME_BUDGET_SECONDS = 2.0

# ---------------- HELPER FUNCTIONS ----------------
def get_video_files(path, skipped=None):
    """Return the files under path that FFmpeg can open.

    A single file path is returned as is. For a directory, every regular
    file is test-opened; the ones FFmpeg rejects are appended to `skipped`
    (a list, if given) as (path, reason) so the caller can report them.
    Sub-directories are not entered.
    """
    if os.path.isfile(path):
        return [path]
    files = []
    for entry in sorted(os.scandir(path), key=lambda e: e.name):
        if entry.is_file():
            try:
                av.open(entry.path).close()
                files.append(entry.path)
            except Exception as e:
                if skipped is not None:
                    skipped.append((entry.path, str(e)))
    return files

def setup_worker_logging(log_queue):
    logger = logging.getLogger()
    logger.handlers.clear()  # Prevent handler accumulation across calls
    qh = QueueHandler(log_queue)
    logger.setLevel(logging.INFO)
    logger.addHandler(qh)

def _open_video_stream(video_path):
    """Open a container and return (container, first video stream).

    Raises av.FFmpegError (or OSError) if the file cannot be opened, and
    ValueError if it holds no video stream. Threading is configured here so
    that every code path (processing, estimating) decodes the same way.
    """
    container = av.open(video_path)
    video_streams = [s for s in container.streams if s.type == "video"]
    if not video_streams:
        container.close()
        raise ValueError("No video stream found")
    stream = video_streams[0]
    stream.codec_context.thread_type = DECODE_THREAD_TYPE
    stream.codec_context.thread_count = DECODE_THREAD_COUNT
    return container, stream

def probe_video(video_path):
    """Read stream metadata without decoding.

    The frame count returned here is what the container *claims* (or an
    estimate from duration and frame rate). It is only used for run-time
    estimates and progress display. The count reported in the manifest and
    CSV always comes from actually decoding the file.
    """
    info = {
        "video": video_path,
        "name": os.path.splitext(os.path.basename(video_path))[0],
        "ok": False,
        "error": None,
        "codec": None,
        "width": None,
        "height": None,
        "duration_seconds": None,
        "average_rate": None,
        "metadata_frame_count": None,
        "estimated_frames": None,
        "estimate_basis": None,
    }
    try:
        container, stream = _open_video_stream(video_path)
    except Exception as e:
        info["error"] = str(e)
        return info
    try:
        cc = stream.codec_context
        info["codec"] = cc.name
        info["width"] = cc.width
        info["height"] = cc.height
        duration = None
        if stream.duration is not None and stream.time_base:
            duration = float(stream.duration * stream.time_base)
        elif container.duration is not None:
            duration = container.duration / av.time_base
        info["duration_seconds"] = duration
        rate = stream.average_rate or stream.guessed_rate
        info["average_rate"] = float(rate) if rate else None
        if stream.frames:
            info["metadata_frame_count"] = int(stream.frames)
            info["estimated_frames"] = int(stream.frames)
            info["estimate_basis"] = "container frame count"
        elif duration and rate:
            info["estimated_frames"] = int(round(duration * float(rate)))
            info["estimate_basis"] = "duration x average frame rate"
        info["ok"] = True
    finally:
        container.close()
    return info

def native_plane_bytes(frame):
    """Return the decoded frame's planes as one tightly packed byte string,
    plane by plane with line padding removed, plus a reason string when the
    layout cannot be derived (in which case the bytes are None).

    This is the decoder's own output in the stream's pixel format, before
    any colour conversion. Video decoders produce it bit-exactly on every
    platform, and it is the same layout ffmpeg hashes with
    `-f framehash`, so the hash of these bytes can be compared across
    machines and against ffmpeg.
    """
    fmt = frame.format
    if fmt.has_palette or fmt.is_bit_stream or fmt.is_bayer:
        return None, f"unsupported pixel format layout {fmt.name}"
    comps = list(fmt.components)
    out = []
    for p, plane in enumerate(frame.planes):
        pc = [c for c in comps if c.plane == p]
        if not pc:
            return None, f"plane {p} has no components in {fmt.name}"
        widths = {c.width for c in pc}
        heights = {c.height for c in pc}
        if len(widths) != 1 or len(heights) != 1:
            return None, f"mixed component sizes in plane {p} of {fmt.name}"
        row_bytes = widths.pop() * sum((c.bits + 7) // 8 for c in pc)
        rows = heights.pop()
        buf = np.frombuffer(plane, dtype=np.uint8)
        line_size = plane.line_size
        if line_size < row_bytes or buf.size < line_size * rows:
            return None, f"plane {p} buffer smaller than expected in {fmt.name}"
        out.append(buf[: line_size * rows].reshape(rows, line_size)[:, :row_bytes].tobytes())
    return b"".join(out), None

def _hash_frame(frame, frame_index, frames_dir, no_frames):
    """Convert one decoded frame to rgb24, hash it, optionally write and
    round-trip verify a PNG. Returns the per-frame record, or raises if the
    frame cannot be converted to rgb24."""
    if frame.pts is not None and frame.time_base:
        timestamp_seconds = float(frame.pts * frame.time_base)
        pts_value = frame.pts
    else:
        timestamp_seconds = None
        pts_value = None

    image_hash = None
    image_filename = None
    hash_verified = None

    native_bytes, native_reason = native_plane_bytes(frame)
    native_hash = (
        hashlib.new(HASH_ALGORITHM, native_bytes).hexdigest() if native_bytes is not None else None
    )

    # rgb24 conversion goes through libswscale, whose rounding differs between
    # CPU architectures, so this hash (and the PNG) is reproducible on the same
    # platform but not necessarily across platforms. The native hash above is.
    rgb = frame.to_ndarray(format="rgb24")
    decoded_hash = hashlib.new(HASH_ALGORITHM, rgb.tobytes()).hexdigest()

    if not no_frames:
        pts_label = pts_value if pts_value is not None else "no_pts"
        image_filename = f"frame_{frame_index:06d}_pts_{pts_label}.png"
        image_path = os.path.join(frames_dir, image_filename)

        Image.fromarray(rgb).save(image_path)
        reloaded = np.array(Image.open(image_path))
        image_hash = hashlib.new(HASH_ALGORITHM, reloaded.tobytes()).hexdigest()
        hash_verified = (decoded_hash == image_hash)

    return {
        "frame_index": frame_index,
        "pts": pts_value,
        "time_base": str(frame.time_base) if frame.time_base else None,
        "timestamp_seconds": timestamp_seconds,
        "frame_duration": None,
        "fps": None,
        "key_frame": bool(frame.key_frame),
        "native_pixel_format": frame.format.name,
        f"native_{HASH_ALGORITHM}": native_hash,
        "native_hash_note": native_reason,
        f"decoded_{HASH_ALGORITHM}": decoded_hash,
        f"image_{HASH_ALGORITHM}": image_hash,
        "hash_verified": hash_verified,
        "image_file": image_filename,
        "decode_method": DECODE_METHOD,
        "decode_hwaccel": DECODE_DETAILS["hwaccel"],
    }

def decode_frames(container, stream, on_frame, on_decode_error=None, stop=None):
    """Demux the stream packet by packet, decode each packet, and call
    on_frame(frame_index, frame) for every frame the decoder returns.

    A packet the decoder rejects (av.FFmpegError from avcodec_send_packet)
    is counted and skipped, and decoding continues with the next packet.
    This is the behaviour of the ffmpeg and ffprobe command line tools, so
    the frame count matches `ffprobe -count_frames`. Stopping at the first
    rejected packet would silently drop every frame after it.

    The demuxer yields a final flush packet per stream, so frames still
    buffered in the decoder (B-frame reordering, threading) are drained.

    Returns dict(packets_read=, frames_decoded=, decode_errors=).
    stop, if given, is a callable returning True when decoding should end
    early (used by the estimator); the frames decoded so far are kept.
    """
    packets_read = 0
    frames_decoded = 0
    decode_errors = 0
    frame_index = 0
    for packet in container.demux(stream):
        if packet.size:
            packets_read += 1
        try:
            frames = packet.decode()
        except av.FFmpegError as e:
            decode_errors += 1
            if on_decode_error:
                on_decode_error(packets_read, packet.pts, e)
            continue
        for frame in frames:
            frames_decoded += 1
            on_frame(frame_index, frame)
            frame_index += 1
        if stop is not None and stop():
            break
    return {
        "packets_read": packets_read,
        "frames_decoded": frames_decoded,
        "decode_errors": decode_errors,
    }

# ---------------- WORKER ----------------
def process_video(args):
    (
        video_path,
        case_dir,
        log_queue,
        no_frames,
        resolved_mode,
        progress_queue,
        estimated_frames,
    ) = args

    setup_worker_logging(log_queue)
    logger = logging.getLogger(__name__)

    video_name = os.path.splitext(os.path.basename(video_path))[0]
    video_dir = os.path.join(case_dir, video_name)
    frames_dir = os.path.join(video_dir, "frames")
    os.makedirs(video_dir, exist_ok=True)

    logger.info(f"[{video_name}] Processing video: {video_path}")

    def report(done, frames_count, packets=0, extra=None):
        if progress_queue is None:
            return
        msg = {
            "video": video_path,
            "name": video_name,
            "frames": frames_count,
            "packets": packets,
            "estimated_frames": estimated_frames,
            "done": done,
        }
        if extra:
            msg.update(extra)
        try:
            progress_queue.put(msg)
        except Exception:
            pass

    try:
        container, stream = _open_video_stream(video_path)
    except Exception as e:
        logger.error(f"[{video_name}] Failed to open: {e}")
        report(True, 0, extra={"error": str(e)})
        return None

    codec = stream.codec_context.codec

    # ======================================================
    # DECODE MODE
    # ======================================================
    if not no_frames:
        os.makedirs(frames_dir, exist_ok=True)

    frames = []
    conversion_failures = 0
    last_report = [time.monotonic(), 0]
    stats = {"packets_read": 0, "frames_decoded": 0, "decode_errors": 0}

    def on_frame(frame_index, frame):
        nonlocal conversion_failures
        try:
            record = _hash_frame(frame, frame_index, frames_dir, no_frames)
        except Exception as e:
            conversion_failures += 1
            logger.warning(
                f"[{video_name}] Frame {frame_index}: could not convert to rgb24 - {e}. Skipping."
            )
            return
        frames.append(record)
        now = time.monotonic()
        if progress_queue is not None and now - last_report[0] >= PROGRESS_INTERVAL_SECONDS:
            last_report[0] = now
            report(False, frame_index + 1)

    def on_decode_error(packet_number, packet_pts, error):
        logger.warning(
            f"[{video_name}] Decoder rejected packet {packet_number} (pts {packet_pts}): {error}. "
            f"{len(frames)} frames decoded so far; continuing with the next packet."
        )

    try:
        stats = decode_frames(container, stream, on_frame, on_decode_error)
    except av.FFmpegError as e:
        # An error from the demuxer (av_read_frame) rather than the decoder.
        # The demux generator cannot be resumed, so decoding ends here and
        # every frame decoded before the error is kept.
        logger.error(
            f"[{video_name}] Demuxer error after {len(frames)} frames: {e}. "
            f"Frames after this point could not be read."
        )
        stats["demux_error"] = str(e)
    finally:
        thread_type = str(stream.codec_context.thread_type)
        thread_count = stream.codec_context.thread_count
        container.close()

    if not frames:
        logger.warning(f"[{video_name}] No frames decoded")
        report(True, 0, stats["packets_read"], extra={"error": "No frames decoded"})
        return None

    if stats["decode_errors"]:
        logger.warning(
            f"[{video_name}] Packets rejected by the decoder: {stats['decode_errors']} "
            f"(of {stats['packets_read']} read). Frame count reflects decodable frames only."
        )
    if conversion_failures:
        logger.warning(
            f"[{video_name}] Frames decoded but not convertible to rgb24: {conversion_failures}"
        )

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

    result = {
        "video": video_path,
        "mode": resolved_mode,
        "frames": len(frames),
        "frames_decoded": stats["frames_decoded"],
        "frames_conversion_failed": conversion_failures,
        "packets_read": stats["packets_read"],
        "corrupt_packets": stats["decode_errors"],
        "demux_error": stats.get("demux_error"),
        "codec": codec.name,
        "codec_long": codec.long_name,
        "pixel_format": stream.codec_context.format.name if stream.codec_context.format else None,
        "decode_thread_type": thread_type,
        "decode_thread_count": thread_count,
        "total_duration": total_duration,
        "average_fps": average_fps,
        "frames_csv": csv_path,
    }
    report(True, len(frames), stats["packets_read"], extra={"result": result})
    return result

# ---------------- ESTIMATE ----------------
def estimate_video(video_path, no_frames):
    """Time the real per-frame pipeline on the first frames of a video and
    extrapolate to the whole file. Returns a dict; see estimate_case()."""
    info = probe_video(video_path)
    est = {
        "video": video_path,
        "name": info["name"],
        "ok": info["ok"],
        "error": info["error"],
        "codec": info["codec"],
        "width": info["width"],
        "height": info["height"],
        "duration_seconds": info["duration_seconds"],
        "estimated_frames": info["estimated_frames"],
        "estimate_basis": info["estimate_basis"],
        "sampled_frames": 0,
        "seconds_per_frame": None,
        "bytes_per_frame": None,
        "estimated_seconds": None,
        "estimated_bytes": None,
    }
    if not info["ok"]:
        return est

    tmp_dir = tempfile.mkdtemp(prefix="fcg_estimate_")
    try:
        container, stream = _open_video_stream(video_path)
        sampled = 0
        png_bytes = 0
        started = time.monotonic()

        def on_frame(frame_index, frame):
            nonlocal sampled, png_bytes
            record = _hash_frame(frame, frame_index, tmp_dir, no_frames)
            sampled += 1
            if record["image_file"]:
                path = os.path.join(tmp_dir, record["image_file"])
                png_bytes += os.path.getsize(path)
                os.remove(path)

        def stop():
            elapsed = time.monotonic() - started
            return sampled >= ESTIMATE_MAX_FRAMES or (
                sampled >= ESTIMATE_MIN_FRAMES and elapsed >= ESTIMATE_TIME_BUDGET_SECONDS
            )

        try:
            decode_frames(container, stream, on_frame, stop=stop)
        except av.FFmpegError as e:
            est["error"] = str(e)
        finally:
            elapsed = time.monotonic() - started
            container.close()

        est["sampled_frames"] = sampled
        if sampled:
            spf = elapsed / sampled
            est["seconds_per_frame"] = spf
            if not no_frames:
                est["bytes_per_frame"] = png_bytes / sampled
            if est["estimated_frames"]:
                est["estimated_seconds"] = spf * est["estimated_frames"]
                if est["bytes_per_frame"] is not None:
                    est["estimated_bytes"] = est["bytes_per_frame"] * est["estimated_frames"]
    except Exception as e:
        est["ok"] = False
        est["error"] = str(e)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
    return est

def estimate_case(video_files, no_frames, workers=MAX_WORKERS, progress_callback=None):
    """Estimate run time and disk use for a set of videos.

    The per-video estimate multiplies the measured seconds-per-frame from a
    short sample (decoded through the same code as a real run) by the frame
    count the container reports. Wall-clock time assumes `workers` videos
    are processed at once, scheduled longest first, which is how the worker
    pool behaves. The figures are approximate: the sample is small, the
    container's frame count can differ from the decoded count, and parallel
    workers compete for disk and CPU.
    """
    per_video = []
    for idx, vp in enumerate(video_files):
        if progress_callback:
            progress_callback(idx, len(video_files), vp)
        per_video.append(estimate_video(vp, no_frames))

    durations = sorted(
        [e["estimated_seconds"] for e in per_video if e["estimated_seconds"]],
        reverse=True,
    )
    lanes = [0.0] * max(1, min(workers, len(video_files)))
    for d in durations:
        lanes[lanes.index(min(lanes))] += d
    wall = max(lanes) if durations else None

    known = [e for e in per_video if e["estimated_frames"]]
    unknown = [e for e in per_video if e["ok"] and not e["estimated_frames"]]
    failed = [e for e in per_video if not e["ok"]]
    return {
        "mode": "decode-only" if no_frames else "full-forensic",
        "workers": len(lanes),
        "videos": per_video,
        "estimated_total_frames": sum(e["estimated_frames"] for e in known) or None,
        "estimated_total_seconds": sum(durations) or None,
        "estimated_wall_seconds": wall,
        "estimated_total_bytes": sum(e["estimated_bytes"] or 0 for e in known) if not no_frames else 0,
        "videos_without_frame_estimate": [e["video"] for e in unknown],
        "videos_failed_to_open": [e["video"] for e in failed],
    }

def format_seconds(seconds):
    if seconds is None:
        return "unknown"
    seconds = int(round(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h {m:02d}m {s:02d}s"
    if m:
        return f"{m}m {s:02d}s"
    return f"{s}s"

def format_bytes(n):
    if n is None:
        return "unknown"
    n = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{int(n)} B"
        n /= 1024

def format_estimate(estimate):
    lines = []
    for e in estimate["videos"]:
        name = os.path.basename(e["video"])
        if not e["ok"]:
            lines.append(f"  {name}: cannot open ({e['error']})")
            continue
        dims = f"{e['width']}x{e['height']} {e['codec']}"
        if e["estimated_frames"]:
            per = f"{e['seconds_per_frame'] * 1000:.1f} ms/frame" if e["seconds_per_frame"] else "n/a"
            disk = f", ~{format_bytes(e['estimated_bytes'])} of PNGs" if e["estimated_bytes"] else ""
            lines.append(
                f"  {name}: {dims}, ~{e['estimated_frames']:,} frames "
                f"({e['estimate_basis']}), ~{format_seconds(e['estimated_seconds'])} "
                f"at {per} over {e['sampled_frames']} sampled frames{disk}"
            )
        else:
            lines.append(f"  {name}: {dims}, frame count not available from metadata, cannot estimate")
    header = [
        f"Estimate for {len(estimate['videos'])} video(s), mode {estimate['mode']}, "
        f"{estimate['workers']} parallel worker(s):",
    ]
    footer = [
        f"  Estimated frames in total: "
        f"{estimate['estimated_total_frames']:,}" if estimate["estimated_total_frames"] else
        "  Estimated frames in total: unknown",
        f"  Estimated wall-clock time: {format_seconds(estimate['estimated_wall_seconds'])}",
    ]
    if estimate["mode"] == "full-forensic":
        footer.append(f"  Estimated disk use for frame images: {format_bytes(estimate['estimated_total_bytes'])}")
    footer.append(
        "  These figures are approximations from a short sample and the container's own frame count. "
        "The reported frame count always comes from decoding the whole file."
    )
    return "\n".join(header + lines + footer)

# ---------------- CASE RUNNER ----------------
def run_case(
    input_path,
    output_root,
    no_frames=False,
    estimate=None,
    progress_callback=None,
    log_handlers=None,
    console=True,
    cancel_event=None,
    workers=MAX_WORKERS,
):
    """Process every video under input_path into a new case directory.

    progress_callback(msg) receives progress dicts from the workers (see
    process_video.report). log_handlers is a list of extra logging handlers
    that receive every log record. cancel_event is a threading.Event; when
    set, the worker pool is terminated and the manifest records the run as
    cancelled. Returns the manifest dict (with "manifest_path" and
    "case_dir" keys).
    """
    case_start_utc = datetime.now(timezone.utc)
    case_id = f"case_{case_start_utc.strftime('%Y%m%dT%H%M%SZ')}"
    case_dir = os.path.join(output_root, case_id)
    os.makedirs(case_dir, exist_ok=True)

    log_path = os.path.join(case_dir, "case_processing.log")
    manager = Manager()
    log_queue = manager.Queue()
    progress_queue = manager.Queue() if progress_callback else None

    file_handler = logging.FileHandler(log_path, mode="w", encoding="utf-8")
    formatter = logging.Formatter("%(asctime)s [%(processName)s] %(levelname)s: %(message)s")
    file_handler.setFormatter(formatter)

    handlers = [file_handler]
    if console:
        stream_handler = logging.StreamHandler(sys.stdout)
        stream_handler.setFormatter(formatter)
        handlers.append(stream_handler)
    for h in (log_handlers or []):
        if h.formatter is None:
            h.setFormatter(formatter)
        handlers.append(h)

    # QueueListener is the single point that dispatches to the handlers,
    # so the root logger must only have the QueueHandler, nothing else.
    listener = QueueListener(log_queue, *handlers, respect_handler_level=True)
    listener.start()

    main_qh = QueueHandler(log_queue)
    main_logger = logging.getLogger()
    previous_handlers = list(main_logger.handlers)
    main_logger.handlers.clear()  # Ensure no pre-existing handlers cause duplicate output
    main_logger.setLevel(logging.INFO)
    main_logger.addHandler(main_qh)

    status = "completed"
    results = []
    skipped = []
    drain_stop = threading.Event()

    def drain_progress():
        while not drain_stop.is_set():
            try:
                msg = progress_queue.get(timeout=0.2)
            except Exception:
                continue
            try:
                progress_callback(msg)
            except Exception:
                pass
        # Deliver anything still queued.
        while True:
            try:
                progress_callback(progress_queue.get_nowait())
            except Exception:
                break

    drain_thread = None
    try:
        main_logger.info(f"Case ID: {case_id}")
        main_logger.info(
            f"Tool version: {TOOL_VERSION} | PyAV {av.__version__} | "
            f"decoder threads: {DECODE_THREAD_COUNT} ({DECODE_THREAD_TYPE})"
        )

        skipped = []
        video_files = get_video_files(input_path, skipped)
        for path, reason in skipped:
            main_logger.warning(f"Skipped, FFmpeg cannot open it: {path} ({reason})")
        if not video_files:
            main_logger.error("No supported video files found")
            status = "no_videos"
        else:
            resolved_mode = "decode-only" if no_frames else "full-forensic"
            main_logger.info(f"Mode: {resolved_mode} | Videos: {len(video_files)} | Workers: {workers}")
            if estimate:
                main_logger.info(
                    f"Pre-run estimate: ~{estimate['estimated_total_frames'] or 'unknown'} frames, "
                    f"~{format_seconds(estimate['estimated_wall_seconds'])} wall-clock"
                    + (f", ~{format_bytes(estimate['estimated_total_bytes'])} of frame images" if not no_frames else "")
                )

            est_by_video = {}
            if estimate:
                est_by_video = {e["video"]: e.get("estimated_frames") for e in estimate["videos"]}

            worker_args = [
                (vp, case_dir, log_queue, no_frames, resolved_mode, progress_queue, est_by_video.get(vp))
                for vp in video_files
            ]

            if progress_queue is not None:
                drain_thread = threading.Thread(target=drain_progress, daemon=True)
                drain_thread.start()

            try:
                with Pool(processes=workers) as pool:
                    async_result = pool.map_async(process_video, worker_args)
                    while not async_result.ready():
                        async_result.wait(0.25)
                        if cancel_event is not None and cancel_event.is_set():
                            main_logger.warning("Cancellation requested; terminating workers")
                            pool.terminate()
                            status = "cancelled"
                            break
                    if status != "cancelled":
                        results = async_result.get()
            except Exception as e:
                main_logger.error(f"Multiprocessing pool error: {e}")
                status = "error"
                results = []

        manifest = {
            "case_id": case_id,
            "case_start_utc": case_start_utc.isoformat(),
            "case_end_utc": datetime.now(timezone.utc).isoformat(),
            "status": status,
            "tool_version": TOOL_VERSION,
            "platform": platform.platform(),
            "python_version": platform.python_version(),
            "pyav_version": av.__version__,
            "ffmpeg_libraries": av.library_versions,
            "processing_modes": {
                "mode": "decode-only" if no_frames else "full-forensic",
                "decode_thread_type": DECODE_THREAD_TYPE,
                "decode_thread_count": DECODE_THREAD_COUNT,
                "workers": workers,
            },
            "hash_algorithm": HASH_ALGORITHM,
            "hash_columns": {
                f"native_{HASH_ALGORITHM}": "decoder output planes in the stream's pixel format, "
                                            "line padding removed; comparable across platforms "
                                            "and with ffmpeg -f framehash",
                f"decoded_{HASH_ALGORITHM}": "rgb24 conversion of the frame by libswscale; "
                                             "reproducible on the same platform only",
                f"image_{HASH_ALGORITHM}": "rgb24 pixels read back from the written PNG",
            },
            "input_path": input_path,
            "files_skipped": [{"path": p, "reason": r} for p, r in skipped],
            "pre_run_estimate": estimate,
            "videos_processed": [r for r in results if r],
            "log_file": log_path,
        }

        manifest_path = os.path.join(case_dir, "case_provenance_manifest.json")
        with open(manifest_path, "w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=2)

        if status == "completed":
            main_logger.info("All videos processed")
        else:
            main_logger.warning(f"Run ended with status: {status}")
        main_logger.info(f"Case provenance manifest written to: {manifest_path}")
        manifest["manifest_path"] = manifest_path
        manifest["case_dir"] = case_dir
        return manifest

    finally:
        # Always stop the listener last, after every log message has been
        # emitted, so nothing queued after pool completion is silently dropped.
        if drain_thread is not None:
            drain_stop.set()
            drain_thread.join(timeout=5)
        listener.stop()
        main_logger.removeHandler(main_qh)
        for h in previous_handlers:
            main_logger.addHandler(h)
        file_handler.close()
        manager.shutdown()

# ---------------- MAIN ----------------
def main(argv=None):
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
            "\n"
            "Estimating:\n"
            "  --estimate       Sample the first frames of each video, print an\n"
            "                   estimated frame count, run time and disk use, and\n"
            "                   record the estimate in the manifest before running.\n"
            "  --estimate-only  Print the estimate and exit without processing.\n"
        )
    )

    parser.add_argument("-i", "--input", required=True, help="Input video file or directory")
    parser.add_argument("-o", "--output", required=False, help="Output directory for case results")

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
    est_group = parser.add_mutually_exclusive_group()
    est_group.add_argument("--estimate", action="store_true",
                           help="Print a run-time and disk estimate before processing")
    est_group.add_argument("--estimate-only", action="store_true",
                           help="Print the estimate and exit without processing")

    args = parser.parse_args(argv)

    if not args.estimate_only and not args.output:
        parser.error("-o/--output is required unless --estimate-only is given")

    estimate = None
    if args.estimate or args.estimate_only:
        skipped = []
        video_files = get_video_files(args.input, skipped)
        for path, reason in skipped:
            print(f"Skipped, FFmpeg cannot open it: {path} ({reason})", file=sys.stderr)
        if not video_files:
            print("No supported video files found", file=sys.stderr)
            return 1
        estimate = estimate_case(video_files, args.no_frames)
        print(format_estimate(estimate))
        if args.estimate_only:
            return 0
        print()

    manifest = run_case(args.input, args.output, no_frames=args.no_frames, estimate=estimate)
    return 0 if manifest["status"] == "completed" else 1


if __name__ == "__main__":
    from multiprocessing import freeze_support
    freeze_support()
    sys.exit(main())
