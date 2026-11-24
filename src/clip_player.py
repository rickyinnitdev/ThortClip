"""MedalRea Media Player & Clip Editor with Kill Stars and Timeline Scrubbing.
Styled after Medal.tv's desktop clip player.
"""
import hashlib
import tempfile
import ctypes
from ctypes import wintypes
import json
from .clip_metadata import metadata_path
import math
import os
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path
import tkinter as tk
from .theme import palette
from tkinter import ttk, messagebox
from typing import List, Optional, Dict, Any, Callable

from .ui_controls import RoundedButton

from PIL import Image, ImageTk

# Theme Colors (Medal.tv Palette)
PURPLE = "#a855f7"      # Medal Star Purple
PURPLE_DARK = "#7e22ce"
DANGER = "#f85149"
TRIM_DIM = "#000000"

user32 = ctypes.windll.user32


def show_clip_in_folder(clip_path: Path):
    if clip_path.exists():
        subprocess.Popen(["explorer.exe", "/select,", str(clip_path.resolve())])


def get_ffplay_path() -> Optional[str]:
    """Find ffplay binary on system."""
    which = shutil.which("ffplay")
    if which:
        return which
    candidates = [
        r"C:\ffmpeg\ffplay.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\MedalRea\tools\ffplay.exe"),
    ]
    for c in candidates:
        if os.path.isfile(c):
            return c
    return None


def get_ffmpeg_path() -> str:
    """Find ffmpeg binary."""
    which = shutil.which("ffmpeg")
    if which:
        return which
    candidates = [
        os.path.expandvars(r"%LOCALAPPDATA%\MedalRea\tools\ffmpeg.exe"),
        r"C:\ffmpeg\ffmpeg.exe",
    ]
    for c in candidates:
        if os.path.isfile(c):
            return c
    return "ffmpeg"


def get_clip_metadata(clip_path: Path) -> Dict[str, Any]:
    """Read the timeline length from the exported media."""
    duration = 25.0
    try:
        res = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(clip_path)],
            capture_output=True, text=True, timeout=3, creationflags=subprocess.CREATE_NO_WINDOW
        )
        duration = float(res.stdout.strip())
    except (OSError, ValueError, subprocess.TimeoutExpired):
        sidecar = metadata_path(clip_path)
        if not sidecar.exists():
            sidecar = clip_path.with_suffix(".json")
        if sidecar.exists():
            try:
                duration = float(json.loads(sidecar.read_text(encoding="utf8"))["duration"])
            except (OSError, ValueError, KeyError, TypeError):
                pass
    return {"duration": duration}


def marker_cache_path(path):
    stat = path.stat()
    key = f"spectator-v1:{path.resolve()}:{stat.st_size}:{stat.st_mtime_ns}"
    return Path("temp/kill_markers") / (hashlib.sha256(key.encode()).hexdigest() + ".json")


def read_markers(path):
    try:
        marks = json.loads(marker_cache_path(path).read_text())
        if isinstance(marks, list) and all(type(t) in (float, int) and t >= 0 for t in marks):
            return marks
    except (OSError, ValueError):
        pass
    return None


def store_markers(path, times):
    target = marker_cache_path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", dir=target.parent, delete=False) as tmp:
        json.dump(times, tmp)
    os.replace(tmp.name, target)


def scan_clip_kills(path, cancel):
    """Read local attacker entries on the exported video's own timeline."""
    import numpy as np
    from .kill_sources.screen_detector import LocalKillFeed

    feed = LocalKillFeed()
    crop = ("[0:v:0]fps=8:start_time=0,split[a][b];"
            "[a]crop=round(ih*520/1080/2)*2:round(ih*240/1080/2)*2:iw-ow:round(ih*40/1080/2)*2,scale=520:240[feed];"
            "[b]crop=round(ih*520/1080/2)*2:round(ih*32/1080/2)*2:0:round(ih*848/1080/2)*2,scale=520:32[hud];"
            "[feed][hud]vstack[out]")
    cmd = [get_ffmpeg_path(), "-v", "error", "-i", str(path), "-filter_complex", crop,
           "-map", "[out]", "-pix_fmt", "rgb24", "-f", "rawvideo", "-"]
    times = []
    frame_size = 520 * 272 * 3
    with subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                          creationflags=subprocess.CREATE_NO_WINDOW) as proc:
        try:
            index = 0
            while not cancel.is_set():
                data = proc.stdout.read(frame_size)
                if not data:
                    if proc.wait() or index == 0:
                        raise ValueError("Video decode failed")
                    return times
                frame = np.frombuffer(data, np.uint8).reshape(272, 520, 3)
                stamp = index / 8
                times.extend([stamp] * feed.update(frame[:240], stamp, frame[240:]))
                index += 1
        finally:
            if proc.poll() is None:
                proc.kill()


def draw_star_badge(canvas: tk.Canvas, cx: float, cy: float, r: float, fill_color: str = PURPLE, outline: str = "#ffffff"):
    """Draw a circular kill star badge with a crisp star in the center."""
    # Outer circular badge
    canvas.create_oval(cx - r, cy - r, cx + r, cy + r, fill=fill_color, outline=outline, width=1.5)
    # Inner star
    star_r_out = r * 0.58
    star_r_in = r * 0.28
    points = []
    for i in range(10):
        angle = i * math.pi / 5 - math.pi / 2
        rad = star_r_out if i % 2 == 0 else star_r_in
        points.extend([cx + rad * math.cos(angle), cy + rad * math.sin(angle)])
    canvas.create_polygon(points, fill="#ffffff", outline="")


class ClipPlayerView(tk.Frame):
    """Full-featured Medal.tv clip player with kill stars, filmstrip, and trimming."""

    def __init__(self, parent: tk.Widget, on_close: Callable[[], None], on_refresh_library: Optional[Callable[[], None]] = None):
        super().__init__(parent, bg=palette.BG)
        self.on_close_cb = on_close
        self.on_refresh_library_cb = on_refresh_library

        self.clip_path: Optional[Path] = None
        self.meta: Dict[str, Any] = {}
        self.duration: float = 1.0
        self.kills: List[float] = []

        self.marker_cancel = threading.Event()
        self.marker_cache = {}

        # Playback state
        self.is_playing: bool = False
        self.is_muted: bool = False
        self.current_time: float = 0.0
        self.start_play_time: float = 0.0
        self.start_seek_time: float = 0.0

        # Trimming state
        self.trim_start: float = 0.0
        self.trim_end: float = 1.0
        self.dragging_handle: Optional[str] = None  # 'start', 'end', or 'playhead'

        # ffplay process
        self.ffplay_proc: Optional[subprocess.Popen] = None
        self.ffplay_hwnd: Optional[int] = None
        self.ffplay_title: str = f"MedalRea_Player_{id(self)}"

        # Filmstrip cache
        self.filmstrip_images: List[ImageTk.PhotoImage] = []

        # Build interface
        self._build_ui()
        self.native = None

    def _build_ui(self):
        # 1. Top Header Bar
        top_bar = tk.Frame(self, bg=palette.BG, padx=20, pady=12)
        top_bar.pack(fill="x")

        # Back Button
        back_btn = RoundedButton(
            top_bar, text="← Back to Library", font=("Segoe UI", 10, "bold"),
            bg=palette.CARD_BG, fg=palette.TEXT, activebackground=palette.CARD_BORDER, activeforeground=palette.TEXT,
            relief="flat", cursor="hand2", padx=14, pady=6, command=self.close_player
        )
        back_btn.pack(side="left")

        # Title & Subtitle Container
        self.title_container = tk.Frame(top_bar, bg=palette.BG, padx=16)
        self.title_container.pack(side="left", fill="x", expand=True)

        self.clip_title_label = tk.Label(
            self.title_container, text="Loading clip...", font=("Georgia", 17, "bold"),
            fg=palette.TEXT, bg=palette.BG, anchor="w"
        )
        self.clip_title_label.pack(anchor="w")

        self.clip_meta_label = tk.Label(
            self.title_container, text="", font=("Segoe UI", 9),
            fg=palette.MUTED, bg=palette.BG, anchor="w"
        )
        self.clip_meta_label.pack(anchor="w", pady=(1, 0))

        # Top Right Actions
        right_box = tk.Frame(top_bar, bg=palette.BG)
        right_box.pack(side="right")

        RoundedButton(
            right_box, text="📁 Show in Folder", font=("Segoe UI", 9),
            bg=palette.CARD_BG, fg=palette.TEXT, activebackground=palette.CARD_BORDER, activeforeground=palette.TEXT,
            relief="flat", cursor="hand2", padx=10, pady=5, command=self._open_in_folder
        ).pack(side="left", padx=4)

        RoundedButton(
            right_box, text="📋 Copy Path", font=("Segoe UI", 9),
            bg=palette.CARD_BG, fg=palette.TEXT, activebackground=palette.CARD_BORDER, activeforeground=palette.TEXT,
            relief="flat", cursor="hand2", padx=10, pady=5, command=self._copy_path
        ).pack(side="left", padx=4)

        # 2. Central Video Player Viewport
        self.video_outer = tk.Frame(self, bg="#05070a", highlightbackground=palette.CARD_BORDER, highlightthickness=1)
        self.video_outer.pack(fill="both", expand=True, padx=20, pady=(0, 8))

        # The actual host frame for embedded ffplay
        self.video_host = tk.Frame(self.video_outer, bg="#000000")
        self.video_host.pack(fill="both", expand=True)
        self.video_host.bind("<Configure>", self._on_video_resize)
        self.video_host.bind("<Button-1>", lambda e: self.toggle_play_pause())

        # Fallback / Overlay canvas when ffplay is starting
        self.video_canvas = tk.Canvas(self.video_host, bg="#000000", highlightthickness=0)
        self.video_canvas.place(x=0, y=0, relwidth=1, relheight=1)
        self.video_canvas.bind("<Button-1>", lambda e: self.toggle_play_pause())

        # 3. Video Controls Bar (Seekbar with Kill Stars ⭐ + Buttons)
        ctrl_bar = tk.Frame(self, bg=palette.CARD_BG, padx=16, pady=8, highlightbackground=palette.CARD_BORDER, highlightthickness=1)
        ctrl_bar.pack(fill="x", padx=20, pady=(0, 8))

        # Buttons on left of seekbar
        btn_box = tk.Frame(ctrl_bar, bg=palette.CARD_BG)
        btn_box.pack(side="left", padx=(0, 12))

        self.btn_play = RoundedButton(
            btn_box, text="▶", font=("Segoe UI", 12, "bold"),
            bg=palette.ACCENT, fg=palette.TEXT, activebackground=palette.ACCENT_HOVER, activeforeground=palette.TEXT,
            relief="flat", cursor="hand2", width=3, pady=2, command=self.toggle_play_pause
        )
        self.btn_play.pack(side="left")

        self.time_label = tk.Label(
            btn_box, text="00:00 / 00:00", font=("Segoe UI", 10, "bold"),
            fg=palette.TEXT, bg=palette.CARD_BG, padx=10
        )
        self.time_label.pack(side="left")

        self.btn_vol = RoundedButton(
            btn_box, text="🔊", font=("Segoe UI", 10),
            bg=palette.CARD_BG, fg=palette.TEXT, activebackground=palette.CARD_BORDER, activeforeground=palette.TEXT,
            relief="flat", cursor="hand2", padx=6, pady=2, command=self.toggle_mute
        )
        self.btn_vol.pack(side="left")

        # Kill Star Seekbar (Canvas)
        self.seekbar = tk.Canvas(ctrl_bar, height=32, bg=palette.CARD_BG, highlightthickness=0, cursor="hand2")
        self.seekbar.pack(side="left", fill="x", expand=True, padx=8)
        self.seekbar.bind("<Configure>", lambda e: self._redraw_seekbar())
        self.seekbar.bind("<Button-1>", self._on_seekbar_click)
        self.seekbar.bind("<B1-Motion>", self._on_seekbar_drag)

        # Right side branding / fullscreen
        right_ctrl = tk.Frame(ctrl_bar, bg=palette.CARD_BG)
        right_ctrl.pack(side="right", padx=(8, 0))

        tk.Label(right_ctrl, text="Thot Clip", font=("Segoe UI", 9, "bold"), fg=palette.LINK, bg=palette.CARD_BG).pack(side="left", padx=(0, 8))

        # 4. Extended Timeline & Filmstrip Section
        timeline_box = tk.Frame(self, bg=palette.CARD_BG, padx=16, pady=10, highlightbackground=palette.CARD_BORDER, highlightthickness=1)
        timeline_box.pack(fill="x", padx=20, pady=(0, 8))

        # Timeline Header / Ruler Canvas
        self.ruler_canvas = tk.Canvas(timeline_box, height=20, bg=palette.CARD_BG, highlightthickness=0)
        self.ruler_canvas.pack(fill="x", pady=(0, 2))
        self.ruler_canvas.bind("<Configure>", lambda e: self._redraw_ruler())

        # Filmstrip & Waveform Canvas (holds thumbnails, audio wave, trim handles, playhead)
        self.filmstrip_canvas = tk.Canvas(timeline_box, height=54, bg="#10141b", highlightthickness=1, highlightbackground="#2d3748", cursor="crosshair")
        self.filmstrip_canvas.pack(fill="x")
        self.filmstrip_canvas.bind("<Configure>", lambda e: self._redraw_filmstrip())
        self.filmstrip_canvas.bind("<Button-1>", self._on_filmstrip_click)
        self.filmstrip_canvas.bind("<B1-Motion>", self._on_filmstrip_drag)
        self.filmstrip_canvas.bind("<ButtonRelease-1>", self._on_filmstrip_release)

        # 5. Bottom Action Bar
        bot_bar = tk.Frame(self, bg=palette.BG, padx=20, pady=8)
        bot_bar.pack(fill="x")

        # Audio Track Indicator
        self.audio_pill = tk.Label(
            bot_bar, text="🔊 Game + Mic Mixed", font=("Segoe UI", 9, "bold"),
            fg=palette.LINK, bg=palette.CARD_BG, padx=10, pady=4, relief="flat"
        )
        self.audio_pill.pack(side="left")

        # Shortcuts guide
        hint = tk.Label(
            bot_bar, text="[Space] Play/Pause  •  [←] [→] Seek 5s  •  Drag [ ] to trim  •  Click ⭐ to jump to Kill",
            font=("Segoe UI", 9), fg=palette.MUTED, bg=palette.BG
        )
        hint.pack(side="left", padx=20)

        # Right: Trim stats & Save Edits button
        right_bot = tk.Frame(bot_bar, bg=palette.BG)
        right_bot.pack(side="right")

        self.trim_stats_label = tk.Label(
            right_bot, text="Trim: 00:00 - 00:00 (0.0s)", font=("Segoe UI", 9),
            fg=palette.MUTED, bg=palette.BG, padx=8
        )
        self.trim_stats_label.pack(side="left")

        self.btn_save_edits = RoundedButton(
            right_bot, text="✂️  Save Edits", font=("Segoe UI", 10, "bold"),
            bg=palette.ACCENT, fg=palette.TEXT, activebackground=palette.ACCENT_HOVER, activeforeground=palette.TEXT,
            relief="flat", cursor="hand2", padx=16, pady=5, command=self.save_edits
        )
        self.btn_save_edits.pack(side="left")

    def _bind_keys(self):
        # Keyboard navigation
        self.bind_all("<space>", self._on_key_space)
        self.bind_all("<Left>", self._on_key_left)
        self.bind_all("<Right>", self._on_key_right)
        self.bind_all("<Escape>", lambda e: self.close_player())

    def _unbind_keys(self):
        self.unbind_all("<space>")
        self.unbind_all("<Left>")
        self.unbind_all("<Right>")
        self.unbind_all("<Escape>")

    # -------------------------------------------------------------
    # Loading & Initializing Clip
    # -------------------------------------------------------------
    def load_clip(self, clip_path: Path):
        """Load and display the given video clip."""
        self.stop_playback()
        self._bind_keys()
        self.focus_set()
        self.clip_path = clip_path
        self.meta = get_clip_metadata(clip_path)
        self.duration = max(0.5, float(self.meta.get("duration", 25.0)))
        self.kills = []

        # Trimming boundaries
        self.trim_start = 0.0
        self.trim_end = self.duration
        self.current_time = 0.0

        # Update headers
        self.clip_title_label.configure(text=clip_path.name)
        self._load_markers(clip_path)
        self._update_trim_label()

        # Update canvases
        self._redraw_seekbar()
        self._redraw_ruler()
        self._redraw_filmstrip()

        # Generate filmstrip thumbnails asynchronously
        self._start_filmstrip_generator()

        # Mount video player
        self.start_playback()

    def _load_markers(self, path):
        self.marker_cancel.set()
        cancel = self.marker_cancel = threading.Event()
        stat = path.stat()
        key = (path.resolve(), stat.st_size, stat.st_mtime_ns)

        def display(times):
            if cancel.is_set():
                return
            if times is None:
                detail = "Kill markers unavailable"
            else:
                self.marker_cache[key] = times
                self.kills = times
                detail = f"{len(times)} personal kill{'s' if len(times) != 1 else ''}"
                if times:
                    detail += " • " + ", ".join(f"K{i} {t:.2f}s" for i, t in enumerate(times, 1))
            self.clip_meta_label.configure(text=f"{self.duration:.1f}s • {detail}")
            self._redraw_seekbar()
            self._redraw_filmstrip()

        cached = read_markers(path)
        if cached is not None:
            display(cached)
            return
        if key in self.marker_cache:
            display(self.marker_cache[key])
            return
        self.clip_meta_label.configure(text=f"{self.duration:.1f}s • Checking kill feed…")

        def work():
            try:
                times = scan_clip_kills(path, cancel)
                if times is not None:
                    try:
                        store_markers(path, times)
                    except OSError:
                        pass
            except Exception:
                times = None
            if not cancel.is_set():
                self.after(0, display, times)

        threading.Thread(target=work, daemon=True).start()

    def leave_player(self):
        self.marker_cancel.set()
        self._unbind_keys()
        self.stop_playback()

    def start_playback(self):
        """Start ffplay embedded into video_host."""
        self.stop_playback()
        if not self.clip_path or not self.clip_path.exists():
            return

        from .vlc_player import VlcPlayer
        try:
            self.native = VlcPlayer(self.clip_path, self.video_host.winfo_id())
        except OSError:
            self.native = None
        if self.native:
            self.video_canvas.place_forget()
            self.pending_seek = None
            self.pending_mute = True
            self.current_time = 0
            self.is_playing = True
            self.btn_play.configure(text="⏸")
            self.native.dll.libvlc_audio_set_mute(self.native.player, self.is_muted)
            self.after(100, self._native_tick, self.native)
            return

        ffplay = get_ffplay_path()
        if not ffplay:
            self._render_fallback_view("ffplay not found on system. Showing preview.")
            return

        w = self.video_host.winfo_width() or 720
        h = self.video_host.winfo_height() or 405

        cmd = [
            ffplay,
            "-noborder", "-left", "-32000", "-top", "-32000",
            "-window_title", self.ffplay_title,
            "-x", str(w),
            "-y", str(h),
            "-autoexit",
            str(self.clip_path.resolve()),
        ]

        try:
            self.ffplay_proc = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW
            )
            self.current_time = 0.0
            self._embed_player(self.ffplay_proc)
        except Exception as exc:
            self._render_fallback_view(f"Failed to play video: {exc}")

    def _embed_player(self, proc, attempts=100):
        if proc is not self.ffplay_proc:
            return
        hwnd = user32.FindWindowW(None, self.ffplay_title)
        if not hwnd and attempts and proc.poll() is None:
            self.after(50, self._embed_player, proc, attempts - 1)
            return
        parent = self.video_host.winfo_id()
        if hwnd:
            style = user32.GetWindowLongW(hwnd, -16)
            user32.SetWindowLongW(hwnd, -16, (style & 0x7fffffff) | 0x40000000)
            user32.SetParent(hwnd, parent)
        if not hwnd or user32.GetParent(hwnd) != parent:
            self.stop_playback()
            self._render_fallback_view("Could not open the video inside the editor. Click to retry.")
            return
        self.ffplay_hwnd = hwnd
        self.video_canvas.place_forget()
        user32.MoveWindow(hwnd, 0, 0, self.video_host.winfo_width(), self.video_host.winfo_height(), True)
        self.is_playing = True
        self.start_play_time = time.time()
        self.start_seek_time = 0.0
        self.btn_play.configure(text="⏸")
        self._playback_tick(proc)

    def _native_tick(self, backend):
        if backend is not self.native:
            return
        state = backend.dll.libvlc_media_player_get_state(backend.player)
        if state in (6, 7):
            self.is_playing = False
            self.btn_play.configure(text="▶")
            if state == 6:
                self.current_time = self.duration
            else:
                self._render_fallback_view("Playback failed. Click to retry.")
        elif state in (3, 4):
            if self.pending_mute:
                backend.dll.libvlc_audio_set_mute(backend.player, self.is_muted)
                self.pending_mute = False
            if self.pending_seek is not None:
                backend.dll.libvlc_media_player_set_time(backend.player, self.pending_seek)
                self.pending_seek = None
            if (state == 3) != self.is_playing:
                backend.dll.libvlc_media_player_set_pause(backend.player, int(not self.is_playing))
            self.current_time = max(0, backend.dll.libvlc_media_player_get_time(backend.player) / 1000)
        self._update_time_label()
        self._redraw_seekbar()
        self._redraw_playhead()
        if state not in (6, 7):
            self.after(100, self._native_tick, backend)

    def stop_playback(self):
        """Terminate ffplay cleanly."""
        self.is_playing = False
        if self.native:
            backend, self.native = self.native, None
            backend.close()
        self.ffplay_hwnd = None
        if self.ffplay_proc:
            try:
                self.ffplay_proc.kill()
                self.ffplay_proc.wait(timeout=0.5)
            except Exception:
                pass
            self.ffplay_proc = None
        if hasattr(self, "btn_play"):
            self.btn_play.configure(text="▶")

    def toggle_play_pause(self):
        """Toggle playback via ffplay spacebar message."""
        if self.native:
            backend = self.native
            if backend.dll.libvlc_media_player_get_state(backend.player) in (6, 7):
                self.start_playback()
                return
            self.is_playing = not self.is_playing
            backend.dll.libvlc_media_player_set_pause(backend.player, int(not self.is_playing))
            self.btn_play.configure(text="⏸" if self.is_playing else "▶")
            return
        if not self.ffplay_proc or self.ffplay_proc.poll() is not None:
            # Restart
            self.start_playback()
            return

        if self.ffplay_hwnd:
            VK_SPACE = 0x20
            WM_KEYDOWN = 0x0100
            WM_KEYUP = 0x0101
            user32.PostMessageW(self.ffplay_hwnd, WM_KEYDOWN, VK_SPACE, 0)
            user32.PostMessageW(self.ffplay_hwnd, WM_KEYUP, VK_SPACE, 0)

        self.is_playing = not self.is_playing
        if self.is_playing:
            self.btn_play.configure(text="⏸")
            self.start_play_time = time.time()
            self.start_seek_time = self.current_time
        else:
            self.btn_play.configure(text="▶")

    def toggle_mute(self):
        """Toggle audio mute via ffplay 'm' key."""
        if self.native:
            self.native.dll.libvlc_audio_set_mute(self.native.player, int(not self.is_muted))
        if self.ffplay_hwnd:
            WM_CHAR = 0x0102
            user32.PostMessageW(self.ffplay_hwnd, WM_CHAR, ord('m'), 0)
        self.is_muted = not self.is_muted
        self.btn_vol.configure(text="🔇" if self.is_muted else "🔊")

    def seek_to(self, target_sec: float):
        """Seek video directly to target second."""
        target_sec = max(0.0, min(self.duration, target_sec))
        if self.native:
            if self.native.dll.libvlc_media_player_get_state(self.native.player) == 6:
                self.start_playback()
            if self.native.dll.libvlc_media_player_get_state(self.native.player) in (3, 4):
                self.native.dll.libvlc_media_player_set_time(self.native.player, int(target_sec * 1000))
            else:
                self.pending_seek = int(target_sec * 1000)
        self.current_time = target_sec
        self.start_play_time = time.time()
        self.start_seek_time = target_sec

        if self.ffplay_hwnd and self.duration > 0:
            frac = target_sec / self.duration
            w = self.video_host.winfo_width() or 640
            h = self.video_host.winfo_height() or 360
            x = int(w * frac)
            y = h // 2
            lparam = (y << 16) | x
            WM_RBUTTONDOWN = 0x0204
            WM_RBUTTONUP = 0x0205
            user32.PostMessageW(self.ffplay_hwnd, WM_RBUTTONDOWN, 0x0002, lparam)
            user32.PostMessageW(self.ffplay_hwnd, WM_RBUTTONUP, 0, lparam)

        self._redraw_seekbar()
        self._redraw_filmstrip()
        self._update_time_label()

    def _playback_tick(self, proc):
        """Update timeline playhead smoothly while playing."""
        if proc is not self.ffplay_proc or not self.winfo_exists():
            return
        if self.is_playing:
            now = time.time()
            self.current_time = self.start_seek_time + (now - self.start_play_time)
            if self.current_time >= self.duration:
                self.current_time = self.duration
                self.is_playing = False
                self.btn_play.configure(text="▶")

            self._redraw_seekbar()
            self._redraw_filmstrip()
            self._update_time_label()

        if self.ffplay_proc and self.ffplay_proc.poll() is None:
            self.after(35, self._playback_tick, proc)
        else:
            if self.is_playing:
                self.is_playing = False
                self.btn_play.configure(text="▶")

    def _on_video_resize(self, event):
        """Resize embedded ffplay window when frame resizes."""
        if self.ffplay_hwnd and event.width > 10 and event.height > 10:
            user32.MoveWindow(self.ffplay_hwnd, 0, 0, event.width, event.height, True)

    def _render_fallback_view(self, text: str):
        """Display fallback message on canvas if ffplay is absent."""
        self.video_canvas.delete("all")
        w = self.video_host.winfo_width() or 640
        h = self.video_host.winfo_height() or 360
        self.video_canvas.create_text(
            w // 2, h // 2, text=text,
            font=("Segoe UI", 12), fill=palette.MUTED
        )
        self.video_canvas.place(x=0, y=0, relwidth=1, relheight=1)

    # -------------------------------------------------------------
    # Seekbar & Kill Stars Rendering
    # -------------------------------------------------------------
    def _redraw_seekbar(self):
        c = self.seekbar
        c.delete("all")
        w = c.winfo_width() or 600
        h = c.winfo_height() or 32
        if w < 10:
            return

        cy = h // 2
        bar_h = 6
        pad_x = 14
        active_w = max(1, w - (pad_x * 2))

        # Background track
        c.create_rectangle(pad_x, cy - bar_h // 2, pad_x + active_w, cy + bar_h // 2,
                           fill="#21262d", outline="", width=0)

        # Progress fill
        frac = min(1.0, max(0.0, self.current_time / self.duration))
        progress_x = pad_x + int(active_w * frac)
        c.create_rectangle(pad_x, cy - bar_h // 2, progress_x, cy + bar_h // 2,
                           fill=palette.ACCENT, outline="", width=0)

        # Draw Kill Stars ⭐ on the seekbar
        for i, k_sec in enumerate(self.kills):
            k_frac = min(1.0, max(0.0, k_sec / self.duration))
            kx = pad_x + int(active_w * k_frac)
            # Distinctive circular purple star badge
            draw_star_badge(c, kx, cy, r=10, fill_color=PURPLE, outline="#ffffff")
            # Number badge above star
            c.create_text(kx, cy - 14, text=f"K{i+1}", font=("Segoe UI", 7, "bold"), fill=PURPLE)

        # Playhead handle (circle)
        c.create_oval(progress_x - 5, cy - 5, progress_x + 5, cy + 5, fill="#ffffff", outline=palette.ACCENT, width=2)

    def _on_seekbar_click(self, event):
        w = self.seekbar.winfo_width() or 600
        pad_x = 14
        active_w = max(1, w - (pad_x * 2))
        x = min(max(pad_x, event.x), pad_x + active_w)
        frac = (x - pad_x) / active_w

        # Check if clicked near a kill star
        for k_sec in self.kills:
            k_x = pad_x + int(active_w * (k_sec / self.duration))
            if abs(event.x - k_x) <= 12:
                self.seek_to(k_sec)
                return

        self.seek_to(frac * self.duration)

    def _on_seekbar_drag(self, event):
        w = self.seekbar.winfo_width() or 600
        pad_x = 14
        active_w = max(1, w - (pad_x * 2))
        x = min(max(pad_x, event.x), pad_x + active_w)
        frac = (x - pad_x) / active_w
        self.seek_to(frac * self.duration)

    # -------------------------------------------------------------
    # Extended Timeline Ruler & Filmstrip
    # -------------------------------------------------------------
    def _redraw_ruler(self):
        c = self.ruler_canvas
        c.delete("all")
        w = c.winfo_width() or 700
        h = c.winfo_height() or 20
        pad_x = 14
        active_w = max(1, w - (pad_x * 2))

        # Interval calculation (e.g. 5s or 10s steps)
        step_sec = 5.0 if self.duration <= 35 else (10.0 if self.duration <= 90 else 20.0)
        t = 0.0
        while t <= self.duration:
            frac = t / self.duration
            x = pad_x + int(active_w * frac)
            # Tick line
            c.create_line(x, h - 6, x, h, fill=palette.DIM, width=1)
            # Label
            c.create_text(x, h - 12, text=self._format_time(t), font=("Segoe UI", 8), fill=palette.MUTED)
            t += step_sec

    def _redraw_filmstrip(self):
        c = self.filmstrip_canvas
        c.delete("all")
        w = c.winfo_width() or 700
        h = c.winfo_height() or 54
        pad_x = 14
        active_w = max(1, w - (pad_x * 2))

        # Draw filmstrip images if loaded
        if self.filmstrip_images:
            num_imgs = len(self.filmstrip_images)
            slot_w = active_w / num_imgs
            for i, photo in enumerate(self.filmstrip_images):
                ix = pad_x + int(i * slot_w)
                c.create_image(ix, 0, image=photo, anchor="nw")
                c.create_line(ix, 0, ix, h, fill="#1f2937", width=1)
        else:
            # Placeholder dark background
            c.create_rectangle(pad_x, 0, pad_x + active_w, h, fill="#121721", outline="")

        # Overlaid audio waveform (stylized smooth wave)
        wave_pts = []
        cy = h // 2 + 6
        segments = 60
        for seg in range(segments + 1):
            sx = pad_x + int(active_w * (seg / segments))
            # Amplitude fluctuates, peaking around kill timestamps
            base_amp = math.sin(seg * 0.45) * 4 + math.cos(seg * 0.9) * 3
            # Boost wave near kills
            cur_s = (seg / segments) * self.duration
            for k in self.kills:
                dist = abs(cur_s - k)
                if dist < 3.0:
                    base_amp += math.sin(seg * 2.0) * (14.0 * (1.0 - dist / 3.0))
            sy = max(6, min(h - 6, cy + base_amp))
            wave_pts.extend([sx, sy])

        if len(wave_pts) >= 4:
            c.create_line(wave_pts, fill="#ffffff", width=1.5, smooth=True)

        # Draw vertical lines & Star Pins for kills along the filmstrip
        for i, k_sec in enumerate(self.kills):
            frac = min(1.0, max(0.0, k_sec / self.duration))
            kx = pad_x + int(active_w * frac)
            # Dashed vertical highlight line
            c.create_line(kx, 12, kx, h, fill=PURPLE, dash=(3, 2), width=1.5)
            # Star badge pinned at the top
            draw_star_badge(c, kx, 10, r=8, fill_color=PURPLE, outline="#ffffff")

        # Trim dimming masks (regions before trim_start and after trim_end)
        start_x = pad_x + int(active_w * (self.trim_start / self.duration))
        end_x = pad_x + int(active_w * (self.trim_end / self.duration))

        if start_x > pad_x:
            c.create_rectangle(pad_x, 0, start_x, h, fill=TRIM_DIM, stipple="gray50", outline="")
        if end_x < pad_x + active_w:
            c.create_rectangle(end_x, 0, pad_x + active_w, h, fill=TRIM_DIM, stipple="gray50", outline="")

        # Trim Handles
        # Left handle
        c.create_rectangle(start_x - 4, 0, start_x, h, fill=palette.ACCENT, outline="")
        c.create_polygon([start_x, 0, start_x + 8, 0, start_x, 8], fill=palette.ACCENT, outline="")
        # Right handle
        c.create_rectangle(end_x, 0, end_x + 4, h, fill=palette.ACCENT, outline="")
        c.create_polygon([end_x, 0, end_x - 8, 0, end_x, 8], fill=palette.ACCENT, outline="")

        self._redraw_playhead()

    def _redraw_playhead(self):
        c = self.filmstrip_canvas
        c.delete("playhead")
        pad_x = 14
        active_w = max(1, c.winfo_width() - 2 * pad_x)
        h = c.winfo_height()
        cur_frac = min(1.0, max(0.0, self.current_time / self.duration))
        playhead_x = pad_x + int(active_w * cur_frac)
        c.create_line(playhead_x, 0, playhead_x, h, tags="playhead", fill=palette.ACCENT, width=2)
        # Pill top
        c.create_rectangle(playhead_x - 14, 0, playhead_x + 14, 14, tags="playhead", fill=palette.ACCENT, outline="")
        c.create_text(playhead_x, 7, text=self._format_time(self.current_time), font=("Segoe UI", 7, "bold"), tags="playhead", fill=palette.TEXT)

    def _on_filmstrip_click(self, event):
        w = self.filmstrip_canvas.winfo_width() or 700
        pad_x = 14
        active_w = max(1, w - (pad_x * 2))
        x = min(max(pad_x, event.x), pad_x + active_w)
        frac = (x - pad_x) / active_w
        click_t = frac * self.duration

        start_x = pad_x + int(active_w * (self.trim_start / self.duration))
        end_x = pad_x + int(active_w * (self.trim_end / self.duration))

        # Check if clicking trim handles
        if abs(event.x - start_x) <= 8:
            self.dragging_handle = "start"
            return
        if abs(event.x - end_x) <= 8:
            self.dragging_handle = "end"
            return

        # Check if clicking kill star
        for k in self.kills:
            kx = pad_x + int(active_w * (k / self.duration))
            if abs(event.x - kx) <= 12:
                self.seek_to(k)
                return

        # Otherwise scrub playhead
        self.dragging_handle = "playhead"
        self.seek_to(click_t)

    def _on_filmstrip_drag(self, event):
        if not self.dragging_handle:
            return
        w = self.filmstrip_canvas.winfo_width() or 700
        pad_x = 14
        active_w = max(1, w - (pad_x * 2))
        x = min(max(pad_x, event.x), pad_x + active_w)
        frac = min(1.0, max(0.0, (x - pad_x) / active_w))
        drag_t = round(frac * self.duration, 2)

        if self.dragging_handle == "start":
            self.trim_start = min(drag_t, self.trim_end - 1.0)
            self._update_trim_label()
            self._redraw_filmstrip()
        elif self.dragging_handle == "end":
            self.trim_end = max(drag_t, self.trim_start + 1.0)
            self._update_trim_label()
            self._redraw_filmstrip()
        elif self.dragging_handle == "playhead":
            self.seek_to(drag_t)

    def _on_filmstrip_release(self, event):
        self.dragging_handle = None

    # -------------------------------------------------------------
    # Filmstrip Background Generator
    # -------------------------------------------------------------
    def _start_filmstrip_generator(self):
        """Extract small thumbnail frames across the clip asynchronously."""
        self.filmstrip_images.clear()
        clip = self.clip_path
        cancel = self.marker_cancel

        def work():
            if not self.clip_path or not self.clip_path.exists():
                return
            ffmpeg = get_ffmpeg_path()
            num_frames = 8
            step = self.duration / max(1, num_frames)
            photos = []

            for i in range(num_frames):
                if cancel.is_set():
                    return
                seek_t = i * step + 0.5
                cmd = [
                    ffmpeg, "-y", "-ss", f"{seek_t:.2f}",
                    "-i", str(clip),
                    "-vframes", "1",
                    "-vf", "scale=120:54",
                    "-f", "image2pipe",
                    "-vcodec", "png",
                    "-"
                ]
                try:
                    res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=2,
                                         creationflags=subprocess.CREATE_NO_WINDOW)
                    if res.stdout:
                        import io
                        img = Image.open(io.BytesIO(res.stdout))
                        photos.append(img)
                except Exception:
                    pass

            def update_ui():
                if cancel.is_set() or not self.winfo_exists():
                    return
                self.filmstrip_images = [ImageTk.PhotoImage(im) for im in photos]
                self._redraw_filmstrip()

            self.after(0, update_ui)

        threading.Thread(target=work, daemon=True).start()

    # -------------------------------------------------------------
    # Keyboard & UI Actions
    # -------------------------------------------------------------
    def _on_key_space(self, event):
        if not isinstance(event.widget, ttk.Button):
            self.toggle_play_pause()

    def _on_key_left(self, event):
        self.seek_to(self.current_time - 5.0)

    def _on_key_right(self, event):
        self.seek_to(self.current_time + 5.0)

    def _format_time(self, seconds: float) -> str:
        s = int(seconds)
        m = s // 60
        sec = s % 60
        return f"{m:02d}:{sec:02d}"

    def _update_time_label(self):
        cur = self._format_time(self.current_time)
        dur = self._format_time(self.duration)
        self.time_label.configure(text=f"{cur} / {dur}")

    def _update_trim_label(self):
        s_str = self._format_time(self.trim_start)
        e_str = self._format_time(self.trim_end)
        diff = max(0.0, self.trim_end - self.trim_start)
        self.trim_stats_label.configure(text=f"Trim: {s_str} - {e_str} ({diff:.1f}s)")

    def _open_in_folder(self):
        if self.clip_path:
            show_clip_in_folder(self.clip_path)

    def _copy_path(self):
        if self.clip_path:
            self.clipboard_clear()
            self.clipboard_append(str(self.clip_path.resolve()))
            messagebox.showinfo("Copied", f"Copied path to clipboard:\n{self.clip_path.name}", parent=self)

    def save_edits(self):
        """Trim clip between trim_start and trim_end with stream copy."""
        if not self.clip_path or not self.clip_path.exists():
            return

        diff = self.trim_end - self.trim_start
        if diff < 1.0:
            messagebox.showwarning("Trim Clip", "Trim range must be at least 1.0 second.", parent=self)
            return

        # Check if full clip
        if abs(self.trim_start - 0.0) < 0.2 and abs(self.trim_end - self.duration) < 0.2:
            messagebox.showinfo("Save Edits", "Trim range covers the entire clip. No trimming needed.", parent=self)
            return

        out_name = f"{self.clip_path.stem}_trimmed.mp4"
        out_path = self.clip_path.parent / out_name

        self.btn_save_edits.configure(text="⏳ Saving...", state="disabled")
        self.stop_playback()

        def work():
            ffmpeg = get_ffmpeg_path()
            cmd = [
                ffmpeg, "-y",
                "-ss", f"{self.trim_start:.2f}",
                "-to", f"{self.trim_end:.2f}",
                "-i", str(self.clip_path),
                "-c", "copy",
                str(out_path)
            ]
            try:
                subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True,
                               creationflags=subprocess.CREATE_NO_WINDOW)
                # Compute adjusted kills for trimmed clip
                adj_kills = [
                    round(k - self.trim_start, 2)
                    for k in self.kills
                    if self.trim_start <= k <= self.trim_end
                ]
                sidecar = {
                    "clip_name": out_name,
                    "duration": round(diff, 2),
                    "kill_count": len(adj_kills),
                    "kills": adj_kills,
                }
                metadata_path(out_path, create=True).write_text(json.dumps(sidecar, indent=2), encoding="utf-8")
                success = True
                msg = f"Trimmed clip saved successfully:\n{out_name}"
            except Exception as exc:
                success = False
                msg = f"Failed to save trimmed clip: {exc}"

            def done():
                self.btn_save_edits.configure(text="✂️  Save Edits", state="normal")
                if success:
                    messagebox.showinfo("Save Edits", msg, parent=self)
                    if self.on_refresh_library_cb:
                        self.on_refresh_library_cb()
                    self.load_clip(out_path)
                else:
                    messagebox.showerror("Save Edits", msg, parent=self)

            self.after(0, done)

        threading.Thread(target=work, daemon=True).start()

    def close_player(self):
        """Stop playback and return to previous view."""
        self.stop_playback()
        self._unbind_keys()
        self.on_close_cb()
