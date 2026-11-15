# EQ Audio Ducking

EQ Audio Ducking makes room for dialogue by lowering the music at the frequencies where the dialogue is present. Arrange audio in the desktop timeline, compare the original and ducked mixes, and export separate WAV files for music and dialogue.

## Start the desktop app

- **macOS:** double-click [`start-mac.command`](start-mac.command).
- **Windows:** double-click [`start-win.bat`](start-win.bat).

The desktop app requires Python 3.10 or later with NumPy and Tkinter. FFmpeg, FFprobe, and FFplay must be on `PATH`. On Windows, the launcher looks for `py` or `python` in Command Prompt.

The timeline lives in memory. Export your stems before closing the app.

## Build your timeline

- Click **+ Add Dialogue…** or **+ Add Music…** on the corresponding track card and select one or more audio files. New files are appended to the end of that lane.
- Drag a waveform to move the clip. Drag its edges to trim it or recover trimmed audio. Overlapping clips in the same track are summed.
- For exact timing, select a clip and enter **Start**, **Source in**, or **Source out** in seconds or `m:ss.mmm`. Click **Apply Timing** or press Enter. Use **Remove**, Delete, or Backspace to remove the selected clip.
- **Snap** aligns clips with the ruler grid, playhead, and other clip edges. **Beat Snap** shows gold beat markers on music and snaps clip edges to those markers. Beat detection runs on import. Music with a weak or irregular rhythm may show few markers.
- Click or drag the ruler to move the playhead. If the preview was playing, it resumes from the new position.
- Pan with two fingers, **Shift + scroll**, or a middle-button drag. Pinch over the timeline on a Mac trackpad to zoom around the pointer. You can also use **⌘ + scroll** on macOS, **Ctrl + scroll** on Windows, or the **−**, **+**, and **Fit** buttons.

## Edit volume and fades

Drag the volume line up or down to set the clip level. The bottom mutes the clip. Add points to shape a section of music or dialogue. The line runs straight between points. Dragging a segment moves its two endpoints, and neighboring ramps follow any shared point. The clip edges serve as endpoints before the first point and after the last.

| Volume action | macOS | Windows |
| --- | --- | --- |
| Add a point on the line | Option-click or double-click | Alt-click or double-click |
| Drag with finer control | Hold Shift | Hold Shift |
| Snap a volume drag to 1 dB | Hold ⌘ | Hold Ctrl |
| Snap a volume drag to 0.1 dB | Hold ⌘ + Shift | Hold Ctrl + Shift |
| Adjust the whole clip when it has points | Option + Shift-drag | Alt + Shift-drag |

Drag a diamond to change its time or level. Right-click a diamond to remove it. From the volume line’s right-click menu, **Reset Section to Clip Level** returns both endpoints to the clip’s base level. **Reset Clip Volume** restores the original level and clears all points.

Drag the gold handle at the left or right end of a clip inward to set a fade. Right-click a handle to pick a curve by its thumbnail. Zoom in if a short clip is too narrow to show its handles. Volume resets preserve the fades. Use **Undo** and **Redo** to revisit clip edits.

## Set the dynamic EQ

| Setting | Default | Effect |
| --- | --- | --- |
| Ducking Amount | 80% | At 0%, music plays at its edited clip levels. At 100%, it follows the full dialogue target. |
| Lookahead | 80 ms | Starts the cut before an upcoming dialogue peak. |
| Release | 80 ms | Controls how long the cut holds and fades back after dialogue gets quieter. |

Press **Play** again after changing a setting or editing a clip to refresh the preview.

## Preview and EQ graph

- **Original** plays the music with its clip levels, fades, and volume points.
- **Ducked** plays the dynamically processed music.
- **Dialogue** toggles the dialogue in the preview mix. Turn it off to listen to the music alone.
- **Play** starts or pauses at the playhead. Press Space when the timeline has keyboard focus.
- The graph follows playback and scrubbing. The **dashed** line shows the dialogue target by frequency. The **green** line shows the total attenuation for the music clip at the playhead, including its volume, fades, and volume points. **Ducked** also shows the additional EQ cut. Overlapping clips can have separate green curves.

The EQ is frequency matched. Each frequency bin in the music responds to dialogue energy in that same range after dialogue levels, fades, and volume points are applied. The cut uses the remaining gap between the clip’s current attenuation and the dialogue target. The target uses a fixed −56 dBFS per-bin reference and is smoothed near silence. Lookahead eases cuts in ahead of peaks, while Release shapes the return after them.

## Export

- **Export Music WAV…** writes one WAV containing all arranged music with dynamic EQ applied. It contains no dialogue.
- **Export Dialogue WAV…** writes one WAV containing all arranged dialogue with its levels, fades, and volume points applied.

Both exports run from timeline zero to the end of the last clip, preserving leading, internal, and trailing gaps in each lane. They use 32-bit float PCM at the highest sample rate among the clips on the timeline. Each stem is mono if all its clips are mono, or stereo if the lane includes stereo or multichannel audio. WAV, AIFF, FLAC, MP3, M4A and other formats supported by your FFmpeg build can be imported.

## Command line

Use the command-line exporter when you want to process a pair of tracks without opening the desktop app:

```sh
python3 -B duck_music.py "music.wav" "dialogue.wav" "music - Ducked.wav"
```

Run `python3 -B duck_music.py --help` to see all options. The command-line interface supports Ducking Amount, Lookahead, Release, Music Attenuation, and `--overwrite` to allow replacing an existing output file. Terminal exports need NumPy, FFmpeg, and FFprobe; they do not use Tkinter or FFplay.

## License and credits

This project is released under the [MIT License](LICENSE). The beat detector is implemented in this project using NumPy, drawing on Daniel P. W. Ellis’s description of [beat tracking with dynamic programming](https://www.ee.columbia.edu/~dpwe/pubs/Ellis07-beattrack.pdf). FFmpeg is an external executable and is not bundled with this project.
