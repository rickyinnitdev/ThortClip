"""Asynchronous thumbnail generation and caching for clip cards."""
import hashlib
import os
import queue
import subprocess
import threading
from pathlib import Path
from typing import Optional, Dict

from PIL import Image, ImageTk

from .camera_manager import get_ffmpeg_binary

class ThumbnailManager:
    def __init__(self, cache_dir: Optional[Path] = None):
        if cache_dir is None:
            cache_dir = Path("temp/thumbnails").resolve()
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._memory_cache: Dict[str, ImageTk.PhotoImage] = {}
        self._work_queue = queue.Queue()
        self._ready_queue = queue.Queue()
        self._worker_thread = threading.Thread(target=self._worker, daemon=True, name="ThumbnailWorker")
        self._worker_thread.start()

    def _get_cache_path(self, video_path: Path) -> Path:
        mtime = int(video_path.stat().st_mtime) if video_path.exists() else 0
        key = f"{video_path.resolve()}_{mtime}"
        h = hashlib.md5(key.encode()).hexdigest()
        return self.cache_dir / f"{h}.jpg"

    def get_thumbnail(self, video_path: Path, width: int = 260, height: int = 150) -> Optional[ImageTk.PhotoImage]:
        """Returns cached PhotoImage if ready, else queues background extraction and returns None."""
        cache_file = self._get_cache_path(video_path)
        cache_key = str(cache_file)

        if cache_key in self._memory_cache:
            return self._memory_cache[cache_key]

        if cache_file.is_file():
            try:
                img = Image.open(cache_file)
                img = img.resize((width, height), Image.Resampling.LANCZOS)
                photo = ImageTk.PhotoImage(img)
                self._memory_cache[cache_key] = photo
                return photo
            except Exception:
                pass

        # Queue background generation
        self._work_queue.put((video_path, cache_file, width, height))
        return None

    def get_ready(self) -> Optional[tuple]:
        """Returns (video_path, PhotoImage) if ready, or None."""
        try:
            return self._ready_queue.get_nowait()
        except queue.Empty:
            return None

    def _worker(self):
        binary = get_ffmpeg_binary()
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        while True:
            try:
                item = self._work_queue.get()
                if item is None:
                    break
                video_path, cache_file, width, height = item
                if not cache_file.is_file() and video_path.is_file():
                    cmd = [
                        binary, "-hide_banner", "-loglevel", "error", "-y",
                        "-ss", "00:00:01",
                        "-i", str(video_path),
                        "-vframes", "1",
                        "-vf", f"scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2",
                        str(cache_file),
                    ]
                    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=flags)

                duration_str = None
                try:
                    res_dur = subprocess.run(
                        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(video_path)],
                        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, creationflags=flags, timeout=3
                    )
                    if res_dur.returncode == 0 and res_dur.stdout.strip():
                        sec = float(res_dur.stdout.strip())
                        duration_str = f"{int(sec // 60)}:{int(sec % 60):02d}"
                except Exception:
                    pass

                if cache_file.is_file():
                    img = Image.open(cache_file)
                    img = img.resize((width, height), Image.Resampling.LANCZOS)
                    # Note: PhotoImage must be created in main thread, so pass the PIL Image
                    self._ready_queue.put((video_path, str(cache_file), img, duration_str))
            except Exception:
                pass
