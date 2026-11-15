# EQ Audio Ducking

Double-click **EQ Audio Ducking.command** on macOS. Choose the music and dialogue files from the `in` folder or anywhere on your Mac.

1. Set **Ducking Amount**, **Lookahead**, **Release**, and **Music Attenuation**, then press **Play**. The defaults are 80%, 80 ms, 80 ms, and 0 dB, respectively. Ducking Amount controls how far the cut moves from the music baseline toward the dialogue target, from 0% to 100%. Music Attenuation sets the baseline reduction from 0 to 24 dB. Lookahead and Release both range from 0 to 1,000 ms. Lookahead controls the lead-in before a dialogue peak. Release controls the return to baseline after a hold of about one-third of the release time. At 0 ms, Lookahead disables the lead-in and Release disables the hold and fade.
2. Use **Original** and **Ducked** to compare the music at the same point and baseline level. **Original** applies Music Attenuation alone. Turn **Hear dialogue** on to hear both tracks together, or off to hear the music alone. The dashed dialogue target always shows the full-strength curve. The dotted line is the music baseline, and the solid line is the cut applied to the music. The graph follows playback and scrubbing. Press **Play** after changing a setting to update the preview.
3. Click **Export Music WAV**. The save dialog starts in `out`. The WAV contains the processed music alone and can replace the original music clip in your edit.

Both inputs should begin at the same point on the timeline. Export leading silence from the edit if dialogue starts later. The WAV preserves the music's duration, sample rate, and mono or stereo layout. It uses 32-bit float PCM and applies the same EQ curve to both stereo channels. WAV, AIFF, FLAC, MP3, and M4A inputs are supported through FFmpeg.

Each frequency bin in the music follows the dialogue level at that same frequency. The full cut is derived in dB against a fixed −56 dBFS reference, with a smooth response near silence. A dialogue target above the music baseline leaves the music at that baseline. For a deeper target, Ducking Amount scales the gap. For example, with 5 dB of Music Attenuation and a target of −10 dB, a Ducking Amount of 75% produces a total cut of −8.75 dB. The extra cut eases in ahead of a dialogue peak, then holds and fades back to the baseline.

## Terminal

```sh
python3 -B duck_music.py "music.wav" "dialogue.wav" "music - Ducked.wav"
```

Run `python3 -B duck_music.py --help` for Ducking Amount, Lookahead, Release, Music Attenuation, and the overwrite option.

The desktop app requires Python 3 with NumPy and Tkinter, plus FFmpeg, FFprobe, and FFplay on the PATH. Terminal exports use NumPy, FFmpeg, and FFprobe.
