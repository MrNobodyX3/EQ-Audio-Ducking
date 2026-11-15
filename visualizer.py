"""Preview EQ graph."""

from math import ceil, log10
import tkinter as tk

from preview import MAX_GRAPH_HZ, MIN_GRAPH_HZ


class CutVisualizer(tk.Canvas):
    def __init__(self, parent):
        super().__init__(parent, height=195, background="#17212b", highlightthickness=0)
        self.trace = None
        self.message = "Press Play to see the EQ curves."
        self.position = 0
        self.ducked = True
        self.drawn_frame = None
        self.x_positions = None
        self.tick_step = 6
        self.depth = 24
        self.bind("<Configure>", self.resize)

    def set_trace(self, trace, message="Press Play to see the EQ curves."):
        self.trace = trace
        self.message = message
        peak = float(trace.frames.max(initial=0)) if trace is not None else 0
        self.tick_step = 6 * max(1, ceil(peak / 36))
        self.depth = max(24, self.tick_step * ceil((peak + 1) / self.tick_step))
        self.resize()

    def resize(self, _event=None):
        self.delete("all")
        self.drawn_frame = None
        self.x_positions = None
        width, height = self.winfo_width(), self.winfo_height()
        if width < 440 or height < 145:
            return
        left, right = 48, width - 16
        self.top, self.bottom = 35, height - 50
        self.left, self.right = left, right

        def x(frequency):
            fraction = log10(frequency / MIN_GRAPH_HZ) / log10(MAX_GRAPH_HZ / MIN_GRAPH_HZ)
            return left + (right - left) * fraction

        self.create_text(left, 16, text="GAIN (dB)", anchor="w", fill="#c4d2de")
        for name, color, dashed, end in [
            ("Dialogue target", "#e5bc84", (4, 3), right),
            ("Applied to music", "#5cddd0", (), right - 150),
        ]:
            label = self.create_text(end, 16, text=name, anchor="e", fill="#e4edf4")
            start = self.bbox(label)[0] - 7
            self.create_line(start - 18, 16, start, 16, fill=color, width=2, dash=dashed)

        for cut in range(0, self.depth + 1, self.tick_step):
            y = self.y(cut)
            self.create_line(left, y, right, y, fill="#657789" if cut == 0 else "#344656")
            label = f"−{cut}" if cut else "0"
            self.create_text(left - 7, y, text=label, anchor="e", fill="#c4d2de")

        for frequency, label in [(20, "20"), (50, "50"), (100, "100"), (200, "200"), (500, "500"),
                                 (1000, "1k"), (2000, "2k"), (5000, "5k"), (10000, "10k"), (20000, "20k")]:
            pixel = x(frequency)
            self.create_line(pixel, self.top, pixel, self.bottom, fill="#344656")
            self.create_text(pixel, self.bottom + 9, text=label, anchor="n", fill="#c4d2de")
        self.create_text((left + right) / 2, height - 4, text="Frequency (Hz)", anchor="s", fill="#c4d2de")

        if self.trace is not None and len(self.trace.frequencies) >= 2:
            self.x_positions = [x(frequency) for frequency in self.trace.frequencies]
        else:
            self.x_positions = []
        self.draw()

    def y(self, cut):
        return self.top + (self.bottom - self.top) * cut / self.depth

    def show(self, position, ducked):
        self.position = position
        self.ducked = ducked
        self.draw()

    def draw(self):
        if self.x_positions is None:
            return
        if self.trace is None or not self.x_positions:
            if not self.find_withtag("prompt"):
                self.create_text(self.winfo_width() / 2, (self.top + self.bottom) / 2,
                                 text=self.message, fill="#c4d2de", tags="prompt")
            return

        index = min(round(self.position / self.trace.frame_interval), len(self.trace.frames) - 1)
        if self.drawn_frame == (index, self.ducked):
            return
        self.drawn_frame = index, self.ducked
        self.delete("curve")
        target, applied = self.trace.frames[index]
        target_points = [(x, self.y(value)) for x, value in zip(self.x_positions, target)]
        baseline_y = self.y(self.trace.soften_db)
        applied_points = [(x, self.y(value) if self.ducked else baseline_y)
                          for x, value in zip(self.x_positions, applied)]
        self.create_polygon([(self.x_positions[0], self.top), *applied_points,
                             (self.x_positions[-1], self.top)], fill="#244e54", tags="curve")
        self.create_line(applied_points, fill="#5cddd0", width=2, tags="curve")
        self.create_line(target_points, fill="#e5bc84", width=1.5, dash=(4, 3), tags="curve")
        if self.trace.soften_db > 0:
            self.create_line(self.left, baseline_y, self.right, baseline_y,
                             fill="#a5bdd4", dash=(2, 4), tags="curve")
            self.create_text(self.right - 5, baseline_y + 3,
                             text=f"Music Attenuation −{self.trace.soften_db:g} dB",
                             anchor="ne", fill="#d9e5ef", tags="curve")
