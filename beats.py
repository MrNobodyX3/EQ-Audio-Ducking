"""Estimate music beats from spectral onsets and rhythmic repetition."""

import numpy as np


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
    scale = np.percentile(flux, 95)
    if scale < 0.00003:
        scale = np.percentile(flux, 99)
    if scale < 0.00003:
        return ()
    pulse = np.clip((flux - np.median(flux)) / scale, 0, 3)
    correlation_input = pulse - pulse.mean()
    power = np.dot(correlation_input, correlation_input)
    if power < 1e-9:
        return ()
    periods = np.arange(30, 111)  # approximately 55–200 BPM
    corr = np.array([np.dot(correlation_input[:-lag], correlation_input[lag:]) / power
                     for lag in periods])
    preference = np.exp(-0.5 * (np.log2(periods / 54) / 0.75) ** 2)
    best = np.argmax(corr * preference)
    if corr[best] < 0.14:
        return ()
    # Prefer the faster pulse when the half-period is nearly as strong.
    for candidate in range(best):
        lag = periods[candidate]
        if (abs(lag * 2 - periods[best]) <= 3 and corr[candidate] >= 0.95 * corr[best]
                and corr[candidate] >= 0.35):
            best = candidate
            break
    period = int(periods[best])

    # Find the strongest sequence of onsets near that period (Ellis, 2007).
    # A missed onset can still carry a beat when the surrounding rhythm agrees.
    offsets = np.arange(max(1, round(period * 0.7)), round(period * 1.35) + 1)
    penalty = 9 * np.log(offsets / period) ** 2
    local = np.convolve(pulse, [0.35, 0.7, 1, 0.7, 0.35], mode="same") - 0.22
    scores = np.zeros(len(pulse))
    links = np.full(len(pulse), -1, dtype=int)
    for index in range(len(pulse)):
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
    if len(chain) < 4:
        return ()
    # Frame center, plus a small correction for the rising side of the FFT window.
    return tuple(round((i * step + size / 2 + step / 2) * 1000 / rate)
                 for i in reversed(chain))
