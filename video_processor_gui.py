#!/usr/bin/env python3
"""Graphical front end for video_processor_cli.py.

Everything that touches a video goes through the functions in
video_processor_cli, so a run started here produces exactly the same case
folder, CSV files, PNG images, log and provenance manifest as the command
line tool. The window adds: file and folder pickers, a pre-run estimate of
frames, time and disk use, live progress, the processing log, and a stop
button.
"""

import logging
import os
import platform
import queue
import subprocess
import sys
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText

import video_processor_cli as vpc


class QueueLogHandler(logging.Handler):
    """Puts formatted log lines on a queue the Tk thread drains."""

    def __init__(self, q):
        super().__init__()
        self.q = q

    def emit(self, record):
        try:
            self.q.put(("log", self.format(record)))
        except Exception:
            pass


class App(tk.Tk):
    POLL_MS = 150

    def __init__(self):
        super().__init__()
        self.title(f"Forensic Video Processor {vpc.TOOL_VERSION}")
        self.minsize(900, 640)

        self.events = queue.Queue()
        self.worker = None
        self.cancel_event = None
        self.estimate = None
        self.estimate_mode = None
        self.run_started = None
        self.progress_by_video = {}
        self.result_manifest = None
        self.video_files = []

        self._build()
        self.after(self.POLL_MS, self._poll)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ---------------- layout ----------------
    def _build(self):
        pad = {"padx": 6, "pady": 4}
        top = ttk.Frame(self)
        top.pack(fill="x", **pad)
        top.columnconfigure(1, weight=1)

        ttk.Label(top, text="Input:").grid(row=0, column=0, sticky="w")
        self.input_var = tk.StringVar()
        ttk.Entry(top, textvariable=self.input_var).grid(row=0, column=1, sticky="ew", padx=4)
        ttk.Button(top, text="File...", command=self._pick_file).grid(row=0, column=2)
        ttk.Button(top, text="Folder...", command=self._pick_folder).grid(row=0, column=3, padx=(4, 0))

        ttk.Label(top, text="Output:").grid(row=1, column=0, sticky="w")
        self.output_var = tk.StringVar()
        ttk.Entry(top, textvariable=self.output_var).grid(row=1, column=1, sticky="ew", padx=4)
        ttk.Button(top, text="Browse...", command=self._pick_output).grid(row=1, column=2, columnspan=2, sticky="ew")

        modes = ttk.Frame(self)
        modes.pack(fill="x", **pad)
        ttk.Label(modes, text="Mode:").pack(side="left")
        self.mode_var = tk.StringVar(value="full-forensic")
        ttk.Radiobutton(modes, text="Full Forensic (write PNG frames)", variable=self.mode_var,
                        value="full-forensic").pack(side="left", padx=8)
        ttk.Radiobutton(modes, text="Decode-Only (hashes, no images)", variable=self.mode_var,
                        value="decode-only").pack(side="left", padx=8)
        ttk.Label(modes, text=f"Workers: {vpc.MAX_WORKERS}").pack(side="right")

        buttons = ttk.Frame(self)
        buttons.pack(fill="x", **pad)
        self.btn_estimate = ttk.Button(buttons, text="Estimate", command=self._start_estimate)
        self.btn_estimate.pack(side="left")
        self.btn_start = ttk.Button(buttons, text="Start", command=self._start_run)
        self.btn_start.pack(side="left", padx=6)
        self.btn_stop = ttk.Button(buttons, text="Stop", command=self._stop_run, state="disabled")
        self.btn_stop.pack(side="left")
        self.btn_open = ttk.Button(buttons, text="Open Output Folder", command=self._open_output, state="disabled")
        self.btn_open.pack(side="right")

        est_frame = ttk.LabelFrame(self, text="Estimate")
        est_frame.pack(fill="x", **pad)
        self.estimate_text = tk.Text(est_frame, height=6, wrap="word", state="disabled")
        self.estimate_text.pack(fill="x", padx=4, pady=4)

        prog = ttk.LabelFrame(self, text="Progress")
        prog.pack(fill="both", expand=False, **pad)
        self.status_var = tk.StringVar(value="Idle")
        ttk.Label(prog, textvariable=self.status_var).pack(anchor="w", padx=4)
        self.progress = ttk.Progressbar(prog, mode="determinate", maximum=1000)
        self.progress.pack(fill="x", padx=4, pady=2)
        cols = ("video", "status", "frames", "estimated", "rejected")
        self.tree = ttk.Treeview(prog, columns=cols, show="headings", height=6)
        for c, w in zip(cols, (380, 110, 90, 90, 130)):
            self.tree.heading(c, text={"video": "Video", "status": "Status", "frames": "Frames",
                                       "estimated": "Estimated", "rejected": "Rejected packets"}[c])
            self.tree.column(c, width=w, anchor="w" if c == "video" else "e")
        self.tree.pack(fill="both", expand=True, padx=4, pady=4)

        logf = ttk.LabelFrame(self, text="Log")
        logf.pack(fill="both", expand=True, **pad)
        self.log = ScrolledText(logf, height=12, wrap="none", state="disabled",
                                font=("Menlo" if platform.system() == "Darwin" else "Consolas", 10))
        self.log.pack(fill="both", expand=True, padx=4, pady=4)

    # ---------------- pickers ----------------
    def _pick_file(self):
        p = filedialog.askopenfilename(title="Select a video file")
        if p:
            self.input_var.set(p)
            self._reset_estimate()

    def _pick_folder(self):
        p = filedialog.askdirectory(title="Select a folder of video files")
        if p:
            self.input_var.set(p)
            self._reset_estimate()

    def _pick_output(self):
        p = filedialog.askdirectory(title="Select the output directory")
        if p:
            self.output_var.set(p)

    # ---------------- helpers ----------------
    def _append_log(self, line):
        self.log.configure(state="normal")
        self.log.insert("end", line + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _set_estimate_text(self, text):
        self.estimate_text.configure(state="normal")
        self.estimate_text.delete("1.0", "end")
        self.estimate_text.insert("end", text)
        self.estimate_text.configure(state="disabled")

    def _reset_estimate(self):
        self.estimate = None
        self.estimate_mode = None
        self._set_estimate_text("")

    def _busy(self, running):
        state = "disabled" if running else "normal"
        self.btn_estimate.configure(state=state)
        self.btn_start.configure(state=state)
        self.btn_stop.configure(state="normal" if running and self.cancel_event is not None else "disabled")

    def _collect_inputs(self, need_output):
        inp = self.input_var.get().strip()
        if not inp or not os.path.exists(inp):
            messagebox.showerror("Input", "Choose an existing video file or folder.")
            return None
        out = self.output_var.get().strip()
        if need_output and not out:
            messagebox.showerror("Output", "Choose an output directory.")
            return None
        skipped = []
        files = vpc.get_video_files(inp, skipped)
        for path, reason in skipped:
            self._append_log(f"Skipped, FFmpeg cannot open it: {path} ({reason})")
        if not files:
            messagebox.showerror("Input", "No file that FFmpeg can open was found at that path.")
            return None
        self.video_files = files
        return inp, out, files

    def _no_frames(self):
        return self.mode_var.get() == "decode-only"

    # ---------------- estimate ----------------
    def _start_estimate(self):
        got = self._collect_inputs(need_output=False)
        if not got:
            return
        _, _, files = got
        no_frames = self._no_frames()
        self._busy(True)
        self._set_estimate_text("Sampling the first frames of each video...")
        self.status_var.set("Estimating")

        def work():
            try:
                est = vpc.estimate_case(
                    files, no_frames,
                    progress_callback=lambda i, n, v: self.events.put(
                        ("status", f"Estimating {i + 1}/{n}: {os.path.basename(v)}")),
                )
                self.events.put(("estimate", est, no_frames))
            except Exception as e:
                self.events.put(("error", f"Estimate failed: {e}"))

        threading.Thread(target=work, daemon=True).start()

    # ---------------- run ----------------
    def _start_run(self):
        got = self._collect_inputs(need_output=True)
        if not got:
            return
        inp, out, files = got
        no_frames = self._no_frames()
        estimate = self.estimate if self.estimate_mode == no_frames else None

        if not no_frames and estimate and estimate.get("estimated_total_bytes"):
            gb = estimate["estimated_total_bytes"] / (1024 ** 3)
            if gb >= 5 and not messagebox.askyesno(
                "Disk use",
                f"Full forensic mode is estimated to write about {gb:.1f} GB of PNG frames.\nContinue?",
            ):
                return

        self.cancel_event = threading.Event()
        self.result_manifest = None
        self.run_started = time.monotonic()
        self.progress_by_video = {}
        for item in self.tree.get_children():
            self.tree.delete(item)
        for f in files:
            est_frames = None
            if estimate:
                est_frames = next((e.get("estimated_frames") for e in estimate["videos"] if e["video"] == f), None)
            self.progress_by_video[f] = {"frames": 0, "estimated": est_frames, "done": False, "rejected": 0}
            self.tree.insert("", "end", iid=f, values=(os.path.basename(f), "queued", 0,
                                                         est_frames if est_frames else "?", 0))
        self.progress.configure(value=0)
        self._busy(True)
        self.btn_open.configure(state="disabled")
        self.status_var.set("Starting")

        handler = QueueLogHandler(self.events)

        def work():
            try:
                manifest = vpc.run_case(
                    inp, out, no_frames=no_frames, estimate=estimate,
                    progress_callback=lambda msg: self.events.put(("progress", msg)),
                    log_handlers=[handler], console=False, cancel_event=self.cancel_event,
                )
                self.events.put(("finished", manifest))
            except Exception as e:
                self.events.put(("error", f"Run failed: {e}"))

        self.worker = threading.Thread(target=work, daemon=True)
        self.worker.start()

    def _stop_run(self):
        if self.cancel_event is not None and messagebox.askyesno(
            "Stop", "Stop processing? Videos still in progress will be incomplete and the "
                    "manifest will record the run as cancelled."):
            self.cancel_event.set()
            self.status_var.set("Stopping...")
            self.btn_stop.configure(state="disabled")

    def _open_output(self):
        path = None
        if self.result_manifest:
            path = self.result_manifest.get("case_dir")
        if not path:
            path = self.output_var.get().strip()
        if not path or not os.path.isdir(path):
            return
        try:
            if platform.system() == "Windows":
                os.startfile(path)  # noqa
            elif platform.system() == "Darwin":
                subprocess.Popen(["open", path])
            else:
                subprocess.Popen(["xdg-open", path])
        except Exception as e:
            messagebox.showerror("Open folder", str(e))

    # ---------------- event pump ----------------
    def _poll(self):
        try:
            while True:
                ev = self.events.get_nowait()
                kind = ev[0]
                if kind == "log":
                    self._append_log(ev[1])
                elif kind == "status":
                    self.status_var.set(ev[1])
                elif kind == "estimate":
                    self.estimate, self.estimate_mode = ev[1], ev[2]
                    self._set_estimate_text(vpc.format_estimate(self.estimate))
                    self.status_var.set("Estimate ready")
                    self._busy(False)
                elif kind == "progress":
                    self._on_progress(ev[1])
                elif kind == "finished":
                    self._on_finished(ev[1])
                elif kind == "error":
                    self._append_log(ev[1])
                    messagebox.showerror("Error", ev[1])
                    self.status_var.set("Error")
                    self.cancel_event = None
                    self._busy(False)
        except queue.Empty:
            pass
        if self.worker is not None and self.worker.is_alive():
            self._update_overall()
        self.after(self.POLL_MS, self._poll)

    def _on_progress(self, msg):
        v = msg.get("video")
        entry = self.progress_by_video.get(v)
        if entry is None:
            return
        entry["frames"] = msg.get("frames", entry["frames"])
        entry["done"] = msg.get("done", False)
        if msg.get("estimated_frames") and not entry["estimated"]:
            entry["estimated"] = msg["estimated_frames"]
        result = msg.get("result")
        if result:
            entry["rejected"] = result.get("corrupt_packets", 0)
            status = "done"
            if result.get("corrupt_packets"):
                status = "done, packets rejected"
            if result.get("demux_error"):
                status = "done, read error"
        elif msg.get("error"):
            status = "failed"
        elif entry["done"]:
            status = "done"
        else:
            status = "decoding"
        if self.tree.exists(v):
            self.tree.item(v, values=(os.path.basename(v), status, entry["frames"],
                                      entry["estimated"] if entry["estimated"] else "?", entry["rejected"]))

    def _update_overall(self):
        done_frames = sum(e["frames"] for e in self.progress_by_video.values())
        est_total = sum(e["estimated"] or 0 for e in self.progress_by_video.values())
        all_known = all(e["estimated"] for e in self.progress_by_video.values()) if self.progress_by_video else False
        elapsed = time.monotonic() - (self.run_started or time.monotonic())
        finished = sum(1 for e in self.progress_by_video.values() if e["done"])
        text = f"Running: {finished}/{len(self.progress_by_video)} videos finished, {done_frames:,} frames, elapsed {vpc.format_seconds(elapsed)}"
        if all_known and est_total:
            frac = min(done_frames / est_total, 1.0)
            self.progress.configure(mode="determinate", value=int(frac * 1000))
            if done_frames > 0 and frac < 1.0:
                remaining = elapsed / done_frames * (est_total - done_frames)
                text += f", about {vpc.format_seconds(remaining)} remaining (from the metadata frame count)"
        else:
            self.progress.configure(mode="determinate", value=int(1000 * finished / max(1, len(self.progress_by_video))))
        self.status_var.set(text)

    def _on_finished(self, manifest):
        self.result_manifest = manifest
        self.worker = None
        self.cancel_event = None
        self._busy(False)
        self.btn_open.configure(state="normal")
        elapsed = time.monotonic() - (self.run_started or time.monotonic())
        status = manifest.get("status")
        vids = manifest.get("videos_processed", [])
        total = sum(v["frames"] for v in vids)
        rejected = sum(v.get("corrupt_packets", 0) for v in vids)
        self.progress.configure(value=1000 if status == "completed" else self.progress["value"])
        summary = (f"Finished with status '{status}' in {vpc.format_seconds(elapsed)}: "
                   f"{len(vids)} video(s), {total:,} frames")
        if rejected:
            summary += f", {rejected} packet(s) rejected by the decoder (see log)"
        expected = len(self.video_files)
        if len(vids) < expected:
            summary += f", {expected - len(vids)} video(s) produced no result (see log)"
        self.status_var.set(summary)
        self._append_log(summary)
        self._append_log(f"Manifest: {manifest.get('manifest_path')}")

    def _on_close(self):
        if self.worker is not None and self.worker.is_alive():
            if not messagebox.askyesno("Quit", "A run is in progress. Stop it and quit?"):
                return
            if self.cancel_event is not None:
                self.cancel_event.set()
            self.worker.join(timeout=10)
        self.destroy()


def main():
    app = App()
    app.mainloop()


if __name__ == "__main__":
    from multiprocessing import freeze_support
    freeze_support()
    main()
