"""Preview preparation and playback."""

from dataclasses import dataclass
import json
import subprocess

import numpy as np

from duck_music import audio_command, render


MUSIC_VOLUME = 45
MIN_GRAPH_HZ = 20
MAX_GRAPH_HZ = 20000


@dataclass(frozen=True)
class EQTrace:
    frequencies: np.ndarray
    frame_interval: float
    frames: np.ndarray
    soften_db: float = 0


class EQTraceBuilder:
    """Preview EQ curve sampling."""

    def __init__(self):
        self.frames = []
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
            self.frames.append(np.stack([
                np.maximum.reduceat(series[rows, self.bins], self.starts, axis=1)
                for series in (dialogue_target, cuts)
            ], axis=1))

    def finish(self, soften_db=0):
        return EQTrace(self.frequencies, self.interval, np.concatenate(self.frames, axis=0), soften_db)


def prepare_preview(music, dialogue, settings, work, progress):
    ducked = work / "ducked.wav"
    trace = EQTraceBuilder()
    render(music, dialogue, ducked, settings, overwrite=True,
           progress=lambda stage, fraction: progress(stage, fraction * 0.8),
           observe_cuts=trace.record)
    info = json.loads(audio_command(
        "ffprobe", "-v", "error", "-select_streams", "a:0",
        "-show_entries", "format=duration:stream=channels", "-of", "json", str(ducked),
    ))
    duration = float(info["format"]["duration"])
    # Unity-gain mono upmix
    music_layout = ("pan=stereo|FL=c0|FR=c0" if info["streams"][0]["channels"] == 1
                    else "aformat=channel_layouts=stereo")

    progress("Preparing preview audio…", 0.82)
    original = music
    if settings.soften_db:
        original = work / "original.wav"
        audio_command(
            "ffmpeg", "-v", "error", "-y", "-i", str(music), "-map", "0:a:0",
            "-af", f"volume=-{settings.soften_db}dB", "-c:a", "pcm_f32le", str(original),
        )

    tracks = {}
    for mode, source in (("Original", original), ("Ducked", ducked)):
        mix = work / f"{mode}-with-dialogue.wav"
        audio_command(
            "ffmpeg", "-v", "error", "-y", "-i", str(source), "-i", str(dialogue),
            "-filter_complex",
            f"[0:a:0]{music_layout}[m];"
            "[1:a:0]aformat=channel_layouts=stereo[d];"
            f"[m][d]amix=duration=first:normalize=0,volume={MUSIC_VOLUME / 100}[out]",
            "-map", "[out]", "-t", str(duration), "-c:a", "pcm_f32le", str(mix),
        )
        tracks[mode, False] = source
        tracks[mode, True] = mix
    return duration, tracks, trace.finish(settings.soften_db)


def play(path, position, with_dialogue):
    try:
        return subprocess.Popen(
            ["ffplay", "-loglevel", "error", "-nodisp", "-autoexit", "-vn", "-sn", "-ast", "a:0",
             "-volume", str(100 if with_dialogue else MUSIC_VOLUME),
             "-ss", f"{position:.6f}", "-i", str(path)],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
            text=True,
        )
    except FileNotFoundError as error:
        raise RuntimeError("FFplay is required for audio previews. Install it and add it to PATH.") from error
