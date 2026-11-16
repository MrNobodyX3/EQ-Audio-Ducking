"""Render music through a dialogue-driven spectral EQ."""

import argparse
from contextlib import contextmanager
from dataclasses import dataclass
import json
import math
import os
from pathlib import Path
import subprocess
import tempfile

import numpy as np


MAX_TIMING_MS = 1000
MAX_SOFTEN_DB = 24
# Per-bin reference amplitude
DIALOGUE_REFERENCE = 10 ** (-56 / 20)


@dataclass(frozen=True)
class Settings:
    influence: float = 0.8
    release_ms: float = 80
    attack_ms: float = 80
    soften_db: float = 0

    def validate(self):
        if not 0 <= self.influence <= 1:
            raise ValueError("Ducking Amount must be between 0 and 100 percent.")
        if not 0 <= self.soften_db <= MAX_SOFTEN_DB:
            raise ValueError(f"Music Attenuation must be between 0 and {MAX_SOFTEN_DB} dB.")
        if not 0 <= self.release_ms <= MAX_TIMING_MS:
            raise ValueError(f"Release must be between 0 and {MAX_TIMING_MS} ms.")
        if not 0 <= self.attack_ms <= MAX_TIMING_MS:
            raise ValueError(f"Lookahead must be between 0 and {MAX_TIMING_MS} ms.")


def audio_command(*args):
    try:
        result = subprocess.run(
            args, stdin=subprocess.DEVNULL, capture_output=True, text=True, check=True
        )
    except FileNotFoundError as error:
        raise RuntimeError(f"{args[0]} is required. Install FFmpeg and add it to PATH.") from error
    except subprocess.CalledProcessError as error:
        raise RuntimeError(error.stderr.strip() or f"{args[0]} failed.") from error
    return result.stdout


def audio_format(path):
    info = json.loads(audio_command(
        "ffprobe", "-v", "error", "-select_streams", "a:0",
        "-show_entries", "stream=sample_rate,channels", "-of", "json", str(path),
    ))
    streams = info.get("streams", [])
    if not streams or not streams[0].get("sample_rate") or not streams[0].get("channels"):
        raise ValueError(f"No readable audio track in {path.name}.")
    return int(streams[0]["sample_rate"]), int(streams[0]["channels"])


@contextmanager
def decoded(path, raw_path, rate, channels):
    audio_command(
        "ffmpeg", "-v", "error", "-xerror", "-i", str(path), "-map", "0:a:0",
        "-ar", str(rate), "-ac", str(channels), "-f", "f32le", str(raw_path),
    )
    if raw_path.stat().st_size == 0:
        raise ValueError(f"{path.name} has no audio samples.")
    samples = np.memmap(raw_path, mode="r", dtype="<f4").reshape(-1, channels)
    try:
        yield samples
    finally:
        # Windows cannot remove a temporary file while it is mapped.
        samples._mmap.close()


def write_float_wav(raw_path, output, rate, channels):
    audio_command(
        "ffmpeg", "-v", "error", "-f", "f32le", "-ar", str(rate), "-ac", str(channels),
        "-i", str(raw_path), "-c:a", "pcm_f32le", "-rf64", "auto", str(output),
    )


def frame_batch(samples, start, count, size, hop):
    length = (count - 1) * hop + size
    block = np.zeros((length, samples.shape[1]), dtype=np.float32)
    left = max(0, start)
    right = min(len(samples), start + length)
    if right > left:
        block[left - start:right - start] = samples[left:right]
    return np.lib.stride_tricks.sliding_window_view(block, size, axis=0)[::hop].swapaxes(1, 2)


def render_pcm(music, dialogue, rate, settings, raw_output, progress, *,
               observe_cuts=None, clip_baseline=None):
    size = 1 << max(4, round(math.log2(rate * 0.043)))
    hop = size // 4
    overlap = size - hop
    window = np.hanning(size + 1)[:-1].astype(np.float32)
    # Hann synthesis window
    synthesis = window / np.sum(window[::hop] ** 2)
    bin_scale = np.full(size // 2 + 1, 2 / window.sum(), dtype=np.float32)
    bin_scale[[0, -1]] *= 0.5
    attack_frames = round(rate * settings.attack_ms / (1000 * hop))
    ramp = np.linspace(1, 0, attack_frames + 1, dtype=np.float32)[1:-1]
    attack_weights = ramp * ramp * (3 - 2 * ramp)
    release = np.float32(math.exp(-hop / (rate * settings.release_ms / 1000))
                         if settings.release_ms else 0)
    hold_frames = round(rate * settings.release_ms / (3000 * hop))
    channels = music.shape[1]
    hold = np.zeros((len(bin_scale), channels), dtype=np.int32)
    envelope = np.zeros_like(hold, dtype=np.float32)
    tail = np.zeros((overlap, channels), dtype=np.float32)
    frame_count = (len(music) + overlap + hop - 1) // hop

    with raw_output.open("wb") as output:
        for first in range(0, frame_count, 128):
            count = min(128, frame_count - first)
            start = first * hop - overlap
            center = start + size // 2
            voice = frame_batch(dialogue, start, count + len(attack_weights), size, hop)
            spectrum = np.fft.rfft(voice * window[None, :, None], axis=1)
            amplitude = np.abs(spectrum)
            if channels == 1 and dialogue.shape[1] > 1:
                # A mono stem has one EQ curve, driven by the combined dialogue power.
                amplitude = np.sqrt(np.mean(amplitude ** 2, axis=2, keepdims=True))
            level = amplitude.astype(np.float32) * bin_scale[:, None] / DIALOGUE_REFERENCE
            # Dialogue target in dB
            dialogue_target = (10 / math.log(10)) * np.log1p(level ** 2)
            if channels == 2 and dialogue_target.shape[2] == 1:
                dialogue_target = np.broadcast_to(dialogue_target, (*dialogue_target.shape[:2], 2))
            baseline = settings.soften_db
            if clip_baseline is not None:
                # The clip envelope is already present in the PCM. Account for
                # it in the target without applying that attenuation twice.
                times = (center + hop * np.arange(len(dialogue_target))) * (1000 / rate)
                baseline = baseline + clip_baseline(times)[:, None, None]
            extra_targets = np.maximum(dialogue_target - baseline, 0) * settings.influence

            cuts = np.empty_like(extra_targets[:count])
            for index, target in enumerate(extra_targets[:count]):
                # Independent hold and release for every bin and output channel.
                rising = target >= envelope
                coefficient = np.where(rising, np.float32(0), np.where(hold > 0, np.float32(1), release))
                envelope = target + coefficient * (envelope - target)
                hold = np.where(rising, hold_frames, np.maximum(hold - 1, 0))
                cuts[index] = envelope

            # Lookahead envelope
            for offset, weight in enumerate(attack_weights, start=1):
                np.maximum(cuts, extra_targets[offset:offset + count] * weight, out=cuts)

            cuts += settings.soften_db
            if observe_cuts is not None:
                observe_cuts(rate, size, hop, center, dialogue_target[:count], cuts)

            frames = frame_batch(music, start, count, size, hop)
            spectrum = np.fft.rfft(frames * window[None, :, None], axis=1)
            gain = 10 ** (-cuts / 20)
            frames = np.fft.irfft(spectrum * gain, n=size, axis=1)
            frames = frames.astype(np.float32) * synthesis[None, :, None]
            block = np.zeros((count * hop + overlap, channels), dtype=np.float32)
            block[:overlap] = tail
            for offset in range(0, size, hop):
                block[offset:offset + count * hop] += frames[:, offset:offset + hop].reshape(
                    -1, channels
                )
            tail = block[count * hop:].copy()
            block[max(0, -start):min(count * hop, len(music) - start)].astype(
                "<f4", copy=False
            ).tofile(output)
            progress("Applying dynamic EQ…", min(0.94, 0.16 + 0.78 * (first + count) / frame_count))


def validate_output(output_path, sources, overwrite):
    output_path = Path(output_path).expanduser().absolute()
    if output_path.suffix.lower() != ".wav":
        raise ValueError("Choose a .wav output filename.")
    if output_path.resolve() in sources:
        raise ValueError("Choose an output filename separate from the source tracks.")
    if output_path.exists() and not overwrite:
        raise FileExistsError(f"Output already exists: {output_path}")
    return output_path


def commit_output(temporary, output, overwrite):
    if overwrite:
        os.replace(temporary, output)
    else:
        os.link(temporary, output)


def silence_outside(raw_path, channels, rate, spans):
    """Keep gaps silent even when a spectral window straddles a clip edge."""
    stride = channels * 4
    zero = memoryview(bytes(1024 * 1024))
    limit = raw_path.stat().st_size

    def clear(stream, start, end):
        if start < end:
            stream.seek(start)
            while start < end:
                count = min(end - start, len(zero))
                stream.write(zero[:count])
                start += count

    cursor = 0
    with raw_path.open("r+b") as stream:
        for start, end in sorted(spans):
            clear(stream, cursor, min(limit, round(start * rate / 1000) * stride))
            cursor = max(cursor, round(end * rate / 1000) * stride)
        clear(stream, cursor, limit)


def render(music_path, dialogue_path, output_path, settings, *,
           overwrite=False, progress=None, observe_cuts=None, music_spans=None, clip_baseline=None):
    settings.validate()
    progress = progress or (lambda stage, fraction: None)
    music_path = Path(music_path).expanduser().resolve(strict=True)
    dialogue_path = Path(dialogue_path).expanduser().resolve(strict=True)
    output_path = validate_output(output_path, (music_path, dialogue_path), overwrite)

    progress("Reading source tracks…", 0.02)
    rate, channels = audio_format(music_path)
    _, voice_channels = audio_format(dialogue_path)
    if channels not in (1, 2):
        raise ValueError("Use a mono or stereo music track.")
    if channels == 2:
        voice_channels = min(voice_channels, 2)
    with tempfile.TemporaryDirectory(prefix="music-ducking-") as work:
        work = Path(work)
        raw_output = work / "ducked.raw"
        with (decoded(music_path, work / "music.raw", rate, channels) as music,
              decoded(dialogue_path, work / "dialogue.raw", rate, voice_channels) as dialogue):
            render_pcm(music, dialogue, rate, settings, raw_output, progress,
                       observe_cuts=observe_cuts, clip_baseline=clip_baseline)

        if music_spans is not None:
            silence_outside(raw_output, channels, rate, music_spans)

        progress("Saving the music WAV…", 0.95)
        with tempfile.TemporaryDirectory(dir=output_path.parent, prefix=".ducking-") as staging:
            temporary = Path(staging) / "music.wav"
            write_float_wav(raw_output, temporary, rate, channels)
            commit_output(temporary, output_path, overwrite)

    progress("Export complete.", 1.0)
    return output_path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("music", type=Path)
    parser.add_argument("dialogue", type=Path)
    parser.add_argument("output", type=Path)
    defaults = Settings()
    influence = parser.add_mutually_exclusive_group()
    influence.add_argument("--amount", "--influence", dest="influence",
                           type=float, default=defaults.influence * 100, metavar="PERCENT",
                           help="ducking amount as a percentage of the gap to a deeper dialogue target (0–100)")
    influence.add_argument("--cut-db", type=float, help="legacy strength scale (0–24)")
    parser.add_argument("--music-attenuation-db", "--soften-db", dest="soften_db",
                        type=float, default=defaults.soften_db, metavar="DB",
                        help="baseline attenuation of the music in dB")
    parser.add_argument("--lookahead-ms", "--attack-ms", dest="attack_ms",
                        type=float, default=defaults.attack_ms, metavar="MS",
                        help="lead-in before a dialogue peak, in milliseconds")
    parser.add_argument("--release-ms", type=float, default=defaults.release_ms, metavar="MS",
                        help="recovery time after a dialogue peak, in milliseconds")
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()

    def progress(stage, _fraction):
        nonlocal previous_stage
        if stage != previous_stage:
            print(stage, flush=True)
            previous_stage = stage

    previous_stage = None
    try:
        amount = args.influence / 100 if args.cut_db is None else args.cut_db / 24
        output = render(
            args.music, args.dialogue, args.output,
            Settings(influence=amount, release_ms=args.release_ms, attack_ms=args.attack_ms,
                     soften_db=args.soften_db),
            overwrite=args.overwrite, progress=progress,
        )
    except (OSError, RuntimeError, ValueError) as error:
        parser.exit(1, f"Error: {error}\n")
    print(output)


if __name__ == "__main__":
    main()
