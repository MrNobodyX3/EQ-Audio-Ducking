"""Preview preparation and playback."""

from dataclasses import dataclass
import shutil
import subprocess

import numpy as np

from duck_music import audio_command
from timeline_audio import assemble, render_timeline_music, stem_channels


MIN_GRAPH_HZ = 20
MAX_GRAPH_HZ = 20000
MONO_TO_STEREO = "pan=stereo|FL=c0|FR=c0"


@dataclass(frozen=True)
class EQTrace:
    frequencies: np.ndarray
    frame_interval: float
    dialogue_target: np.ndarray
    clip_cuts: dict[int, np.ndarray]
    soften_db: float


class EQTraceBuilder:
    """Preview EQ curve sampling."""

    def __init__(self, *, capture_dialogue):
        self.dialogue_frames = [] if capture_dialogue else None
        self.cut_frames = []
        self.frequencies = None

    def record(self, rate, size, hop, center, dialogue_target, cuts):
        if self.frequencies is None:
            frequencies = np.fft.rfftfreq(size, d=1 / rate)
            low, high = np.searchsorted(frequencies, (MIN_GRAPH_HZ, MAX_GRAPH_HZ))
            self.bins = slice(low, high)
            bins = frequencies[self.bins]
            # Log-frequency display groups
            columns = (np.log10(bins / MIN_GRAPH_HZ) * 64).astype(int)
            _, self.starts = np.unique(columns, return_index=True)
            self.frequencies = bins[self.starts]
            self.stride = max(1, round(rate / (hop * 24)))
            self.interval = self.stride * hop / rate

        indices = center // hop + np.arange(len(cuts))
        rows = (indices >= 0) & (indices % self.stride == 0)
        if np.any(rows):
            def sample(series):
                return np.maximum.reduceat(series[rows, self.bins], self.starts, axis=1)

            self.cut_frames.append(sample(cuts))
            if self.dialogue_frames is not None:
                self.dialogue_frames.append(sample(dialogue_target))


def prepare_timeline_preview(clips, settings, work, progress):
    folder = work / "session"
    if folder.exists():
        shutil.rmtree(folder)
    folder.mkdir()
    stems = assemble(clips, folder, lambda stage, fraction: progress(stage, fraction * .26))
    ducked = folder / "ducked.wav"
    traces = []

    def observe_layer(layer):
        builder = EQTraceBuilder(capture_dialogue=not traces)
        traces.append((layer, builder))
        return builder.record

    render_timeline_music(clips, stems, folder, ducked, settings, observe_layer=observe_layer,
                          progress=lambda stage, fraction: progress(stage, .26 + fraction * .56))
    clip_cuts = {}
    for layer, builder in traces:
        if layer:
            cuts = np.concatenate(builder.cut_frames)
            clip_cuts.update((clip.key, cuts) for clip in layer)
    first = traces[0][1]
    trace = EQTrace(first.frequencies, first.interval, np.concatenate(first.dialogue_frames),
                    clip_cuts, settings.soften_db)
    tracks = preview_tracks(stems["Music"], stems["Dialogue"], ducked, settings.soften_db,
                            stem_channels(clips, "Music"), stem_channels(clips, "Dialogue"),
                            folder, progress)
    return tracks, trace


def preview_tracks(music, dialogue, ducked, soften_db, music_channels, dialogue_channels,
                   work, progress):
    # Copy mono stems to both speakers without applying a center-channel reduction.
    stereo = "aformat=channel_layouts=stereo"
    music_layout = MONO_TO_STEREO if music_channels == 1 else stereo
    dialogue_layout = MONO_TO_STEREO if dialogue_channels == 1 else stereo

    progress("Preparing preview audio…", 0.82)
    original = music
    if soften_db:
        original = work / "original.wav"
        audio_command(
            "ffmpeg", "-v", "error", "-y", "-i", str(music), "-map", "0:a:0",
            "-af", f"volume=-{soften_db}dB", "-c:a", "pcm_f32le", "-rf64", "auto", str(original),
        )

    tracks = {}
    for mode, source in (("Original", original), ("Ducked", ducked)):
        mix = work / f"{mode}-with-dialogue.wav"
        audio_command(
            "ffmpeg", "-v", "error", "-y", "-i", str(source), "-i", str(dialogue),
            "-filter_complex",
            f"[0:a:0]{music_layout}[m];"
            f"[1:a:0]{dialogue_layout}[d];"
            "[m][d]amix=inputs=2:duration=first:normalize=0[out]",
            "-map", "[out]", "-c:a", "pcm_f32le", "-rf64", "auto", str(mix),
        )
        tracks[mode, False] = source
        tracks[mode, True] = mix
    return tracks


def play(path, position, *, mono=False, volume=100):
    layout = ["-af", MONO_TO_STEREO] if mono else []
    try:
        return subprocess.Popen(
            ["ffplay", "-loglevel", "error", "-nodisp", "-autoexit", "-vn", "-sn", "-ast", "a:0",
             "-volume", str(volume), *layout,
             "-ss", f"{position:.6f}", "-i", str(path)],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
            text=True,
        )
    except FileNotFoundError as error:
        raise RuntimeError("FFplay is required for audio previews. Install it and add it to PATH.") from error
