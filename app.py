"""Desktop timeline, audition and export interface."""

from dataclasses import replace
from pathlib import Path
from queue import Empty, Queue
import subprocess
import sys
from tempfile import TemporaryDirectory
from threading import Thread
from time import monotonic
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from duck_music import MAX_TIMING_MS, Settings
from preview import play, prepare_timeline_preview
from timeline import (
    ACCENT, BG, BLUE, BORDER, LAVENDER, MUTED, PANEL, SURFACE, TEXT,
    Timeline, clock, parse_time,
)
from timeline_audio import (
    Clip, clip_amplitude, duration_of, export_timeline, inspect_asset, level_text, stem_channels,
)
from updates import github_commits_behind
from visualizer import CutVisualizer

PROJECT = Path(__file__).resolve().parent
APP_NAME = "EQ Audio Ducking"
FORMATS = " ".join(f"*.{ext}" for ext in (
    "wav", "aif", "aiff", "flac", "mp3", "m4a", "aac", "ogg", "opus", "wma", "caf"
))
HOVER = "#3b506c"
PRESSED = "#425b77"
DISABLED_TEXT = "#a1afc5"


def theme(root):
    root.configure(background=BG)
    style = ttk.Style(root)
    style.theme_use("clam")
    style.configure(".", background=BG, foreground=TEXT, bordercolor=BORDER,
                    lightcolor=BORDER, darkcolor=BORDER, troughcolor=BG,
                    selectbackground=ACCENT, selectforeground=BG, focuscolor=ACCENT)
    style.configure("TButton", background=SURFACE, padding=(10, 5), relief="raised")
    style.map("TButton", background=[("disabled", PANEL), ("pressed", PRESSED), ("active", HOVER)],
              bordercolor=[("focus", ACCENT)],
              foreground=[("disabled", DISABLED_TEXT)])
    style.configure("Primary.TButton", background=ACCENT, foreground=BG, font=("TkDefaultFont", 11, "bold"))
    style.map("Primary.TButton", background=[("disabled", SURFACE), ("pressed", "#6bd0bb"),
                                             ("active", "#a5f7e6")],
              foreground=[("disabled", DISABLED_TEXT)])
    style.configure("Transport.TButton", background="#225654", padding=(11, 5))
    for lane, accent, background in (("Dialogue", BLUE, "#29445b"), ("Music", LAVENDER, "#45385c")):
        name = f"{lane}Add.TButton"
        style.configure(name, foreground=accent, background=background, padding=(6, 4))
        style.map(name, foreground=[("disabled", DISABLED_TEXT)],
                  bordercolor=[("focus", accent)])
    for variant in ("Toggle.TCheckbutton", "Mode.TRadiobutton"):
        style.layout(variant, style.layout("TButton"))
        style.configure(variant, background=SURFACE, padding=(9, 5), relief="raised",
                        font=("TkDefaultFont", 11, "bold"))
        style.map(variant, background=[("disabled", PANEL), ("selected", "active", "#a5f7e6"),
                                       ("selected", ACCENT), ("active", HOVER)],
                  bordercolor=[("focus", TEXT)],
                  foreground=[("disabled", DISABLED_TEXT), ("selected", BG)])
    style.configure("TEntry", fieldbackground=SURFACE, insertcolor=TEXT, padding=(5, 4))
    style.map("TEntry", fieldbackground=[("disabled", PANEL)], bordercolor=[("focus", ACCENT)],
              foreground=[("disabled", DISABLED_TEXT)])
    style.configure("TScrollbar", width=15, background=BORDER, arrowcolor=BG,
                    borderwidth=0, arrowsize=13)
    style.map("TScrollbar", background=[("active", "#91a9c8")])
    style.configure("Horizontal.TProgressbar", background=ACCENT, thickness=3)
    style.configure("Playback.Horizontal.TScale", background=ACCENT, troughcolor="#28504e",
                    sliderlength=22, arrowsize=20, gripcount=0, bordercolor=BORDER,
                    lightcolor=ACCENT, darkcolor=BG)
    style.map("Playback.Horizontal.TScale", background=[("active", TEXT)],
              bordercolor=[("focus", TEXT)])
    style.configure("Title.TLabel", font=("TkDefaultFont", 23, "bold"))
    style.configure("Heading.TLabel", font=("TkDefaultFont", 13, "bold"))
    style.configure("Muted.TLabel", foreground=MUTED)
    style.configure("Time.TLabel", foreground=ACCENT, font=("TkFixedFont", 12))
    style.configure("Inspector.TFrame", background=PANEL)
    style.configure("Inspector.TLabel", background=PANEL)
    style.configure("InspectorMuted.TLabel", background=PANEL, foreground=MUTED)
    style.configure("EQ.TFrame", background=PANEL, bordercolor=BORDER, relief="solid", borderwidth=1)
    style.configure("EQHeading.TLabel", background=PANEL, font=("TkDefaultFont", 14, "bold"))
    style.configure("EQ.TLabel", background=PANEL)


def labeled_toggle(parent, title, variable, command):
    toggle = ttk.Checkbutton(parent, variable=variable, style="Toggle.TCheckbutton", command=command)

    def show_state(*_):
        toggle.configure(text=f"{title}: {'On' if variable.get() else 'Off'}")

    variable.trace_add("write", show_state)
    show_state()
    return toggle


class MusicDucking:
    def __init__(self, root):
        self.root = root
        theme(root)
        self.events = Queue()
        self.task = self.output = None
        self.work = TemporaryDirectory(prefix="music-ducking-preview-")
        self.preview_request = self.preview_trace = None
        self.tracks = {}
        self.clips = ()
        self.active = None
        self.next_key = 0
        self.undo_stack = []
        self.redo_stack = []
        self.position = 0
        self.player = None
        self.player_volume = 100
        self.volume_timer = None
        self.play_started = 0
        self.resume_after_scrub = False
        self.status = tk.StringVar(value="Add dialogue or music to get started.")
        self.progress = tk.DoubleVar()
        self.playback_volume = tk.DoubleVar(value=100)
        self.volume_label = tk.StringVar(value="100%")
        defaults = Settings()
        self.influence = tk.DoubleVar(value=100 * defaults.influence)
        self.attack = tk.DoubleVar(value=defaults.attack_ms)
        self.smoothness = tk.DoubleVar(value=defaults.release_ms)
        self.mode = tk.StringVar(value="Ducked")
        self.channel_mode = tk.IntVar(value=-1)
        self.hear_dialogue = tk.BooleanVar(value=True)
        self.snap = tk.BooleanVar(value=True)
        self.beats = tk.BooleanVar(value=False)
        self.time_label = tk.StringVar(value="0:00.000 / 0:00.000")
        self.clip_label = tk.StringVar(value="No clip selected")
        self.clip_detail = tk.StringVar(value="Select a clip to edit its timing.")
        self.inspector_values = [tk.StringVar() for _ in range(3)]
        self.editor_controls = []
        self.clip_controls = []

        root.title(APP_NAME)
        root.minsize(925, 700)
        root.geometry(f"{min(1210, root.winfo_screenwidth() - 70)}x"
                      f"{min(795, root.winfo_screenheight() - 100)}")
        root.protocol("WM_DELETE_WINDOW", self.close)
        shell = ttk.Frame(root, padding=(22, 15, 22, 11))
        shell.pack(fill="both", expand=True)
        shell.columnconfigure(0, weight=1)
        shell.rowconfigure(2, weight=1, minsize=205)
        self.build_header(shell)
        self.build_transport(shell)
        self.timeline = Timeline(shell, self.select_clip, self.edit_clip, self.seek, self.scrub,
                                 self.choose)
        self.timeline.grid(row=2, column=0, sticky="nsew")
        self.editor_controls.extend(self.timeline.add_buttons.values())
        self.build_inspector(shell)
        self.build_eq(shell)

        footer = ttk.Frame(shell)
        footer.grid(row=5, column=0, sticky="ew", pady=(7, 0))
        footer.columnconfigure(0, weight=1)
        ttk.Label(footer, textvariable=self.status, style="Muted.TLabel").grid(row=0, column=0, sticky="w")
        self.reveal_button = ttk.Button(footer, text="Show Export", command=self.reveal)
        self.reveal_button.grid(row=0, column=1, padx=(10, 0))
        self.reveal_button.grid_remove()
        self.progressbar = ttk.Progressbar(shell, variable=self.progress, maximum=1)
        self.progressbar.grid(row=6, column=0, sticky="ew", pady=(5, 0))
        self.progressbar.grid_remove()

        for sequence, handler in (("<space>", self.space), ("<Delete>", self.key_remove),
                                  ("<BackSpace>", self.key_remove), ("<Control-z>", self.undo),
                                  ("<Control-Shift-Z>", self.redo), ("<Control-y>", self.redo)):
            root.bind(sequence, handler)
        if root.tk.call("tk", "windowingsystem") == "aqua":
            root.bind("<Command-z>", self.undo)
            root.bind("<Command-Shift-Z>", self.redo)
        self.update_controls()
        root.after(42, self.tick)
        self.check_github_updates()

    def build_header(self, shell):
        title = ttk.Frame(shell)
        title.grid(row=0, column=0, sticky="ew", pady=(0, 13))
        title.columnconfigure(0, weight=1)
        heading = ttk.Frame(title)
        heading.grid(row=0, column=0, sticky="w")
        ttk.Label(heading, text=APP_NAME, style="Title.TLabel").pack(side="left")
        self.update_button = ttk.Button(heading, text="GitHub update available…",
                                        style="Transport.TButton")
        ttk.Label(title, text="Arrange your audio and shape the music around the dialogue.",
                  style="Muted.TLabel").grid(row=1, column=0, sticky="w", pady=(2, 0))
        self.export_button = ttk.Button(title, text="Export Music WAV…", style="Primary.TButton",
                                        command=lambda: self.export("Music"))
        self.export_button.grid(row=0, column=2, rowspan=2, padx=(6, 0))
        self.dialogue_export = ttk.Button(title, text="Export Dialogue WAV…",
                                          command=lambda: self.export("Dialogue"))
        self.dialogue_export.grid(row=0, column=1, rowspan=2)

    def check_github_updates(self):
        result = Queue(maxsize=1)

        def check():
            result.put(github_commits_behind(PROJECT))

        def show_result():
            try:
                count = result.get_nowait()
            except Empty:
                self.root.after(250, show_result)
                return
            if count:
                self.update_button.configure(command=lambda: self.show_update_instructions(count))
                self.update_button.pack(side="left", padx=(12, 0))

        Thread(target=check, daemon=True).start()
        self.root.after(250, show_result)

    def show_update_instructions(self, count):
        messagebox.showinfo(
            "GitHub update available",
            f"GitHub has {count} newer {'commit' if count == 1 else 'commits'}.\n\n"
            "Export any work you need to keep, then close the app and run this in the project folder:\n\n"
            f"git pull --ff-only origin main\n\n{PROJECT}\n\nThen reopen the app.",
            parent=self.root,
        )

    def build_transport(self, shell):
        bar = ttk.Frame(shell)
        bar.grid(row=1, column=0, sticky="ew", pady=(0, 6))
        self.play_button = ttk.Button(bar, text="Play", width=7, style="Transport.TButton",
                                      command=self.toggle_play)
        self.play_button.pack(side="left")
        home = ttk.Button(bar, text="To Start", command=lambda: self.seek(0))
        home.pack(side="left", padx=(5, 10))
        self.editor_controls.append(home)
        ttk.Label(bar, textvariable=self.time_label, style="Time.TLabel").pack(side="left")
        for title, factor in (("Fit", None), ("+", 1.6), ("−", 1 / 1.6)):
            ttk.Button(bar, text=title, width=3, command=lambda factor=factor: self.timeline.change_zoom(
                factor)).pack(side="right", padx=(4, 0))
        for title, variable in (("Beat Snap", self.beats), ("Snap", self.snap)):
            toggle = labeled_toggle(
                bar, title, variable, lambda variable=variable: self.update_snapping(variable)
            )
            toggle.pack(side="right", padx=(4, 0))
        self.redo_button = ttk.Button(bar, text="Redo", command=self.redo)
        self.undo_button = ttk.Button(bar, text="Undo", command=self.undo)
        self.redo_button.pack(side="right", padx=(4, 0))
        self.undo_button.pack(side="right", padx=(4, 0))

    def build_inspector(self, shell):
        row = ttk.Frame(shell, style="Inspector.TFrame", padding=(11, 7))
        row.grid(row=3, column=0, sticky="ew", pady=(5, 10))
        row.columnconfigure(0, weight=1)
        ttk.Label(row, textvariable=self.clip_label, style="Inspector.TLabel",
                  font=("TkDefaultFont", 11, "bold")).grid(row=0, column=0, sticky="w")
        ttk.Label(row, textvariable=self.clip_detail, style="InspectorMuted.TLabel").grid(
            row=1, column=0, sticky="w")
        for column, (title, variable) in enumerate(zip(("Start", "Source in", "Source out"),
                                                       self.inspector_values), 1):
            ttk.Label(row, text=title, style="InspectorMuted.TLabel").grid(
                row=0, column=column, sticky="w", padx=(9, 0))
            entry = ttk.Entry(row, width=10, textvariable=variable)
            entry.grid(row=1, column=column, padx=(9, 0))
            entry.bind("<Return>", self.apply_timing)
            entry.bind("<Escape>", lambda _event: self.select_clip(self.active))
            self.clip_controls.append(entry)
        for column, (title, handler) in enumerate((("Apply Timing", self.apply_timing),
                                                    ("Remove", self.remove)), 4):
            button = ttk.Button(row, text=title, command=handler)
            button.grid(row=0, column=column, rowspan=2, padx=(7, 0))
            self.clip_controls.append(button)

    def build_eq(self, shell):
        lower = ttk.Frame(shell)
        lower.grid(row=4, column=0, sticky="ew")
        lower.columnconfigure(0, weight=3)
        lower.columnconfigure(1, weight=2)
        controls = ttk.Frame(lower, padding=(14, 9), style="EQ.TFrame")
        controls.grid(row=0, column=0, sticky="nsew", padx=(0, 12))
        controls.columnconfigure(1, weight=1)
        ttk.Label(controls, text="Dynamic EQ", style="EQHeading.TLabel").grid(
            row=0, column=0, columnspan=3, sticky="w", pady=(0, 5))
        style = ttk.Style(shell)
        for row, (title, maximum, value, display, accent, track) in enumerate([
            ("Ducking Amount", 100, self.influence, "{:.0f}%", ACCENT, "#28504e"),
            ("Lookahead", MAX_TIMING_MS, self.attack, "{:.0f} ms", BLUE, "#2c4963"),
            ("Release", MAX_TIMING_MS, self.smoothness, "{:.0f} ms", LAVENDER, "#45385c"),
        ], 1):
            slider_style = f"EQ{row}.Horizontal.TScale"
            value_style = f"EQValue{row}.TLabel"
            style.configure(slider_style, background=accent, troughcolor=track, sliderlength=24,
                            arrowsize=24, gripcount=0, bordercolor=BORDER,
                            lightcolor=accent, darkcolor=BG)
            style.map(slider_style, background=[("disabled", BORDER), ("active", TEXT)],
                      lightcolor=[("disabled", BORDER), ("active", TEXT)],
                      bordercolor=[("focus", TEXT)])
            style.configure(value_style, foreground=accent, background=PANEL,
                            font=("TkFixedFont", 12, "bold"))
            label = tk.StringVar(value=display.format(value.get()))
            ttk.Label(controls, text=title, style="EQ.TLabel").grid(row=row, column=0, sticky="w")
            slider = ttk.Scale(controls, from_=0, to=maximum, variable=value, length=185,
                               style=slider_style,
                               command=lambda number, label=label, display=display:
                               self.settings_changed(number, label, display))
            slider.grid(row=row, column=1, padx=(12, 8), pady=6, sticky="ew")
            self.editor_controls.append(slider)
            ttk.Label(controls, textvariable=label, width=8, style=value_style, anchor="e").grid(
                row=row, column=2)
        ttk.Label(controls, text="Drag volume line · Option/Alt-click to add a point",
                  style="InspectorMuted.TLabel").grid(
            row=4, column=0, columnspan=3, sticky="w", pady=(4, 2))

        audition = ttk.Frame(lower)
        audition.grid(row=0, column=1, sticky="nsew")
        audition.columnconfigure(0, weight=1)
        audition.rowconfigure(2, weight=1)
        toolbar = ttk.Frame(audition)
        toolbar.grid(row=0, column=0, sticky="ew", pady=(0, 3))
        ttk.Label(toolbar, text="Preview EQ", style="Heading.TLabel").pack(side="left", padx=(0, 9))
        for mode in ("Original", "Ducked"):
            choice = ttk.Radiobutton(toolbar, text=mode, value=mode, variable=self.mode,
                                     style="Mode.TRadiobutton",
                                     command=self.switch_preview)
            choice.pack(side="left", padx=(0, 7))
            self.editor_controls.append(choice)
        voice = labeled_toggle(toolbar, "Dialogue", self.hear_dialogue, self.switch_preview)
        voice.pack(side="right")
        self.editor_controls.append(voice)

        monitor = ttk.Frame(audition)
        monitor.grid(row=1, column=0, sticky="ew", pady=(0, 5))
        monitor.columnconfigure(1, weight=1)
        ttk.Label(monitor, text="Playback volume").grid(row=0, column=0, sticky="w")
        volume = ttk.Scale(monitor, from_=0, to=100, variable=self.playback_volume,
                           style="Playback.Horizontal.TScale", command=self.change_playback_volume)
        volume.grid(row=0, column=1, sticky="ew", padx=(10, 8))
        ttk.Label(monitor, textvariable=self.volume_label, width=5, anchor="e",
                  style="Time.TLabel").grid(row=0, column=2)
        self.channel_controls = ttk.Frame(monitor)
        self.channel_controls.grid(row=0, column=3, padx=(12, 0))
        ttk.Label(self.channel_controls, text="Channel").pack(side="left", padx=(0, 5))
        for channel, label in ((-1, "Both"), (0, "Left"), (1, "Right")):
            choice = ttk.Radiobutton(self.channel_controls, text=label, value=channel,
                                     variable=self.channel_mode, style="Mode.TRadiobutton",
                                     command=self.switch_channel)
            choice.pack(side="left", padx=(0, 2))

        self.visualizer = CutVisualizer(audition)
        self.visualizer.configure(height=150, width=440)
        self.visualizer.grid(row=2, column=0, sticky="nsew")

    @property
    def duration(self):
        return duration_of(self.clips)

    @property
    def selected(self):
        return next((clip for clip in self.clips if clip.key == self.active), None)

    def choose(self, lane):
        self.stop_player()
        paths = filedialog.askopenfilenames(
            parent=self.root, title=f"Add {lane.lower()} clips",
            initialdir=PROJECT / "in" if (PROJECT / "in").is_dir() else PROJECT,
            filetypes=[("Audio files", FORMATS), ("All files", "*")],
        )
        if not paths:
            return

        def import_clips(progress):
            assets, errors, inspected = [], [], {}
            for index, path in enumerate(paths):
                path = Path(path)
                prefix = "Reading and detecting beats in" if lane == "Music" else "Reading"
                progress(f"{prefix} {path.name}…", index / len(paths))
                try:
                    if path not in inspected:
                        inspected[path] = inspect_asset(path, find_beats=lane == "Music")
                    assets.append(inspected[path])
                except (OSError, ValueError, RuntimeError) as error:
                    errors.append(f"{path.name}: {error}")
            return lane, assets, errors

        self.start_task("import", import_clips)

    def add_assets(self, lane, assets, errors):
        if assets:
            added = []
            start = max((clip.end for clip in self.clips if clip.lane == lane), default=0)
            for asset in assets:
                self.next_key += 1
                added.append(Clip(self.next_key, lane, asset, start, 0, asset.duration))
                start += asset.duration
            fit = self.timeline.offset == 0 and self.timeline.zoom == 1
            self.commit(self.clips + tuple(added), added[0].key)
            if fit and self.duration > self.timeline.visible:
                self.timeline.change_zoom(None)
            self.timeline.reveal_clip(added[0])
            count = len(added)
            self.status.set(f"Added {count} {lane.lower()} clip{'s' if count != 1 else ''}. "
                            "Drag to move or pull either edge to trim.")
            self.timeline.canvas.focus_set()
        if errors:
            if not assets:
                self.status.set("No clips were added.")
            messagebox.showwarning("Some files could not be added", "\n\n".join(errors[:5])
                                   + (f"\n\n… and {len(errors) - 5} more." if len(errors) > 5 else ""),
                                   parent=self.root)

    def select_clip(self, key):
        self.active = key
        clip = self.selected
        if clip:
            name = clip.asset.path.name
            self.clip_label.set(name if len(name) <= 43 else name[:23] + "…" + name[-17:])
            self.clip_detail.set(f"{clip.lane}  ·  {clock(clip.length)} long  ·  "
                                 f"Volume {level_text(clip.gain ** 3)}")
            for variable, value in zip(self.inspector_values, (clip.start, clip.source_in, clip.source_out)):
                variable.set(clock(value))
        else:
            self.clip_label.set("No clip selected")
            self.clip_detail.set("Select a waveform to edit its timing.")
            for variable in self.inspector_values:
                variable.set("")
        self.timeline.set_project(self.clips, self.active)
        self.update_controls()

    def edit_clip(self, edited):
        self.commit(tuple(edited if clip.key == edited.key else clip for clip in self.clips), edited.key)

    def apply_timing(self, _event=None):
        clip = self.selected
        if not clip or self.task:
            return "break"
        try:
            start, source_in, source_out = [parse_time(item.get()) for item in self.inspector_values]
            if not 0 <= source_in < source_out <= clip.asset.duration:
                raise ValueError
        except ValueError:
            self.status.set(f"Use seconds or m:ss.mmm. Source in must precede source out "
                            f"(max {clock(clip.asset.duration)}).")
            return "break"
        edited = replace(clip, start=start, source_in=source_in, source_out=source_out)
        self.edit_clip(edited)
        self.timeline.reveal_clip(edited)
        self.timeline.canvas.focus_set()
        return "break"

    def remove(self):
        if self.selected and not self.task:
            self.commit(tuple(clip for clip in self.clips if clip.key != self.active), None)
            self.timeline.canvas.focus_set()

    def commit(self, clips, active):
        if clips == self.clips:
            self.select_clip(active)
            return
        self.undo_stack.append((self.clips, self.active))
        self.undo_stack = self.undo_stack[-100:]
        self.redo_stack.clear()
        self.restore(clips, active)

    def restore(self, clips, active):
        self.stop_player()
        self.clips = clips
        self.invalidate("Press Play to hear the updated timeline." if clips else
                        "Add dialogue or music to get started.")
        self.select_clip(active)
        self.set_position(min(self.position, self.duration))

    def focus_is_text(self):
        focus = self.root.focus_get()
        return isinstance(focus, (ttk.Entry, tk.Entry, tk.Text))

    def undo(self, event=None):
        if event is not None and self.focus_is_text():
            return
        if self.undo_stack and not self.task and not self.timeline.drag:
            self.redo_stack.append((self.clips, self.active))
            self.restore(*self.undo_stack.pop())
        return "break"

    def redo(self, event=None):
        if event is not None and self.focus_is_text():
            return
        if self.redo_stack and not self.task and not self.timeline.drag:
            self.undo_stack.append((self.clips, self.active))
            self.restore(*self.redo_stack.pop())
        return "break"

    def key_remove(self, _event):
        if not self.focus_is_text() and not self.timeline.drag:
            self.remove()
            return "break"

    def space(self, _event):
        if self.root.focus_get() in (self.timeline.canvas, self.root) and not self.timeline.drag:
            self.toggle_play()
            return "break"

    def update_snapping(self, changed):
        self.timeline.snap = self.snap.get()
        self.timeline.beats = self.beats.get()
        self.timeline.queue_draw()
        if changed is self.snap:
            self.status.set("Snap on · Align to clip edges, the playhead, or the ruler grid."
                            if self.snap.get() else "Grid, clip edge, and playhead snapping are off.")
        elif self.beats.get():
            if any(clip.asset.beats for clip in self.clips if clip.lane == "Music"):
                self.status.set("Beats on · Gold guides mark the estimated beat grid. Drag or trim an edge to snap.")
            else:
                self.status.set("Beats on · Guides appear on music with a detectable rhythm.")
        else:
            self.status.set("Beat guides and beat snapping are off.")

    def settings_changed(self, number, label, display):
        label.set(display.format(float(number)))
        if self.clips:
            self.stop_player()
            self.invalidate("Press Play to hear the updated EQ.")

    def invalidate(self, status):
        self.preview_request = self.preview_trace = None
        self.visualizer.set_trace(None)
        self.status.set(status)

    def request(self):
        return self.clips, Settings(
            influence=round(self.influence.get()) / 100,
            release_ms=round(self.smoothness.get()),
            attack_ms=round(self.attack.get()),
        )

    def update_controls(self):
        idle = self.task is None
        self.timeline.enabled = idle
        stereo = any(clip.asset.channels > 1 for clip in self.clips)
        if stereo:
            self.channel_controls.grid()
        else:
            self.channel_controls.grid_remove()
            if self.channel_mode.get() != -1:
                self.channel_mode.set(-1)
                self.visualizer.set_channel(None)
        for widgets, enabled in [
            (self.editor_controls, idle),
            (self.clip_controls, idle and self.selected is not None),
            ([self.play_button], idle and bool(self.clips)),
            ([self.export_button], idle and any(clip.lane == "Music" for clip in self.clips)),
            ([self.dialogue_export], idle and any(clip.lane == "Dialogue" for clip in self.clips)),
            ([self.undo_button], idle and bool(self.undo_stack)),
            ([self.redo_button], idle and bool(self.redo_stack)),
            ([self.reveal_button], idle and self.output is not None),
        ]:
            for widget in widgets:
                widget.state(["!disabled" if enabled else "disabled"])

    def set_position(self, ms, follow=False):
        self.position = ms
        self.time_label.set(f"{clock(ms)} / {clock(self.duration)}")
        self.timeline.set_position(ms, follow)
        levels = {
            clip.key: clip_amplitude(clip, ms - clip.start) for clip in self.clips
            if clip.lane == "Music" and clip.start <= ms < clip.end
        }
        self.visualizer.show(ms / 1000, self.mode.get() == "Ducked", levels)

    def current_position(self):
        if self.player is not None:
            return min(self.duration, round((monotonic() - self.play_started) * 1000))
        return self.position

    def seek(self, ms):
        if self.task:
            return
        resume = self.player is not None
        self.stop_player()
        position = max(0, min(ms, self.duration))
        self.timeline.follow_playhead = True
        self.set_position(position, follow=True)
        if resume and position < self.duration:
            self.start_player()

    def scrub(self, phase, ms):
        if phase == "start":
            self.resume_after_scrub = self.player is not None
            self.stop_player()
        position = max(0, min(ms, self.duration))
        self.set_position(position)
        if phase == "end":
            resume, self.resume_after_scrub = self.resume_after_scrub, False
            self.timeline.follow_playhead = True
            if resume and not self.task and position < self.duration:
                self.start_player()

    def toggle_play(self):
        if self.task or not self.clips or self.timeline.scrub_origin is not None:
            return
        if self.player is not None:
            self.stop_player()
            return
        request = self.request()
        if request == self.preview_request:
            self.start_player()
            return
        self.visualizer.set_trace(None, "Preparing the timeline preview…")

        def prepare(progress):
            tracks, trace = prepare_timeline_preview(*request, Path(self.work.name), progress)
            return request, tracks, trace

        self.start_task("preview", prepare)

    def switch_preview(self):
        if self.player is not None:
            self.stop_player()
            self.start_player()
        else:
            self.set_position(self.position)

    @property
    def audition_channel(self):
        channel = self.channel_mode.get()
        return None if channel == -1 else channel

    def switch_channel(self):
        self.visualizer.set_channel(self.audition_channel)
        self.switch_preview()

    def change_playback_volume(self, value):
        volume = round(float(value))
        self.volume_label.set(f"{volume}%")
        self.cancel_volume_timer()
        if self.player is not None and volume != self.player_volume:
            # Coalesce drag events before reopening the already-rendered preview.
            self.volume_timer = self.root.after(120, self.apply_playback_volume)

    def cancel_volume_timer(self):
        if self.volume_timer is not None:
            self.root.after_cancel(self.volume_timer)
            self.volume_timer = None

    def apply_playback_volume(self):
        self.volume_timer = None
        if self.player is not None and self.player.poll() is None:
            follow = self.timeline.follow_playhead
            self.stop_player()
            if self.position < self.duration:
                self.start_player(follow=follow)

    def start_player(self, *, follow=True):
        mode = self.mode.get()
        voice = self.hear_dialogue.get()
        position = self.position if self.position < self.duration else 0
        if self.visualizer.trace is not self.preview_trace:
            self.visualizer.set_trace(self.preview_trace)
        self.timeline.follow_playhead = follow
        self.set_position(position, follow=follow)
        try:
            mono = not voice and stem_channels(self.clips, "Music") == 1
            volume = round(self.playback_volume.get())
            self.player = play(self.tracks[mode, voice], position / 1000, mono=mono, volume=volume,
                               channel=self.audition_channel)
            self.player_volume = volume
        except (OSError, RuntimeError) as error:
            self.status.set(f"Playback failed. {error}")
            return
        self.play_started = monotonic() - position / 1000
        self.play_button.configure(text="Pause")
        self.status.set(f"Playing {mode.lower()} preview {'with' if voice else 'without'} dialogue.")

    def stop_player(self):
        self.cancel_volume_timer()
        if self.player is None:
            return
        position = self.current_position()
        if self.player.poll() is None:
            self.player.terminate()
            try:
                self.player.wait(timeout=0.25)
            except subprocess.TimeoutExpired:
                self.player.kill()
                self.player.wait()
        self.player.stderr.close()
        self.player = None
        self.set_position(position)
        self.play_button.configure(text="Play")
        self.status.set("Preview paused.")

    def tick(self):
        if self.player is not None:
            exit_code = self.player.poll()
            if exit_code is not None:
                error = self.player.stderr.read().strip()
                self.stop_player()
                if error or exit_code:
                    self.status.set(f"Playback failed. {error or f'FFplay exited with code {exit_code}.'}")
                else:
                    self.set_position(self.duration, follow=self.timeline.follow_playhead)
                    self.status.set("Preview finished.")
            else:
                self.set_position(self.current_position(),
                                  follow=self.timeline.follow_playhead and not self.timeline.drag
                                  and self.timeline.pan_origin is None)
        self.root.after(42, self.tick)

    def export(self, lane):
        self.stop_player()
        clips, settings = self.request()
        lane_clips = [clip for clip in clips if clip.lane == lane]
        if not lane_clips:
            return
        name = lane_clips[0].asset.path.stem if len(lane_clips) == 1 else lane
        suffix = "Ducked" if lane == "Music" else "Dialogue Stem"
        destination = filedialog.asksaveasfilename(
            parent=self.root, title=f"Export {lane.lower()} WAV",
            initialdir=PROJECT / "out" if (PROJECT / "out").is_dir() else PROJECT,
            initialfile=f"{name} - {suffix}.wav",
            defaultextension=".wav", filetypes=[("WAV audio", "*.wav")],
        )
        if destination:
            output = Path(destination)
            overwrite = output.exists()
            self.start_task("export", lambda progress: export_timeline(
                clips, lane, output, settings, overwrite=overwrite, progress=progress
            ))

    def start_task(self, name, action):
        self.task = name
        self.timeline.cancel()
        self.update_controls()
        self.progress.set(0)
        self.progressbar.grid()
        self.status.set({"import": "Reading audio files…", "preview": "Preparing preview…",
                         "export": "Preparing timeline export…"}[name])
        Thread(target=self.run_task, args=(action,)).start()
        self.root.after(60, self.poll_task)

    def run_task(self, action):
        def progress(stage, fraction):
            self.events.put(("progress", (stage, fraction)))

        try:
            result = action(progress)
        except Exception as error:
            self.events.put(("error", str(error)))
        else:
            self.events.put(("done", result))

    def poll_task(self):
        while True:
            try:
                event, data = self.events.get_nowait()
            except Empty:
                self.root.after(60, self.poll_task)
                return
            if event == "progress":
                stage, fraction = data
                self.status.set(stage)
                self.progress.set(fraction)
                continue

            task, self.task = self.task, None
            self.progressbar.grid_remove()
            self.update_controls()
            if event == "error":
                if task == "preview":
                    self.visualizer.set_trace(None, "Press Play to try the preview again.")
                self.status.set(f"{task.title()} failed.")
                messagebox.showerror(f"{task.title()} failed", data, parent=self.root)
            elif task == "import":
                self.add_assets(*data)
            elif task == "preview":
                self.preview_request, self.tracks, self.preview_trace = data
                self.start_player()
            else:
                self.output = data
                self.reveal_button.grid()
                self.status.set(f"Saved {data.name}")
                self.update_controls()
            return

    def reveal(self):
        if sys.platform == "darwin":
            command = ["open", "-R", str(self.output)]
        elif sys.platform == "win32":
            command = ["explorer", "/select,", str(self.output)]
        else:
            command = ["xdg-open", str(self.output.parent)]
        try:
            subprocess.Popen(command)
        except OSError as error:
            self.status.set(f"Could not show the export. {error}")

    def close(self):
        if self.task is not None:
            messagebox.showinfo("Working", "Wait for the current operation to finish.", parent=self.root)
            return
        self.stop_player()
        self.work.cleanup()
        self.root.destroy()


if __name__ == "__main__":
    root = tk.Tk()
    MusicDucking(root)
    root.mainloop()
