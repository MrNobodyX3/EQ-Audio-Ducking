"""Canvas timeline with waveform clips, beat guides and direct manipulation."""

from bisect import bisect_left, bisect_right
from dataclasses import replace
from heapq import heappop, heappush
import logging
from math import ceil, floor, isfinite, log10, prod
from time import monotonic
import tkinter as tk
from tkinter import font, ttk

import numpy as np

from timeline_audio import (
    FADE_CURVES, LANES, clip_amplitude, duration_of, fade_shape, level_text, point_level,
    volume_level,
)

BG = "#111827"
PANEL = "#1a2334"
SURFACE = "#26354b"
BORDER = "#5b7190"
TEXT = "#f4f7ff"
MUTED = "#bdcce2"
ACCENT = "#85f0d9"
BEAT = "#ffcf85"
BLUE = "#a2dcff"
LAVENDER = "#d8c3ff"
GRID = "#394b64"
COLORS = {
    "Dialogue": ("#1d3b51", BLUE, "#6495b4"),
    "Music": ("#342b50", LAVENDER, "#9079b9"),
}
HEADER = 146
RULER = 34
ROW = 56
SHIFT = 0x0001
CONTROL = 0x0004
COMMAND = 0x0008
OPTION = 0x0010  # Mod2 on macOS


def clock(ms, decimals=3):
    seconds, ms = divmod(max(0, round(ms)), 1000)
    minutes, seconds = divmod(seconds, 60)
    return f"{minutes}:{seconds:02d}" + (f".{ms:03d}"[:decimals + 1] if decimals else "")


def parse_time(value):
    parts = value.strip().split(":")
    if len(parts) == 2:
        minutes, seconds = int(parts[0]), float(parts[1])
        if minutes < 0 or not 0 <= seconds < 60:
            raise ValueError
        seconds += minutes * 60
    elif len(parts) == 1:
        seconds = float(parts[0])
    else:
        raise ValueError
    if not isfinite(seconds) or not 0 <= seconds < 24 * 60 * 60:
        raise ValueError
    return round(seconds * 1000)


def bounded_level(level):
    return min(1, max(0, level))


def snap_volume(level, step):
    if not step or level <= 0:
        return level
    db = 60 * log10(level)  # The audio uses the cube of the control level.
    return 10 ** (round(db / step) * step / 60)


def shift_volume_pair(levels, fraction, change, step):
    def shifted(delta):
        return tuple(bounded_level(level + delta) for level in levels)

    def at_pointer(pair):
        return pair[0] + (pair[1] - pair[0]) * fraction

    result = shifted(change)
    if not step or result == levels:
        return result
    target = snap_volume(at_pointer(result), step)
    if abs(at_pointer(result) - target) < 1e-12:
        return result
    # A shared offset keeps the segment's slope until a marker hits a limit.
    low, high = -1.0, 1.0
    for _ in range(36):
        midway = (low + high) / 2
        if at_pointer(shifted(midway)) < target:
            low = midway
        else:
            high = midway
    return shifted((low + high) / 2)


def stack_lane(clips):
    rows, free, active = {}, [], []
    count = 0
    for clip in sorted(clips, key=lambda item: (item.start, item.key)):
        while active and active[0][0] <= clip.start:
            _, row = heappop(active)
            heappush(free, row)
        if free:
            row = heappop(free)
        else:
            row = count
            count += 1
        rows[clip.key] = row
        heappush(active, (clip.end, row))
    return rows, max(1, count)


def upcoming_beats(clip, left, right):
    source_left = max(clip.source_in, left - clip.start + clip.source_in)
    source_right = min(clip.source_out, right - clip.start + clip.source_in)
    beats = clip.asset.beats
    return (clip.start + beat - clip.source_in for beat in
            beats[bisect_left(beats, source_left):bisect_right(beats, source_right)])


class Timeline(ttk.Frame):
    def __init__(self, parent, select, edit, seek, scrub, add_clips):
        super().__init__(parent)
        self.select, self.edit, self.seek = select, edit, seek
        self.scrub = scrub
        self.clips = ()
        self.duration = 0
        self.fit_span = 12000
        windowing = self.tk.call("tk", "windowingsystem")
        self.aqua = windowing == "aqua"
        legacy_tk = self.tk.call("package", "vcompare", self.tk.call("package", "provide", "Tk"),
                                 "8.7a0") < 0
        self.legacy_aqua = windowing == "aqua" and legacy_tk
        self.zoom_modifiers = CONTROL | (COMMAND if windowing == "aqua" else 0)
        self.point_modifier = OPTION if self.aqua else 0x0008  # Alt/Mod1 elsewhere
        self.volume_snap_modifier = COMMAND if self.aqua else CONTROL
        self.active = None
        self.position = 0
        self.zoom = 1
        self.offset = self.top = 0
        self.snap = True
        self.beats = False
        self.enabled = True
        self.pending = self.drag = self.guide = None
        self.last_volume_click = None
        self.pan_origin = self.scrub_origin = None
        self.follow_playhead = True
        self.pan_timer = self.paint_timer = None
        self.width, self.height = 760, 250
        self.body = 0
        self.lane_layout = {lane: ({}, 1) for lane in LANES}
        self.hit = []
        self.volume_hit = {}
        self.curve_icons = {}
        self.clip_font = font.Font(family="TkDefaultFont", size=11, weight="bold")
        self.lane_font = font.Font(family="TkDefaultFont", size=12, weight="bold")
        self.small_font = font.Font(family="TkDefaultFont", size=10)
        self.canvas = tk.Canvas(self, background=BG, highlightthickness=0, takefocus=True,
                                width=self.width, height=self.height)
        self.context_menu = tk.Menu(self.canvas, tearoff=False, background=PANEL, foreground=TEXT,
                                    activebackground=SURFACE, activeforeground=TEXT)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.add_buttons = {
            lane: ttk.Button(self.canvas, text=f"+ Add {lane}…", style=f"{lane}Add.TButton",
                             command=lambda lane=lane: add_clips(lane))
            for lane in LANES
        }
        self.horizontal = ttk.Scrollbar(self, orient="horizontal", command=self.scroll_x)
        self.horizontal.grid(row=1, column=0, sticky="ew")
        self.vertical = ttk.Scrollbar(self, orient="vertical", command=self.scroll_y)
        self.vertical.grid(row=0, column=1, sticky="ns")
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        middle = 3 if self.legacy_aqua else 2
        bindings = [
            ("<Configure>", self.resize), ("<Button-1>", self.press),
            (f"<Button-{2 if self.legacy_aqua else 3}>", self.clip_menu),
            ("<B1-Motion>", self.motion), ("<ButtonRelease-1>", self.release),
            (f"<Button-{middle}>", self.start_pan), (f"<B{middle}-Motion>", self.drag_pan),
            (f"<ButtonRelease-{middle}>", self.end_pan),
            ("<Motion>", self.hover), ("<MouseWheel>", self.wheel),
            ("<Escape>", self.cancel),
        ]
        if windowing == "x11" and legacy_tk:
            bindings.extend((f"<Button-{button}>", self.wheel) for button in (4, 5, 6, 7))
        if self.aqua:
            bindings.append(("<Control-Button-1>", self.clip_menu))
        for event, handler in bindings:
            self.canvas.bind(event, handler)
        try:
            self.canvas.bind("<TouchpadScroll>", self.trackpad_scroll)
        except tk.TclError:
            pass  # Tk 8.6 reports trackpad gestures as MouseWheel events.
        if self.aqua:
            from mac_pinch import MacPinchMonitor

            try:
                self.pinch_monitor = MacPinchMonitor()
            except (OSError, AttributeError, ValueError, RuntimeError):
                logging.getLogger(__name__).exception("Could not enable Mac trackpad pinch zoom")
            else:
                self.pinch_timer = self.after(16, self.poll_pinch)
                self.canvas.bind("<Destroy>", self.stop_pinch, add="+")

    def poll_pinch(self):
        # Run all widget access in a Tcl timer, outside of AppKit event dispatch.
        deltas = self.pinch_monitor.drain()
        if deltas and self.canvas.grab_current() is None:
            x_root, y_root = self.canvas.winfo_pointerxy()
            if self.canvas.winfo_containing(x_root, y_root) is self.canvas:
                factor = prod(max(0.1, min(4, 1 + delta)) for delta in deltas if isfinite(delta))
                if factor != 1:
                    self.change_zoom(factor, x=x_root - self.canvas.winfo_rootx())
        self.pinch_timer = self.after(16, self.poll_pinch)

    def stop_pinch(self, _event):
        self.after_cancel(self.pinch_timer)
        self.pinch_monitor.close()

    @property
    def viewport(self):
        return max(1, self.width - HEADER)

    @property
    def document_span(self):
        duration = self.duration
        return max(3000, duration * 1.06) if duration else 12000

    @property
    def scale(self):
        return self.viewport * self.zoom / self.fit_span

    @property
    def visible(self):
        return self.viewport / self.scale

    @property
    def extent(self):
        end = max(self.document_span, self.pending.end if self.pending else 0)
        return max(end, self.visible) + (self.visible * 0.38 if self.zoom > 1 or self.drag else 0)

    def x(self, time):
        return HEADER + (time - self.offset) * self.scale

    def time(self, x):
        return round(self.offset + (x - HEADER) / self.scale)

    def set_project(self, clips, active):
        was_empty = not self.clips
        if clips != self.clips:
            self.lane_layout = {
                lane: stack_lane(clip for clip in clips if clip.lane == lane)
                for lane in LANES
            }
            self.duration = duration_of(clips)
        self.clips, self.active = clips, active
        if was_empty or not clips:
            self.change_zoom(None)
        self.limit_scroll()
        self.queue_draw()

    def set_position(self, position, follow=False):
        self.position = position
        if follow and not self.offset <= position < self.offset + self.visible:
            self.offset = max(0, position - self.visible * 0.1)
            self.limit_scroll()
            self.queue_draw()
        elif self.paint_timer is None:
            self.draw_head()

    def resize(self, event):
        self.width, self.height = event.width, event.height
        self.limit_scroll()
        self.queue_draw()

    def limit_scroll(self):
        self.offset = min(max(0, self.offset), max(0, self.extent - self.visible))

    def change_zoom(self, factor, x=None):
        if self.drag or self.pan_origin is not None or self.scrub_origin is not None:
            return
        if factor is None:
            self.zoom, self.offset = 1, 0
            self.fit_span = self.document_span
        else:
            if x is None:
                x = self.x(self.position)
                if not HEADER <= x <= self.width:
                    x = HEADER + self.viewport / 2
            fraction = min(1, max(0, (x - HEADER) / self.viewport))
            anchor = self.offset + fraction * self.visible
            maximum = max(8, self.fit_span / 250)
            minimum = min(1, self.fit_span / self.document_span)
            self.zoom = min(maximum, max(minimum, self.zoom * factor))
            self.offset = anchor - fraction * self.visible
            self.follow_playhead = False
        self.limit_scroll()
        self.queue_draw()

    def reveal_clip(self, clip):
        if not self.offset <= clip.start < self.offset + self.visible:
            self.offset = max(0, clip.start - self.visible * 0.12)
            self.limit_scroll()
        self.queue_draw()

    def scroll_x(self, action, amount, unit=None):
        if self.drag:
            return
        if action == "moveto":
            self.offset = float(amount) * self.extent
        else:
            self.offset += int(amount) * self.visible * (0.8 if unit == "pages" else 0.075)
        self.follow_playhead = False
        self.limit_scroll()
        self.queue_draw()

    def scroll_y(self, action, amount, unit=None):
        if self.drag:
            return
        if action == "moveto":
            self.top = float(amount) * self.body
        else:
            self.top += int(amount) * (max(ROW, self.height - RULER - ROW) if unit == "pages" else ROW)
        self.queue_draw()

    def wheel(self, event):
        horizontal = event.state & SHIFT or event.num in (6, 7)
        if event.num in (4, 5, 6, 7):
            pixels = 48 if event.num in (4, 6) else -48
        else:
            pixels = event.delta * (8 if self.legacy_aqua else 48 / 120)
        return self.navigate_scroll(pixels if horizontal else 0,
                                    0 if horizontal else pixels, event)

    def trackpad_scroll(self, event):
        # Tk packs the signed horizontal and vertical pixel deltas into %D.
        dx = ((event.delta >> 16) + 32768) % 65536 - 32768
        dy = (event.delta + 32768) % 65536 - 32768
        if event.state & SHIFT and not dx and not event.state & self.zoom_modifiers:
            dx, dy = dy, 0
        return self.navigate_scroll(dx, dy, event)

    def navigate_scroll(self, dx, dy, event):
        if self.drag or self.pan_origin is not None or self.scrub_origin is not None:
            return "break"
        if event.state & self.zoom_modifiers:
            delta = dy or dx
            self.change_zoom(1.24 ** max(-6, min(6, delta / 48)), x=event.x)
        elif self.body <= self.height - RULER:
            self.pan_by(dx or dy, 0)
        else:
            self.pan_by(dx, dy)
        return "break"

    def pan_by(self, dx, dy):
        before = self.offset
        self.offset -= dx / self.scale
        self.top = min(max(0, self.top - dy), max(0, self.body - (self.height - RULER)))
        self.limit_scroll()
        if self.offset != before:
            self.follow_playhead = False
        self.queue_draw()

    def start_pan(self, event):
        if not self.drag and self.scrub_origin is None:
            self.pan_origin = (event.x, event.y)
            self.canvas.focus_set()
            self.canvas.configure(cursor="fleur")
        return "break"

    def drag_pan(self, event):
        if self.pan_origin is not None:
            x, y = self.pan_origin
            self.pan_origin = (event.x, event.y)
            self.pan_by(event.x - x, event.y - y)
        return "break"

    def end_pan(self, event):
        if self.pan_origin is None:
            return "break"
        self.drag_pan(event)
        self.pan_origin = None
        self.canvas.configure(cursor="")
        self.hover(event)
        return "break"

    def queue_draw(self):
        if self.paint_timer is None:
            self.paint_timer = self.after_idle(self.draw)

    def refresh_hit_targets(self):
        if self.paint_timer is not None:
            self.after_cancel(self.paint_timer)
            self.draw()

    def draw(self):
        self.paint_timer = None
        c = self.canvas
        c.delete("all")
        self.hit.clear()
        self.volume_hit.clear()
        if self.width <= HEADER or self.height <= RULER:
            for button in self.add_buttons.values():
                button.place_forget()
            return
        end = self.offset + self.visible
        # Keep overlap rows stable under the pointer; pack them again on drop.
        placed = [self.pending if self.pending and self.pending.key == clip.key else clip
                  for clip in self.clips]
        available = self.height - RULER
        minimums = [max(98, self.lane_layout[lane][1] * ROW + 18) for lane in LANES]
        slack = max(0, available - sum(minimums))
        lane_heights = [value + slack / 2 for value in minimums]
        self.body = sum(lane_heights)
        self.top = min(max(0, self.top), max(0, self.body - available))
        self.horizontal.set(self.offset / self.extent, min(1, end / self.extent))
        self.vertical.set(self.top / self.body, min(1, (self.top + available) / self.body))
        step = self.grid_interval()
        first = floor(self.offset / step)
        last = ceil(end / step)
        y = RULER - self.top
        spans = {}
        for index, lane in enumerate(LANES):
            bottom = y + lane_heights[index]
            spans[lane] = (y, bottom)
            c.create_rectangle(0, y, self.width, bottom, fill=BG if index == 0 else "#161f31",
                               outline="")
            c.create_line(0, bottom, self.width, bottom, fill=BORDER)
            y = bottom
        for tick in range(first, last + 1):
            x = self.x(tick * step)
            if x >= HEADER:
                c.create_line(x, RULER, x, self.height, fill=GRID)

        if self.beats:
            # Guides cross both lanes, while gold ticks on each music clip identify the source.
            last_x = -100
            for beat in sorted({beat for clip in placed if clip.lane == "Music"
                                for beat in upcoming_beats(clip, self.offset, end)}):
                x = self.x(beat)
                if x - last_x >= 5:
                    c.create_line(x, RULER, x, self.height, fill="#a78b59", dash=(2, 4))
                    c.create_line(x, RULER, x, RULER + 4, fill=BEAT)
                    last_x = x

        for lane in LANES:
            y, bottom = spans[lane]
            rows, count = self.lane_layout[lane]
            row_height = min(91, (bottom - y - 18) / count)
            origin = y + (bottom - y - row_height * count) / 2
            clips = [clip for clip in placed if clip.lane == lane]
            for clip in clips:
                top = origin + rows[clip.key] * row_height + 3
                self.draw_clip(clip, top, top + row_height - 6)
            if not clips:
                c.create_text(HEADER + 17, (y + bottom) / 2, anchor="w", fill=MUTED,
                              font=self.small_font, text=f"Import one or more {lane.lower()} files")
            self.draw_lane_header(lane, len(clips), y, bottom)
        c.create_line(HEADER, RULER, HEADER, self.height, fill=BORDER)
        c.create_rectangle(0, 0, self.width, RULER, fill=SURFACE, outline="")
        c.create_text(16, RULER / 2, text="TIMELINE", fill=MUTED, anchor="w", font=self.small_font)
        c.create_line(0, RULER, self.width, RULER, fill=BORDER)
        decimals = max(0, 3 - floor(log10(step)))
        for tick in range(first, last + 1):
            ms = tick * step
            x = self.x(ms)
            if HEADER <= x <= self.width:
                c.create_line(x, RULER - 7, x, RULER, fill=MUTED)
                c.create_text(x + 5, 14, text=clock(ms, min(3, decimals)), anchor="w",
                              font=self.small_font, fill=MUTED)
        if self.guide is not None:
            snap, is_beat = self.guide
            x = self.x(snap)
            if HEADER <= x <= self.width:
                c.create_line(x, RULER, x, self.height, fill=BEAT if is_beat else ACCENT, width=2)
        self.draw_head()
        if self.pending:
            mode = self.drag["mode"]
            if mode == "gain":
                label = level_text(self.pending.gain ** 3)
            elif mode == "section":
                label = level_text(volume_level(self.pending, self.drag["at"] - self.pending.start) ** 3)
            elif mode == "point":
                _, level = self.pending.points[self.drag["index"]]
                label = level_text(level ** 3)
            elif mode in ("fade_in", "fade_out"):
                label = clock(getattr(self.pending, mode))
            else:
                label = clock(self.pending.end if mode == "out" else self.pending.start)
            if self.guide is not None and self.guide[1]:
                label += "  •  BEAT"
            right = min(self.width - 5, max(HEADER + 100, self.drag["x"] + 45))
            left = right - self.small_font.measure(label) - 18
            c.create_rectangle(left, 4, right, 27, fill=TEXT, outline="")
            c.create_text((left + right) / 2, 15, text=label, fill=BG, font=self.small_font)

    def draw_lane_header(self, lane, count, top, bottom):
        canvas = self.canvas
        canvas.create_rectangle(0, top, HEADER, bottom, fill=PANEL, outline="")
        canvas.create_line(0, bottom, HEADER, bottom, fill=BORDER)
        visible_top, visible_bottom = max(RULER, top), min(self.height, bottom)
        visible_height = visible_bottom - visible_top
        center = (visible_top + visible_bottom) / 2
        button = self.add_buttons[lane]
        button_height = max(26, button.winfo_reqheight())
        if visible_height >= button_height + 44:
            label_y = center - (button_height + 12) / 2
            count_label = f"{count} clip" if count == 1 else f"{count} clips"
            canvas.create_text(25, label_y + 16, text=count_label, anchor="w",
                               font=self.small_font, fill=MUTED)
            button.place(x=10, y=round(center + 6), width=HEADER - 20, height=button_height)
        else:
            button.place_forget()
            if visible_height < 20:
                return
            label_y = center
        canvas.create_rectangle(13, label_y - 7, 17, label_y + 7,
                                fill=COLORS[lane][1], outline="")
        canvas.create_text(25, label_y, text=lane, anchor="w", font=self.lane_font, fill=TEXT)

    def grid_interval(self):
        target = 77 / self.scale
        power = 10 ** floor(log10(target))
        return max(1, round(next(mult * power for mult in (1, 2, 5, 10) if mult * power >= target)))

    def draw_clip(self, clip, top, bottom):
        x0, x1 = self.x(clip.start), self.x(clip.end)
        left, right = max(x0, HEADER), min(x1, self.width)
        if right <= left or bottom <= RULER or top >= self.height:
            return
        right = max(left + 1, right)
        self.hit.append((clip, left, right, top, bottom, x0, x1))
        c = self.canvas
        bg, wave, outline = COLORS[clip.lane]
        chosen = clip.key == self.active
        c.create_rectangle(left, top, right, bottom, fill=bg, outline=wave if chosen else outline,
                           width=2 if chosen else 1)
        mid = (top + bottom + 15) / 2
        amplitude = max(1, (bottom - top - 20) / 2)
        peaks = clip.asset.peaks
        if peaks and right - left > 4:
            px0, px1 = round(left + 2), round(right - 2)
            ms_per_pixel = 1 / self.scale
            upper, lower = [], []
            pixels = range(px0, px1 + 1, 2)
            times = np.clip((np.asarray(pixels) - x0) * ms_per_pixel, 0, clip.length)
            gains = np.broadcast_to(np.sqrt(clip_amplitude(clip, times)), times.shape)
            for px, gain in zip(pixels, gains):
                source_ms = clip.source_in + (px - x0) * ms_per_pixel
                stop_ms = min(clip.source_out, source_ms + 2 * ms_per_pixel)
                start_bin = max(0, min(len(peaks) - 1, int(source_ms * len(peaks) / clip.asset.duration)))
                end_bin = min(len(peaks), ceil(stop_ms * len(peaks) / clip.asset.duration))
                peak = max(peaks[start_bin:max(start_bin + 1, end_bin)])
                h = max(0.5, amplitude * peak * gain / 255)
                upper.append((px, mid - h))
                lower.append((px, mid + h))
            if len(upper) > 1:
                c.create_polygon(upper + lower[::-1], fill=wave)
        if self.beats and clip.lane == "Music":
            last = left - 5
            for beat in upcoming_beats(clip, self.time(left) - 1, self.time(right) + 1):
                x = self.x(beat)
                if left <= x <= right and x - last >= 5:
                    c.create_line(x, bottom - 3, x, top + 4, fill=BG, width=3)
                    c.create_line(x, bottom - 3, x, top + 4, fill=BEAT, dash=(3, 2))
                    last = x
        if right - left > 30:
            label = clip.asset.path.name
            area = right - left - 21
            if self.clip_font.measure(label) > area:
                lo, hi = 0, len(label)
                while lo < hi:
                    cut = (lo + hi + 1) // 2
                    if self.clip_font.measure(label[:cut] + "…") <= area:
                        lo = cut
                    else:
                        hi = cut - 1
                label = label[:lo] + "…"
            c.create_text(left + 12, top + 10, text=label, anchor="w", fill=TEXT, font=self.clip_font)
        if right - left >= 23:
            for x in (x0 + 4, x1 - 4):
                if left + 2 <= x <= right - 2:
                    c.create_line(x, mid - 7, x, mid + 7, fill=wave, width=2)
        if x1 - x0 >= 28:
            self.draw_volume(clip, left, right, x0, x1, top, bottom, chosen)

    def draw_volume(self, clip, left, right, x0, x1, top, bottom, chosen):
        c = self.canvas
        ceiling, floor_y = top + 21, bottom - 5
        height = floor_y - ceiling
        start, end = clip.fades
        handles = {}
        def inset(length):
            return min(x1 - x0 - 6, max(6, length * self.scale))

        in_x, out_x = x0 + inset(start), x1 - inset(end)
        if out_x - in_x < 12:
            center = min(x1 - 12, max(x0 + 12, (in_x + out_x) / 2))
            in_x, out_x = center - 6, center + 6
        for mode, edge, direction, length, curve in (
            ("fade_in", x0, 1, start, clip.in_curve),
            ("fade_out", x1, -1, end, clip.out_curve),
        ):
            handle = in_x if mode == "fade_in" else out_x
            if length:
                fade_left, fade_right = sorted((edge, edge + direction * length * self.scale))
                fade_left, fade_right = max(left, fade_left), min(right, fade_right)
                if fade_right > fade_left:
                    xs = np.linspace(fade_left, fade_right, 24)
                    shape = fade_shape((xs - edge) * direction / (length * self.scale), curve)
                    path = list(zip(xs, floor_y - height * shape))
                    c.create_line(path, fill=BEAT, width=2, dash=(4, 2))
            if left + 3 <= handle <= right - 3:
                handles[mode] = handle
                c.create_polygon(handle - 5, top + 3, handle + 5, top + 3, handle, top + 12,
                                 fill=BEAT, outline=BG)

        point_positions = {}
        xs = {left + 2, right - 2}
        for index, (source, level) in enumerate(clip.points):
            x = self.x(clip.start + source - clip.source_in)
            if clip.source_in < source < clip.source_out and left + 2 < x < right - 2:
                xs.add(x)
                if left + 3 <= x <= right - 3:
                    point_positions[index] = (x, floor_y - height * level)
        if right - left > 7:
            xs = np.array(sorted(xs))
            times = self.offset + (xs - HEADER) / self.scale - clip.start
            levels = np.broadcast_to(volume_level(clip, times), times.shape)
            path = list(zip(xs, floor_y - height * levels))
            c.create_line(path, fill=BG, width=5)
            c.create_line(path, fill=ACCENT if chosen else TEXT, width=2)
            if self.drag and self.drag["mode"] == "section" and self.drag["clip"].key == clip.key:
                start, end = self.drag["section"]
                highlighted = [xy for xy, time in zip(path, times)
                               if start <= time + clip.source_in <= end]
                if len(highlighted) > 1:
                    c.create_line(highlighted, fill=ACCENT, width=4)
        for x, y in point_positions.values():
            c.create_polygon(x - 5, y, x, y - 5, x + 5, y, x, y + 5,
                             fill=TEXT, outline=BG, width=1.5)
        self.volume_hit[clip.key] = (ceiling, floor_y, top, handles, point_positions)

    def draw_head(self):
        c = self.canvas
        c.delete("playhead")
        x = self.x(self.position)
        if HEADER <= x <= self.width:
            c.create_line(x, RULER - 5, x, self.height, fill=BG, width=4, tags="playhead")
            c.create_line(x, RULER - 5, x, self.height, fill=ACCENT, width=2, tags="playhead")
            c.create_polygon(x - 5, RULER - 10, x + 5, RULER - 10, x + 5, RULER - 5,
                             x, RULER, x - 5, RULER - 5, fill=ACCENT, tags="playhead")

    def target(self, x, y):
        if x >= HEADER and y >= RULER:
            for clip, left, right, top, bottom, x0, x1 in reversed(self.hit):
                if left <= x <= right and top <= y <= bottom:
                    controls = self.volume_hit.get(clip.key)
                    if controls:
                        ceiling, floor_y, top, handles, points = controls
                        for mode, handle in handles.items():
                            if abs(x - handle) <= 6 and y <= top + 14:
                                return clip, mode
                        for index, (px, py) in points.items():
                            if abs(x - px) <= 6 and abs(y - py) <= 6:
                                return clip, ("point", index)
                    if x1 - x0 >= 23 and x - x0 <= 8:
                        return clip, "in"
                    if x1 - x0 >= 23 and x1 - x <= 8:
                        return clip, "out"
                    if controls:
                        level_y = floor_y - (floor_y - ceiling) * volume_level(clip, self.time(x) - clip.start)
                        if abs(y - level_y) <= 5:
                            return clip, "gain"
                    return clip, "move"
        return None, None

    def hover(self, event):
        if not self.enabled or self.drag or self.pan_origin is not None:
            return
        if event.x >= HEADER and event.y < RULER:
            self.canvas.configure(cursor="sb_h_double_arrow")
            return
        _, mode = self.target(event.x, event.y)
        if isinstance(mode, tuple):
            mode = mode[0]
        self.canvas.configure(cursor={"in": "sb_h_double_arrow", "out": "sb_h_double_arrow",
                                      "fade_in": "sb_h_double_arrow", "fade_out": "sb_h_double_arrow",
                                      "gain": "sb_v_double_arrow", "point": "fleur",
                                      "move": "fleur"}.get(mode, ""))

    def press(self, event):
        if not self.enabled or self.pan_origin is not None:
            return
        self.refresh_hit_targets()
        self.canvas.focus_set()
        if event.x < HEADER:
            return
        if event.y < RULER:
            self.scrub_origin = self.position
            self.scrub("start", self.time(event.x))
            return
        clip, mode = self.target(event.x, event.y)
        if not clip:
            self.select(None)
            self.seek(self.time(event.x))
            return
        self.select(clip.key)
        index = None
        if isinstance(mode, tuple):
            mode, index = mode
        seed = None
        section = None
        whole_clip = event.state & self.point_modifier and event.state & SHIFT
        if event.state & self.point_modifier and not whole_clip and mode == "gain":
            seed = self.clip_with_point(clip, event.x)
            if seed != clip:
                mode = "point"
                index = next(i for i, point in enumerate(seed.points) if point not in clip.points)
                self.pending = seed
        elif mode == "gain" and clip.points and not whole_clip:
            section = self.volume_section(clip, event.x)
            mode = "section"
        self.drag = {"clip": clip, "mode": mode, "at": self.time(event.x),
                     "origin": event.x, "x": event.x, "origin_y": event.y, "y": event.y,
                     "index": index, "volume": self.volume_hit.get(clip.key),
                     "seed": seed, "section": section, "state": event.state,
                     "volume_travel": 0.0, "moved": False}
        self.queue_draw()

    def volume_section(self, clip, x):
        source = self.time(x) - clip.start + clip.source_in
        bounds = [clip.source_in, *(time for time, _ in clip.points
                                    if clip.source_in < time < clip.source_out), clip.source_out]
        right = min(len(bounds) - 1, max(1, bisect_right(bounds, source)))
        return bounds[right - 1], bounds[right]

    def with_volume_endpoints(self, clip, times, levels):
        points = dict(clip.points)
        points.update(zip(times, levels))
        return replace(clip, points=tuple(sorted(points.items())))

    def reset_section_volume(self, clip, bounds):
        self.edit(self.with_volume_endpoints(clip, bounds, (clip.gain, clip.gain)))

    def clip_with_point(self, clip, x):
        source = self.time(x) - clip.start + clip.source_in
        if not clip.source_in < source < clip.source_out or any(t == source for t, _ in clip.points):
            return clip
        return self.with_volume_endpoints(clip, (source,), (float(point_level(clip, source)),))

    def fade_icon(self, curve, mode, selected):
        key = curve, mode, selected
        if key not in self.curve_icons:
            icon = tk.PhotoImage(master=self.canvas, width=84, height=27)
            icon.put(PANEL, to=(0, 0, 84, 27))
            icon.put(BORDER, to=(15, 22, 78, 23))
            if selected:
                icon.put(ACCENT, to=(4, 10, 9, 15))
            for x in range(16, 77):
                fraction = (x - 16) / 60
                if mode == "fade_out":
                    fraction = 1 - fraction
                y = 21 - round(16 * fade_shape(fraction, curve))
                icon.put(BEAT, to=(x, y, x + 1, y + 2))
            self.curve_icons[key] = icon
        return self.curve_icons[key]

    def clip_menu(self, event):
        if not self.enabled or self.drag or self.pan_origin is not None:
            return "break"
        self.refresh_hit_targets()
        clip, mode = self.target(event.x, event.y)
        if clip is None:
            return "break"
        self.select(clip.key)
        menu = self.context_menu
        menu.delete(0, "end")
        if mode in ("fade_in", "fade_out"):
            field = "in_curve" if mode == "fade_in" else "out_curve"
            for curve in FADE_CURVES:
                menu.add_command(image=self.fade_icon(curve, mode, getattr(clip, field) == curve),
                                 command=lambda curve=curve: self.edit(replace(clip, **{field: curve})))
        else:
            if isinstance(mode, tuple):
                index = mode[1]
                menu.add_command(label="Remove Volume Point",
                                 command=lambda: self.edit(replace(
                                     clip, points=clip.points[:index] + clip.points[index + 1:])))
            elif clip.length > 1:
                menu.add_command(label="Add Volume Point",
                                 command=lambda: self.edit(self.clip_with_point(clip, event.x)))
            if menu.index("end") is not None:
                menu.add_separator()
            bounds = self.volume_section(clip, event.x)
            has_automation = bool(clip.points)
            menu.add_command(label="Reset Section to Clip Level",
                             command=lambda: self.reset_section_volume(clip, bounds),
                             state="normal" if has_automation else "disabled")
            menu.add_command(label="Reset Clip Volume",
                             command=lambda: self.edit(replace(clip, gain=1.0, points=())),
                             state="normal" if has_automation or clip.gain != 1 else "disabled")
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()
        return "break"

    def snapped(self, clip, points, low, high):
        if not (self.snap or self.beats):
            return 0, None
        threshold = min(180, 8 / self.scale)
        candidates = []
        for value in points:
            marks = []
            if self.snap:
                marks.append((self.position, False))
                marks.extend((time, False) for other in self.clips if other.key != clip.key
                             for time in (other.start, other.end))
                step = self.grid_interval()
                marks.append((round(value / step) * step, False))
            if self.beats:
                for other in self.clips:
                    # A trim can snap to its own source beats, including newly revealed audio.
                    if other.key == clip.key and self.drag["mode"] != "move":
                        other = replace(clip, start=clip.start - clip.source_in,
                                        source_in=0, source_out=clip.asset.duration)
                    elif other.key == clip.key:
                        continue
                    if other.lane == "Music":
                        marks.extend((beat, True) for beat in upcoming_beats(
                            other, value - threshold, value + threshold))
            for mark, is_beat in marks:
                delta = mark - value
                if abs(delta) <= threshold and low <= delta <= high:
                    candidates.append((not is_beat, abs(delta), delta, mark))
        if candidates:
            normal, _, delta, mark = min(candidates)
            return delta, (mark, not normal)
        return 0, None

    def update_drag(self):
        drag = self.drag
        clip, mode = drag["clip"], drag["mode"]
        delta = self.time(drag["x"]) - drag["at"]
        if mode in ("gain", "section", "point", "fade_in", "fade_out"):
            self.update_volume_drag(clip, drag, delta)
            self.queue_draw()
            return
        if mode == "move":
            start = max(0, min(86_399_999, clip.start + delta))
            snap, self.guide = self.snapped(clip, (start, start + clip.length),
                                            -start, 86_399_999 - start)
            self.pending = replace(clip, start=start + snap)
        elif mode == "in":
            low = max(-clip.start, -clip.source_in)
            high = clip.length - 1
            delta = max(low, min(high, delta))
            snap, self.guide = self.snapped(clip, (clip.start + delta,), low - delta, high - delta)
            self.pending = replace(clip, start=clip.start + delta + snap,
                                   source_in=clip.source_in + delta + snap)
        else:
            low, high = 1 - clip.length, clip.asset.duration - clip.source_out
            delta = max(low, min(high, delta))
            snap, self.guide = self.snapped(clip, (clip.end + delta,), low - delta, high - delta)
            self.pending = replace(clip, source_out=clip.source_out + delta + snap)
        self.queue_draw()

    def update_volume_drag(self, clip, drag, delta):
        mode = drag["mode"]
        ceiling, floor_y, *_ = drag["volume"]
        change = drag["volume_travel"] / (floor_y - ceiling)
        finer = bool(drag["state"] & SHIFT)
        step = (0.1 if finer else 1) if drag["state"] & self.volume_snap_modifier else 0
        if mode == "gain":
            gain = bounded_level(clip.gain + change)
            if step and gain != clip.gain:
                gain = snap_volume(gain, step)
                change = gain - clip.gain
            direct_drag = abs(drag["volume_travel"] - (drag["origin_y"] - drag["y"])) < 1e-9
            if direct_drag and not finer and drag["y"] >= floor_y:
                gain, change = 0, -1
            points = tuple((time, bounded_level(level + change)) for time, level in clip.points)
            self.pending = replace(clip, gain=gain, points=points)
        elif mode == "section":
            times = drag["section"]
            previous = tuple(float(point_level(clip, time)) for time in times)
            source = drag["at"] - clip.start + clip.source_in
            fraction = bounded_level((source - times[0]) / (times[1] - times[0]))
            levels = shift_volume_pair(previous, fraction, change, step)
            self.pending = self.with_volume_endpoints(clip, times, levels) if levels != previous else clip
        elif mode == "point":
            clip = drag["seed"] or clip
            index = drag["index"]
            original, old_level = clip.points[index]
            low, high = clip.source_in + 1, clip.source_out - 1
            if index:
                low = max(low, clip.points[index - 1][0] + 1)
            if index + 1 < len(clip.points):
                high = min(high, clip.points[index + 1][0] - 1)
            source = min(high, max(low, original + delta))
            level = bounded_level(old_level + change)
            if step and level != old_level:
                level = snap_volume(level, step)
            self.pending = replace(clip, points=(*clip.points[:index], (source, level),
                                                *clip.points[index + 1:]))
        else:
            fade_in, fade_out = clip.fades
            if mode == "fade_in":
                length = min(clip.length - fade_out, max(0, fade_in + delta))
                self.pending = replace(clip, fade_in=length)
            else:
                length = min(clip.length - fade_in, max(0, fade_out - delta))
                self.pending = replace(clip, fade_out=length)

    def motion(self, event):
        if self.scrub_origin is not None:
            self.scrub("move", self.time(event.x))
            return
        if not self.drag:
            return
        self.drag["x"] = event.x
        travel = self.drag["y"] - event.y
        self.drag["volume_travel"] += travel * (0.2 if event.state & SHIFT else 1)
        self.drag["y"] = event.y
        self.drag["state"] = event.state
        vertical = self.drag["mode"] in ("gain", "section", "point")
        distance = abs(event.x - self.drag["origin"])
        if vertical:
            distance = max(distance, abs(event.y - self.drag["origin_y"]))
        if not self.drag["moved"] and distance < 3:
            return
        self.drag["moved"] = True
        self.update_drag()
        if self.pan_timer is None and self.drag["mode"] not in ("gain", "section"):
            self.auto_pan()

    def auto_pan(self):
        self.pan_timer = None
        if not self.drag:
            return
        x = self.drag["x"]
        push = x - (self.width - 22) if x > self.width - 22 else min(0, x - (HEADER + 22))
        if not push:
            return
        self.offset += max(-18, min(18, push * 0.28)) / self.scale
        self.limit_scroll()
        self.update_drag()
        self.pan_timer = self.after(28, self.auto_pan)

    def release(self, event):
        if self.scrub_origin is not None:
            self.scrub_origin = None
            self.scrub("end", self.time(event.x))
            return
        if not self.drag:
            return
        pending = self.pending
        original = self.drag["clip"]
        if not self.drag["moved"] and self.drag["mode"] in ("gain", "section"):
            previous = self.last_volume_click
            now = monotonic()
            if (previous and previous[0] == original.key and now - previous[1] <= .45
                    and abs(event.x - previous[2]) <= 5 and abs(event.y - previous[3]) <= 5):
                pending = self.clip_with_point(original, event.x)
                self.last_volume_click = None
            else:
                self.last_volume_click = original.key, now, event.x, event.y
        else:
            self.last_volume_click = None
        offset = self.offset
        self.cancel()
        if pending and pending != original:
            self.offset = offset
            self.edit(pending)

    def cancel(self, _event=None):
        if self.scrub_origin is not None:
            origin, self.scrub_origin = self.scrub_origin, None
            self.scrub("end", origin)
        if self.pan_origin is not None:
            self.pan_origin = None
            self.canvas.configure(cursor="")
        if self.pan_timer is not None:
            self.after_cancel(self.pan_timer)
            self.pan_timer = None
        self.drag = self.pending = self.guide = None
        self.limit_scroll()
        self.queue_draw()
        return "break"
