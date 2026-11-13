#!/usr/bin/env python3
"""Graphical front end for video_processor_cli.py.

Everything that touches a video goes through the functions in
video_processor_cli, so a run started here produces exactly the same case
folder, CSV files, PNG images, log and provenance manifest as the command
line tool. The window adds: file and folder pickers, a pre-run estimate of
frames, time and disk use, live progress, the processing log, and a stop
button.

The window is drawn with CustomTkinter (MIT licence) when it is installed, in
the look of AppWalk and the probes: a header with the logo and a light/dark
switch, panels, and one blue button. Without it the plain Tk window opens;
both run the same code underneath (the Runner class).
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

try:
    import customtkinter as ctk
except ImportError:
    ctk = None

HERE = os.path.dirname(os.path.abspath(__file__))
ASSETS = os.path.join(getattr(sys, "_MEIPASS", HERE), "assets")
LOGO = "frame-counts-galore"


def window_icon(win):
    """The logo as the icon of the window (dock, taskbar, title bar). On Windows the .ico goes on
    through iconbitmap first: CustomTkinter puts its own icon on a window about 200 ms after it
    opens unless iconbitmap was called (customtkinter 6.0.0, ctk_tk.py). Returns True when set."""
    try:
        icons = [tk.PhotoImage(master=win, file=os.path.join(ASSETS, f"{LOGO}_{s}.png")) for s in (256, 64, 32)]
        win.iconphoto(True, *icons)
        win._logo_icons = icons   # Tk drops an image nothing in Python holds
    except (tk.TclError, OSError):
        return False
    if platform.system() == "Windows":
        try:
            win.iconbitmap(os.path.join(ASSETS, f"{LOGO}.ico"))
        except tk.TclError:
            pass
    return True


def smoke_check(win, kind):
    """For a packaged build: when FRAMES_GALORE_SMOKE names a file, the window opens, writes there
    what it loaded, and closes. Nothing else changes."""
    path = os.environ.get("FRAMES_GALORE_SMOKE")
    if not path:
        return

    def report():
        import json
        try:
            facts = {"window": kind, "tk": str(tk.TkVersion), "logo": getattr(win, "logo", None) is not None,
                     "icon": bool(getattr(win, "_logo_icons", None)), "assets": os.path.isdir(ASSETS),
                     "frozen": bool(getattr(sys, "frozen", False))}
            with open(path, "w", encoding="utf-8") as f:
                json.dump(facts, f)
        finally:
            win.destroy()
    win.after(2000, report)


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


class Runner:
    """What the window does, whichever toolkit draws it: the subclass gives _build()."""
    POLL_MS = 150

    def _start(self):
        self.title(f"Forensic Video Processor {vpc.TOOL_VERSION}")
        self.logo = None
        window_icon(self)
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


    # ---------------- pickers ----------------
    def _pick_file(self):
        p = filedialog.askopenfilename(title="Select a video file")
        if p:
            self.input_var.set(os.path.normpath(p))
            self._reset_estimate()

    def _pick_folder(self):
        p = filedialog.askdirectory(title="Select a folder of video files")
        if p:
            self.input_var.set(os.path.normpath(p))
            self._reset_estimate()

    def _pick_output(self):
        p = filedialog.askdirectory(title="Select the output directory")
        if p:
            self.output_var.set(os.path.normpath(p))

    # ---------------- helpers ----------------
    def _append_log(self, line):
        self.log.configure(state="normal")
        self.log.insert("end", line + "\n")
        self.log.see("end")
        self.log.yview_moveto(1.0)   # the last line at the bottom, not alone at the top
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
        # Tk dialogs return forward-slash paths on Windows, and os.startfile
        # cannot open a forward-slash UNC path; normpath fixes both.
        path = os.path.normpath(path)
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


class App(Runner, tk.Tk):
    """The plain Tk window, opened when CustomTkinter is not installed."""

    def __init__(self):
        tk.Tk.__init__(self)
        self.minsize(900, 640)
        self._start()

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
        self.estimate_text = ScrolledText(est_frame, height=6, wrap="word", state="disabled")
        self.estimate_text.pack(fill="x", padx=4, pady=4)

        prog = ttk.LabelFrame(self, text="Progress")
        prog.pack(fill="both", expand=False, **pad)
        self.status_var = tk.StringVar(value="Idle")
        ttk.Label(prog, textvariable=self.status_var).pack(anchor="w", padx=4)
        self.progress = ttk.Progressbar(prog, mode="determinate", maximum=1000)
        self.progress.pack(fill="x", padx=4, pady=2)
        cols = ("video", "status", "frames", "estimated", "rejected")
        self.tree = ttk.Treeview(prog, columns=cols, show="headings", height=6)
        for c, w in zip(cols, (330, 170, 80, 90, 120)):
            self.tree.heading(c, text={"video": "Video", "status": "Status", "frames": "Frames",
                                       "estimated": "Estimated", "rejected": "Rejected packets"}[c])
            self.tree.column(c, width=w, anchor="w" if c == "video" else "e")
        self.tree.pack(fill="both", expand=True, padx=4, pady=4)

        logf = ttk.LabelFrame(self, text="Log")
        logf.pack(fill="both", expand=True, **pad)
        self.log = ScrolledText(logf, height=12, wrap="none", state="disabled",
                                font=("Menlo" if platform.system() == "Darwin" else "Consolas", 10))
        self.log.pack(fill="both", expand=True, padx=4, pady=4)


if ctk is not None:
    # Each colour is (light, dark), as in AppWalk's window; CustomTkinter picks by the appearance mode.
    BG, CARD, SOFT = ("#F3F4F6", "#111318"), ("#FFFFFF", "#1A1D23"), ("#EEF0F3", "#22262E")
    INK, MUTED, LINE = ("#111827", "#E6E8EC"), ("#6B7280", "#9AA3AF"), ("#E3E6EA", "#2C313A")
    ACCENT, ACCENT_HOVER = ("#2563EB", "#3B82F6"), ("#1D4ED8", "#2563EB")
    SELECT = ("#DCE7FD", "#24427A")

    class Primary(ctk.CTkButton):
        """The blue button. Greyed when it is off, so a pressed Start does not look ready to press again."""

        def configure(self, require_redraw=False, **kw):
            if "state" in kw:
                kw["fg_color"] = SOFT if kw["state"] == "disabled" else ACCENT
            return super().configure(require_redraw=require_redraw, **kw)

    class Progress(ctk.CTkProgressBar):
        """A progress bar that takes the plain Tk calls the run code makes: configure(mode=, value=0..1000)
        and progress["value"]."""

        def configure(self, require_redraw=False, **kw):
            value = kw.pop("value", None)
            kw.pop("maximum", None)
            out = super().configure(require_redraw=require_redraw, **kw) if kw else None
            if value is not None:
                self.set(max(0.0, min(1.0, value / 1000)))
            return out

        def __getitem__(self, key):
            return self.get() * 1000 if key == "value" else self.cget(key)

    class ModernApp(Runner, ctk.CTk):
        """The window drawn with CustomTkinter."""

        def __init__(self):
            ctk.set_appearance_mode("system")
            ctk.set_default_color_theme("blue")
            ctk.CTk.__init__(self, fg_color=BG)
            self.minsize(960, 760)
            self.geometry(f"1040x{min(900, self.winfo_screenheight() - 120)}")
            self._start()

        def _build(self):
            f_body, f_small = ctk.CTkFont(size=14), ctk.CTkFont(size=12)
            f_bold, f_title, f_big = ctk.CTkFont(size=14, weight="bold"), ctk.CTkFont(size=20, weight="bold"), \
                ctk.CTkFont(size=16, weight="bold")
            self.f_body = f_body

            def ghost(parent, text, command, **kw):
                return ctk.CTkButton(parent, text=text, command=command, font=f_body, height=34, corner_radius=10,
                                     fg_color=CARD, hover_color=SOFT, text_color=INK, border_width=1, border_color=LINE,
                                     text_color_disabled=MUTED, **kw)

            def card(parent, title, **pack):
                outer = ctk.CTkFrame(parent, fg_color=CARD, corner_radius=14, border_width=1, border_color=LINE)
                outer.pack(fill="x", pady=(0, 12), **pack)
                ctk.CTkLabel(outer, text=title, font=f_big, text_color=INK, anchor="w").pack(fill="x", padx=18, pady=(12, 0))
                inner = ctk.CTkFrame(outer, fg_color="transparent")
                inner.pack(fill="both", expand=True, padx=18, pady=(6, 14))
                return inner

            head = ctk.CTkFrame(self, fg_color=CARD, corner_radius=0)
            head.pack(fill="x")
            try:
                from PIL import Image, ImageTk  # noqa: F401  CTkImage needs ImageTk, which Ubuntu packs apart
                im = Image.open(os.path.join(ASSETS, f"{LOGO}_256.png"))
                self.logo = ctk.CTkImage(light_image=im, dark_image=im, size=(36, 36))
            except ImportError:
                try:
                    self.logo = tk.PhotoImage(master=self, file=os.path.join(ASSETS, f"{LOGO}_36.png"))
                except tk.TclError:
                    self.logo = None
            except OSError:
                self.logo = None
            if self.logo is not None:
                import warnings
                logo = ctk.CTkLabel(head, text="")
                with warnings.catch_warnings():   # a plain PhotoImage works; CustomTkinter warns it will not scale
                    warnings.simplefilter("ignore")
                    logo.configure(image=self.logo)
                logo.pack(side="left", padx=(22, 0), pady=12)
            ctk.CTkLabel(head, text="Forensic Video Processor", font=f_title, text_color=INK).pack(
                side="left", padx=(10 if self.logo else 24, 12), pady=16)
            ctk.CTkLabel(head, text=f"{vpc.TOOL_VERSION}   frames counted by decoding them", font=f_small,
                         text_color=MUTED).pack(side="left", pady=(4, 0))
            self.theme_btn = ctk.CTkSegmentedButton(head, values=["Light", "Dark"], command=self._pick_theme,
                                                    font=f_small, selected_color=ACCENT, selected_hover_color=ACCENT_HOVER)
            self.theme_btn.set("Dark" if ctk.get_appearance_mode() == "Dark" else "Light")
            self.theme_btn.pack(side="right", padx=20)
            ctk.CTkFrame(self, height=1, fg_color=LINE, corner_radius=0).pack(fill="x")

            body = ctk.CTkFrame(self, fg_color="transparent")
            body.pack(fill="both", expand=True, padx=22, pady=16)

            c = card(body, "Videos")
            c.columnconfigure(1, weight=1)
            self.input_var = tk.StringVar()
            self.output_var = tk.StringVar()
            for row, (label, var) in enumerate((("Input", self.input_var), ("Output", self.output_var))):
                ctk.CTkLabel(c, text=label, font=f_body, text_color=INK).grid(row=row, column=0, sticky="w", padx=(0, 12), pady=4)
                ctk.CTkEntry(c, textvariable=var, height=34, corner_radius=8, font=f_body, border_color=LINE,
                             fg_color=SOFT, text_color=INK).grid(row=row, column=1, sticky="ew", pady=4)
            ghost(c, "File...", self._pick_file, width=96).grid(row=0, column=2, padx=(10, 0))
            ghost(c, "Folder...", self._pick_folder, width=96).grid(row=0, column=3, padx=(8, 0))
            ghost(c, "Browse...", self._pick_output).grid(row=1, column=2, columnspan=2, sticky="ew", padx=(10, 0))
            self.mode_var = tk.StringVar(value="full-forensic")
            modes = ctk.CTkFrame(c, fg_color="transparent")
            modes.grid(row=2, column=0, columnspan=4, sticky="ew", pady=(8, 0))
            for value, text in (("full-forensic", "Full Forensic (write PNG frames)"),
                                ("decode-only", "Decode-Only (hashes, no images)")):
                ctk.CTkRadioButton(modes, text=text, variable=self.mode_var, value=value, font=f_body, text_color=INK,
                                   fg_color=ACCENT, hover_color=ACCENT_HOVER, border_color=MUTED).pack(side="left", padx=(0, 22))
            ctk.CTkLabel(modes, text=f"Workers: {vpc.MAX_WORKERS}", font=f_small, text_color=MUTED).pack(side="right")

            buttons = ctk.CTkFrame(body, fg_color="transparent")
            buttons.pack(fill="x", pady=(0, 12))
            self.btn_start = Primary(buttons, text="Start", command=self._start_run, font=f_bold, height=36, width=120,
                                     corner_radius=10, fg_color=ACCENT, hover_color=ACCENT_HOVER, text_color="#FFFFFF",
                                     text_color_disabled=MUTED)
            self.btn_start.pack(side="left")
            self.btn_estimate = ghost(buttons, "Estimate", self._start_estimate, width=110)
            self.btn_estimate.pack(side="left", padx=8)
            self.btn_stop = ghost(buttons, "Stop", self._stop_run, width=90, state="disabled")
            self.btn_stop.pack(side="left")
            self.btn_open = ghost(buttons, "Open Output Folder", self._open_output, state="disabled")
            self.btn_open.pack(side="right")

            c = card(body, "Estimate")
            self.estimate_text = ctk.CTkTextbox(c, height=96, font=f_small, fg_color=SOFT, text_color=INK,
                                                corner_radius=10, wrap="word", border_width=0)
            self.estimate_text.configure(state="disabled")
            self.estimate_text.pack(fill="x")

            c = card(body, "Progress")
            self.status_var = tk.StringVar(value="Idle")
            ctk.CTkLabel(c, textvariable=self.status_var, font=f_body, text_color=MUTED, anchor="w",
                         justify="left", wraplength=900).pack(fill="x")
            self.progress = Progress(c, height=8, corner_radius=4, progress_color=ACCENT, fg_color=SOFT)
            self.progress.set(0)
            self.progress.pack(fill="x", pady=(6, 10))
            cols = ("video", "status", "frames", "estimated", "rejected")
            self.tree = ttk.Treeview(c, columns=cols, show="headings", height=5, style="Galore.Treeview")
            for col, w in zip(cols, (330, 170, 80, 90, 120)):
                self.tree.heading(col, text={"video": "Video", "status": "Status", "frames": "Frames",
                                             "estimated": "Estimated", "rejected": "Rejected packets"}[col])
                self.tree.column(col, width=w, anchor="w" if col == "video" else "e")
            self.tree.pack(fill="x")
            self._tree_colours()

            c = card(body, "Log", expand=True)
            c.master.pack_configure(fill="both", expand=True, pady=0)
            self.log = ctk.CTkTextbox(c, font=ctk.CTkFont(family="Menlo" if platform.system() == "Darwin" else "Consolas",
                                                          size=12),
                                      fg_color=SOFT, text_color=INK, corner_radius=10, wrap="none", border_width=0)
            self.log.configure(state="disabled")
            self.log.pack(fill="both", expand=True)

        def _pick_theme(self, value):
            ctk.set_appearance_mode("dark" if value == "Dark" else "light")
            self._tree_colours()

        def _tree_colours(self):
            """ttk's table does not follow CustomTkinter's mode: colour it from the same pairs."""
            i = 1 if ctk.get_appearance_mode() == "Dark" else 0
            style = ttk.Style(self)
            style.theme_use("clam")
            rows = max(22, self.f_body.metrics("linespace") + 8)
            style.configure("Galore.Treeview", background=CARD[i], fieldbackground=CARD[i], foreground=INK[i],
                            bordercolor=LINE[i], lightcolor=CARD[i], darkcolor=CARD[i], rowheight=rows,
                            font=("TkDefaultFont", 12))
            style.map("Galore.Treeview", background=[("selected", SELECT[i])],
                      foreground=[("selected", "#FFFFFF" if i else INK[0])])
            style.configure("Galore.Treeview.Heading", background=SOFT[i], foreground=MUTED[i], bordercolor=LINE[i],
                            lightcolor=SOFT[i], darkcolor=SOFT[i], relief="flat")
            style.map("Galore.Treeview.Heading", background=[("active", LINE[i])])


def main():
    if ctk is None:
        print("CustomTkinter is not installed; opening the plain window. pip install customtkinter for the newer one.")
        app = App()
        kind = "plain Tk"
    else:
        app = ModernApp()
        kind = "CustomTkinter"
    smoke_check(app, kind)
    app.mainloop()


if __name__ == "__main__":
    from multiprocessing import freeze_support
    freeze_support()
    main()
