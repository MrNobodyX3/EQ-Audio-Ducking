"""Preview EQ graph."""

from math import ceil, inf, isfinite, log10
import tkinter as tk

import numpy as np

from preview import MAX_GRAPH_HZ, MIN_GRAPH_HZ
from timeline import ACCENT, BEAT, BORDER, GRID, MUTED, PANEL, TEXT

MAX_VISIBLE_CUT = 96


class CutVisualizer(tk.Canvas):
    def __init__(self, parent):
        super().__init__(parent, height=195, background=PANEL, highlightthickness=0)
        self.trace = None
        self.message = "Press Play to see the EQ curves."
        self.position = 0
        self.ducked = True
        self.channel = None
        self.music_levels = {}
        self.trace_peak = 0
        self.drawn_frame = None
        self.x_positions = None
        self.tick_step = 6
        self.depth = 24
        self.bind("<Configure>", self.resize)

    def set_trace(self, trace, message="Press Play to see the EQ curves."):
        self.trace = trace
        self.message = message
        self.trace_peak = float(trace.dialogue_target.max(initial=0)) if trace is not None else 0
        self.resize()

    def resize(self, _event=None):
        self.x_positions = None
        self.draw()

    def draw_grid(self):
        self.delete("all")
        self.drawn_frame = None
        width, height = self.winfo_width(), self.winfo_height()
        left, right = 48, width - 16
        self.top, self.bottom = 35, height - 50
        self.left, self.right = left, right

        def x(frequency):
            fraction = log10(frequency / MIN_GRAPH_HZ) / log10(MAX_GRAPH_HZ / MIN_GRAPH_HZ)
            return left + (right - left) * fraction

        self.create_text(left, 16, text="GAIN (dB)", anchor="w", fill=MUTED)
        for name, color, dashed, end in [
            ("Dialogue target", BEAT, (4, 3), right),
            ("Applied to music", ACCENT, (), right - 150),
        ]:
            label = self.create_text(end, 16, text=name, anchor="e", fill=TEXT)
            start = self.bbox(label)[0] - 7
            self.create_line(start - 18, 16, start, 16, fill=color, width=2, dash=dashed)

        for cut in range(0, self.depth + 1, self.tick_step):
            y = self.y(cut)
            self.create_line(left, y, right, y, fill=BORDER if cut == 0 else GRID)
            label = f"−{cut}" if cut else "0"
            self.create_text(left - 7, y, text=label, anchor="e", fill=MUTED)

        for frequency, label in [(20, "20"), (50, "50"), (100, "100"), (200, "200"), (500, "500"),
                                 (1000, "1k"), (2000, "2k"), (5000, "5k"), (10000, "10k"), (20000, "20k")]:
            pixel = x(frequency)
            self.create_line(pixel, self.top, pixel, self.bottom, fill=GRID)
            self.create_text(pixel, self.bottom + 19, text=label, anchor="n", fill=MUTED)
        self.create_text((left + right) / 2, height - 4, text="Frequency (Hz)", anchor="s", fill=MUTED)

        if self.trace is not None and len(self.trace.frequencies) >= 2:
            self.x_positions = [x(frequency) for frequency in self.trace.frequencies]
        else:
            self.x_positions = [left, right]

    def y(self, cut):
        if cut == inf:
            return self.bottom + 10
        return self.top + (self.bottom - self.top) * min(cut, self.depth) / self.depth

    def show(self, position, ducked, music_levels):
        self.position = position
        self.ducked = ducked
        self.music_levels = music_levels
        self.draw()

    def set_channel(self, channel):
        self.channel = channel
        self.draw()

    def draw(self):
        if self.winfo_width() < 440 or self.winfo_height() < 145:
            self.delete("all")
            self.x_positions = None
            return
        trace = self.trace
        index = (min(round(self.position / trace.frame_interval), len(trace.dialogue_target) - 1)
                 if trace is not None else None)
        channels = (0,)
        if trace is not None and trace.dialogue_target.shape[-1] == 2:
            channels = (0, 1) if self.channel is None else (self.channel,)
        music = tuple(
            (key, round(-20 * log10(level), 2) if level > 0 else inf)
            for key, level in self.music_levels.items()
        )
        frame = index, channels, self.ducked, music
        if self.x_positions is not None and self.drawn_frame == frame:
            return
        cuts = []
        peak = self.trace_peak
        for key, baseline in music:
            if trace is None:
                curves = ((baseline, baseline),)
            elif self.ducked and key in trace.clip_cuts:
                curves = tuple(baseline + trace.clip_cuts[key][index, :, channel] for channel in channels)
            else:
                curves = ((baseline + trace.soften_db,) * len(trace.frequencies),)
            for side, values in enumerate(curves):
                if side and np.array_equal(values, curves[0]):
                    continue
                if isfinite(baseline):
                    peak = max(peak, min(MAX_VISIBLE_CUT - 1, float(max(values, default=0))))
                cuts.append((side, values))

        step = (6 if peak < 30 else 12 if peak < 60 else 24)
        depth = max(24, step * ceil((peak + 1) / step))
        if self.x_positions is None or (step, depth) != (self.tick_step, self.depth):
            self.tick_step, self.depth = step, depth
            self.draw_grid()
        self.drawn_frame = frame
        self.delete("curve")
        if trace is not None and len(trace.frequencies) >= 2:
            targets = tuple(trace.dialogue_target[index, :, channel] for channel in channels)
            for side, values in enumerate(targets):
                if side and np.array_equal(values, targets[0]):
                    continue
                target = [(x, self.y(value)) for x, value in zip(self.x_positions, values)]
                self.create_line(target, fill=BEAT, width=2, dash=(1, 3) if side else (4, 3), tags="curve")
        for side, values in cuts:
            points = [(x, self.y(value)) for x, value in zip(self.x_positions, values)]
            if len(points) >= 2:
                self.create_line(points, fill=ACCENT, width=2.5, dash=(5, 3) if side else (), tags="curve")
        if any(baseline == inf for _, baseline in music):
            self.create_text(self.left - 7, self.y(inf), text="−∞", anchor="e",
                             fill=MUTED, tags="curve")
        if trace is None:
            self.create_text(self.right - 4, self.bottom - 4, text=self.message, anchor="se",
                             fill=TEXT, tags="curve")
