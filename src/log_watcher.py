import subprocess
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
        identity = None
        initial = True
        while self._running:
            try:
                stat = self.log_path.stat()
                file_id = (stat.st_dev, stat.st_ino)
                if self._file_handle is None or identity != file_id or stat.st_size < self._file_handle.tell():
                    if self._file_handle:
                        self._file_handle.close()
                    self._file_handle = self.log_path.open(encoding="utf-8", errors="ignore")
                    identity = file_id
                    if initial:
                        initial = False
                        self._restore_session(stat.st_size)
                    elif self.in_match:
                        self.in_match = False
                        self.match_id = None
                        self.current_round = 0
                        if self.on_match_ended:
                            self.on_match_ended()

                while self._running:
                    line = self._file_handle.readline()
                    if not line:
                        break
                    self._process_line(line.strip())
                time.sleep(0.2)
            except FileNotFoundError:
                initial = False
                time.sleep(1)
            except Exception as error:
                if self._running:
                    print(f"[LogWatcher] {error}")
                time.sleep(1)

    def _restore_session(self, initial_size):
        # An abandoned log can end mid-round after a crash. Only restore it
        # when the actual game process is present, including when minimized.
        try:
            result = subprocess.run(
                ["tasklist.exe", "/FI", "IMAGENAME eq VALORANT-Win64-Shipping.exe", "/FO", "CSV", "/NH"],
                capture_output=True, timeout=5, creationflags=subprocess.CREATE_NO_WINDOW,
            )
            running = b'"valorant-win64-shipping.exe"' in result.stdout.lower()
        except (OSError, subprocess.TimeoutExpired):
            running = False
        if not running:
            self._file_handle.seek(initial_size)
            return
        # Reconstruct state without dispatching historical clips or round ends.
        history = LogWatcher(self.log_path)
        for line in self._file_handle:
            history._process_line(line)
        self.match_id = history.match_id
        self.in_match = history.in_match
        self.current_round = history.current_round
        if self.in_match and self.on_match_detected:
            self.on_match_detected()

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
