import os
import time
import tempfile
import threading
import subprocess
import ctypes
from ctypes import wintypes
from pathlib import Path
from typing import Optional, List
from .config import Config
from .webcam_recorder import WebcamRecorder

BELOW_NORMAL_PRIORITY_CLASS = 0x00004000
CREATE_NO_WINDOW = 0x08000000

class DEVMODEW(ctypes.Structure):
    _fields_ = [
        ("dmDeviceName", wintypes.WCHAR * 32),
        ("dmSpecVersion", wintypes.WORD),
        ("dmDriverVersion", wintypes.WORD),
        ("dmSize", wintypes.WORD),
        ("dmDriverExtra", wintypes.WORD),
        ("dmFields", wintypes.DWORD),
        ("dmPositionX", wintypes.LONG),
        ("dmPositionY", wintypes.LONG),
        ("dmDisplayOrientation", wintypes.DWORD),
        ("dmDisplayFixedOutput", wintypes.DWORD),
        ("dmColor", wintypes.SHORT),
        ("dmDuplex", wintypes.SHORT),
        ("dmYResolution", wintypes.SHORT),
        ("dmTTOption", wintypes.SHORT),
        ("dmCollate", wintypes.SHORT),
        ("dmFormName", wintypes.WCHAR * 32),
        ("dmLogPixels", wintypes.WORD),
        ("dmBitsPerPel", wintypes.DWORD),
        ("dmPelsWidth", wintypes.DWORD),
        ("dmPelsHeight", wintypes.DWORD),
        ("dmDisplayFlags", wintypes.DWORD),
        ("dmDisplayFrequency", wintypes.DWORD),
    ]

def get_primary_display_info():
    """Returns (width, height, refresh_rate_hz) of the primary monitor."""
    dm = DEVMODEW()
    dm.dmSize = ctypes.sizeof(DEVMODEW)
    user32 = ctypes.windll.user32
    if user32.EnumDisplaySettingsW(None, -1, ctypes.byref(dm)):
        return dm.dmPelsWidth, dm.dmPelsHeight, max(60, dm.dmDisplayFrequency)
    return 1920, 1080, 240

def valorant_window():
    """Resolve the visible gameplay HWND independently of Windows display numbering."""
    user = ctypes.windll.user32
    kernel = ctypes.windll.kernel32
    kernel.OpenProcess.restype = wintypes.HANDLE
    user.MonitorFromWindow.restype = wintypes.HANDLE
    result = []
    callback = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    @callback
    def visit(handle, _):
        hwnd = wintypes.HWND(handle)
        if not user.IsWindowVisible(hwnd) or user.IsIconic(hwnd):
            return True
        rect = wintypes.RECT()
        if not user.GetClientRect(hwnd, ctypes.byref(rect)) or rect.right < 320 or rect.bottom < 200:
            return True
        pid = wintypes.DWORD()
        user.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        process = kernel.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        name = ""
        if process:
            path = ctypes.create_unicode_buffer(32768)
            length = wintypes.DWORD(len(path))
            try:
                if kernel.QueryFullProcessImageNameW(wintypes.HANDLE(process), 0, path, ctypes.byref(length)):
                    name = Path(path.value).name.casefold()
            finally:
                kernel.CloseHandle(wintypes.HANDLE(process))
        if name != "valorant-win64-shipping.exe":
            if name:
                return True
            # Protected processes can deny image queries. Match both window properties.
            title, klass = ctypes.create_unicode_buffer(256), ctypes.create_unicode_buffer(256)
            user.GetWindowTextW(hwnd, title, len(title))
            user.GetClassNameW(hwnd, klass, len(klass))
            if title.value.strip().casefold() != "valorant" or klass.value != "UnrealWindow":
                return True
        result.append((rect.right * rect.bottom, handle, user.MonitorFromWindow(hwnd, 2)))
        return True

    user.EnumWindows(visit, 0)
    if result:
        _, hwnd, monitor = max(result)
        return hwnd, monitor
    return None


class ScreenRecorder:
    """Manages high-performance GPU video recording into 2-second .ts rolling segments without clamping 240Hz displays."""

    def __init__(self, config: Config):
        self.config = config
        self.monitor_handle = None
        self.window_handle = None
        self.keep_from = None
        self.retain_match = False
        self.playback = None
        self.capture_status = ""
        self._video_profile = None
        self.webcam: Optional[WebcamRecorder] = None
        self.process: Optional[subprocess.Popen] = None
        self.segment_dir: Path = self.config.temp_segment_dir
        self.start_wall_time: float = 0.0
        
        self._camera_stop = threading.Event()
        self._camera_thread = None
        self._prune_stop = threading.Event()
        self._prune_thread: Optional[threading.Thread] = None

    def _capture_encoder(self, binary, graph, fps, height):
        preferred = self.config.encoder
        download = "hwdownload,format=bgra,format=nv12"
        candidates = []
        if preferred in ("h264_nvenc", "hevc_nvenc"):
            candidates.append((preferred, "", fps))
        candidates.extend((encoder, download, fps) for encoder in
                          dict.fromkeys((preferred, "h264_nvenc", "h264_qsv", "h264_amf"))
                          if encoder != "libx264")
        scale = f"scale=-2:{min(720, height)},format=yuv420p"
        software = ("libx264", f"{download},{scale}", min(30, fps))
        if preferred == "libx264":
            candidates.insert(0, software)
        else:
            candidates.append(software)
        with tempfile.TemporaryDirectory(prefix="thortclip-probe-") as folder:
            sample = str(Path(folder) / "sample.ts")
            for encoder, filters, rate in candidates:
                probe = [binary, "-y", "-v", "error", "-f", "lavfi", "-i", graph]
                if filters:
                    probe += ["-vf", filters]
                probe += ["-r", str(rate), "-frames:v", "6", "-c:v", encoder]
                if encoder == "libx264":
                    probe += ["-preset", "ultrafast"]
                probe += ["-b:v", self.config.bitrate, "-f", "mpegts", sample]
                try:
                    options = dict(stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL, creationflags=CREATE_NO_WINDOW, timeout=4)
                    if subprocess.run(probe, **options).returncode == 0 and subprocess.run(
                        [binary, "-v", "error", "-xerror", "-f", "mpegts", "-i", sample, "-frames:v", "1",
                         "-f", "null", "-"], **options
                    ).returncode == 0:
                        return encoder, filters, rate
                except subprocess.TimeoutExpired:
                    continue
        raise RuntimeError("Screen capture failed. Try another graphics driver or explicitly select desktop capture.")

    def start_recording(self, *, resume=False, target=None):
        """Starts recording into 2-second .ts segments with GPU acceleration."""
        if self.is_recording():
            print("[Recorder] Recording is already active.")
            return

        self._stop_camera_session()
        self.segment_dir.mkdir(parents=True, exist_ok=True)

        existing = sorted(self.segment_dir.glob("seg_*.ts"))
        self.first_segment = int(existing[-1].stem.split("_")[-1]) + 1 if existing else 0
        self.start_wall_time = time.time()
        segment_pattern = str(self.segment_dir / "seg_%06d.ts")

        # Detect native monitor refresh rate to prevent Windows DWM from clamping 240Hz down to 60Hz
        native_w, native_h, native_hz = get_primary_display_info()
        print(f"[Recorder] Detected primary display: {native_w}x{native_h} @ {native_hz}Hz")

        method = self.config.capture_method.lower()
        from .camera_manager import get_ffmpeg_binary
        binary = get_ffmpeg_binary()
        cmd = [binary, "-hide_banner", "-loglevel", "error", "-nostats", "-y"]

        gpu_capture = method in ("auto", "ddagrab", "gfxcapture")
        encoder, filters = self.config.encoder, ""
        fps = self.config.fps
        if gpu_capture:
            graph = f"ddagrab=output_idx=0:framerate={self.config.fps}:draw_mouse=1"
            if method == "gfxcapture":
                width, height = map(int, self.config.resolution.lower().split("x"))
                target = target or valorant_window()
                self.window_handle, self.monitor_handle = target or (None, None)
                selector = f"hwnd={self.window_handle}" if self.window_handle else "monitor_idx=0"
                graph = (f"gfxcapture={selector}:width={width}:height={height}:"
                         f"resize_mode=scale:max_framerate={self.config.fps}")
            if resume and self._video_profile:
                encoder, filters, fps = self._video_profile
            else:
                encoder, filters, fps = self._capture_encoder(
                    binary, graph, fps, height if method == "gfxcapture" else native_h)
        self.start_wall_time = time.time()
        self.capture_status = f"{method} · {encoder} · {fps} FPS"
        audio_device = self.config.audio_device
        if audio_device == "system":
            from .playback_audio import PlaybackAudio
            try:
                self.playback = PlaybackAudio(
                    microphone=self.config.microphone_enabled,
                    playback_device=self.config.playback_device,
                    microphone_device=self.config.microphone_device,
                    playback_volume=self.config.playback_volume,
                    microphone_volume=self.config.microphone_volume,
                    game_audio_enabled=self.config.game_audio_enabled,
                )
            except (OSError, ValueError, LookupError) as exc:
                audio_device = "none"
                self.capture_status += " · Audio unavailable"
                print(f"[Recorder] Audio unavailable: {exc}")
        if self.playback:
            origin = time.perf_counter()
            epoch = time.time()
            cmd += ["-copyts"]
            if method != "gfxcapture":
                cmd += ["-thread_queue_size", "8"]
        if gpu_capture:
            if self.playback:
                graph += f",setpts=(RTCTIME/1000000-{epoch:.6f})/TB"
            cmd += [
                "-f", "lavfi",
                "-i", graph,
            ]
        else:
            cmd += [
                "-f", "gdigrab",
                "-framerate", str(self.config.fps),
                "-offset_x", "0",
                "-offset_y", "0",
                "-video_size", f"{native_w}x{native_h}",
                "-draw_mouse", "1",
                "-i", "desktop",
            ]

        # Audio device if enabled
        if self.playback:
            if method != "gfxcapture":
                cmd += ["-thread_queue_size", "8"]
            cmd += ["-probesize", "32", "-analyzeduration", "1", "-f", "s16le", "-ar", str(self.playback.rate),
                    "-ac", str(self.playback.channels), "-i", "pipe:0", "-shortest"]
            if self.config.audio_offset_ms < 0:
                trim = -self.config.audio_offset_ms / 1000
                cmd += ["-af", f"atrim=start={trim},asetpts=PTS-STARTPTS"]
            elif self.config.audio_offset_ms > 0:
                cmd += ["-af", f"adelay={self.config.audio_offset_ms}:all=1"]
        elif audio_device and audio_device.lower() != "none":
            cmd += ["-f", "dshow", "-i", f"audio={audio_device}"]

        if self.playback and not gpu_capture:
            cmd += ["-vf", f"setpts=PTS-{epoch:.6f}/TB"]

        if filters:
            cmd += ["-vf", filters]

        if self.playback:
            cmd += ["-r", str(fps), "-fps_mode", "cfr"]
        elif fps != self.config.fps:
            cmd += ["-r", str(fps)]

        if encoder != self.config.encoder:
            print(f"[Recorder] Using {encoder} for this display ({self.config.encoder} unavailable).")

        # Hardware video encoder (NVENC / AMF / QSV)
        if encoder in ("h264_nvenc", "hevc_nvenc"):
            cmd += [
                "-c:v", encoder,
                "-preset", "p1",        # Fastest NVENC hardware preset (zero CPU, minimal GPU)
                "-tune", "ull",         # Ultra-low latency
                "-rc", "cbr",
                "-b:v", self.config.bitrate,

            ]
            if not gpu_capture:
                cmd += ["-pix_fmt", "yuv420p"]
        elif encoder == "h264_amf":
            cmd += ["-c:v", "h264_amf", "-quality", "speed", "-b:v", self.config.bitrate]
        elif encoder == "h264_qsv":
            cmd += ["-c:v", "h264_qsv", "-preset", "veryfast", "-b:v", self.config.bitrate]
        else:
            cmd += ["-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-tune", "zerolatency", "-b:v", self.config.bitrate]

        cmd += ["-g", str(fps * 2)]  # Match the rolling segment duration.

        # Audio encoder if audio included
        if audio_device and audio_device.lower() != "none":
            cmd += ["-c:a", "aac", "-b:a", "192k"]

        if self.config.stretch_to_widescreen:
            cmd += ["-aspect:v", "16:9"]

        # 2-second MPEG-TS segments
        cmd += [
            "-f", "segment",
            "-segment_start_number", str(self.first_segment),
            "-segment_time", "2",
            "-segment_format", "mpegts",
            "-reset_timestamps", "1",
            segment_pattern,
        ]

        flags = CREATE_NO_WINDOW | BELOW_NORMAL_PRIORITY_CLASS
        print(f"[Recorder] Capturing at {self.config.fps}fps (method: {method})...")

        try:
            with (self.segment_dir / "capture-errors.log").open("ab") as errors:
                self.process = subprocess.Popen(
                    cmd,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.DEVNULL,
                    stderr=errors,
                    creationflags=flags,
                )
        except Exception:
            if self.playback:
                self.playback.close()
                self.playback = None
            raise
        try:
            if self.playback:
                self.playback.start(self.process.stdin, origin)
            first_video = self.segment_dir / f"seg_{self.first_segment:06d}.ts"
            deadline = time.monotonic() + 6
            while self.process.poll() is None and time.monotonic() < deadline:
                try:
                    if first_video.stat().st_size > 0:
                        break
                except FileNotFoundError:
                    pass
                time.sleep(0.1)
            else:
                raise RuntimeError("Capture did not produce a segment.")
        except Exception as exc:
            self.stop_recording()
            with (self.segment_dir / "capture-errors.log").open("rb") as errors:
                errors.seek(max(0, errors.seek(0, 2) - 1024))
                err = errors.read().decode(errors="replace").strip()
            raise RuntimeError(f"{exc} {err[-300:]}".strip()) from exc
        self._video_profile = (encoder, filters, fps)

        if self.config.overlay_enabled and self.config.overlay_device:
            self._camera_stop.clear()
            self._camera_thread = threading.Thread(
                target=self._camera_session, args=(self.process, first_video, resume or bool(existing)),
                name="GameplayWebcam", daemon=True)
            self._camera_thread.start()

        if self.retain_match:
            return
        self._prune_stop.clear()
        self._prune_thread = threading.Thread(target=self._prune_loop, daemon=True, name="SegmentPruner")
        self._prune_thread.start()
        print(f"[Recorder] Active recording into: {self.segment_dir}")

    def _camera_session(self, gameplay, first_video, resume):
        camera = WebcamRecorder(self.config, self.segment_dir)
        try:
            while gameplay.poll() is None and self.config.overlay_enabled and not self._camera_stop.is_set():
                if self.webcam is None:
                    try:
                        ready = first_video.stat().st_size > 0
                    except FileNotFoundError:
                        ready = False
                    if ready:
                        self.webcam = camera
                        camera.start_recording(resume=resume)
                if self._camera_stop.wait(0.2):
                    break
        finally:
            camera.stop_recording()

    def disable_webcam(self):
        self._camera_stop.set()

    def _stop_camera_session(self):
        self.disable_webcam()
        if self._camera_thread:
            self._camera_thread.join()
            self._camera_thread = None
        self.webcam = None

    def get_completed_segments(self) -> List[Path]:
        """Returns sorted list of completed .ts segments, excluding the newest active one."""
        if not self.segment_dir.exists():
            return []
        files = sorted(self.segment_dir.glob("seg_*.ts"))
        if getattr(self, "first_segment", 0):
            first = f"seg_{self.first_segment:06d}.ts"
            files = [p for p in files if p.name >= first]
        return files[:-1] if self.is_recording() else files

    def get_completed_cam_segments(self) -> List[Path]:
        """Returns sorted list of completed webcam .ts segments."""
        if self.webcam:
            return self.webcam.get_completed_segments()
        if not self.segment_dir.exists():
            return []
        return sorted(self.segment_dir.glob("cam_*.ts"))

    def _prune_loop(self):
        """Periodically removes segments older than buffer_duration_sec to keep disk space bounded."""
        max_segments = int(self.config.buffer_duration_sec / 2) + 5

        while not self._prune_stop.is_set():
            try:
                segments = sorted(self.segment_dir.glob("seg_*.ts"))
                if len(segments) > max_segments:
                    to_remove = segments[: len(segments) - max_segments]
                    for seg in to_remove:
                        try:
                            if self.keep_from is None or seg.stat().st_mtime < self.keep_from:
                                seg.unlink(missing_ok=True)
                        except Exception:
                            pass
                cam_segments = sorted(self.segment_dir.glob("cam_*.ts"))
                if len(cam_segments) > max_segments:
                    to_remove = cam_segments[: len(cam_segments) - max_segments]
                    for seg in to_remove:
                        try:
                            if self.keep_from is None or seg.stat().st_mtime < self.keep_from:
                                seg.unlink(missing_ok=True)
                        except Exception:
                            pass
            except Exception:
                pass
            self._prune_stop.wait(2.0)

    def is_recording(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def stop_recording(self):
        """Stops recording cleanly."""
        self._prune_stop.set()
        if self._prune_thread:
            self._prune_thread.join()
        self._stop_camera_session()
        if self.playback:
            self.playback.done.set()
        if not self.is_recording():
            if self.playback:
                self.playback.close()
                self.playback = None
            return

        print("[Recorder] Stopping FFmpeg recorder...")
        try:
            if self.process.stdin and not self.playback:
                self.process.stdin.write(b"q")
                self.process.stdin.flush()
            self.process.wait(timeout=4)
        except Exception:
            try:
                self.process.kill()
                self.process.wait()
            except Exception:
                pass

        if self.playback:
            self.playback.close()
            self.playback = None
        self.process = None
        print("[Recorder] Recorder stopped.")

