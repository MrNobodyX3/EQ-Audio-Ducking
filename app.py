"""Desktop preview and export interface."""

from pathlib import Path
from queue import Empty, Queue
import subprocess
from tempfile import TemporaryDirectory
from threading import Thread
from time import monotonic
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from duck_music import MAX_SOFTEN_DB, MAX_TIMING_MS, Settings, render
from preview import play, prepare_preview
from visualizer import CutVisualizer


PROJECT = Path(__file__).resolve().parent
APP_NAME = "EQ Audio Ducking"


def clock(seconds):
    minutes, seconds = divmod(int(seconds), 60)
    return f"{minutes}:{seconds:02d}"


class MusicDucking:
    def __init__(self, root):
        self.root = root
        self.events = Queue()
        self.task = None
        self.output = None
        self.work = TemporaryDirectory(prefix="music-ducking-preview-")
        self.preview_request = None
        self.preview_trace = None
        self.tracks = {}
        self.duration = 0.0
        self.player = None
        self.play_started = 0.0
        self.seek_timer = None
        self.music = tk.StringVar()
        self.dialogue = tk.StringVar()
        self.status = tk.StringVar(value="Choose a music track and a dialogue track.")
        self.progress = tk.DoubleVar()
        defaults = Settings()
        self.influence = tk.DoubleVar(value=100 * defaults.influence)
        self.influence_label = tk.StringVar(value=f"{self.influence.get():.0f}%")
        self.attack = tk.DoubleVar(value=defaults.attack_ms)
        self.attack_label = tk.StringVar(value=f"{defaults.attack_ms:.0f} ms")
        self.smoothness = tk.DoubleVar(value=defaults.release_ms)
        self.smoothness_label = tk.StringVar(value=f"{defaults.release_ms:.0f} ms")
        self.soften = tk.DoubleVar(value=defaults.soften_db)
        self.soften_label = tk.StringVar(value=f"{defaults.soften_db:.1f} dB")
        self.mode = tk.StringVar(value="Ducked")
        self.hear_dialogue = tk.BooleanVar(value=True)
        self.position = tk.DoubleVar()
        self.time_label = tk.StringVar(value="0:00 / 0:00")

        root.title(APP_NAME)
        root.minsize(625, 650)
        root.protocol("WM_DELETE_WINDOW", self.close)
        panel = ttk.Frame(root, padding=(24, 20))
        panel.pack(fill="both", expand=True)
        panel.columnconfigure(0, weight=1)
        panel.rowconfigure(5, weight=1)

        ttk.Label(panel, text=APP_NAME, font=("TkDefaultFont", 22, "bold")).grid(
            row=0, column=0, sticky="w"
        )
        ttk.Label(panel, text="Make room for dialogue with frequency-matched cuts in the music.").grid(
            row=1, column=0, sticky="w", pady=(4, 16)
        )
        self.pickers = [
            self.track_picker(panel, 2, "Music track", self.music),
            self.track_picker(panel, 3, "Dialogue track", self.dialogue),
        ]

        controls = ttk.LabelFrame(panel, text="Dynamic EQ", padding=(12, 8))
        controls.grid(row=4, column=0, sticky="ew", pady=(8, 12))
        controls.columnconfigure(1, weight=1)
        self.settings_sliders = []
        for row, (title, maximum, value, label, display) in enumerate([
            ("Ducking Amount", 100, self.influence, self.influence_label, "{:.0f}%"),
            ("Lookahead", MAX_TIMING_MS, self.attack, self.attack_label, "{:.0f} ms"),
            ("Release", MAX_TIMING_MS, self.smoothness, self.smoothness_label, "{:.0f} ms"),
            ("Music Attenuation", MAX_SOFTEN_DB, self.soften, self.soften_label, "{:.1f} dB"),
        ]):
            padding = (5 if row else 0, 0)
            ttk.Label(controls, text=title).grid(
                row=row, column=0, sticky="w", padx=(0, 12), pady=padding
            )
            slider = ttk.Scale(
                controls, from_=0, to=maximum, variable=value,
                command=lambda number, label=label, display=display: self.settings_changed(number, label, display),
            )
            slider.grid(row=row, column=1, sticky="ew", padx=(0, 10), pady=padding)
            self.settings_sliders.append(slider)
            ttk.Label(controls, textvariable=label, width=8, anchor="e").grid(
                row=row, column=2, pady=padding
            )

        audition = ttk.LabelFrame(panel, text="Preview", padding=(12, 8))
        audition.grid(row=5, column=0, sticky="nsew", pady=(0, 14))
        audition.columnconfigure(1, weight=1)
        audition.rowconfigure(2, weight=1)
        choices = ttk.Frame(audition)
        choices.grid(row=0, column=0, columnspan=3, sticky="ew", pady=(0, 5))
        self.mode_controls = []
        for mode in ("Original", "Ducked"):
            choice = ttk.Radiobutton(
                choices, text=mode, value=mode, variable=self.mode, command=self.switch_preview
            )
            choice.pack(side="left", padx=(0, 12))
            self.mode_controls.append(choice)
        voice = ttk.Checkbutton(
            choices, text="Hear dialogue", variable=self.hear_dialogue, command=self.switch_preview
        )
        voice.pack(side="right")
        self.mode_controls.append(voice)
        self.play_button = ttk.Button(audition, text="Play", width=8, command=self.toggle_play)
        self.play_button.grid(row=1, column=0, padx=(0, 10))
        self.scrubber = ttk.Scale(audition, variable=self.position, from_=0, to=1, command=self.seek)
        self.scrubber.grid(row=1, column=1, sticky="ew")
        ttk.Label(audition, textvariable=self.time_label, width=13, anchor="e").grid(row=1, column=2)
        self.visualizer = CutVisualizer(audition)
        self.visualizer.grid(row=2, column=0, columnspan=3, sticky="nsew", pady=(9, 0))

        buttons = ttk.Frame(panel)
        buttons.grid(row=6, column=0, sticky="ew")
        self.export_button = ttk.Button(buttons, text="Export Music WAV…", command=self.export)
        self.export_button.pack(side="left")
        self.reveal_button = ttk.Button(buttons, text="Show in Finder", command=self.reveal)
        self.reveal_button.pack(side="left", padx=(8, 0))
        ttk.Progressbar(panel, variable=self.progress, maximum=1).grid(
            row=7, column=0, sticky="ew", pady=(12, 4)
        )
        ttk.Label(panel, textvariable=self.status, wraplength=570).grid(row=8, column=0, sticky="w")
        self.update_controls()
        root.after(42, self.tick)

    def track_picker(self, parent, row, title, variable):
        picker = ttk.Frame(parent)
        picker.grid(row=row, column=0, sticky="ew", pady=(0, 6))
        picker.columnconfigure(1, weight=1)
        ttk.Label(picker, text=title, width=13).grid(row=0, column=0, sticky="w")
        ttk.Entry(picker, textvariable=variable, state="readonly").grid(
            row=0, column=1, sticky="ew", padx=8
        )
        button = ttk.Button(
            picker, text="Choose…", command=lambda: self.choose_track(title, variable)
        )
        button.grid(row=0, column=2)
        return button

    def choose_track(self, title, variable):
        self.stop_player()
        path = filedialog.askopenfilename(
            parent=self.root, title=f"Choose {title.lower()}", initialdir=PROJECT / "in"
        )
        if path:
            variable.set(path)
            self.preview_request = None
            self.preview_trace = None
            self.visualizer.set_trace(None)
            self.duration = 0
            self.set_position(0)
            self.progress.set(0)
            if self.music.get() and self.dialogue.get():
                self.status.set("Press Play to preview, or export the processed music.")
            self.update_controls()

    def settings_changed(self, number, label, display):
        label.set(display.format(float(number)))
        self.stop_player()
        if self.music.get() and self.dialogue.get():
            self.status.set("Press Play to preview the updated settings.")
            self.visualizer.set_trace(None, "Press Play to update the EQ curves.")

    def request(self):
        music = Path(self.music.get())
        dialogue = Path(self.dialogue.get())
        if not music.is_file() or not dialogue.is_file():
            messagebox.showerror("Choose both tracks", "Select an existing music and dialogue file.",
                                 parent=self.root)
            return None
        settings = Settings(
            influence=round(self.influence.get()) / 100,
            release_ms=round(self.smoothness.get()),
            attack_ms=round(self.attack.get()),
            soften_db=round(self.soften.get(), 1),
        )
        return music, dialogue, settings

    def update_controls(self):
        idle = self.task is None
        ready = idle and self.music.get() and self.dialogue.get()
        for widget in [*self.pickers, *self.settings_sliders, *self.mode_controls]:
            widget.state(["!disabled" if idle else "disabled"])
        for widget, enabled in [
            (self.play_button, ready),
            (self.export_button, ready),
            (self.scrubber, idle and self.duration > 0),
            (self.reveal_button, idle and self.output is not None),
        ]:
            widget.state(["!disabled" if enabled else "disabled"])

    def set_position(self, position):
        self.position.set(position)
        self.update_position_display(position)

    def update_position_display(self, position):
        self.time_label.set(f"{clock(position)} / {clock(self.duration)}")
        self.visualizer.show(position, self.mode.get() == "Ducked")

    def current_position(self):
        if self.player is not None:
            return min(self.duration, monotonic() - self.play_started)
        return self.position.get()

    def seek(self, value):
        self.update_position_display(float(value))
        if self.player is not None:
            if self.seek_timer is not None:
                self.root.after_cancel(self.seek_timer)
            self.seek_timer = self.root.after(180, self.finish_seek)

    def finish_seek(self):
        self.seek_timer = None
        position = self.position.get()
        self.stop_player()
        self.set_position(position)
        if position < self.duration:
            self.start_player()

    def toggle_play(self):
        if self.player is not None:
            self.stop_player()
            return
        request = self.request()
        if request is None:
            return
        if request == self.preview_request:
            self.start_player()
            return

        self.preview_request = None
        self.visualizer.set_trace(None, "Analyzing the dialogue and music…")

        def prepare(progress):
            duration, tracks, trace = prepare_preview(*request, Path(self.work.name), progress)
            return request, duration, tracks, trace

        self.start_task("preview", prepare)

    def switch_preview(self):
        if self.player is not None:
            self.stop_player()
            self.start_player()
        else:
            self.update_position_display(self.position.get())

    def start_player(self):
        mode = self.mode.get()
        voice = self.hear_dialogue.get()
        position = self.position.get()
        if position >= self.duration:
            position = 0
        if self.visualizer.trace is not self.preview_trace:
            self.visualizer.set_trace(self.preview_trace)
        self.set_position(position)
        try:
            self.player = play(self.tracks[mode, voice], position, voice)
        except (OSError, RuntimeError) as error:
            self.status.set(f"Playback failed. {error}")
            return
        self.play_started = monotonic() - position
        self.play_button.configure(text="Pause")
        self.status.set(f"Playing {mode.lower()} music {'with' if voice else 'without'} dialogue.")

    def stop_player(self):
        if self.player is None:
            return
        position = self.position.get() if self.seek_timer is not None else self.current_position()
        if self.seek_timer is not None:
            self.root.after_cancel(self.seek_timer)
            self.seek_timer = None
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
                seeking = self.seek_timer is not None
                self.stop_player()
                if error or exit_code:
                    self.status.set(f"Playback failed. {error or f'FFplay exited with code {exit_code}.'}")
                elif seeking and self.position.get() < self.duration:
                    self.start_player()
                else:
                    self.set_position(self.duration)
                    self.status.set("Preview finished.")
            elif self.seek_timer is None:
                self.set_position(self.current_position())
        self.root.after(42, self.tick)

    def export(self):
        request = self.request()
        if request is None:
            return
        self.stop_player()
        music, dialogue, settings = request
        destination = filedialog.asksaveasfilename(
            parent=self.root,
            title="Save processed music",
            initialdir=PROJECT / "out",
            initialfile=f"{music.stem} - Ducked.wav",
            defaultextension=".wav",
            filetypes=[("WAV audio", "*.wav")],
        )
        if destination:
            output = Path(destination)
            overwrite = output.exists()
            self.start_task("export", lambda progress: render(
                music, dialogue, output, settings, overwrite=overwrite, progress=progress
            ))

    def start_task(self, name, action):
        self.task = name
        self.update_controls()
        self.progress.set(0)
        self.status.set("Reading source tracks…")
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
            if event == "error":
                if task == "preview":
                    self.visualizer.set_trace(None, "Press Play to try the preview again.")
                self.status.set(f"{task.title()} failed.")
                self.progress.set(0)
                self.update_controls()
                messagebox.showerror(f"{task.title()} failed", data, parent=self.root)
            elif task == "preview":
                self.preview_request, self.duration, self.tracks, self.preview_trace = data
                self.scrubber.configure(to=self.duration)
                self.progress.set(1)
                self.update_controls()
                self.start_player()
            else:
                self.output = data
                self.status.set(f"Saved {data.name}")
                self.update_controls()
            return

    def reveal(self):
        subprocess.Popen(["open", "-R", str(self.output)])

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
