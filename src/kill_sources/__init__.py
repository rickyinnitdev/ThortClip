from .base import KillSourceBase
from .hotkey_source import HotkeyKillSource
def __getattr__(name):
    if name in {"ScreenDetectionKillSource", "count_hsv_matches", "rgb_to_hsv"}:
        from . import screen_detector
        return getattr(screen_detector, name)
    raise AttributeError(name)

__all__ = ["KillSourceBase", "HotkeyKillSource", "ScreenDetectionKillSource", "count_hsv_matches", "rgb_to_hsv"]
