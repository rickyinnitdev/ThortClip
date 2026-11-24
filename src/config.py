import os
import json
import subprocess
import tempfile
from pathlib import Path

def detect_encoder() -> str:
    """Probe a real frame so unsupported drivers can fall back cleanly."""
    from .camera_manager import get_ffmpeg_binary
    for encoder in ("h264_nvenc", "h264_amf", "h264_qsv"):
        try:
            probe = subprocess.run(
                [get_ffmpeg_binary(), "-v", "error", "-f", "lavfi", "-i",
                 "color=size=640x360:rate=30", "-frames:v", "2", "-pix_fmt", "yuv420p",
                 "-c:v", encoder, "-f", "null", "-"], stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW, timeout=12)
            if probe.returncode == 0:
                return encoder
        except (OSError, subprocess.TimeoutExpired):
            continue
    return "libx264"

def get_default_valorant_log_path() -> str:
    local_app_data = os.environ.get("LOCALAPPDATA", "")
    if local_app_data:
        default_path = Path(local_app_data) / "VALORANT" / "Saved" / "Logs" / "ShooterGame.log"
        return str(default_path)
    return ""

class Config:
    def __init__(self, config_file: str = "config.json"):
        self.config_path = Path(config_file).resolve()
        if not self.config_path.exists():
            raise FileNotFoundError(f"Configuration file not found: {self.config_path}")

        with open(self.config_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        self.dark_mode = bool(data.get("appearance", {}).get("dark_mode", False))
        rec = data.get("recorder", {})
        self.capture_method = rec.get("capture_method", "auto")
        self.encoder = rec.get("encoder", "auto")
        if self.encoder == "auto":
            self.encoder = detect_encoder()

        self.resolution = rec.get("resolution", "1920x1080")
        self.stretch_to_widescreen = bool(rec.get("stretch_to_widescreen", False))
        self.fps = int(rec.get("fps", 60))
        self.bitrate = rec.get("bitrate", "8M")
        self.buffer_duration_sec = int(rec.get("buffer_duration_sec", 120))
        self.clip_duration_before_sec = int(rec.get("clip_duration_before_sec", 20))
        self.clip_duration_after_sec = int(rec.get("clip_duration_after_sec", 5))
        self.audio_offset_ms = int(rec.get("audio_offset_ms", 0))
        self.microphone_enabled = bool(rec.get("microphone_enabled", False))
        self.audio_device = rec.get("audio_device", "none")
        self.idle_outside_matches = bool(rec.get("idle_outside_matches", True))
        self.auto_clip_round = bool(rec.get("auto_clip_round", True))

        aud = data.get("audio", {})
        self.game_audio_enabled = bool(aud.get("game_audio_enabled", True))
        self.playback_device = aud.get("playback_device", "default")
        self.playback_volume = float(aud.get("playback_volume", 1.0))
        if "microphone_enabled" in aud:
            self.microphone_enabled = bool(aud["microphone_enabled"])
        elif "microphone_enabled" in rec:
            self.microphone_enabled = bool(rec["microphone_enabled"])
        self.microphone_device = aud.get("microphone_device", "default")
        self.microphone_volume = float(aud.get("microphone_volume", 1.0))

        det = data.get("detector", {})
        self.detector_enabled = det.get("enabled", True)
        self.detector_poll_fps = int(det.get("poll_fps", 8))
        self.detector_target_color = det.get("target_color", "yellow")
        self.yellow_hsv_min = det.get("yellow_hsv_min", [35, 100, 120])
        self.yellow_hsv_max = det.get("yellow_hsv_max", [65, 255, 255])
        self.min_matching_pixels = int(det.get("min_matching_pixels", 80))
        self.detector_cooldown_sec = float(det.get("cooldown_sec", 1.5))
        self.roi_percent = det.get("roi_percent", {
            "x_min": 0.40, "x_max": 0.60, "y_min": 0.74, "y_max": 0.88
        })

        paths = data.get("paths", {})
        self.clips_output_dir = Path(paths.get("clips_output_dir", "clips")).resolve()
        self.temp_segment_dir = Path(paths.get("temp_segment_dir", "temp/segments")).resolve()
        
        raw_log = paths.get("valorant_log_path", "auto")
        if raw_log == "auto":
            self.valorant_log_path = Path(get_default_valorant_log_path()).resolve()
        else:
            self.valorant_log_path = Path(raw_log).resolve()

        hotkey = data.get("hotkey", {})
        self.hotkey_enabled = hotkey.get("enabled", True)
        self.hotkey_key = hotkey.get("key", "F8")

        overlay = data.get("overlay", {})
        self.overlay_enabled = bool(overlay.get("enabled", False))
        self.overlay_device = overlay.get("device", "USB2.0 HD UVC WebCam")
        self.overlay_virtual_camera = bool(overlay.get("virtual_camera_enabled", False))
        self.overlay_position = overlay.get("position", "bottom-right")
        self.overlay_x_percent = float(overlay.get("x_percent", 80.0))
        self.overlay_y_percent = float(overlay.get("y_percent", 72.0))
        self.overlay_width_percent = float(overlay.get("width_percent", 20.0))
        self.overlay_height_percent = float(overlay.get("height_percent", 25.0))
        self.overlay_size_preset = overlay.get("size_preset", "medium")

        self.clips_output_dir.mkdir(parents=True, exist_ok=True)
        self.temp_segment_dir.mkdir(parents=True, exist_ok=True)

    def get_overlay_dict(self) -> dict:
        return {
            "enabled": self.overlay_enabled,
            "device": self.overlay_device,
            "virtual_camera_enabled": self.overlay_virtual_camera,
            "position": self.overlay_position,
            "x_percent": self.overlay_x_percent,
            "y_percent": self.overlay_y_percent,
            "width_percent": self.overlay_width_percent,
            "height_percent": self.overlay_height_percent,
            "size_preset": self.overlay_size_preset,
        }

    def set_overlay_settings(self, enabled=None, device=None, virtual_camera=None,
                             position=None, x_pct=None, y_pct=None,
                             width_pct=None, height_pct=None, size_preset=None):
        data = json.loads(self.config_path.read_text(encoding="utf-8"))
        ov = data.setdefault("overlay", {})
        if enabled is not None:
            ov["enabled"] = bool(enabled)
            self.overlay_enabled = bool(enabled)
        if device is not None:
            ov["device"] = str(device)
            self.overlay_device = str(device)
        if virtual_camera is not None:
            ov["virtual_camera_enabled"] = bool(virtual_camera)
            self.overlay_virtual_camera = bool(virtual_camera)
        if position is not None:
            ov["position"] = str(position)
            self.overlay_position = str(position)
        if x_pct is not None:
            ov["x_percent"] = round(float(x_pct), 1)
            self.overlay_x_percent = round(float(x_pct), 1)
        if y_pct is not None:
            ov["y_percent"] = round(float(y_pct), 1)
            self.overlay_y_percent = round(float(y_pct), 1)
        if width_pct is not None:
            ov["width_percent"] = round(float(width_pct), 1)
            self.overlay_width_percent = round(float(width_pct), 1)
        if height_pct is not None:
            ov["height_percent"] = round(float(height_pct), 1)
            self.overlay_height_percent = round(float(height_pct), 1)
        if size_preset is not None:
            ov["size_preset"] = str(size_preset)
            self.overlay_size_preset = str(size_preset)

        with tempfile.NamedTemporaryFile(dir=self.config_path.parent, delete=False) as temp:
            pending = Path(temp.name)
        try:
            pending.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
            pending.replace(self.config_path)
        finally:
            pending.unlink(missing_ok=True)

    def get_audio_dict(self) -> dict:
        return {
            "game_audio_enabled": self.game_audio_enabled,
            "playback_device": self.playback_device,
            "playback_volume": self.playback_volume,
            "microphone_enabled": self.microphone_enabled,
            "microphone_device": self.microphone_device,
            "microphone_volume": self.microphone_volume,
        }

    def set_audio_settings(self, game_audio_enabled=None, playback_device=None,
                           playback_volume=None, microphone_enabled=None,
                           microphone_device=None, microphone_volume=None):
        data = json.loads(self.config_path.read_text(encoding="utf-8"))
        aud = data.setdefault("audio", {})
        rec = data.setdefault("recorder", {})
        if game_audio_enabled is not None:
            aud["game_audio_enabled"] = bool(game_audio_enabled)
            self.game_audio_enabled = bool(game_audio_enabled)
        if playback_device is not None:
            aud["playback_device"] = str(playback_device)
            self.playback_device = str(playback_device)
        if playback_volume is not None:
            aud["playback_volume"] = round(float(playback_volume), 2)
            self.playback_volume = round(float(playback_volume), 2)
        if microphone_enabled is not None:
            aud["microphone_enabled"] = bool(microphone_enabled)
            rec["microphone_enabled"] = bool(microphone_enabled)
            self.microphone_enabled = bool(microphone_enabled)
        if microphone_device is not None:
            aud["microphone_device"] = str(microphone_device)
            self.microphone_device = str(microphone_device)
        if microphone_volume is not None:
            aud["microphone_volume"] = round(float(microphone_volume), 2)
            self.microphone_volume = round(float(microphone_volume), 2)

        with tempfile.NamedTemporaryFile(dir=self.config_path.parent, delete=False) as temp:
            pending = Path(temp.name)
        try:
            pending.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
            pending.replace(self.config_path)
        finally:
            pending.unlink(missing_ok=True)

    def set_preference(self, section, key, value):
        data = json.loads(self.config_path.read_text(encoding="utf-8"))
        data.setdefault(section, {})[key] = value
        with tempfile.NamedTemporaryFile(dir=self.config_path.parent, delete=False) as temp:
            pending = Path(temp.name)
        try:
            pending.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
            pending.replace(self.config_path)
        finally:
            pending.unlink(missing_ok=True)

    def set_auto_clip_round(self, enabled: bool):
        self.set_preference("recorder", "auto_clip_round", enabled)
        self.auto_clip_round = enabled
