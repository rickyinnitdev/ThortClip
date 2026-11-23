"""Replay the November Swiftplay regression clips without running Valorant.

Usage: python test_swiftplay_replay.py [folder containing the six original clips]
"""
import csv
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path


import threading

from src.clip_player import scan_clip_kills
from src.main import MedalReaApp

# Times refer to confirmed local attacker entries in the saved excerpts.
CASES = {
    "1K_20261123_173756": [],
    "Clip_Round1_20261123_173833": [],
    "Clip_Round2_20261123_174010": [],
    "Clip_Round3_20261123_174210": [],
    "Clip_Round4_20261123_174339": [2.75],
    "Clip_Round5_20261123_174518": [0.125, 5.125],
}


def personal_kills(path):
    return scan_clip_kills(path, threading.Event())


def verify(source, expected, folder):
    times = personal_kills(source)
    assert len(times) == len(expected) and all(abs(a-b) <= 0.5 for a, b in zip(times, expected)), (source, times)
    cfg = json.loads(Path("config.json").read_text())
    cfg["paths"].update(temp_segment_dir=str(folder / "segments"), clips_output_dir=str(folder / "clips"))
    settings = folder / "config.json"
    settings.write_text(json.dumps(cfg))
    app = MedalReaApp(str(settings))
    segments = app.config.temp_segment_dir
    manifest = segments / "times.csv"
    subprocess.run(["ffmpeg", "-v", "error", "-i", str(source.resolve()), "-map", "0:v", "-map", "0:a",
                    "-c", "copy", "-f", "segment", "-segment_time", "2", "-reset_timestamps", "1",
                    "-segment_list", str(manifest), "seg_%06d.ts"], cwd=segments, check=True)
    # Replay finished segment times on a historical wall clock so no sleep is needed.
    origin = time.time() - 120
    app.recorder.start_wall_time = origin
    with manifest.open() as stream:
        for name, _, end in csv.reader(stream):
            os.utime(segments / name, (origin + float(end),) * 2)
    for stamp in times:
        app._on_local_kill(origin + stamp, 1, {})
    app._on_round_end(0, origin + (times[-1] if times else 0))
    app._on_match_ended()
    outputs = list(app.cutter.output_dir.glob("*.mp4"))
    assert len(outputs) == int(bool(expected)), (source, outputs)
    if outputs:
        clip, = outputs
        assert clip.name.startswith(f"{len(expected)}K_Round1_")
        assert len(personal_kills(clip)) == len(expected), clip
        subprocess.run(["ffmpeg", "-v", "error", "-xerror", "-i", str(clip), "-map", "0:v", "-map", "0:a",
                        "-f", "null", "-"], check=True)
    print(f"{source.name}: {len(times)} personal kills, {len(outputs)} automatic clips", flush=True)


if __name__ == "__main__":
    sources = Path(sys.argv[1] if len(sys.argv) > 1 else "clips")
    spectator = sources / "3K_Round11_20261123_224404_277740600.mp4"
    if spectator.is_file():
        with tempfile.TemporaryDirectory() as temporary:
            verify(spectator, [20.625, 21.5], Path(temporary))
    ace = next(sources.glob("Ace_Round7_Ascent_20261123_1825.mp4"), None)
    if ace:
        with tempfile.TemporaryDirectory() as temporary:
            verify(ace, [24.75, 29.5, 44.75, 59.75, 76], Path(temporary))
    for prefix, expected in CASES.items():
        source, = sources.glob(prefix + "*.mp4")
        with tempfile.TemporaryDirectory() as temporary:
            verify(source, expected, Path(temporary))
