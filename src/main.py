import os
import sys
import time
import signal
from collections import deque
from pathlib import Path
from typing import Optional, Dict, Any, List

from .clip_metadata import metadata_path
from .camera_manager import CameraPreview
from .config import Config
from .recorder import ScreenRecorder
from .log_watcher import LogWatcher
from .cutter import VideoCutter
from .kill_sources.hotkey_source import HotkeyKillSource

import threading
from .match_results import MatchResults
from .launcher_job import contain_launcher

class MedalReaApp:
    def __init__(self, config_path: str = "config.json"):
        self.config = Config(config_path)
        self.recorder = ScreenRecorder(self.config)
        self.cutter = VideoCutter(self.config.clips_output_dir)
        
        self.results = MatchResults(self.config, self.cutter)
        self.result_match = None

        # Kill source slots
        self.hotkey_source: Optional[HotkeyKillSource] = None
        if self.config.hotkey_enabled:
            self.hotkey_source = HotkeyKillSource(self.config.hotkey_key)

        self.screen_detector = None
        self._kill_times = deque(maxlen=4096)
        self._kill_lock = threading.Lock()
        self._round_kills = []
        self._round_number = 1

        # Log watcher
        self.log_watcher = LogWatcher(
            log_path=self.config.valorant_log_path,
            on_match_ended=self._on_match_ended,
            on_round_end=self._on_round_end,
            on_match_detected=self._on_match_detected,
        )

        self.camera_preview = None
        self._session_lock = threading.Lock()
        self._running = False

    def start_services(self):
        self._running = True
        print("=" * 65)
        print("       Thot Clip Lightweight Valorant Auto-Clipper")
        print("=" * 65)
        print(f" Video Encoder:       {self.config.encoder} (Hardware accelerated)")
        print(f" Capture Mode:        2-second .ts rolling segments")
        print(f" Resolution & FPS:    {self.config.resolution} @ {self.config.fps}fps")
        print(f" Buffer Retention:    {self.config.buffer_duration_sec}s rolling window")
        print(f" Standby Mode:        {'Idle outside matches (zero load in menus)' if self.config.idle_outside_matches else 'Always recording'}")
        print(f" Screen Detector:     {'Enabled (Personal kill feed @ ' + str(self.config.detector_poll_fps) + ' FPS)' if self.config.detector_enabled else 'Disabled'}")
        print(f" Manual Hotkey:       [{self.config.hotkey_key}]")
        print(f" Output Clips Dir:    {self.config.clips_output_dir}")
        print(f" Valorant Log Path:   {self.config.valorant_log_path}")
        print("=" * 65)

        # 1. Start passive log watcher
        self.results.start()
        self.log_watcher.start()

        # 2. Start kill sources
        if self.hotkey_source:
            self.hotkey_source.start(on_trigger=self._on_clip_triggered)
        if not self.config.idle_outside_matches:
            self._on_match_detected()
        else:
            print("\n[Standby] Waiting for Valorant match to start before recording (0% CPU/GPU load)...")

    def start(self):
        self.start_services()
        print("Press Ctrl+C in this console to stop.\n")

        try:
            while self._running:
                time.sleep(0.5)
        except KeyboardInterrupt:
            self.stop()

    def start_camera_preview(self, device):
        if not self._session_lock.acquire(blocking=False):
            return None
        try:
            if self.recorder.is_recording():
                return None
            self.stop_camera_preview()
            self.camera_preview = CameraPreview(device)
            return self.camera_preview
        finally:
            self._session_lock.release()

    def stop_camera_preview(self):
        preview, self.camera_preview = self.camera_preview, None
        if preview:
            preview.stop()

    def _on_match_detected(self):
        with self._session_lock:
            if (self.results.enabled and self.log_watcher.match_id
                    and self.result_match != self.log_watcher.match_id and self.recorder.is_recording()):
                if self.screen_detector:
                    self.screen_detector.stop()
                self.recorder.stop_recording()
                self._flush_round()
                self._queue_results()
            if self._running and not self.recorder.is_recording():
                with self._kill_lock:
                    self._kill_times.clear()
                    self._round_kills.clear()
                    self.recorder.keep_from = None
                self.result_match = self.log_watcher.match_id if self.results.enabled else None
                self.recorder.retain_match = bool(self.result_match)
                self.recorder.segment_dir = self.config.temp_segment_dir
                if self.result_match:
                    self.results.begin(self.recorder, self.result_match)
                self.stop_camera_preview()
                self.recorder.start_recording()
                if not self.result_match and self.config.detector_enabled and self.recorder.is_recording():
                    if self.screen_detector is None:
                        from .kill_sources.screen_detector import ScreenDetectionKillSource
                        self.screen_detector = ScreenDetectionKillSource(self.config)
                    self.screen_detector.start(on_trigger=self._on_local_kill)

    def _on_match_ended(self):
        if not self.config.idle_outside_matches and not self.result_match:
            self._flush_round()
        if self.config.idle_outside_matches or self.result_match:
            with self._session_lock:
                if self.screen_detector:
                    self.screen_detector.stop()
                self.recorder.stop_recording()
                self._flush_round()
                self._queue_results()

    def _queue_results(self):
        if self.result_match:
            self.results.finish(self.recorder, self.result_match)
            self.result_match = None

    def _on_local_kill(self, timestamp, kill_count, meta):
        with self._kill_lock:
            self._kill_times.append(timestamp)
            if not self._round_kills:
                self._round_number = self.log_watcher.current_round or 1
                self.recorder.keep_from = timestamp - self.config.clip_duration_before_sec - 2
            self._round_kills.append(timestamp)

    def _flush_round(self, number=None, ended=None):
        with self._kill_lock:
            if not self._round_kills:
                return
            kills = self._round_kills
            self._round_kills = []
            number = number or self._round_number
        try:
            self._on_clip_triggered(kills[-1], len(kills), {
                "source": "personal_round", "round": number, "first": kills[0],
                "ending": ended if self.config.auto_clip_round else None,
                "kills": list(kills),
            })
        finally:
            with self._kill_lock:
                if not self._round_kills:
                    self.recorder.keep_from = None

    def _on_round_end(self, round_num: int, wall_time: float):
        # The game can log the round win before its final kill-feed row reaches
        # the screen. Give the detector time to see and confirm that row.
        if self.screen_detector and self.recorder.is_recording():
            grace = 0.25 + 3 / max(1, self.config.detector_poll_fps)
            time.sleep(max(0, wall_time + grace - time.time()))
        self._flush_round(round_num + 1, wall_time)
        # Recover at a round boundary without clearing the surviving buffer.
        with self._session_lock:
            if self._running and self.log_watcher.in_match and self.recorder.process is not None:
                files = self.recorder.get_completed_segments()
                latest = files[-1].stat().st_mtime if files else self.recorder.start_wall_time
                if not self.recorder.is_recording() or time.time() - latest > 15:
                    self.recorder.stop_recording()
                    self.recorder.start_recording(resume=True)

    def _on_clip_triggered(self, trigger_wall_time: float, kill_count: Optional[int], meta: Dict[str, Any]):
        if not self.recorder.is_recording() and meta.get("source") != "personal_round":
            if meta.get("source") == "hotkey":
                self._on_match_detected()
            return

        source = meta.get("source", "unknown")
        print(f"\n[App] Clip trigger from [{source}]! Preparing clip...")

        session = self.recorder.start_wall_time
        active_round = meta.get("round") or self.log_watcher.current_round or None
        wanted_start = meta.get("first", trigger_wall_time) - self.config.clip_duration_before_sec
        wanted_end = (meta.get("ending") or trigger_wall_time) + self.config.clip_duration_after_sec
        deadline = time.monotonic() + max(0, wanted_end - time.time()) + 4
        # A segment is available only after FFmpeg closes it. A fixed sleep can
        # expire before the segment containing the kill is available to the cutter.
        while True:
            if self.recorder.start_wall_time != session:
                return None
            recording = self.recorder.is_recording()
            files = self.recorder.get_completed_segments()
            bounds = [(path, path.stat().st_mtime) for path in files if path.exists()]
            if not recording or (bounds and bounds[-1][1] >= wanted_end):
                break
            if time.monotonic() >= deadline:
                break
            time.sleep(0.15)

        clip_segments = []
        start = session
        previous_end = None
        for path, segment_end in bounds:
            segment_start = previous_end if previous_end is not None else max(session, segment_end - 2)
            previous_end = segment_end
            if segment_end < wanted_start or segment_start > wanted_end:
                continue
            if not clip_segments:
                start = segment_start
            clip_segments.append(path)
            end = segment_end
        if not clip_segments or not start <= trigger_wall_time <= end:
            return None
        if source != "personal_round":
            with self._kill_lock:
                kill_count = sum(start <= t <= end for t in self._kill_times) or None

        recorded_kills = meta.get("kills")
        if not recorded_kills:
            with self._kill_lock:
                recorded_kills = [t for t in self._kill_times if start <= t <= end]
        relative_kills = [round(max(0.0, t - start), 2) for t in (recorded_kills or [])]

        cam_clip_segments = []
        cam_offset = 0.0
        if self.config.overlay_enabled:
            cam_files = self.recorder.get_completed_cam_segments()
            cam_bounds = [(p, p.stat().st_mtime) for p in cam_files if p.exists()]
            previous = None
            for p, seg_end in cam_bounds:
                seg_start = previous if previous is not None else seg_end - 2
                previous = seg_end
                if seg_end > start and seg_start < end:
                    if not cam_clip_segments:
                        cam_offset = seg_start - start
                    cam_clip_segments.append(p)

        # Slice clip instantly using concat demuxer (or with webcam overlay if enabled)
        saved_path = self.cutter.cut_from_segments(
            segments=clip_segments,
            round_number=active_round,
            kill_count=kill_count,
            cam_segments=cam_clip_segments,
            cam_offset=cam_offset,
            overlay_config=self.config.get_overlay_dict(),
            screen_resolution=self.config.resolution,
            bitrate=self.config.bitrate,
            encoder=self.config.encoder,
        )
        if saved_path:
            try:
                import json
                sidecar_data = {
                    "clip_name": saved_path.name,
                    "duration": round(max(1.0, end - start), 2),
                    "kill_count": len(relative_kills) if relative_kills else kill_count,
                    "kills": relative_kills,
                    "round": active_round,
                    "created_at": time.time(),
                }
                metadata_path(saved_path, create=True).write_text(json.dumps(sidecar_data, indent=2), encoding="utf-8")
            except Exception as exc:
                print(f"[App] Warning: Failed to write clip metadata: {exc}")
        return saved_path

    def manual_start_recording(self):
        self._on_match_detected()

    def manual_stop_recording(self):
        with self._session_lock:
            if self.screen_detector:
                self.screen_detector.stop()
            self.recorder.stop_recording()
            self._flush_round()
            self._queue_results()

    def manual_save_clip(self, source: str = "manual"):
        return self._on_clip_triggered(time.time(), None, {"source": source})

    def stop(self):
        print("\n[App] Shutting down...")
        self._running = False
        if self.hotkey_source:
            self.hotkey_source.stop()
        if self.screen_detector:
            self.screen_detector.stop()
        self.log_watcher.stop()
        self.recorder.stop_recording()
        self._flush_round()
        self._queue_results()
        print("[App] Shutdown complete. Goodbye!")

def main():
    contain_launcher()
    app = MedalReaApp()
    app.start()

if __name__ == "__main__":
    main()
