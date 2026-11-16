# EQ Audio Ducking v1.0.0

Arrange dialogue and music on a waveform timeline, shape clip levels, hear how dynamic EQ ducking changes the mix, and export finished WAV stems.

## Features

- **Multitrack timeline:** Arrange multiple dialogue and music clips, preserve gaps, and overlap clips within either lane.
- **Waveform editing:** Move clips, trim either edge, scrub the playhead, and zoom or pan with a mouse or trackpad.
- **Beat-aware snapping:** Show beat guides and snap clips to detected beats, including tracks whose tempo changes over time.
- **Per-clip volume automation:** Adjust clip volume, add and move control points, shape sections, and add fades with selectable curve presets.
- **Dynamic EQ ducking:** Match cuts to dialogue frequencies with adjustable ducking amount, lookahead, and release.
- **Stereo-aware processing:** Duck left and right channels independently, with Both, Left, and Right audition options.
- **EQ preview:** Compare original and ducked audio, audition dialogue, follow attenuation in the live EQ view, and adjust playback-only volume.
- **Project files:** Save and reopen clip arrangements, edits, EQ and snapping settings, and audition preferences.
- **WAV exports:** Export one music mix with gaps preserved, and optionally export the dialogue stem. Output uses 32-bit float PCM at the highest sample rate among the project clips.

## Download and requirements

Download `EQ-Audio-Ducking-v1.0.0.zip` below and extract it. The archive includes the app source, README, license, and Mac and Windows launchers. It is a source package, not a standalone installer.

Install Python 3.10 or later and NumPy (`python3 -m pip install -r requirements.txt` on macOS, or `py -3 -m pip install -r requirements.txt` on Windows). Install FFmpeg so `ffmpeg`, `ffprobe`, and `ffplay` are available on `PATH`, then launch `start-mac.command` or `start-win.bat`.

## License

Released under the MIT License. FFmpeg is an external dependency and is not included in the download.
