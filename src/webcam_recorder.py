"""Lightweight background recorder for webcam overlay segments."""
import os
import subprocess
import time
from pathlib import Path
from typing import List, Optional

from .camera_manager import get_ffmpeg_binary
from .config import Config

BELOW_NORMAL_PRIORITY_CLASS = 0x00004000
CREATE_NO_WINDOW = 0x08000000

class WebcamRecorder:
    """Captures webcam stream into 2-second .ts rolling segments without affecting screen capture."""

    def __init__(self, config: Config, segment_dir: Path):
        self.config = config
        self.segment_dir = Path(segment_dir)
        self.process: Optional[subprocess.Popen] = None
        self.start_wall_time: float = 0.0

    def is_recording(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def start_recording(self, *, resume=False):
        if self.is_recording():
            return
        if not self.config.overlay_enabled or not self.config.overlay_device:
            return

        self.segment_dir.mkdir(parents=True, exist_ok=True)
        self.start_wall_time = time.time()
        existing = sorted(self.segment_dir.glob("cam_*.ts")) if resume else []
        first = int(existing[-1].stem.split("_")[-1]) + 1 if existing else 0
        cam_pattern = str(self.segment_dir / "cam_%06d.ts")
        binary = get_ffmpeg_binary()

        cmd = [
            binary, "-hide_banner", "-loglevel", "error", "-nostats", "-y",
            "-f", "dshow",
            "-rtbufsize", "100M",
            "-video_size", "640x480",
            "-i", f"video={self.config.overlay_device}",
            "-pix_fmt", "yuv420p",
        ]

        if self.config.encoder in ("hevc_nvenc", "h264_nvenc"):
            cmd += [
                "-c:v", "hevc_nvenc",
                "-preset", "p1",
                "-tune", "ull",
                "-b:v", "2M",
                "-g", "60",
            ]
        else:
            cmd += [
                "-c:v", "libx264",
                "-preset", "ultrafast",
                "-tune", "zerolatency",
                "-b:v", "2M",
                "-g", "60",
            ]

        cmd += [
            "-f", "segment",
            "-segment_start_number", str(first),
            "-segment_time", "2",
            "-segment_format", "mpegts",
            "-reset_timestamps", "1",
            cam_pattern,
        ]

        flags = CREATE_NO_WINDOW | BELOW_NORMAL_PRIORITY_CLASS
        print(f"[WebcamRecorder] Starting webcam recording: {self.config.overlay_device}")

        try:
            with (self.segment_dir / "camera-errors.log").open("ab") as errors:
                self.process = subprocess.Popen(
                    cmd,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.DEVNULL,
                    stderr=errors,
                    creationflags=flags,
                )
            time.sleep(0.3)
            if self.process.poll() is not None:
                with (self.segment_dir / "camera-errors.log").open("rb") as errors:
                    errors.seek(max(0, errors.seek(0, 2) - 512))
                    err = errors.read().decode(errors="replace")
                print(f"[WebcamRecorder] Warning: Webcam capture failed ({self.process.returncode}): {err[-200:]}")
                self.process = None
        except Exception as exc:
            print(f"[WebcamRecorder] Could not start webcam capture: {exc}")
            self.process = None

    def get_completed_segments(self) -> List[Path]:
        if not self.segment_dir.exists():
            return []
        files = sorted(self.segment_dir.glob("cam_*.ts"))
        return files[:-1] if self.is_recording() else files

    def stop_recording(self):
        if not self.is_recording():
            self.process = None
            return

        print("[WebcamRecorder] Stopping webcam capture...")
        try:
            if self.process.stdin:
                self.process.stdin.write(b"q")
                self.process.stdin.flush()
            self.process.wait(timeout=2)
        except Exception:
            try:
                self.process.kill()
                self.process.wait()
            except Exception:
                pass
        self.process = None
