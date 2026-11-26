"""Minimal libVLC 3 bindings for the embedded Windows player."""
import ctypes as C
import os
from pathlib import Path


class VlcPlayer:
    def __init__(self, path, hwnd):
        folder = Path(os.environ["ProgramFiles"]) / "VideoLAN/VLC"
        if os.environ.get("THORTCLIP_BUNDLE_TOOLS"):
            folder = Path(os.environ["THORTCLIP_BUNDLE_TOOLS"]) / "vlc"
        with os.add_dll_directory(str(folder)):
            dll = C.CDLL(str(folder / "libvlc.dll"))
        ptr, integer, clock = C.c_void_p, C.c_int, C.c_int64
        for name, result, args in [
            ("new", ptr, [integer, ptr]), ("release", None, [ptr]),
            ("media_new_path", ptr, [ptr, C.c_char_p]), ("media_release", None, [ptr]),
            ("media_player_new_from_media", ptr, [ptr]), ("media_player_release", None, [ptr]),
            ("media_player_set_hwnd", None, [ptr, ptr]), ("media_player_play", integer, [ptr]),
            ("media_player_set_pause", None, [ptr, integer]), ("media_player_get_state", integer, [ptr]),
            ("media_player_get_time", clock, [ptr]), ("media_player_set_time", None, [ptr, clock]),
            ("audio_set_mute", None, [ptr, integer]), ("audio_get_mute", integer, [ptr]),
            ("video_set_mouse_input", None, [ptr, integer]), ("video_set_key_input", None, [ptr, integer])
        ]:
            fn = getattr(dll, "libvlc_" + name)
            fn.argtypes, fn.restype = args, result
        self.dll = dll
        self.instance = dll.libvlc_new(0, None)
        if not self.instance:
            raise OSError("VLC initialization failed")
        media = dll.libvlc_media_new_path(self.instance, str(path.resolve()).encode())
        self.player = dll.libvlc_media_player_new_from_media(media) if media else None
        if media:
            dll.libvlc_media_release(media)
        if not self.player:
            self.close()
            raise OSError("Could not load clip")
        dll.libvlc_media_player_set_hwnd(self.player, hwnd)
        dll.libvlc_video_set_mouse_input(self.player, 0)
        dll.libvlc_video_set_key_input(self.player, 0)
        if dll.libvlc_media_player_play(self.player) < 0:
            self.close()
            raise OSError("Could not start playback")

    def close(self):
        if self.player:
            self.dll.libvlc_media_player_release(self.player)
        self.dll.libvlc_release(self.instance)
