"""DirectShow camera discovery and preview utilities."""
import os
import re
import subprocess
from pathlib import Path
from typing import List, Dict, Tuple, Optional

def get_ffmpeg_binary() -> str:
    if os.environ.get("MEDALREA_BUNDLE_TOOLS"):
        return str(Path(os.environ["MEDALREA_BUNDLE_TOOLS"]) / "ffmpeg.exe")
    local_app_data = os.environ.get("LOCALAPPDATA", "")
    if local_app_data:
        local_path = Path(local_app_data) / "MedalRea" / "tools" / "ffmpeg.exe"
        if local_path.is_file():
            return str(local_path)
    return "ffmpeg"

def is_virtual_camera_name(name: str) -> bool:
    lower = name.lower()
    virtual_keywords = ["virtual", "obs", "vmix", "xsplit", "manycam", "droidcam", "iriun", "snap camera"]
    return any(k in lower for k in virtual_keywords)

def list_camera_devices(include_virtual: bool = True) -> List[str]:
    """Returns detected video capture devices via DirectShow."""
    binary = get_ffmpeg_binary()
    try:
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        res = subprocess.run(
            [binary, "-hide_banner", "-list_devices", "true", "-f", "dshow", "-i", "dummy"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            errors="ignore",
            creationflags=flags,
            timeout=5,
        )
        output = res.stderr or ""
    except Exception as exc:
        print(f"[CameraManager] Error listing devices: {exc}")
        return []

    devices = []
    # Lines look like: [in#0 @ 000...] "USB2.0 HD UVC WebCam" (video)
    for line in output.splitlines():
        if "(video)" in line:
            m = re.search(r'"([^"]+)"\s*\(video\)', line)
            if m:
                dev = m.group(1).strip()
                if dev and dev not in devices:
                    devices.append(dev)

    if not include_virtual:
        devices = [d for d in devices if not is_virtual_camera_name(d)]

    return devices

def capture_camera_frame(device_name: str, output_path: Path, width: int = 320, height: int = 240) -> bool:
    """Captures a single snapshot frame from the camera device."""
    if not device_name:
        return False
    binary = get_ffmpeg_binary()
    try:
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        cmd = [
            binary, "-hide_banner", "-loglevel", "error", "-y",
            "-f", "dshow",
            "-rtbufsize", "50M",
            "-i", f"video={device_name}",
            "-vframes", "1",
            "-vf", f"scale={width}:{height}",
            str(output_path),
        ]
        res = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, creationflags=flags, timeout=4)
        return res.returncode == 0 and output_path.is_file()
    except Exception as exc:
        print(f"[CameraManager] Snapshot error: {exc}")
        return False

class CameraPreview:
    """One DirectShow reader with a single latest-frame slot."""
    def __init__(self, device):
        import threading
        self.frame = None
        self.process = subprocess.Popen(
            [get_ffmpeg_binary(), "-hide_banner", "-loglevel", "error", "-f", "dshow",
             "-i", f"video={device}", "-an", "-vf", "fps=10,scale=320:240",
             "-pix_fmt", "rgb24", "-f", "rawvideo", "pipe:1"],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW)
        threading.Thread(target=self._read, daemon=True, name="CameraPreview").start()

    def _read(self):
        with self.process.stdout as stream:
            size = 320 * 240 * 3
            while len(data := stream.read(size)) == size:
                self.frame = data

    def stop(self):
        if self.process.poll() is None:
            self.process.kill()
        self.process.wait()
