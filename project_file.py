"""Read and write EQ Audio Ducking project files."""

from dataclasses import asdict
import json
from pathlib import Path

from duck_music import Settings
from timeline_audio import Clip, inspect_asset

PROJECT_VERSION = 1


def save_project(path, clips, settings, *, snap, beats, zoom, offset,
                 mode, channel, hear_dialogue, active):
    path = Path(path).expanduser().resolve()
    data = {
        "format": "eq-audio-ducking-project",
        "version": PROJECT_VERSION,
        "settings": asdict(settings),
        "editor": {
            "snap": bool(snap), "beats": bool(beats), "zoom": float(zoom),
            "offset": float(offset), "mode": mode, "channel": int(channel),
            "hear_dialogue": bool(hear_dialogue), "active": active,
        },
        "clips": [],
    }
    for clip in clips:
        source = clip.asset.path.resolve()
        try:
            stored_path = source.relative_to(path.parent).as_posix()
            relative = True
        except ValueError:
            stored_path = str(source)
            relative = False
        data["clips"].append({
            "key": clip.key, "lane": clip.lane, "path": stored_path,
            "relative": relative, "start": clip.start, "source_in": clip.source_in,
            "source_out": clip.source_out, "gain": clip.gain,
            "fade_in": clip.fade_in, "fade_out": clip.fade_out,
            "in_curve": clip.in_curve, "out_curve": clip.out_curve,
            "points": [list(point) for point in clip.points],
        })

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    try:
        temporary.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
    return path


def load_project(path, progress=lambda _stage, _fraction: None):
    path = Path(path).expanduser().resolve(strict=True)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("format") != "eq-audio-ducking-project":
            raise ValueError("This is not an EQ Audio Ducking project file.")
        if data.get("version") != PROJECT_VERSION:
            raise ValueError(f"Unsupported project version: {data.get('version')}.")
        settings = Settings(**data["settings"])
        settings.validate()
        editor = data["editor"]
        if editor["mode"] not in ("Original", "Ducked"):
            raise ValueError("Invalid preview mode in project.")
        if int(editor["channel"]) not in (-1, 0, 1):
            raise ValueError("Invalid channel selection in project.")
        clip_data = data["clips"]
        if not isinstance(clip_data, list):
            raise ValueError("Project clips must be a list.")
    except (KeyError, TypeError, json.JSONDecodeError) as error:
        raise ValueError(f"Could not read project file: {error}") from error

    assets = {}
    clips = []
    for index, item in enumerate(clip_data):
        try:
            stored_path = Path(item["path"])
            source = (path.parent / stored_path if item["relative"] else stored_path).resolve(strict=True)
            lane = item["lane"]
            if lane not in ("Dialogue", "Music"):
                raise ValueError(f"Invalid track lane: {lane}.")
            asset_key = source, lane == "Music"
            if asset_key not in assets:
                progress(f"Loading {source.name}…", index / max(1, len(clip_data)))
                assets[asset_key] = inspect_asset(source, find_beats=lane == "Music")
            clip = Clip(
                key=int(item["key"]), lane=lane, asset=assets[asset_key],
                start=int(item["start"]), source_in=int(item["source_in"]),
                source_out=int(item["source_out"]), gain=float(item["gain"]),
                fade_in=int(item["fade_in"]), fade_out=int(item["fade_out"]),
                in_curve=item["in_curve"], out_curve=item["out_curve"],
                points=tuple((int(time), float(level)) for time, level in item["points"]),
            )
            if (clip.key < 1 or clip.start < 0 or not 0 <= clip.gain <= 1
                    or not 0 <= clip.source_in < clip.source_out <= clip.asset.duration):
                raise ValueError(f"Invalid timing or level for {source.name}.")
            if clip.in_curve not in ("linear", "smooth", "fast", "slow") or clip.out_curve not in (
                    "linear", "smooth", "fast", "slow"):
                raise ValueError(f"Invalid fade curve for {source.name}.")
            if any(time < 0 or time > clip.asset.duration or not 0 <= level <= 1
                   for time, level in clip.points):
                raise ValueError(f"Invalid volume points for {source.name}.")
            if any(left[0] >= right[0] for left, right in zip(clip.points, clip.points[1:])):
                raise ValueError(f"Unordered volume points for {source.name}.")
            clips.append(clip)
        except (KeyError, TypeError, OSError) as error:
            raise ValueError(f"Could not load clip {index + 1}: {error}") from error

    if len({clip.key for clip in clips}) != len(clips):
        raise ValueError("Project contains duplicate clip IDs.")
    return tuple(clips), settings, editor
