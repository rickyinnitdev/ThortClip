"""Reconcile retained recordings with official VAL-MATCH-V1 results."""
import json
from .clip_metadata import metadata_path
import os
import threading
import time
from urllib.request import Request, urlopen
from uuid import UUID


class MatchResults:
    def __init__(self, config, cutter):
        self.key = os.getenv("THORTCLIP_RIOT_API_KEY", os.getenv("MEDALREA_RIOT_API_KEY"))
        self.player = os.getenv("THORTCLIP_PLAYER_PUUID", os.getenv("MEDALREA_PLAYER_PUUID"))
        self.region = os.getenv("THORTCLIP_VAL_REGION", os.getenv("MEDALREA_VAL_REGION", "")).lower()
        self.enabled = bool(self.key and self.player and self.region in {"ap", "br", "eu", "kr", "latam", "na"})
        self.root = config.temp_segment_dir.parent / "pending-matches"
        self.cutter = cutter
        self.before = config.clip_duration_before_sec
        self.after = config.clip_duration_after_sec
        self.wake = threading.Event()
        self.thread = None
        self.status = ""

    def start(self):
        if self.enabled and self.thread is None:
            self.thread = threading.Thread(target=self._worker, daemon=True, name="MatchResults")
            self.thread.start()

    def _worker(self):
        while True:
            self.wake.clear()
            for metadata in sorted(self.root.glob("*/ready.json")):
                try:
                    job = json.loads(metadata.read_text())
                    match = str(UUID(job["match"]))
                    request = Request(f"https://{self.region}.api.riotgames.com/val/match/v1/matches/{match}",
                                      headers={"X-Riot-Token": self.key})
                    with urlopen(request, timeout=12) as response:
                        details = json.load(response)
                    self.export(metadata.parent, job, details)
                    # Remove only the known files from this completed recording.
                    for path in metadata.parent.glob("seg_*.ts"):
                        path.unlink()
                    metadata.unlink()
                    metadata.parent.rmdir()
                    self.status = "Match highlights saved."
                except Exception as exc:
                    self.status = f"Match export pending ({type(exc).__name__})."
            self.wake.wait(120)

    def begin(self, recorder, match):
        session = self.root / f"{UUID(match)}_{time.time_ns()}"
        session.mkdir(parents=True)
        recorder.segment_dir = session

    def finish(self, recorder, match):
        folder = recorder.segment_dir
        if folder.parent != self.root:
            return
        job = {"match": match, "player": self.player, "start": recorder.start_wall_time}
        tmp = folder / "ready.tmp"
        tmp.write_text(json.dumps(job))
        tmp.replace(folder / "ready.json")
        self.wake.set()

    def export(self, folder, job, details):
        info = details["matchInfo"]
        if info["matchId"].lower() != job["match"].lower() or not info.get("isCompleted"):
            raise ValueError("Match results are incomplete")
        if job["player"] not in {p["puuid"] for p in details["players"]}:
            raise ValueError("Local player absent from match")
        epoch = info["gameStartMillis"] / 1000
        events = sorted({(epoch + kill["timeSinceGameStartMillis"] / 1000, rnd["roundNum"] + 1, kill["victim"])
                         for rnd in details["roundResults"] for stats in rnd["playerStats"]
                         for kill in stats["kills"] if kill["killer"] == job["player"]})
        files = sorted(folder.glob("seg_*.ts"))
        if not files:
            raise ValueError("Recording unavailable")
        bounds = []
        start = job["start"]
        for path in files:
            end = path.stat().st_mtime
            bounds.append((start, end, path))
            start = end
        rounds = {}
        for stamp, rnd, victim in events:
            if bounds[0][0] <= stamp <= bounds[-1][1]:
                rounds.setdefault(rnd, []).append((stamp, victim))
        for rnd, kills in rounds.items():
            selected = [p for a, b, p in bounds if b >= kills[0][0] - self.before
                        and a <= kills[-1][0] + self.after]
            prefix = "Ace" if len({victim for _, victim in kills}) >= 5 else f"{len(kills)}K"
            name = f"{prefix}_Round{rnd}_{job['match']}.mp4"
            out_file = self.cutter.output_dir / name
            if not out_file.exists():
                if not self.cutter.cut_from_segments(selected, clip_name=name):
                    raise RuntimeError("Clip export failed")
                try:
                    clip_start = next((a for a, b, p in bounds if p == selected[0]), kills[0][0] - self.before)
                    rel_kills = [round(max(0.0, stamp - clip_start), 2) for stamp, _ in kills]
                    sidecar = {
                        "clip_name": name,
                        "save_type": "auto",
                        "round": rnd,
                        "kill_count": len(kills),
                        "kills": rel_kills,
                        "duration": round(kills[-1][0] - clip_start + self.after, 2),
                    }
                    metadata_path(out_file, create=True).write_text(json.dumps(sidecar, indent=2), encoding="utf-8")
                except Exception:
                    pass
