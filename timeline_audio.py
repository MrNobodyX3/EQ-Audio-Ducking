"""Inspect source audio and assemble the two timeline lanes at sample precision."""

from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np

from beats import detect_beats
from duck_music import (
    audio_command, audio_format, commit_output, decoded, render, validate_output, write_float_wav,
)

LANES = ("Dialogue", "Music")
PEAK_RATE = 8000
FADE_CURVES = ("linear", "smooth", "fast", "slow")


@dataclass(frozen=True)
class Asset:
    path: Path
    duration: int  # milliseconds
    rate: int
    channels: int
    peaks: bytes
    beats: tuple = ()


@dataclass(frozen=True)
class Clip:
    key: int
    lane: str
    asset: Asset
    start: int
    source_in: int
    source_out: int
    gain: float = 1.0
    fade_in: int = 0
    fade_out: int = 0
    in_curve: str = "linear"
    out_curve: str = "linear"
    # Source-relative milliseconds and absolute control levels between zero and one.
    points: tuple[tuple[int, float], ...] = ()

    @property
    def length(self):
        return self.source_out - self.source_in

    @property
    def end(self):
        return self.start + self.length

    @property
    def fades(self):
        fade_in = min(self.fade_in, self.length)
        return fade_in, min(self.fade_out, self.length - fade_in)

    @property
    def has_volume(self):
        if not self.points:
            return self.gain > 0
        return (any(level > 0 for time, level in self.points if self.source_in < time < self.source_out)
                or point_level(self, self.source_in) > 0 or point_level(self, self.source_out) > 0)


def fade_shape(x, curve):
    if curve == "smooth":
        return x * x * (3 - 2 * x)
    if curve == "fast":
        return 1 - (1 - x) ** 2
    if curve == "slow":
        return x * x
    return x


def point_level(clip, source_ms):
    if not clip.points:
        return clip.gain
    points = clip.points
    if points[0][0] > 0:
        points = ((0, clip.gain), *points)
    if points[-1][0] < clip.asset.duration:
        points = (*points, (clip.asset.duration, clip.gain))
    times, levels = zip(*points)
    return np.interp(source_ms, times, levels)


def volume_level(clip, local_ms):
    return point_level(clip, clip.source_in + local_ms)


def clip_amplitude(clip, local_ms):
    gain = volume_level(clip, local_ms) ** 3
    fade_in, fade_out = clip.fades
    if fade_in:
        gain = gain * fade_shape(np.clip(local_ms / fade_in, 0, 1), clip.in_curve)
    if fade_out:
        gain = gain * fade_shape(np.clip((clip.length - local_ms) / fade_out, 0, 1), clip.out_curve)
    return gain


def level_text(amplitude):
    if amplitude <= 0:
        return "Mute"
    return f"{20 * np.log10(amplitude):.1f} dB".replace("-", "−")


def duration_of(clips):
    return max((clip.end for clip in clips), default=0)


def music_spans(clips):
    return tuple((clip.start, clip.end) for clip in clips if clip.lane == "Music" and clip.has_volume)


def music_layers(clips):
    """Group clips that can safely share a render. Close edges can share an FFT window."""
    music = sorted((clip for clip in clips if clip.lane == "Music" and clip.has_volume),
                   key=lambda clip: clip.start)
    if (len({clip.gain for clip in music}) <= 1
            and not any(clip.points or any(clip.fades) for clip in music)):
        return [music]
    layers = []
    for clip in music:
        for layer in layers:
            if layer[-1].end + 64 <= clip.start:
                layer.append(clip)
                break
        else:
            layers.append([clip])
    return layers or [[]]


def layer_baseline(layer):
    # Extend each clip's boundary gain into gaps, switching at their midpoints.
    boundaries = [(left.end + right.start) / 2 for left, right in zip(layer, layer[1:])]

    def baseline(times):
        indices = np.searchsorted(boundaries, times)
        cuts = np.zeros(len(times), dtype=np.float32)
        for index in np.unique(indices):
            clip = layer[index]
            mask = indices == index
            amplitude = clip_amplitude(clip, np.clip(times[mask] - clip.start, 0, clip.length))
            with np.errstate(divide="ignore"):
                cuts[mask] = -20 * np.log10(amplitude)
        return cuts

    if layer and any(clip.gain != 1 or clip.points or any(clip.fades) for clip in layer):
        return baseline
    return None


def inspect_asset(path, *, find_beats=False):
    path = Path(path).resolve(strict=True)
    rate, channels = audio_format(path)
    with TemporaryDirectory(prefix="ducking-waveform-") as folder:
        with decoded(path, Path(folder) / "peaks.raw", PEAK_RATE, min(2, channels)) as samples:
            # Bound the number of displayed peaks even for long recordings.
            block_size = max(80, (len(samples) + 4095) // 4096)
            blocks, remainder = divmod(len(samples), block_size)
            full = samples[:blocks * block_size].reshape(blocks, -1) if blocks else None
            peaks = np.max(np.abs(full), axis=1) if blocks else np.empty(0)
            if remainder:
                peaks = np.append(peaks, np.max(np.abs(samples[-remainder:])))
            values = np.nan_to_num(peaks, nan=0).clip(0, 1)
            # Make quiet speech visible without changing the rendered levels.
            envelope = np.round(np.sqrt(values) * 255).astype(np.uint8).tobytes()
            beats = detect_beats(samples, PEAK_RATE) if find_beats else ()
            return Asset(path, max(1, round(len(samples) * 1000 / PEAK_RATE)),
                         rate, channels, envelope, beats)


def stem_channels(clips, lane):
    return 2 if any(clip.asset.channels > 1 for clip in clips if clip.lane == lane) else 1


def assemble(clips, folder, progress, *, lanes=LANES, music_layer=None):
    """Write aligned float WAV stems, summing overlaps and filling gaps with silence."""
    if not clips:
        raise ValueError("Add audio to the timeline first.")
    for clip in clips:
        if (clip.lane not in LANES or clip.start < 0
                or not 0 <= clip.source_in < clip.source_out <= clip.asset.duration):
            raise ValueError(f"Invalid timing for {clip.asset.path.name}.")

    rate = max(clip.asset.rate for clip in clips)

    def frame(milliseconds):
        return round(milliseconds * rate / 1000)

    frames = max(1, frame(duration_of(clips)))
    outputs = {}
    completed = 0
    total = sum(clip.lane in lanes for clip in clips) if music_layer is None else len(music_layer)
    for lane in lanes:
        lane_clips = [clip for clip in clips if clip.lane == lane]
        channels = stem_channels(clips, lane)
        if lane == "Music" and music_layer is not None:
            lane_clips = music_layer
        raw_path = folder / f"{lane}.raw"
        raw = np.memmap(raw_path, mode="w+", dtype="<f4", shape=(frames, channels))
        try:
            # Reuse a single decode for repeated placements of the same file.
            groups = {}
            for clip in lane_clips:
                groups.setdefault(clip.asset.path, []).append(clip)
            for path, placements in groups.items():
                progress(f"Placing {path.name}…", 0.04 + 0.83 * completed / total)
                source_path = folder / "source.raw"
                with decoded(path, source_path, rate, channels) as samples:
                    for clip in placements:
                        left = frame(clip.start)
                        source = frame(clip.source_in)
                        clip_frames = frame(clip.end) - left
                        count = min(clip_frames, len(samples) - source)
                        if count > 0 and clip.has_volume:
                            # Only touch the pages needed for this placement.
                            for offset in range(0, count, 262144):
                                size = min(262144, count - offset)
                                block = samples[source + offset:source + offset + size]
                                if clip.points or clip.fade_in or clip.fade_out:
                                    milliseconds = (np.arange(offset, offset + size)
                                                    * (clip.length / max(1, clip_frames - 1)))
                                    block = block * clip_amplitude(clip, milliseconds)[:, None]
                                elif clip.gain != 1:
                                    block = block * clip.gain ** 3
                                raw[left + offset:left + offset + size] += block
                        completed += 1
                source_path.unlink()
            raw.flush()
        finally:
            raw._mmap.close()

        output = folder / f"{lane}.wav"
        write_float_wav(raw_path, output, rate, channels)
        raw_path.unlink()
        outputs[lane] = output
    progress("Timeline ready.", 1.0)
    return outputs


def render_timeline_music(clips, stems, work, output, settings, *, overwrite=False,
                          progress, observe_layer=None):
    """Apply the remaining EQ cut to each music layer, then sum the layers."""
    output = validate_output(output, {clip.asset.path for clip in clips}, overwrite)
    layers = music_layers(clips)

    def render_layer(layer, music, destination, report, *, replace_output=False):
        return render(music, stems["Dialogue"], destination, settings, overwrite=replace_output,
                      music_spans=music_spans(layer), clip_baseline=layer_baseline(layer),
                      progress=report, observe_cuts=observe_layer(layer) if observe_layer else None)

    if len(layers) == 1:
        return render_layer(layers[0], stems["Music"], output, progress, replace_output=overwrite)

    rendered = []
    for index, layer in enumerate(layers):
        folder = work / f"layer-{index}"
        folder.mkdir()

        def layer_progress(stage, fraction):
            progress(stage, (index + fraction) / len(layers) * 0.95)

        music = assemble(clips, folder, lambda stage, fraction: layer_progress(stage, fraction * .2),
                         lanes=("Music",), music_layer=layer)["Music"]
        rendered.append(render_layer(
            layer, music, folder / "ducked.wav",
            lambda stage, fraction: layer_progress(stage, .2 + .8 * fraction),
        ))

    progress("Mixing the processed music clips…", 0.96)
    with TemporaryDirectory(dir=output.parent, prefix=".ducking-") as staging:
        mixed = Path(staging) / "music.wav"
        inputs = [argument for path in rendered for argument in ("-i", str(path))]
        audio_command("ffmpeg", "-v", "error", *inputs, "-filter_complex",
                      f"amix=inputs={len(rendered)}:normalize=0", "-c:a", "pcm_f32le",
                      "-rf64", "auto", str(mixed))
        commit_output(mixed, output, overwrite)
    progress("Export complete.", 1.0)
    return output


def export_timeline(clips, lane, output, settings, *, overwrite=False, progress):
    if lane not in LANES or not any(clip.lane == lane for clip in clips):
        raise ValueError(f"Add a {lane.lower()} clip before exporting.")
    output = validate_output(output, {clip.asset.path for clip in clips}, overwrite)
    if lane == "Dialogue":
        with TemporaryDirectory(dir=output.parent, prefix=".ducking-") as folder:
            stems = assemble(clips, Path(folder), progress, lanes=("Dialogue",))
            commit_output(stems[lane], output, overwrite)
        progress("Export complete.", 1.0)
        return output

    with TemporaryDirectory(prefix="ducking-timeline-") as folder:
        work = Path(folder)
        stems = assemble(clips, work,
                         lambda stage, fraction: progress(stage, fraction * 0.25))
        return render_timeline_music(clips, stems, work, output, settings, overwrite=overwrite,
                                     progress=lambda stage, fraction: progress(stage, .25 + .75 * fraction))
