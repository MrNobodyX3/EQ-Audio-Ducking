# EQ Audio Ducking

EQ Audio Ducking makes room for dialogue by lowering the music at the frequencies where the dialogue is present. It includes a macOS and Windows desktop app, a live preview with an EQ graph, and a command-line exporter.

## Start the desktop app

- **macOS:** double-click [`start-mac.command`](start-mac.command).
- **Windows:** double-click [`start-win.bat`](start-win.bat).

Choose one music track and one dialogue track with the **Choose…** buttons. The app opens the file chooser in the project's `in` folder when it exists; you can choose files from elsewhere too. Both tracks should start at the same point on your timeline. If dialogue starts later, include that leading silence in the dialogue file so it stays in sync with the music.

## Settings

The sliders apply to the preview and to the exported WAV. Their defaults are 80%, 80 ms, 80 ms, and 0 dB.

- **Ducking Amount (0–100%)** sets how much of the frequency-specific gap to the dialogue target is applied. At 0%, the music stays at its Music Attenuation baseline. At 100%, it follows the full calculated target. Use a smaller value for a subtler dip.
- **Lookahead (0–1,000 ms)** starts the cut gradually before a dialogue peak, helping the music move out of the way before a word or transient arrives. Higher values start the dip earlier. At 0 ms, there is no lead-in.
- **Release (0–1,000 ms)** controls how the cut recovers after dialogue gets quieter. The cut holds briefly (about one-third of this setting) and then fades smoothly toward the baseline. Higher values make the recovery longer; at 0 ms, there is no hold or fade.
- **Music Attenuation (0–24 dB)** lowers the music by a fixed amount across the whole track and sets the baseline for dynamic ducking. At 0 dB, there is no fixed reduction. Raising it makes the music quieter even when dialogue is silent.

Changing a slider stops the current preview and marks the graph for an update. Press **Play** again to hear and see the new settings.

## Preview and EQ graph

- **Original** plays the music with Music Attenuation only. It does not include the dialogue-driven EQ cuts.
- **Ducked** plays the dynamically processed music.
- **Hear dialogue** toggles the dialogue in the preview mix. Turn it off to listen to the music alone.
- **Play** starts or stops playback. Drag the scrubber to compare different moments; the time display shows the current position and track duration.
- The graph follows playback and scrubbing. Its horizontal axis is frequency and its vertical axis is gain reduction in dB. The **dashed** line shows the dialogue target by frequency, the **solid** line shows the cut applied to the music, and the **dotted** line marks the Music Attenuation baseline when it is above 0 dB.

The EQ is frequency matched: each frequency bin in the music responds to dialogue energy in that same frequency range. A dialogue target above the attenuation baseline leaves the music at the baseline; a deeper target adds reduction according to Ducking Amount. The target uses a fixed −56 dBFS per-bin reference and is smoothed near silence. Lookahead eases cuts in ahead of peaks, while Release shapes the return after them.

## Export

Click **Export Music WAV…** and choose where to save the result. The save dialog starts in the project's `out` folder when it exists. The exported WAV contains the processed music **without dialogue**.

The export preserves the music's duration, sample rate, and mono or stereo layout. It uses 32-bit float PCM and applies the same EQ curve to both stereo channels. WAV, AIFF, FLAC, MP3, and M4A inputs are supported through FFmpeg.

## Requirements

The desktop app needs Python 3 with NumPy and Tkinter, plus FFmpeg, FFprobe, and FFplay available on `PATH`. On Windows, install Python 3 so that `py` or `python` works in Command Prompt. Tkinter is included with many standard Python installers.

- FFmpeg and FFprobe read and convert the audio files.
- FFplay provides playback for the preview.
- NumPy performs the frequency analysis and processing.
- Tkinter provides the desktop interface.

## Command line

Use the command-line exporter when you want to process a pair of tracks without opening the desktop app:

```sh
python3 -B duck_music.py "music.wav" "dialogue.wav" "music - Ducked.wav"
```

Run `python3 -B duck_music.py --help` to see all options. The command-line interface supports Ducking Amount, Lookahead, Release, Music Attenuation, and `--overwrite` to allow replacing an existing output file. Terminal exports need NumPy, FFmpeg, and FFprobe; they do not use Tkinter or FFplay.
