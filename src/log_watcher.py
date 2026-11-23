import os
import re
import time
import threading
from pathlib import Path
from typing import Callable, Optional

ROUND_ENDED_REGEX = re.compile(r"AShooterGameState::OnRoundEnded for round '(\d+)'")
MATCH_STARTED_REGEX = re.compile(r"LogShooterGameState: (?:Replicated NetServerMaxTickRate|Warning: Gameplay started at)")
LOG_TIMESTAMP_REGEX = re.compile(r"\[(\d{4}\.\d{2}\.\d{2}-\d{2}\.\d{2}\.\d{2}:\d{3})\]")

class LogWatcher:
    """Tails Valorant's ShooterGame.log passively to track round boundaries and match state."""

    def __init__(
        self,
        log_path: Path,
        on_round_end: Optional[Callable[[int, float], None]] = None,
        on_match_detected: Optional[Callable[[], None]] = None,
        on_match_ended: Optional[Callable[[], None]] = None,
    ):
        self.log_path = Path(log_path)
        self.on_round_end = on_round_end
        self.on_match_detected = on_match_detected
        
        self.on_match_ended = on_match_ended
        self.match_id = None
        self.current_round = 0
        self.in_match = False
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._file_handle = None

    def start(self):
        self._running = True
        self._thread = threading.Thread(target=self._watch_loop, daemon=True, name="LogWatcherThread")
        self._thread.start()

    def stop(self):
        self._running = False
        if self._file_handle:
            try:
                self._file_handle.close()
            except Exception:
                pass

    def _watch_loop(self):
        print(f"[LogWatcher] Monitoring log: {self.log_path}")
        last_size = 0

        while self._running:
            if not self.log_path.exists():
                time.sleep(2.0)
                continue

            try:
                cur_size = self.log_path.stat().st_size
                # If file rotated or truncated, reset position
                if cur_size < last_size:
                    if self._file_handle:
                        self._file_handle.close()
                    self._file_handle = open(self.log_path, "r", encoding="utf-8", errors="ignore")
                    last_size = 0

                if self._file_handle is None:
                    self._file_handle = open(self.log_path, "r", encoding="utf-8", errors="ignore")
                    # Start at the end of the file so we don't replay old matches
                    self._file_handle.seek(0, os.SEEK_END)
                    last_size = cur_size

                line = self._file_handle.readline()
                if line:
                    last_size = self._file_handle.tell()
                    self._process_line(line.strip())
                else:
                    time.sleep(0.2)
            except Exception as e:
                time.sleep(1.0)

    def _process_line(self, line: str):
        if not line:
            return

        match = re.search(r"\[Match Load Times\] MatchId:\s*([0-9a-fA-F-]{36})", line)
        if match and self.match_id != match.group(1):
            self.match_id = match.group(1)
            self.in_match = True
            if self.on_match_detected:
                self.on_match_detected()
        if self.in_match and re.search(r"(?:LogLoad: LoadMap:|LogTravelManager: Beginning travel to) .*?/Menu(?:[./?\s]|$)|LogExit: Exiting", line, re.I):
            self.in_match = False
            self.current_round = 0
            if self.on_match_ended:
                self.on_match_ended()
            self.match_id = None
            return

        # Check for match / round start
        if MATCH_STARTED_REGEX.search(line):
            if not self.in_match:
                self.in_match = True
                print("[LogWatcher] Match / round gameplay start detected!")
                if self.on_match_detected:
                    self.on_match_detected()

        # Check for round end
        m_round = ROUND_ENDED_REGEX.search(line)
        if m_round:
            round_num = int(m_round.group(1))
            self.current_round = round_num + 2
            if not self.in_match:
                self.in_match = True
                if self.on_match_detected:
                    self.on_match_detected()

            wall_time = time.time()
            print(f"[LogWatcher] Round {round_num} ended. Active round is now {self.current_round}.")
            if self.on_round_end:
                self.on_round_end(round_num, wall_time)
