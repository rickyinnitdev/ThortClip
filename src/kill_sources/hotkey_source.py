import time
import threading
import ctypes
from ctypes import wintypes
from typing import Callable, Dict, Any, Optional
from .base import KillSourceBase

# Windows Virtual Key Codes
VK_MAP = {
    "F1": 0x70, "F2": 0x71, "F3": 0x72, "F4": 0x73,
    "F5": 0x74, "F6": 0x75, "F7": 0x76, "F8": 0x77,
    "F9": 0x78, "F10": 0x79, "F11": 0x7A, "F12": 0x7B,
    "F8": 0x77,
}

MOD_NOREPEAT = 0x4000
WM_HOTKEY = 0x0312
HOTKEY_ID = 101

user32 = ctypes.windll.user32

class HotkeyKillSource(KillSourceBase):
    """Global Windows hotkey listener using native win32 API (zero third-party dependencies)."""

    def __init__(self, key_name: str = "F8"):
        self.key_name = key_name.upper()
        self.vk_code = VK_MAP.get(self.key_name, 0x77) # Default to F8
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._callback: Optional[Callable[[float, Optional[int], Dict[str, Any]], None]] = None

    def start(self, on_trigger: Callable[[float, Optional[int], Dict[str, Any]], None]):
        self._callback = on_trigger
        self._running = True
        self._thread = threading.Thread(target=self._msg_loop, daemon=True, name="HotkeyThread")
        self._thread.start()

    def stop(self):
        self._running = False
        if self._thread and self._thread.is_alive():
            user32.PostThreadMessageW(self._thread.ident, 0x0012, 0, 0) # WM_QUIT

    def _msg_loop(self):
        thread_id = threading.get_ident()
        if not user32.RegisterHotKey(None, HOTKEY_ID, MOD_NOREPEAT, self.vk_code):
            print(f"[HotkeySource] Warning: Failed to register hotkey {self.key_name} (may be in use).")
            return

        print(f"[HotkeySource] Registered global clip hotkey: [{self.key_name}]")
        msg = wintypes.MSG()

        try:
            while self._running:
                # Wait for messages with timeout
                if user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1): # PM_REMOVE
                    if msg.message == WM_HOTKEY and msg.wParam == HOTKEY_ID:
                        trigger_time = time.time()
                        print(f"\n[HotkeySource] Clip hotkey [{self.key_name}] pressed!")
                        if self._callback:
                            self._callback(trigger_time, None, {"source": "hotkey", "key": self.key_name})
                    elif msg.message == 0x0012: # WM_QUIT
                        break
                    user32.TranslateMessage(ctypes.byref(msg))
                    user32.DispatchMessageW(ctypes.byref(msg))
                else:
                    time.sleep(0.05)
        finally:
            user32.UnregisterHotKey(None, HOTKEY_ID)
            print("[HotkeySource] Hotkey unregistered.")
