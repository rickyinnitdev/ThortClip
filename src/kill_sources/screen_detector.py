import time
import threading
import numpy as np
from pathlib import Path
from PIL import Image
from typing import Callable, Dict, Any, Optional
from .base import KillSourceBase
from ..config import Config
from ..support_log import log

try:
    import dxcam
except ImportError:
    dxcam = None

def rgb_to_hsv(rgb: np.ndarray):
    """Fast vectorized RGB -> HSV conversion using pure numpy."""
    rgb_f = rgb.astype("float32") / 255.0
    r, g, b = rgb_f[..., 0], rgb_f[..., 1], rgb_f[..., 2]
    cmax = np.maximum(np.maximum(r, g), b)
    cmin = np.minimum(np.minimum(r, g), b)
    delta = cmax - cmin

    # Hue (0 - 360)
    h = np.zeros_like(cmax)
    mask_r = (cmax == r) & (delta != 0)
    h[mask_r] = (60 * ((g[mask_r] - b[mask_r]) / delta[mask_r]) + 360) % 360
    mask_g = (cmax == g) & (delta != 0)
    h[mask_g] = (60 * ((b[mask_g] - r[mask_g]) / delta[mask_g]) + 120) % 360
    mask_b = (cmax == b) & (delta != 0)
    h[mask_b] = (60 * ((r[mask_b] - g[mask_b]) / delta[mask_b]) + 240) % 360

    # Saturation (0 - 255)
    s = np.zeros_like(cmax)
    mask_cmax = cmax != 0
    s[mask_cmax] = (delta[mask_cmax] / cmax[mask_cmax]) * 255.0

    # Value (0 - 255)
    v = cmax * 255.0

    return h, s, v

def count_hsv_matches(rgb: np.ndarray, h_min: float, h_max: float, s_min: float, s_max: float, v_min: float, v_max: float) -> int:
    h, s, v = rgb_to_hsv(rgb)
    mask = (
        (h >= h_min) & (h <= h_max) &
        (s >= s_min) & (s <= s_max) &
        (v >= v_min) & (v <= v_max)
    )
    return int(np.count_nonzero(mask))

class LocalKillFeed:
    """Track highlighted attacker rows containing the local-player label."""
    def __init__(self):
        with Image.open(Path(__file__).with_name("feed_me.png")) as image:
            self.letters = np.asarray(image) > 0
        with Image.open(Path(__file__).with_name("switch_player.png")) as image:
            self.switch_letters = np.asarray(image, dtype=bool)
        self.last_spectator = float("-inf")
        self.rows = []

    def update(self, rgb, now, spectator_strip=None):
        if spectator_strip is not None:
            strip = Image.fromarray(spectator_strip)
            if strip.size != (520, 32):
                strip = strip.resize((520, 32))
            gray = np.asarray(strip)[7:24, 162:275].min(axis=2)
            patches = np.lib.stride_tricks.sliding_window_view(gray, self.switch_letters.shape)
            ink = patches[..., self.switch_letters].mean(axis=-1)
            gaps = patches[..., ~self.switch_letters].mean(axis=-1)
            if np.any((ink > 160) & (ink - gaps > 40)):
                self.last_spectator = now
        if rgb.shape[:2] != (240, 520):
            rgb = np.asarray(Image.fromarray(rgb).resize((520, 240)))
        # The local portrait has a slim gold strip. Search only the attacker side.
        r, g, b = rgb[:, :320:2].transpose(2, 0, 1)
        gold = (r > 187) & (g > 190) & (b < 170) & (r < 253)
        vertical = gold[:-26] & gold[8:-18] & gold[17:-9] & gold[26:]
        ys, xs = np.nonzero(vertical & ~np.pad(vertical[:-1], ((1, 0), (0, 0))))
        centers = []
        for y, half_x in zip(ys, xs):
            if any(abs(y - old) < 12 for old in centers):
                continue
            x = int(half_x) * 2
            patch = rgb[y+6:y+30, x+59:x+96]
            if patch.shape[:2] != (24, 37):
                continue
            gray = patch.min(axis=2).astype(np.float32)
            windows = np.lib.stride_tricks.sliding_window_view(gray, self.letters.shape)
            ink = windows[..., self.letters].mean(axis=-1)
            gaps = windows[..., ~self.letters].mean(axis=-1)
            contrast = np.where(ink > 172, ink - gaps, 0)
            dy, dx = np.unravel_index(contrast.argmax(), contrast.shape)
            if contrast[dy, dx] <= 70:
                continue
            # The local attacker is on the friendly panel, opposite an enemy
            # victim panel. Gold HUD details and similar text alone are ambiguous.
            baseline = int(y + dy - 4)
            friendly = rgb[baseline+4:baseline+28:2, x+88:x+136:2].astype(np.int16)
            enemy = rgb[baseline+4:baseline+28:2, 400:437:2].astype(np.int16)
            if (np.mean(friendly[..., 1] - friendly[..., 0] > 18) >= 0.25
                    and np.mean(enemy[..., 0] - enemy[..., 1] > 24) >= 0.25):
                # Anchor the portrait to the matched text baseline. The gold
                # strip changes shape during fades and as other rows appear.
                centers.append(int(y + dy - 4))
        # Victim portraits stay at the right edge as rows move down the feed.
        self.rows = [(pic, seen, hits) for pic, seen, hits in self.rows
                     if now - seen < (4.0 if hits >= 2 else 0.45)]
        events = 0
        matched = set()
        for y in centers:
            pic = rgb[y+5:y+26:3, 467:496:3].astype(np.int16)
            if pic.shape != (7, 10, 3):
                continue
            for i, (previous, seen, hits) in enumerate(self.rows):
                if i in matched:
                    continue
                # Additive HUD fades affect the whole portrait. Compare texture
                # as well as raw colour to retain identity through a fade.
                diff = pic - previous
                if np.abs(diff).mean() < 36 or np.abs(diff - diff.mean(axis=(0, 1))).mean() < 24:
                    matched.add(i)
                    self.rows[i] = (pic, now, hits+1)
                    events += hits == 1
                    break
            else:
                matched.add(len(self.rows))
                self.rows.append((pic, now, 1))
        return events if now - self.last_spectator > 0.5 else 0


def detector_output(monitor_handle):
    if monitor_handle:
        # DXcam 0.3.0 exposes monitor handles in its factory's DXGI outputs.
        for device_idx, outputs in enumerate(vars(dxcam)["__factory"].outputs):
            for output_idx, output in enumerate(outputs):
                if output.hmonitor == monitor_handle:
                    return device_idx, output_idx
        raise RuntimeError("Recording display is unavailable to the kill detector")
    return 0, 0


class ScreenDetectionKillSource(KillSourceBase):
    """Detect personal kills in the top-right Valorant kill feed."""

    def __init__(self, config: Config):
        self.config = config
        self.monitor_handle = None
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._callback: Optional[Callable[[float, Optional[int], Dict[str, Any]], None]] = None
        

    def start(self, on_trigger: Callable[[float, Optional[int], Dict[str, Any]], None]):
        self._callback = on_trigger
        self._running = True
        self._thread = threading.Thread(target=self._detection_loop, daemon=True, name="ScreenDetector")
        self._thread.start()
        print(f"[ScreenDetector] Started at {self.config.detector_poll_fps} FPS. Target: local attacker in kill feed.")

    def stop(self):
        self._running = False

        if self._thread:
            self._thread.join()

    def _detection_loop(self):
        interval = 1.0 / max(1, self.config.detector_poll_fps)

        if dxcam is None:
            log.error("Automatic kill detection unavailable: DXcam is missing")
            print("[ScreenDetector] Error: dxcam is not available.")
            return

        camera = None
        try:
            device, output = detector_output(self.monitor_handle)
            camera = dxcam.create(device_idx=device, output_idx=output, output_color="BGRA")
            mon_w = camera.width
            mon_h = camera.height
            scale = mon_h / 1080
            region = (max(0, mon_w - round(520 * scale)), round(40 * scale),
                      mon_w, round(280 * scale))
            feed = LocalKillFeed()
            print(f"[ScreenDetector] Connected to DirectX GPU VRAM ({mon_w}x{mon_h}). ROI: {region}")
        except Exception as e:
            log.exception("Could not start automatic kill detection")
            print(f"[ScreenDetector] Warning: Could not initialize DirectX duplication: {e}")
            return

        while self._running:
            loop_start = time.time()
            try:
                frame = camera.grab()
                if frame is not None and self._callback:
                    left, top, right, bottom = region
                    hud = frame[round(848*scale):round(880*scale), :round(520*scale), 2::-1]
                    rows = frame[top:bottom, left:right, 2::-1]
                    for _ in range(feed.update(rows, time.monotonic(), hud)):
                        self._callback(time.time(), 1, {"source": "local_kill_feed"})
            except Exception:
                pass

            elapsed = time.time() - loop_start
            sleep_t = max(0.01, interval - elapsed)
            time.sleep(sleep_t)

        try:
            del camera
        except Exception:
            pass
