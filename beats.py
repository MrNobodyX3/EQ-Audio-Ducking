"""Estimate music beats from spectral onsets and rhythmic repetition."""

import numpy as np


def local_periods(pulse):
    """Estimate beat spacing in overlapping windows of the onset envelope."""
    periods = np.arange(27, 135)  # approximately 45–220 BPM at 100 frames/s
    centers = np.arange(0, len(pulse), 100)
    correlations = np.zeros((len(centers), len(periods)))
    for index, center in enumerate(centers):
        left, right = max(0, center - 300), min(len(pulse), center + 301)
        positions = np.arange(left, right) - center
        window = np.exp(-0.5 * (positions / 125) ** 2)
        block = pulse[left:right]
        weighted = (block - np.average(block, weights=window)) * window
        power = np.dot(weighted, weighted)
        if power > 1e-9:
            correlation = np.correlate(weighted, weighted, mode="full")
            correlations[index] = correlation[len(block) - 1 + periods] / power

    if correlations.max() < 0.24:
        return None
    # Follow local evidence while discouraging isolated jumps to half/double time.
    octaves = np.log2(periods / 54)
    transition = 0.8 * (octaves[:, None] - octaves) ** 2
    emissions = np.maximum(correlations, 0) - 0.04 * octaves ** 2
    links = np.zeros_like(correlations, dtype=int)
    choices = np.arange(len(periods))
    score = emissions[0]
    for index in range(1, len(centers)):
        options = score[:, None] - transition
        links[index] = np.argmax(options, axis=0)
        score = emissions[index] + options[links[index], choices]
    path = np.empty(len(centers), dtype=int)
    path[-1] = int(np.argmax(score))
    for index in range(len(centers) - 1, 0, -1):
        path[index - 1] = links[index, path[index]]

    strength = correlations[np.arange(len(centers)), path]
    supported = strength >= 0.24
    neighbors = np.r_[False, supported[:-1]] | np.r_[supported[1:], False]
    trusted = (supported & neighbors) | (strength >= 0.39)
    if not trusted.any():
        return None
    return np.rint(np.interp(np.arange(len(pulse)), centers[trusted],
                             periods[path[trusted]])).astype(int)


def extend_grid(tracked, duration_ms):
    """Keep tracked beats and project each edge using its nearby tempo."""
    def nearby_period(beats):
        # Multiple-beat intervals average out the tracker's 10 ms frame rounding.
        span = min(8, len(beats) // 2)
        return float(np.median((beats[span:] - beats[:-span]) / span))

    first, last = tracked[0], tracked[-1]
    intro_period = nearby_period(tracked[:16])
    outro_period = nearby_period(tracked[-16:])
    intro_count = int(first // intro_period)
    outro_count = int((duration_ms - last) // outro_period)
    intro = (first - beat * intro_period for beat in range(intro_count, 0, -1))
    outro = (last + beat * outro_period for beat in range(1, outro_count + 1))
    return tuple(time for time in map(round, (*intro, *tracked, *outro))
                 if 0 <= time < round(duration_ms))


def detect_beats(samples, rate):
    # Spectral flux at 100 frames/s. Process in batches for long source files.
    step = rate // 100
    size = 256
    if len(samples) < 2 * rate:
        return ()
    windows = np.lib.stride_tricks.sliding_window_view(samples, size, axis=0)[::step]
    window = np.hanning(size).astype(np.float32)
    flux = np.empty(len(windows))
    previous = np.zeros((samples.shape[1], size // 2 + 1))
    weights = np.where(np.fft.rfftfreq(size, 1 / rate) <= 250, 3.0, 1.0)
    for left in range(0, len(windows), 1024):
        block = windows[left:left + 1024]
        spectrum = np.log1p(4 * np.abs(np.fft.rfft(block * window, axis=-1)))
        changes = np.diff(spectrum, axis=0, prepend=previous[None, :, :])
        flux[left:left + len(block)] = (np.maximum(changes, 0) * weights).mean(axis=(1, 2))
        previous = spectrum[-1]
    flux = np.nan_to_num(flux, nan=0, posinf=0)
    # Suppress isolated transients and very low levels before estimating tempo.
    flux[:2] = 0
    if flux.max() < 0.00003:
        return ()
    # A quiet section can still supply beat evidence next to a much louder one.
    centers = np.arange(0, len(flux), 100)
    scales = [np.percentile(flux[max(0, center - 200):center + 201], 98) for center in centers]
    scale = np.interp(np.arange(len(flux)), centers, scales).clip(min=0.00003)
    pulse = np.clip((flux - np.median(flux)) / scale, 0, 3)
    tempo = local_periods(pulse)
    if tempo is None:
        return ()

    # Find a sequence of onsets close to the period at each point in the song.
    transitions = {}
    for period in np.unique(tempo):
        offsets = np.arange(round(period * 0.68), round(period * 1.4) + 1)
        transitions[period] = offsets, 9 * np.log(offsets / period) ** 2
    local = np.convolve(pulse, [0.35, 0.7, 1, 0.7, 0.35], mode="same") - 0.12
    scores = np.zeros(len(pulse))
    links = np.full(len(pulse), -1, dtype=int)
    for index, period in enumerate(tempo):
        offsets, penalty = transitions[period]
        available = np.searchsorted(offsets, index, side="right")
        if available:
            candidates = scores[index - offsets[:available]] - penalty[:available]
            best = int(np.argmax(candidates))
            if candidates[best] > 0:
                links[index] = index - offsets[best]
                scores[index] = candidates[best]
        scores[index] += local[index]

    index = int(np.argmax(scores))
    chain = []
    while index >= 0:
        chain.append(index)
        index = links[index]
    if len(chain) < 4 or np.count_nonzero(local[chain] > 0.2) < 4:
        return ()
    # Frame center, plus a small correction for the rising side of the FFT window.
    tracked = (np.array(chain[::-1]) * step + size / 2 + step / 2) * (1000 / rate)
    return extend_grid(tracked, len(samples) * (1000 / rate))
