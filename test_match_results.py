"""Replay official match-result payloads against a short real FFmpeg recording."""
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from src.cutter import VideoCutter
from src.log_watcher import LogWatcher
from src.match_results import MatchResults


class MatchResultsTest(unittest.TestCase):
    def test_local_kills_only_and_idempotent_export(self):
        with tempfile.TemporaryDirectory() as root:
            folder = Path(root)
            cutter = VideoCutter(folder / "clips")
            config = SimpleNamespace(temp_segment_dir=folder, clip_duration_before_sec=1,
                                     clip_duration_after_sec=1)
            results = MatchResults(config, cutter)
            start = 1000
            subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=size=64x64:rate=10",
                            "-f", "lavfi", "-i", "anullsrc", "-t", "20", "-c:v", "libx264",
                            "-g", "20", "-c:a", "aac", "-f", "segment", "-segment_time", "2",
                            "-reset_timestamps", "1", str(folder / "seg_%06d.ts")], check=True)
            for i, path in enumerate(sorted(folder.glob("*.ts"))):
                os.utime(path, (start + 2*i + 2,) * 2)
            match = str(uuid4())
            job = {"match": match, "start": start, "player": "local"}
            kills = [{"killer": "local", "victim": str(i), "timeSinceGameStartMillis": 1000*i}
                     for i in (1, 3, 5, 7, 9, 11, 13)]
            kills.append({"killer": "ally", "victim": "other", "timeSinceGameStartMillis": 17000})
            payload = {"matchInfo": {"matchId": match, "gameStartMillis": start*1000, "isCompleted": True},
                       "players": [{"puuid": "local"}],
                       "roundResults": [{"roundNum": 0, "playerStats": [{"kills": kills}]}]}
            results.export(folder, job, payload)
            outputs = sorted(cutter.output_dir.glob("*.mp4"))
            self.assertEqual(len(outputs), 1)
            self.assertTrue(outputs[0].name.startswith("Ace_Round1_"))
            times = [p.stat().st_mtime_ns for p in outputs]
            results.export(folder, job, payload)
            self.assertEqual(times, [p.stat().st_mtime_ns for p in outputs])
            for path in outputs:
                subprocess.run(["ffmpeg", "-v", "error", "-xerror", "-i", str(path),
                                "-map", "0:v", "-map", "0:a", "-f", "null", "-"], check=True)

    def test_menu_transition(self):
        events = []
        log = LogWatcher(Path("unused"), on_match_detected=lambda: events.append(log.match_id),
                         on_match_ended=lambda: events.append("menu"))
        match = str(uuid4())
        log._process_line(f"LogLoadTimeMetrics: Display: [Match Load Times] MatchId: {match}, Loading")
        log._process_line("LogTravelManager: Beginning travel to /Game/Maps/Menu/MainMenuV2")
        self.assertEqual(events, [match, "menu"])
        self.assertFalse(log.in_match)


if __name__ == "__main__":
    unittest.main()
