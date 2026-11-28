"""Tkinter control panel and clips library for ThortClip."""
import ctypes
from ctypes import wintypes
import json
from .clip_metadata import metadata_path
import math
import os
import queue
import re
import shutil
import subprocess
import threading
import time
import customtkinter as ctk
import tkinter as tk
from .theme import palette
from tkinter import ttk, messagebox, filedialog
from pathlib import Path
from typing import Optional, List, Dict, Any

from .ui_controls import RoundedButton, style_dropdowns

from PIL import Image, ImageTk

from .audio_manager import list_playback_devices, list_microphone_devices, test_microphone_level
from .camera_manager import list_camera_devices, capture_camera_frame, is_virtual_camera_name
from .clip_player import read_markers, show_clip_in_folder
from .launcher_job import contain_launcher
from .main import ThortClipApp
from .thumbnail_cache import ThumbnailManager

# Modern Medal.tv Dark Palette
RED = "#ff4d4f"
BLUE = "#3b82f6"


def format_bytes(bytes_count: int) -> str:
    if bytes_count < 1024:
        return f"{bytes_count} B"
    elif bytes_count < 1024 * 1024:
        return f"{bytes_count / 1024:.1f} KB"
    elif bytes_count < 1024 * 1024 * 1024:
        return f"{bytes_count / (1024 * 1024):.1f} MB"
    else:
        return f"{bytes_count / (1024 * 1024 * 1024):.2f} GB"


class ThortClipToggle(tk.Canvas):
    """Custom animated-style toggle switch widget."""
    def __init__(self, parent, initial_value: bool = False, command=None, width=44, height=24, **kwargs):
        super().__init__(parent, width=width, height=height, bg=parent["bg"], highlightthickness=0, cursor="hand2", **kwargs)
        self.value = initial_value
        self.command = command
        self.w = width
        self.h = height
        self.r = height // 2
        self.bind("<Button-1>", self._toggle)
        self.redraw()

    def _toggle(self, event=None):
        self.value = not self.value
        self.redraw()
        if self.command:
            self.command(self.value)

    def set_value(self, val: bool):
        if self.value != val:
            self.value = val
            self.redraw()

    def redraw(self):
        self.delete("all")
        bg_color = palette.ACCENT if self.value else "#ACA99F"
        # Pill track
        self.create_oval(1, 1, self.h - 1, self.h - 1, fill=bg_color, outline="")
        self.create_oval(self.w - self.h + 1, 1, self.w - 1, self.h - 1, fill=bg_color, outline="")
        self.create_rectangle(self.r, 1, self.w - self.r, self.h - 1, fill=bg_color, outline="")
        # Circle thumb
        thumb_pad = 3
        thumb_d = self.h - (thumb_pad * 2)
        if self.value:
            tx = self.w - thumb_pad - thumb_d
        else:
            tx = thumb_pad
        self.create_oval(tx, thumb_pad, tx + thumb_d, thumb_pad + thumb_d, fill="#ffffff" if self.value else palette.MUTED, outline="")


class RecorderWindow:
    def __init__(self, root: tk.Tk, app: ThortClipApp):
        self.root = root
        self.app = app
        self.events = queue.SimpleQueue()
        self.busy = False
        self.notice = ""
        self.last_state = None
        self.active_view = "library"
        self._clip_meta_cache: Dict[str, dict] = {}
        self.thumbnails = ThumbnailManager()
        self.preview_stream = None
        self.preview_timer = None
        self.camera_snapshot_img = None
        self.camera_snapshot_photo = None

        root.title("ThortClip")
        root.iconbitmap(str(Path(__file__).with_name("assets") / "thortclip.ico"))
        root.geometry("1060x680")
        root.minsize(880, 560)
        root.update_idletasks()

        # Disable DWM window transitions for the library window only.
        frame = wintypes.HWND(int(root.tk.call("wm", "frame", root._w), 0))
        disabled = wintypes.BOOL(True)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            frame, 3, ctypes.byref(disabled), ctypes.sizeof(disabled)
        )

        self.checking_update = False
        self.manual_update = False
        self.startup_update = False
        self.pending_update = None
        self.build_interface("library")
        self.repaint_pending = None
        root.bind("<Configure>", self._schedule_repaint, add="+")
        root.bind("<Activate>", self._schedule_repaint, add="+")
        root.bind("<Map>", self._schedule_repaint, add="+")
        root.protocol("WM_DELETE_WINDOW", self.close)
        self.refresh()
        if os.environ.get("THORTCLIP_BUNDLE_TOOLS"):
            self.root.after(2500, lambda: self.check_update(startup=True))

    def _schedule_repaint(self, event):
        if event.widget is not self.root:
            return
        if self.repaint_pending is not None:
            self.root.after_cancel(self.repaint_pending)
        self.repaint_pending = self.root.after(80, self._repaint_window)

    def _repaint_window(self):
        self.repaint_pending = None
        if self.root.winfo_viewable():
            # Invalidate the native frame and all Tk child windows on restore.
            user32 = ctypes.windll.user32
            client = ctypes.c_void_p(self.root.winfo_id())
            get_parent = user32.GetParent
            get_parent.restype = ctypes.c_void_p
            frame = ctypes.c_void_p(get_parent(client) or client.value)
            # RDW_INVALIDATE | RDW_ERASE | RDW_FRAME | RDW_ALLCHILDREN
            user32.RedrawWindow(frame, None, None, 0x485)

    def build_interface(self, view_name):
        root = self.root
        palette.select(self.app.config.dark_mode)
        ctk.set_appearance_mode("dark" if palette.dark else "light")
        root.configure(bg=palette.BG)
        # Modern Dark Theme for ttk widgets
        try:
            style = ttk.Style()
            style.theme_use("clam")
            style.configure(
                "Vertical.TScrollbar",
                gripcount=0,
                background=palette.TRACK,
                darkcolor=palette.TRACK,
                lightcolor=palette.TRACK,
                troughcolor=palette.BG,
                bordercolor=palette.BG,
                arrowcolor=palette.MUTED
            )
            style.map("Vertical.TScrollbar", background=[("active", palette.TRACK_HOVER)])
            style_dropdowns(root, style)
        except Exception:
            pass

        # Dark Titlebar for Windows 10/11
        try:
            hwnd = ctypes.windll.user32.GetParent(root.winfo_id()) or root.winfo_id()
            dark = ctypes.c_int(int(palette.dark))
            ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(dark), 4)
        except Exception:
            pass

        # Outer Container
        self.shell = tk.Frame(root, bg=palette.BG)
        self.shell.pack(fill="both", expand=True)

        # Left Navigation Sidebar
        self.build_sidebar()

        # Right Main Column
        self.main_column = tk.Frame(self.shell, bg=palette.BG)
        self.main_column.pack(side="left", fill="both", expand=True)

        # Top Header Bar
        self.build_topbar()

        # Content Viewports Container
        self.view_container = tk.Frame(self.main_column, bg=palette.BG)
        self.view_container.pack(fill="both", expand=True)

        # Views
        self.views = {}
        self.build_library_view()
        self.build_dashboard_view()
        self.build_overlay_view()
        self.build_audio_view()
        self.build_settings_view()
        self.build_player_view()

        # Default View
        self.show_view(view_name)

    # ---------------------------------------------------------
    # Sidebar
    # ---------------------------------------------------------
    def build_sidebar(self):
        self.sidebar = tk.Frame(self.shell, bg=palette.CARD_BG, width=220)
        self.sidebar.pack(side="left", fill="y")
        self.sidebar.pack_propagate(False)

        tk.Frame(self.shell, bg=palette.CARD_BORDER, width=1).pack(side="left", fill="y")

        # Brand Header
        brand = tk.Frame(self.sidebar, bg=palette.CARD_BG, padx=18, pady=20)
        brand.pack(fill="x")

        with Image.open(Path(__file__).with_name("assets") / "thortclip.png") as logo:
            self.brand_icon = ImageTk.PhotoImage(logo.resize((36, 36), Image.Resampling.LANCZOS))
        tk.Label(brand, image=self.brand_icon, bg=palette.CARD_BG).pack(side="left", padx=(0, 10))

        title_box = tk.Frame(brand, bg=palette.CARD_BG)
        title_box.pack(side="left", fill="y")
        tk.Label(title_box, text="ThortClip", font=("Georgia", 17, "bold"), fg=palette.TEXT, bg=palette.CARD_BG).pack(anchor="w")
        tk.Label(title_box, text="VALORANT", font=("Segoe UI", 8, "bold"), fg=palette.MUTED, bg=palette.CARD_BG).pack(anchor="w")

        # Nav Section Header
        nav_label = tk.Label(self.sidebar, text="MENU", font=("Segoe UI", 8, "bold"), fg=palette.DIM, bg=palette.CARD_BG, padx=20, pady=8)
        nav_label.pack(anchor="w")

        # Nav Items
        self.nav_buttons = {}
        nav_items = [
            ("library", "🎬  Clips Library"),
            ("dashboard", "⚡  Capture Status"),
            ("overlays", "📷  Video Overlays"),
            ("audio", "🔊  Audio Settings"),
            ("settings", "⚙️  Settings"),
        ]
        for key, caption in nav_items:
            btn = RoundedButton(
                self.sidebar, text=f"  {caption}", anchor="w",
                font=("Segoe UI", 10, "bold"), fg=palette.MUTED, bg=palette.CARD_BG,
                activebackground=palette.CARD_HOVER, activeforeground=palette.TEXT,
                relief="flat", borderwidth=0, padx=16, pady=10, cursor="hand2",
                command=lambda k=key: self.show_view(k)
            )
            btn.pack(fill="x", padx=10, pady=2)
            self.nav_buttons[key] = btn

        self.check_button = RoundedButton(
            self.sidebar, text="Restart to update" if self.pending_update else "Check for update",
            command=lambda: self.restart_for_update() if self.pending_update else self.check_update(manual=True),
            fg=palette.TEXT, bg=palette.CARD_BG, activebackground=palette.CARD_HOVER,
            activeforeground=palette.TEXT, font=("Segoe UI", 9, "bold"),
        )
        self.check_button.pack(fill="x", padx=10, pady=2)

        # Spacer to push storage to bottom
        spacer = tk.Frame(self.sidebar, bg=palette.CARD_BG)
        spacer.pack(fill="both", expand=True)

        # Bottom PC Storage Card
        storage_card = tk.Frame(self.sidebar, bg=palette.CARD_BG, padx=18, pady=18)
        storage_card.pack(fill="x", side="bottom")

        tk.Label(storage_card, text="PC STORAGE", font=("Segoe UI", 8, "bold"), fg=palette.DIM, bg=palette.CARD_BG).pack(anchor="w")
        self.storage_progress = tk.Canvas(storage_card, height=6, bg=palette.TRACK, highlightthickness=0)
        self.storage_progress.pack(fill="x", pady=6)
        self.storage_fill = self.storage_progress.create_rectangle(0, 0, 0, 6, fill=palette.ACCENT, outline="")
        self.storage_text = tk.Label(storage_card, text="Calculating...", font=("Segoe UI", 8), fg=palette.MUTED, bg=palette.CARD_BG)
        self.storage_text.pack(anchor="w")

        folder_btn = RoundedButton(
            storage_card, text="📁 Open clips folder", font=("Segoe UI", 8, "bold"),
            fg=palette.LINK, bg=palette.CARD_BG, activebackground=palette.CARD_BG, activeforeground=palette.TEXT,
            relief="flat", borderwidth=0, cursor="hand2", command=self.open_folder, padx=0, pady=4
        )
        folder_btn.pack(anchor="w", pady=(4, 0))

    # ---------------------------------------------------------
    # Topbar
    # ---------------------------------------------------------
    def build_topbar(self):
        self.topbar = tk.Frame(self.main_column, bg=palette.CARD_BG, height=56, padx=22)
        self.topbar.pack(fill="x")
        self.topbar.pack_propagate(False)

        # Left Section: Current View Title
        self.top_title = tk.Label(self.topbar, text="Clips Library", font=("Georgia", 17, "bold"), fg=palette.TEXT, bg=palette.CARD_BG)
        self.top_title.pack(side="left", pady=14)

        # Right Section: Status Indicator & Action Buttons
        actions_box = tk.Frame(self.topbar, bg=palette.CARD_BG)
        actions_box.pack(side="right", fill="y", pady=10)

        # Game Status Pill
        self.status_pill = tk.Frame(actions_box, bg=palette.CARD_BG, padx=12, pady=4)
        self.status_pill.pack(side="left", padx=(0, 12))
        self.status_canvas = tk.Canvas(self.status_pill, width=12, height=12, bg=palette.CARD_BG, highlightthickness=0)
        self.status_canvas.pack(side="left", padx=(0, 6))
        self.status_dot = self.status_canvas.create_oval(2, 2, 10, 10, fill=palette.MUTED, outline="")
        self.status_text = tk.Label(self.status_pill, text="Standby", font=("Segoe UI", 9, "bold"), fg=palette.MUTED, bg=palette.CARD_BG)
        self.status_text.pack(side="left")

        # Hotkey Chip
        if self.app.config.hotkey_enabled:
            hotkey_chip = tk.Frame(actions_box, bg=palette.LAVENDER, padx=8, pady=4)
            hotkey_chip.pack(side="left", padx=(0, 12))
            tk.Label(hotkey_chip, text=f"{self.app.config.hotkey_key} Clip", font=("Segoe UI", 8, "bold"), fg=palette.LINK, bg=palette.LAVENDER).pack()

        # Primary Action Buttons in Topbar
        self.top_btn_start = RoundedButton(
            actions_box, text="▶ Start recording", font=("Segoe UI", 9, "bold"),
            fg=palette.TEXT, bg=palette.ACCENT, activebackground=palette.ACCENT_HOVER, activeforeground=palette.TEXT,
            relief="flat", borderwidth=0, padx=14, pady=5, cursor="hand2", command=self.start_capture
        )
        self.top_btn_start.pack(side="left")

        self.top_btn_save = RoundedButton(
            actions_box, text="Save manual clip", font=("Segoe UI", 9, "bold"),
            fg=palette.TEXT, bg=palette.ACCENT, activebackground=palette.ACCENT_HOVER, activeforeground=palette.TEXT,
            relief="flat", borderwidth=0, padx=12, pady=5, cursor="hand2", command=self.save_clip
        )

        self.top_btn_stop = RoundedButton(
            actions_box, text="⏹ Stop recording", font=("Segoe UI", 9, "bold"),
            fg=palette.STOP_TEXT, bg=palette.STOP_BG, activebackground=palette.STOP_HOVER, activeforeground=palette.STOP_ACTIVE,
            relief="flat", borderwidth=0, padx=12, pady=5, cursor="hand2", command=self.stop_capture
        )

    def show_view(self, name: str):
        if getattr(self, "active_view", None) == "player" and name != "player":
            self.clip_player.leave_player()

        if self.active_view == "overlays" and name != "overlays":
            self.stop_live_preview()
        self.active_view = name
        titles = {
            "library": "Clips Library",
            "dashboard": "Capture Status & Controls",
            "overlays": "Video Overlays",
            "audio": "Audio Settings",
            "settings": "Settings",
            "player": "Clip Player & Editor",
        }
        self.top_title.configure(text=titles.get(name, "ThortClip"))

        for key, btn in self.nav_buttons.items():
            if key == name:
                btn.configure(fg=palette.TEXT, bg=palette.ACTIVE)
            else:
                btn.configure(fg=palette.MUTED, bg=palette.CARD_BG)

        for view in self.views.values():
            view.pack_forget()

        if name in self.views:
            self.views[name].pack(fill="both", expand=True)

        if name == "library":
            self.refresh_clips_grid(only_if_changed=True)
        elif name == "overlays":
            self.refresh_overlay_view()
        elif name == "audio":
            self.refresh_audio_view()

    # ---------------------------------------------------------
    # View 1: Clips Library View
    # ---------------------------------------------------------
    def build_library_view(self):
        view = tk.Frame(self.view_container, bg=palette.BG)
        self.views["library"] = view

        # Filter & Action Bar
        toolbar = tk.Frame(view, bg=palette.BG, padx=24, pady=16)
        toolbar.pack(fill="x")

        self.library_count_label = tk.Label(toolbar, text="All Clips (0)", font=("Segoe UI", 12, "bold"), fg=palette.TEXT, bg=palette.BG)
        self.library_count_label.pack(side="left")

        # Search Box
        self.search_var = tk.StringVar()
        self.search_var.trace_add("write", lambda *_: self.render_clip_cards())
        search_box = ctk.CTkFrame(toolbar, fg_color=palette.CARD_BG, bg_color=palette.BG, border_color=palette.CARD_BORDER,
                                  border_width=1, corner_radius=16)
        search_box.pack(side="left", fill="x", expand=True, padx=12)
        tk.Label(search_box, text="Search", fg=palette.MUTED, bg=palette.CARD_BG, font=("Segoe UI", 9)).pack(side="left", padx=(12, 4), pady=6)
        tk.Entry(search_box, textvariable=self.search_var, relief="flat", width=6, bg=palette.CARD_BG,
                 fg=palette.TEXT, insertbackground=palette.TEXT).pack(side="left", fill="x", expand=True, padx=(0, 12))

        # Sort Dropdown
        sort_box = tk.Frame(toolbar, bg=palette.BG)
        sort_box.pack(side="right")
        tk.Label(sort_box, text="Sort  ", font=("Segoe UI", 9), fg=palette.MUTED, bg=palette.BG).pack(side="left")
        self.sort_var = tk.StringVar(value="Newest")
        sort_combo = ctk.CTkOptionMenu(sort_box, variable=self.sort_var,
            values=["Newest", "Oldest", "Most Kills"], command=lambda _: self.render_clip_cards(),
            width=102, height=30, corner_radius=12, fg_color=palette.SELECT, button_color=palette.SELECT_ARROW,
            button_hover_color=palette.SELECT_HOVER, text_color=palette.TEXT, dropdown_fg_color=palette.CARD_BG,
            dropdown_text_color=palette.TEXT, dropdown_hover_color=palette.CARD_HOVER)
        sort_combo.pack(side="left")

        # Refresh Button
        refresh_btn = RoundedButton(
            toolbar, text="Refresh", font=("Segoe UI", 9, "bold"),
            fg=palette.TEXT, bg=palette.CARD_BG, activebackground=palette.CARD_HOVER, activeforeground=palette.TEXT,
            relief="flat", borderwidth=0, padx=12, pady=5, cursor="hand2", command=self.refresh_clips_grid
        )
        refresh_btn.pack(side="right", padx=(0, 10))

        filters = tk.Frame(view, bg=palette.BG)
        filters.pack(fill="x", padx=24, pady=(0, 6))
        self.clip_filter = "All clips"
        self.filter_buttons = {}
        def select_filter(name):
            self.clip_filter = name
            for label, button in self.filter_buttons.items():
                button.configure(fg_color=palette.ACTIVE if label == name else palette.CARD_BG)
            self.render_clip_cards()
        for name in ("All clips", "Multi-kills"):
            btn = ctk.CTkButton(filters, text=name, width=86, height=28, fg_color=palette.ACTIVE if name == self.clip_filter else palette.CARD_BG,
                                text_color=palette.TEXT, border_width=1, border_color=palette.CARD_BORDER, corner_radius=14,
                                hover_color=palette.CARD_HOVER, command=lambda n=name: select_filter(n))
            btn.pack(side="left", padx=(0, 7))
            self.filter_buttons[name] = btn

        # Scrollable Canvas
        self.clips_canvas = tk.Canvas(view, bg=palette.BG, highlightthickness=0)
        self.clips_scrollbar = ttk.Scrollbar(view, orient="vertical", command=self.clips_canvas.yview)
        self.clips_scrollable_frame = tk.Frame(self.clips_canvas, bg=palette.BG)

        self.clips_scrollable_frame.bind(
            "<Configure>",
            lambda e: self.clips_canvas.configure(scrollregion=self.clips_canvas.bbox("all"))
        )
        self.clips_canvas_window = self.clips_canvas.create_window((0, 0), window=self.clips_scrollable_frame, anchor="nw")
        self.clips_canvas.configure(yscrollcommand=self._library_scrolled)

        self.clips_canvas.pack(side="left", fill="both", expand=True, padx=(24, 0))
        self.clips_scrollbar.pack(side="right", fill="y")

        # Mouse wheel scrolling
        self.clips_canvas.bind_all("<MouseWheel>", self._on_mousewheel)
        self.clips_canvas.bind("<Configure>", self._on_canvas_configure)

    def _on_canvas_configure(self, event):
        self.clips_canvas.itemconfig(self.clips_canvas_window, width=event.width)
        columns = max(1, min(4, event.width // 330))
        if columns != getattr(self, "_library_columns", 2):
            self._library_columns = columns
            if getattr(self, "_card_widgets", None) and hasattr(self, "_card_pitch"):
                for i in range(4):
                    self._card_grid.columnconfigure(i, weight=int(i < columns), uniform="clipcol" if i < columns else "")
                self._layout_card_rows()

    def _layout_card_rows(self):
        columns = getattr(self, "_library_columns", 2)
        rows = (len(self._card_paths) + columns - 1) // columns
        for row in range(max(rows, getattr(self, "_card_rows", 0))):
            self._card_grid.rowconfigure(row, minsize=self._card_pitch if row < rows else 0)
        self._card_rows = rows
        self._show_card_window(self.clips_canvas.yview()[0], reposition=True)

    def _library_scrolled(self, first, last):
        self.clips_scrollbar.set(first, last)
        if getattr(self, "_card_widgets", None) and hasattr(self, "_card_pitch"):
            self._show_card_window(float(first))

    def _show_card_window(self, fraction, reposition=False):
        columns = getattr(self, "_library_columns", 2)
        row = int(fraction * self._card_rows)
        visible_rows = self.clips_canvas.winfo_height() // self._card_pitch + 3
        start = max(0, row - 1) * columns
        stop = min(len(self._card_paths), (row + visible_rows) * columns)
        visible = set(range(start, stop))
        for i in self._mapped_cards - visible:
            self._thumb_canvases.pop(self._card_paths[i], None)
            self._card_widgets.pop(i).destroy()
        for i in (visible if reposition else visible - self._mapped_cards):
            if i not in self._card_widgets:
                self._card_widgets[i] = self._create_card(self._card_paths[i])
            self._card_widgets[i].grid(row=i // columns, column=i % columns, padx=8, pady=8, sticky="nsew")
        self._mapped_cards = visible

    def _on_mousewheel(self, event):
        if self.active_view == "library" and hasattr(self, "clips_canvas"):
            self.clips_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
        elif self.active_view == "overlays" and hasattr(self, "overlay_scroll_c"):
            self.overlay_scroll_c.yview_scroll(int(-1 * (event.delta / 120)), "units")
        elif self.active_view == "audio" and hasattr(self, "audio_scroll_c"):
            self.audio_scroll_c.yview_scroll(int(-1 * (event.delta / 120)), "units")

    def get_clip_metadata(self, clip_path: Path) -> dict:
        key = str(clip_path.resolve())
        mtime = clip_path.stat().st_mtime
        marks = read_markers(clip_path)
        sidecar = metadata_path(clip_path)
        try:
            sidecar_time = sidecar.stat().st_mtime_ns
        except OSError:
            sidecar_time = None
        cached = self._clip_meta_cache.get(key)
        if cached and cached["mtime"] == mtime and cached["sidecar_time"] == sidecar_time:
            if marks is not None:
                self._clip_meta_cache[key]["kills"] = len(marks)
            return self._clip_meta_cache[key]

        save_type = None
        if sidecar_time is not None:
            try:
                data = json.loads(sidecar.read_text(encoding="utf8"))
                if isinstance(data, dict):
                    save_type = data.get("save_type")
            except (OSError, ValueError):
                pass
        name = clip_path.stem
        kills = 0
        m = re.match(r"^(\d+)K", name, re.IGNORECASE)
        if m:
            kills = int(m.group(1))
        elif "Ace" in name:
            kills = 5

        round_num = None
        rm = re.search(r"Round(\d+)", name, re.IGNORECASE)
        if rm:
            round_num = int(rm.group(1))

        # Size and date
        size_str = format_bytes(clip_path.stat().st_size)
        date_str = time.strftime("%b %d, %H:%M", time.localtime(mtime))

        meta = {
            "path": clip_path,
            "save_type": save_type,
            "sidecar_time": sidecar_time,
            "name": name,
            "kills": len(marks) if marks is not None else kills,
            "round": round_num,
            "size_str": size_str,
            "date_str": date_str,
            "mtime": mtime,
            "duration": None,
        }
        self._clip_meta_cache[key] = meta
        return meta

    def refresh_clips_grid(self, only_if_changed=False):
        output_dir = self.app.config.clips_output_dir
        snapshot = {}
        if output_dir.is_dir():
            with os.scandir(output_dir) as entries:
                snapshot = {entry.name: entry.stat().st_mtime_ns for entry in entries
                            if entry.name.lower().endswith(".mp4") and entry.is_file()}
        signature = (output_dir, snapshot)
        if only_if_changed and signature == getattr(self, "_library_snapshot", None):
            player = getattr(self, "clip_player", None)
            path = player.clip_path if player else None
            if path is None or path not in self.current_clips:
                return
            meta = self._clip_meta_cache.get(str(path.resolve()))
            marks = read_markers(path)
            if meta is None or marks is None or meta["kills"] == len(marks):
                return
        self._library_snapshot = signature
        self.current_clips = [output_dir / name for name in sorted(snapshot, key=snapshot.get, reverse=True)]
        self.library_count_label.configure(text=f"{len(self.current_clips)} clips")
        self.render_clip_cards()
        self.update_storage_meter()

    def render_clip_cards(self):
        self._card_widgets = {}
        self._mapped_cards = set()
        self._card_rows = 0
        self._thumb_canvases = {}
        # Clear existing cards
        for widget in self.clips_scrollable_frame.winfo_children():
            widget.destroy()

        query = self.search_var.get().lower().strip()
        sort_mode = self.sort_var.get()

        clips = list(self.current_clips)
        if self.clip_filter == "Multi-kills":
            clips = [p for p in clips if self.get_clip_metadata(p)["kills"] >= 2]
        if query:
            clips = [p for p in clips if query in p.name.lower()]

        if sort_mode == "Oldest":
            clips.sort(key=lambda p: p.stat().st_mtime)
        elif sort_mode == "Most Kills":
            clips.sort(key=lambda p: self.get_clip_metadata(p)["kills"], reverse=True)
        else: # Newest
            clips.sort(key=lambda p: p.stat().st_mtime, reverse=True)

        if not clips:
            empty_box = tk.Frame(self.clips_scrollable_frame, bg=palette.BG, pady=60)
            empty_box.pack(fill="x", expand=True)
            tk.Label(empty_box, text="🎬", font=("Segoe UI", 36), fg=palette.MUTED, bg=palette.BG).pack()
            tk.Label(empty_box, text="No clips found", font=("Segoe UI", 14, "bold"), fg=palette.TEXT, bg=palette.BG).pack(pady=4)
            tk.Label(empty_box, text="Play a match in Valorant or press F8 to save your highlights!", font=("Segoe UI", 10), fg=palette.MUTED, bg=palette.BG).pack()
            return

        # Grid of Cards (3 Columns)
        cols = getattr(self, "_library_columns", 2)
        grid_frame = tk.Frame(self.clips_scrollable_frame, bg=palette.BG)
        self._card_grid = grid_frame
        grid_frame.pack(fill="x", expand=True, pady=(0, 20))

        for c in range(cols):
            grid_frame.columnconfigure(c, weight=1, uniform="clipcol")

        self._card_paths = clips
        if not hasattr(self, "_card_pitch"):
            sample = self._create_card(clips[0])
            sample.update_idletasks()
            self._card_pitch = sample.winfo_reqheight() + 16
            self._thumb_canvases.pop(clips[0], None)
            sample.destroy()
        self._layout_card_rows()

    def _create_card(self, clip_path):
        meta = self.get_clip_metadata(clip_path)

        card = ctk.CTkFrame(self._card_grid, fg_color=palette.CARD_BG, bg_color=palette.BG, border_width=2, border_color=palette.CARD_BORDER, corner_radius=14)

        # Thumbnail Frame
        thumb_frame = tk.Frame(card, bg="#000000", height=140)
        thumb_frame.pack(fill="x", padx=6, pady=(6, 0))
        thumb_frame.pack_propagate(False)

        thumb_canvas = tk.Canvas(thumb_frame, bg="#000000", highlightthickness=0, cursor="hand2")
        thumb_canvas.pack(fill="both", expand=True)

        self._thumb_canvases[clip_path] = thumb_canvas
        # Load thumbnail or placeholder
        photo = self.thumbnails.get_thumbnail(clip_path, width=280, height=140)
        thumb_canvas.bind("<Configure>", lambda e: e.widget.coords("preview", e.width / 2, e.height / 2) if e.widget.find_withtag("preview") else None)
        if photo:
            thumb_canvas.create_image(140, 70, image=photo, tags="preview")
            thumb_canvas.image = photo
        else:
            thumb_canvas.create_rectangle(0, 0, 280, 140, fill="#121620", outline="", tags="placeholder")
            thumb_canvas.create_text(140, 60, tags="placeholder", text="▶", font=("Segoe UI", 22), fill=palette.MUTED)

        # Badges over thumbnail
        kills = meta["kills"]
        if kills > 0:
            badge_bg = palette.ORANGE if kills >= 3 else palette.ACCENT
            badge_text = "ACE" if kills >= 5 else f"{kills}K"
            # Rounded pill badge
            thumb_canvas.create_rectangle(10, 10, 46, 28, fill=badge_bg, outline="")
            thumb_canvas.create_text(28, 19, text=badge_text, font=("Segoe UI", 9, "bold"), fill=palette.TEXT)

        # Duration badge on bottom right
        dur_str = meta.get("duration_str")
        if dur_str:
            thumb_canvas.create_rectangle(226, 114, 270, 132, fill="#0b0e14", outline="")
            thumb_canvas.create_text(248, 123, text=dur_str, font=("Segoe UI", 8, "bold"), fill="#FFFFFF")

        # Info Details
        info = tk.Frame(card, bg=palette.CARD_BG, padx=12, pady=10)
        info.pack(fill="x", padx=5, pady=(0, 6))

        title_text = f"{kills}K Highlight" if kills > 0 else "Match Clip"
        title_text = {"manual": "Manual clip", "auto": "Auto highlight", "edit": "Edited clip"}.get(meta["save_type"], title_text)
        if meta["round"]:
            title_text += f" (Round {meta['round']})"

        tk.Label(info, text=title_text, font=("Georgia", 12, "bold"), fg=palette.TEXT, bg=palette.CARD_BG).pack(anchor="w")

        sub_line = tk.Frame(info, bg=palette.CARD_BG)
        sub_line.pack(fill="x", pady=(2, 6))
        tk.Label(sub_line, text=meta["date_str"], font=("Segoe UI", 8), fg=palette.MUTED, bg=palette.CARD_BG).pack(side="left")
        tk.Label(sub_line, text=f"• {meta['size_str']}", font=("Segoe UI", 8), fg=palette.DIM, bg=palette.CARD_BG).pack(side="left", padx=4)

        # Action Buttons Row
        actions = tk.Frame(info, bg=palette.CARD_BG)
        actions.pack(fill="x")

        play_btn = RoundedButton(
            actions, text="▶ Play", font=("Segoe UI", 9, "bold"),
            fg=palette.TEXT, bg=palette.ACCENT, activebackground=palette.ACCENT_HOVER, activeforeground=palette.TEXT,
            relief="flat", borderwidth=0, padx=8, pady=3, cursor="hand2",
            command=lambda p=clip_path: self.play_clip(p)
        )
        play_btn.pack(side="left")

        copy_btn = RoundedButton(
            actions, text="Copy", font=("Segoe UI", 9),
            fg=palette.MUTED, bg=palette.SECONDARY, activebackground=palette.CARD_HOVER, activeforeground=palette.TEXT,
            relief="flat", borderwidth=0, padx=8, pady=3, cursor="hand2",
            command=lambda p=clip_path: self.copy_clip_path(p)
        )
        copy_btn.pack(side="left", padx=6)

        RoundedButton(
            actions, text="Open clip in folder", font=("Segoe UI", 9),
            fg=palette.MUTED, bg=palette.SECONDARY, activebackground=palette.CARD_HOVER, activeforeground=palette.TEXT,
            relief="flat", borderwidth=0, padx=8, pady=3, cursor="hand2",
            command=lambda p=clip_path: show_clip_in_folder(p)
        ).pack(side="left")

        del_btn = RoundedButton(
            actions, text="🗑️", font=("Segoe UI", 8),
            fg=palette.MUTED, bg=palette.SECONDARY, activebackground=RED, activeforeground=palette.TEXT,
            relief="flat", borderwidth=0, padx=6, pady=3, cursor="hand2",
            command=lambda p=clip_path: self.delete_clip(p)
        )
        del_btn.pack(side="right")

        # Click thumbnail to play
        thumb_canvas.bind("<Button-1>", lambda e, p=clip_path: self.play_clip(p))

        return card

    def build_player_view(self):
        from .clip_player import ClipPlayerView
        self.clip_player = ClipPlayerView(
            self.view_container,
            on_close=lambda: self.show_view("library"),
            on_refresh_library=self.refresh_clips_grid,
        )
        self.views["player"] = self.clip_player

    def open_clip_player(self, clip_path: Path):
        self.show_view("player")
        self.clip_player.load_clip(clip_path)

    def play_clip(self, clip_path: Path):
        self.open_clip_player(clip_path)

    def copy_clip_path(self, clip_path: Path):
        self.root.clipboard_clear()
        self.root.clipboard_append(str(clip_path.resolve()))
        self.notice = f"Copied path: {clip_path.name}"
        self.status_text.configure(text=self.notice)

    def delete_clip(self, clip_path: Path):
        if messagebox.askyesno("Delete Clip", f"Delete {clip_path.name} permanently?", parent=self.root):
            try:
                clip_path.unlink(missing_ok=True)
                self.refresh_clips_grid()
            except Exception as exc:
                messagebox.showerror("Delete Clip", str(exc), parent=self.root)

    def update_storage_meter(self):
        output_dir = self.app.config.clips_output_dir
        total_size = 0
        if output_dir.exists():
            for f in output_dir.glob("*.mp4"):
                try:
                    total_size += f.stat().st_size
                except Exception:
                    pass

        try:
            usage = shutil.disk_usage(output_dir)
            free_bytes = usage.free
            total_disk = usage.total
            pct = min(1.0, total_size / max(1, total_size + free_bytes))
        except Exception:
            pct = 0.1

        width = self.storage_progress.winfo_width() or 180
        fill_w = max(4, int(width * pct))
        self.storage_progress.coords(self.storage_fill, 0, 0, fill_w, 6)
        self.storage_text.configure(text=f"{format_bytes(total_size)} used")

    # ---------------------------------------------------------
    # View 2: Video Overlays (Matching Medal.tv)
    # ---------------------------------------------------------
    def build_overlay_view(self):
        view = tk.Frame(self.view_container, bg=palette.BG, padx=28, pady=20)
        self.views["overlays"] = view

        # Header Title
        title_box = tk.Frame(view, bg=palette.BG)
        title_box.pack(fill="x", pady=(0, 10))
        tk.Label(title_box, text="Video Overlays", font=("Segoe UI", 15, "bold"), fg=palette.TEXT, bg=palette.BG).pack(anchor="w")
        tk.Label(title_box, text="Position your webcam over the recording.", font=("Segoe UI", 8), fg=palette.MUTED, bg=palette.BG).pack(anchor="w")

        # Main Scrollable Container for settings & preview
        scroll_c = tk.Canvas(view, bg=palette.BG, highlightthickness=0)
        self.overlay_scroll_c = scroll_c
        scroll_b = ttk.Scrollbar(view, orient="vertical", command=scroll_c.yview)
        body = tk.Frame(scroll_c, bg=palette.BG)
        body.bind("<Configure>", lambda e: scroll_c.configure(scrollregion=scroll_c.bbox("all")))
        scroll_win = scroll_c.create_window((0, 0), window=body, anchor="nw")
        scroll_c.configure(yscrollcommand=scroll_b.set)
        scroll_c.pack(side="left", fill="both", expand=True)
        scroll_b.pack(side="right", fill="y")
        scroll_c.bind("<Configure>", lambda e: scroll_c.itemconfig(scroll_win, width=e.width))

        # Card: Video Overlays Switches & Device Selection
        card = tk.Frame(body, bg=palette.CARD_BG, highlightbackground=palette.CARD_BORDER, highlightthickness=1, padx=18, pady=10)
        card.pack(fill="x", pady=(0, 12))

        tk.Label(card, text="Video Overlays", font=("Georgia", 12, "bold"), fg=palette.TEXT, bg=palette.CARD_BG).pack(anchor="w")
        tk.Frame(card, bg=palette.CARD_BORDER, height=1).pack(fill="x", pady=6)

        # 1. Enable Video Overlays Toggle
        row1 = tk.Frame(card, bg=palette.CARD_BG)
        row1.pack(fill="x", pady=3)
        t_box1 = tk.Frame(row1, bg=palette.CARD_BG)
        t_box1.pack(side="left", fill="x", expand=True)
        tk.Label(t_box1, text="Enable Video Overlays", font=("Segoe UI", 9, "bold"), fg=palette.TEXT, bg=palette.CARD_BG).pack(anchor="w")
        tk.Label(t_box1, text="Turn on to include video overlays such as a webcam in your recordings.", font=("Segoe UI", 8), fg=palette.MUTED, bg=palette.CARD_BG).pack(anchor="w")
        self.toggle_overlay = ThortClipToggle(row1, initial_value=self.app.config.overlay_enabled, command=self.on_toggle_overlay)
        self.toggle_overlay.pack(side="right")

        # 2. Enable Virtual Camera Toggle
        row2 = tk.Frame(card, bg=palette.CARD_BG)
        row2.pack(fill="x", pady=3)
        t_box2 = tk.Frame(row2, bg=palette.CARD_BG)
        t_box2.pack(side="left", fill="x", expand=True)
        tk.Label(t_box2, text="Enable Virtual Camera", font=("Segoe UI", 9, "bold"), fg=palette.TEXT, bg=palette.CARD_BG).pack(anchor="w")
        tk.Label(t_box2, text="Turn this on to choose capture from virtual camera sources like OBS Virtual Camera.", font=("Segoe UI", 8), fg=palette.MUTED, bg=palette.CARD_BG).pack(anchor="w")
        self.toggle_virtual = ThortClipToggle(row2, initial_value=self.app.config.overlay_virtual_camera, command=self.on_toggle_virtual)
        self.toggle_virtual.pack(side="right")

        # 3. Webcam Device Selection
        row3 = tk.Frame(card, bg=palette.CARD_BG)
        row3.pack(fill="x", pady=3)
        t_box3 = tk.Frame(row3, bg=palette.CARD_BG)
        t_box3.pack(side="left", fill="x", expand=True)
        tk.Label(t_box3, text="Select Webcam Device", font=("Segoe UI", 9, "bold"), fg=palette.TEXT, bg=palette.CARD_BG).pack(anchor="w")
        tk.Label(t_box3, text="Choose which USB webcam device to capture into your clips.", font=("Segoe UI", 8), fg=palette.MUTED, bg=palette.CARD_BG).pack(anchor="w")

        right_dev = tk.Frame(row3, bg=palette.CARD_BG)
        right_dev.pack(side="right")
        self.camera_var = tk.StringVar(value=self.app.config.overlay_device)
        self.camera_combo = ttk.Combobox(right_dev, textvariable=self.camera_var, state="readonly", width=26)
        self.camera_combo.pack(side="left", padx=(0, 8))
        self.camera_combo.bind("<<ComboboxSelected>>", self.on_camera_selected)

        rescan_btn = RoundedButton(
            right_dev, text="🔄", font=("Segoe UI", 9),
            fg=palette.TEXT, bg=palette.SECONDARY, activebackground=palette.CARD_HOVER, activeforeground=palette.TEXT,
            relief="flat", borderwidth=0, padx=8, pady=2, cursor="hand2", command=self.populate_cameras
        )
        rescan_btn.pack(side="left")

        # Section: Interactive Video Overlay Preview
        preview_card = tk.Frame(body, bg=palette.CARD_BG, highlightbackground=palette.CARD_BORDER, highlightthickness=1, padx=18, pady=10)
        preview_card.pack(fill="x", pady=(0, 10))

        tk.Label(preview_card, text="Video Overlay Preview", font=("Georgia", 12, "bold"), fg=palette.TEXT, bg=palette.CARD_BG).pack(anchor="w")
        tk.Label(
            preview_card,
            text="This is a preview of how your video overlays will be positioned in your recordings. Drag the webcam box anywhere inside the frame, or use the position and size presets below.",
            font=("Segoe UI", 8), fg=palette.MUTED, bg=palette.CARD_BG
        ).pack(anchor="w", pady=(1, 6))

        # Canvas for Drag-and-Drop Positioning (Exact 16:9)
        self.preview_w = 480
        self.preview_h = 270
        self.canvas_frame = tk.Frame(preview_card, bg="#05070a", highlightbackground=palette.CARD_BORDER, highlightthickness=1)
        self.canvas_frame.pack(anchor="center", pady=(0, 8))

        self.preview_canvas = tk.Canvas(
            self.canvas_frame, width=self.preview_w, height=self.preview_h,
            bg="#070a10", highlightthickness=0, cursor="crosshair"
        )
        self.preview_canvas.pack()

        # Canvas bindings for dragging
        self.preview_canvas.bind("<Button-1>", self.on_cam_drag_start)
        self.preview_canvas.bind("<B1-Motion>", self.on_cam_drag_motion)
        self.preview_canvas.bind("<ButtonRelease-1>", self.on_cam_drag_end)

        # Position Presets Toolbar
        ctrl_bar = tk.Frame(preview_card, bg=palette.CARD_BG)
        ctrl_bar.pack(fill="x", pady=6)

        pos_box = tk.Frame(ctrl_bar, bg=palette.CARD_BG)
        pos_box.pack(side="left")
        tk.Label(pos_box, text="Position Presets: ", font=("Segoe UI", 9, "bold"), fg=palette.MUTED, bg=palette.CARD_BG).pack(side="left", padx=(0, 6))

        for pos in ["Top-Left", "Top-Right", "Bottom-Left", "Bottom-Right", "Center"]:
            btn = RoundedButton(
                pos_box, text=pos, font=("Segoe UI", 8),
                fg=palette.TEXT, bg=palette.SECONDARY, activebackground=palette.CARD_HOVER, activeforeground=palette.TEXT,
                relief="flat", borderwidth=0, padx=8, pady=4, cursor="hand2",
                command=lambda p=pos.lower(): self.apply_position_preset(p)
            )
            btn.pack(side="left", padx=3)

        # Size Presets & Slider
        size_bar = tk.Frame(preview_card, bg=palette.CARD_BG)
        size_bar.pack(fill="x", pady=(6, 0))

        size_box = tk.Frame(size_bar, bg=palette.CARD_BG)
        size_box.pack(side="left")
        tk.Label(size_box, text="Webcam Size: ", font=("Segoe UI", 9, "bold"), fg=palette.MUTED, bg=palette.CARD_BG).pack(side="left", padx=(0, 6))

        for s_name, pct in [("Small (15%)", 15), ("Medium (20%)", 20), ("Large (28%)", 28)]:
            btn = RoundedButton(
                size_box, text=s_name, font=("Segoe UI", 8),
                fg=palette.TEXT, bg=palette.SECONDARY, activebackground=palette.CARD_HOVER, activeforeground=palette.TEXT,
                relief="flat", borderwidth=0, padx=8, pady=4, cursor="hand2",
                command=lambda p=pct: self.apply_size_preset(p)
            )
            btn.pack(side="left", padx=3)

        # Slider for fine-grained sizing
        slider_box = tk.Frame(size_bar, bg=palette.CARD_BG)
        slider_box.pack(side="right")
        self.size_label = tk.Label(slider_box, text=f"{int(self.app.config.overlay_width_percent)}%", font=("Segoe UI", 8, "bold"), fg=palette.LINK, bg=palette.CARD_BG)
        self.size_slider = ttk.Scale(
            slider_box, from_=10, to=45, orient="horizontal", length=140,
            command=self.on_size_slider_changed
        )
        self.size_slider.set(self.app.config.overlay_width_percent)
        self.size_slider.pack(side="right", padx=(6, 0))
        self.size_label.pack(side="right")

        # Snapshot / Test Camera Button
        test_box = tk.Frame(preview_card, bg=palette.CARD_BG)
        test_box.pack(fill="x", pady=(12, 0))
        self.live_cam_btn = RoundedButton(
            test_box, text="Start live preview", font=("Segoe UI", 9, "bold"),
            fg=palette.TEXT, bg=palette.ACCENT, activebackground=palette.ACCENT_HOVER, activeforeground=palette.TEXT,
            padx=12, pady=6, command=self.toggle_live_preview)
        self.live_cam_btn.pack(side="left", padx=(0, 6))
        self.test_cam_btn = RoundedButton(
            test_box, text="📷 Snapshot", font=("Segoe UI", 9, "bold"),
            fg=palette.TEXT, bg=palette.ACCENT, activebackground=palette.ACCENT_HOVER, activeforeground=palette.TEXT,
            relief="flat", borderwidth=0, padx=12, pady=6, cursor="hand2", command=self.test_camera_snapshot
        )
        self.test_cam_btn.pack(side="left")
        self.test_status_label = tk.Label(test_box, text="", font=("Segoe UI", 8), fg=palette.MUTED, bg=palette.CARD_BG)
        self.test_status_label.pack(side="left", padx=12)

        self.redraw_preview_canvas()

    def populate_cameras(self):
        virtual = self.app.config.overlay_virtual_camera
        def scan():
            devices = list_camera_devices(include_virtual=virtual)
            self.events.put(("camera_devices", devices, virtual))
        threading.Thread(target=scan, daemon=True).start()
        self._camera_scan_started = True

    def _apply_cameras(self, devices, virtual):
        if virtual != self.app.config.overlay_virtual_camera:
            return
        self.camera_combo["values"] = devices
        current = self.app.config.overlay_device
        if current in devices:
            self.camera_var.set(current)
        elif devices:
            self.camera_var.set(devices[0])
            self.app.config.set_overlay_settings(device=devices[0])
        else:
            self.camera_var.set("No cameras found")

    def on_toggle_overlay(self, val: bool):
        self.app.config.set_overlay_settings(enabled=val)
        if not val:
            self.app.recorder.disable_webcam()
        self.redraw_preview_canvas()

    def on_toggle_virtual(self, val: bool):
        self.app.config.set_overlay_settings(virtual_camera=val)
        self.populate_cameras()

    def on_camera_selected(self, event=None):
        selected = self.camera_var.get()
        if selected and selected != "No cameras found":
            self.app.config.set_overlay_settings(device=selected)
            self.redraw_preview_canvas()

    def apply_position_preset(self, preset: str):
        cfg = self.app.config
        w_pct = cfg.overlay_width_percent
        h_pct = cfg.overlay_height_percent
        margin_x = 3.0
        margin_y = 4.0

        if preset == "top-left":
            x, y = margin_x, margin_y
        elif preset == "top-right":
            x, y = 100.0 - w_pct - margin_x, margin_y
        elif preset == "bottom-left":
            x, y = margin_x, 100.0 - h_pct - margin_y
        elif preset == "bottom-right":
            x, y = 100.0 - w_pct - margin_x, 100.0 - h_pct - margin_y
        elif preset == "center":
            x, y = (100.0 - w_pct) / 2, (100.0 - h_pct) / 2
        else:
            x, y = 80.0, 72.0

        cfg.set_overlay_settings(position=preset, x_pct=x, y_pct=y)
        self.redraw_preview_canvas()

    def apply_size_preset(self, width_pct: int):
        height_pct = round(width_pct * 1.25, 1) # Standard 4:3 webcam ratio
        self.app.config.set_overlay_settings(width_pct=width_pct, height_pct=height_pct, size_preset="custom")
        self.size_slider.set(width_pct)
        self.size_label.configure(text=f"{width_pct}%")
        self.redraw_preview_canvas()

    def on_size_slider_changed(self, value):
        val = int(float(value))
        if hasattr(self, "size_label"):
            self.size_label.configure(text=f"{val}%")
        h_val = round(val * 1.25, 1)
        self.app.config.set_overlay_settings(width_pct=val, height_pct=h_val, size_preset="custom")
        self.redraw_preview_canvas()

    # Dragging logic on preview canvas
    def on_cam_drag_start(self, event):
        self._drag_start_x = event.x
        self._drag_start_y = event.y

    def on_cam_drag_motion(self, event):
        cfg = self.app.config
        box_w = (self.preview_w * cfg.overlay_width_percent) / 100.0
        box_h = (self.preview_h * cfg.overlay_height_percent) / 100.0

        new_x = event.x - (box_w / 2)
        new_y = event.y - (box_h / 2)

        new_x = max(0, min(new_x, self.preview_w - box_w))
        new_y = max(0, min(new_y, self.preview_h - box_h))

        x_pct = round((new_x / self.preview_w) * 100.0, 1)
        y_pct = round((new_y / self.preview_h) * 100.0, 1)

        cfg.overlay_x_percent = x_pct
        cfg.overlay_y_percent = y_pct
        cfg.overlay_position = "custom"
        self.redraw_preview_canvas()

    def on_cam_drag_end(self, event):
        self.app.config.set_overlay_settings(
            position="custom",
            x_pct=self.app.config.overlay_x_percent,
            y_pct=self.app.config.overlay_y_percent
        )

    def redraw_preview_canvas(self):
        c = self.preview_canvas
        c.delete("all")
        pw = self.preview_w
        ph = self.preview_h
        cfg = self.app.config

        # Simulated game background
        c.create_rectangle(0, 0, pw, ph, fill="#070a10", outline="")
        # Crosshair / HUD hint
        c.create_line(pw // 2 - 15, ph // 2, pw // 2 + 15, ph // 2, fill="#1c2432", width=1)
        c.create_line(pw // 2, ph // 2 - 15, pw // 2, ph // 2 + 15, fill="#1c2432", width=1)
        c.create_text(pw // 2, ph // 2 + 25, text=f"GAMEPLAY ({self.app.config.resolution.replace('x', ' × ')})", font=("Segoe UI", 9, "bold"), fill="#C1BDAD")

        # Top-right killfeed simulation
        c.create_rectangle(pw - 120, 15, pw - 15, 35, fill="#EFE4CF", outline="#544F48")
        c.create_text(pw - 67, 25, text="Kill Feed Area", font=("Segoe UI", 7), fill=palette.DIM)

        # Webcam box
        box_w = (pw * cfg.overlay_width_percent) / 100.0
        box_h = (ph * cfg.overlay_height_percent) / 100.0
        bx = (pw * cfg.overlay_x_percent) / 100.0
        by = (ph * cfg.overlay_y_percent) / 100.0

        # Boundary checks
        bx = max(0, min(bx, pw - box_w))
        by = max(0, min(by, ph - box_h))

        is_enabled = cfg.overlay_enabled
        box_border = palette.ACCENT if is_enabled else palette.MUTED
        box_bg = palette.ACCENT_BG if is_enabled else palette.FIELD

        c.create_rectangle(bx, by, bx + box_w, by + box_h, fill=box_bg, outline=box_border, width=2)

        # If live snapshot is available and enabled, display it inside
        if self.camera_snapshot_photo and (is_enabled or self.preview_stream):
            c.create_image(bx + box_w // 2, by + box_h // 2, image=self.camera_snapshot_photo)
        else:
            dev_name = cfg.overlay_device or "Webcam"
            c.create_text(bx + box_w // 2, by + box_h // 2 - 8, text="📷", font=("Segoe UI", 16), fill=palette.ACCENT if is_enabled else palette.MUTED)
            c.create_text(bx + box_w // 2, by + box_h // 2 + 14, text=dev_name[:18], font=("Segoe UI", 7, "bold"), fill=palette.TEXT)

        # Position Tag
        c.create_rectangle(bx, by - 16, bx + 55, by, fill=box_border, outline="")
        c.create_text(bx + 27, by - 8, text=f"{int(cfg.overlay_x_percent)}%, {int(cfg.overlay_y_percent)}%", font=("Segoe UI", 7, "bold"), fill=palette.TEXT)

    def stop_live_preview(self, message=""):
        if self.preview_timer is not None:
            self.root.after_cancel(self.preview_timer)
            self.preview_timer = None
        if self.preview_stream:
            self.app.stop_camera_preview()
            self.preview_stream = None
            self.camera_snapshot_photo = None
            self.redraw_preview_canvas()
        self.live_cam_btn.configure(text="Start live preview")
        self.test_cam_btn.configure(state="normal")
        self.test_status_label.configure(text=message)

    def toggle_live_preview(self):
        if self.preview_stream:
            self.stop_live_preview()
            return
        device = self.camera_var.get()
        if not device or device == "No cameras found":
            self.test_status_label.configure(text="Select a camera first.")
            return
        try:
            self.preview_stream = self.app.start_camera_preview(device)
        except OSError:
            self.test_status_label.configure(text="Could not open camera preview.")
            return
        if not self.preview_stream:
            self.test_status_label.configure(text="Preview is available between recordings.")
            return
        self.preview_device = device
        self.preview_started = time.monotonic()
        self.live_cam_btn.configure(text="Stop live preview")
        self.test_cam_btn.configure(state="disabled")
        self.test_status_label.configure(text="Opening camera…")
        self._update_live_preview()

    def _update_live_preview(self):
        self.preview_timer = None
        stream = self.preview_stream
        if stream is not self.app.camera_preview:
            self.stop_live_preview("Preview stopped for recording.")
            return
        if self.camera_var.get() != self.preview_device:
            self.stop_live_preview()
            self.toggle_live_preview()
            return
        frame = stream.frame
        if stream.process.poll() is not None or (frame is None and time.monotonic() - self.preview_started > 8):
            self.stop_live_preview("Camera unavailable. Check the device and retry.")
            return
        if frame is not None:
            cfg = self.app.config
            size = (max(1, int(self.preview_w * cfg.overlay_width_percent / 100) - 2),
                    max(1, int(self.preview_h * cfg.overlay_height_percent / 100) - 2))
            img = Image.frombytes("RGB", (320, 240), frame).resize(size, Image.Resampling.BILINEAR)
            self.camera_snapshot_photo = ImageTk.PhotoImage(img)
            self.redraw_preview_canvas()
            self.test_status_label.configure(text="Live")
        self.preview_timer = self.root.after(100, self._update_live_preview)

    def test_camera_snapshot(self):
        device = self.app.config.overlay_device
        if not device or device == "No cameras found":
            messagebox.showwarning("Test Camera", "Please select a valid webcam device first.", parent=self.root)
            return

        self.test_status_label.configure(text="Capturing snapshot from webcam...")
        self.test_cam_btn.configure(state="disabled")

        def work():
            temp_snap = Path("temp/camera_snapshot.jpg")
            ok = capture_camera_frame(device, temp_snap, width=320, height=240)
            self.events.put(("camera_snapshot", ok, temp_snap))

        threading.Thread(target=work, daemon=True).start()

    def refresh_overlay_view(self):
        self.toggle_overlay.set_value(self.app.config.overlay_enabled)
        self.toggle_virtual.set_value(self.app.config.overlay_virtual_camera)
        if not getattr(self, "_camera_scan_started", False):
            self.populate_cameras()
        self.redraw_preview_canvas()

    # ---------------------------------------------------------
    # View: Audio Settings
    # ---------------------------------------------------------
    def build_audio_view(self):
        view = tk.Frame(self.view_container, bg=palette.BG, padx=28, pady=20)
        self.views["audio"] = view

        # Header Title
        title_box = tk.Frame(view, bg=palette.BG)
        title_box.pack(fill="x", pady=(0, 14))
        tk.Label(title_box, text="Audio Settings", font=("Georgia", 18, "bold"), fg=palette.TEXT, bg=palette.BG).pack(anchor="w")
        tk.Label(title_box, text="Configure game sound output and microphone input devices and volume levels.", font=("Segoe UI", 9), fg=palette.MUTED, bg=palette.BG).pack(anchor="w", pady=(2, 0))

        # Main Scrollable Container
        scroll_c = tk.Canvas(view, bg=palette.BG, highlightthickness=0)
        self.audio_scroll_c = scroll_c
        scroll_b = ttk.Scrollbar(view, orient="vertical", command=scroll_c.yview)
        body = tk.Frame(scroll_c, bg=palette.BG)
        body.bind("<Configure>", lambda e: scroll_c.configure(scrollregion=scroll_c.bbox("all")))
        scroll_win = scroll_c.create_window((0, 0), window=body, anchor="nw")
        scroll_c.configure(yscrollcommand=scroll_b.set)
        scroll_c.pack(side="left", fill="both", expand=True)
        scroll_b.pack(side="right", fill="y")
        scroll_c.bind("<Configure>", lambda e: scroll_c.itemconfig(scroll_win, width=e.width))

        # Card 1: Game Sound (Output)
        card_sound = tk.Frame(body, bg=palette.CARD_BG, highlightbackground=palette.CARD_BORDER, highlightthickness=1, padx=20, pady=16)
        card_sound.pack(fill="x", pady=(0, 16))

        tk.Label(card_sound, text="Game Sound (Output)", font=("Segoe UI", 11, "bold"), fg=palette.TEXT, bg=palette.CARD_BG).pack(anchor="w")
        tk.Frame(card_sound, bg=palette.CARD_BORDER, height=1).pack(fill="x", pady=8)

        # 1. Game sound toggle
        r1 = tk.Frame(card_sound, bg=palette.CARD_BG)
        r1.pack(fill="x", pady=4)
        t_box1 = tk.Frame(r1, bg=palette.CARD_BG)
        t_box1.pack(side="left", fill="x", expand=True)
        tk.Label(t_box1, text="Record Game Audio", font=("Georgia", 12, "bold"), fg=palette.TEXT, bg=palette.CARD_BG).pack(anchor="w")
        tk.Label(t_box1, text="Capture in-game sounds, gunshots, and voice chat into your clips.", font=("Segoe UI", 8), fg=palette.MUTED, bg=palette.CARD_BG).pack(anchor="w")
        self.toggle_game_audio = ThortClipToggle(r1, initial_value=self.app.config.game_audio_enabled, command=self.on_toggle_game_audio)
        self.toggle_game_audio.pack(side="right")

        # 2. Output Device selection
        r2 = tk.Frame(card_sound, bg=palette.CARD_BG)
        r2.pack(fill="x", pady=8)
        t_box2 = tk.Frame(r2, bg=palette.CARD_BG)
        t_box2.pack(side="left", fill="x", expand=True)
        tk.Label(t_box2, text="Playback Output Device", font=("Georgia", 12, "bold"), fg=palette.TEXT, bg=palette.CARD_BG).pack(anchor="w")
        tk.Label(t_box2, text="Select your primary speaker, headset, or loopback audio device.", font=("Segoe UI", 8), fg=palette.MUTED, bg=palette.CARD_BG).pack(anchor="w")

        right_play = tk.Frame(r2, bg=palette.CARD_BG)
        right_play.pack(side="right")
        self.playback_var = tk.StringVar(value="Default System Audio")
        self.playback_combo = ttk.Combobox(right_play, textvariable=self.playback_var, state="readonly", width=28)
        self.playback_combo.pack(side="left", padx=(0, 8))
        self.playback_combo.bind("<<ComboboxSelected>>", self.on_playback_selected)

        rescan_play_btn = RoundedButton(
            right_play, text="🔄", font=("Segoe UI", 9),
            fg=palette.TEXT, bg=palette.SECONDARY, activebackground=palette.CARD_HOVER, activeforeground=palette.TEXT,
            relief="flat", borderwidth=0, padx=8, pady=2, cursor="hand2", command=self.populate_audio_devices
        )
        rescan_play_btn.pack(side="left")

        # 3. Game Audio Volume Slider
        r3 = tk.Frame(card_sound, bg=palette.CARD_BG)
        r3.pack(fill="x", pady=(6, 0))
        tk.Label(r3, text="Game Audio Volume", font=("Segoe UI", 10, "bold"), fg=palette.TEXT, bg=palette.CARD_BG).pack(side="left")
        self.playback_vol_label = tk.Label(r3, text=f"{int(self.app.config.playback_volume * 100)}%", font=("Segoe UI", 9, "bold"), fg=palette.LINK, bg=palette.CARD_BG)
        self.playback_vol_label.pack(side="right", padx=(8, 0))
        self.playback_vol_scale = ttk.Scale(
            r3, from_=0, to=150, orient="horizontal", length=160,
            command=self.on_playback_vol_changed
        )
        self.playback_vol_scale.set(self.app.config.playback_volume * 100)
        self.playback_vol_scale.pack(side="right")

        # Card 2: Microphone (Input)
        card_mic = tk.Frame(body, bg=palette.CARD_BG, highlightbackground=palette.CARD_BORDER, highlightthickness=1, padx=20, pady=16)
        card_mic.pack(fill="x", pady=(0, 16))

        tk.Label(card_mic, text="Microphone (Input)", font=("Segoe UI", 11, "bold"), fg=palette.TEXT, bg=palette.CARD_BG).pack(anchor="w")
        tk.Frame(card_mic, bg=palette.CARD_BORDER, height=1).pack(fill="x", pady=8)

        # 1. Mic toggle
        m1 = tk.Frame(card_mic, bg=palette.CARD_BG)
        m1.pack(fill="x", pady=4)
        m_box1 = tk.Frame(m1, bg=palette.CARD_BG)
        m_box1.pack(side="left", fill="x", expand=True)
        tk.Label(m_box1, text="Enable Microphone", font=("Georgia", 12, "bold"), fg=palette.TEXT, bg=palette.CARD_BG).pack(anchor="w")
        tk.Label(m_box1, text="Record your voice alongside the game audio in all saved clips.", font=("Segoe UI", 8), fg=palette.MUTED, bg=palette.CARD_BG).pack(anchor="w")
        self.toggle_mic = ThortClipToggle(m1, initial_value=self.app.config.microphone_enabled, command=self.on_toggle_mic)
        self.toggle_mic.pack(side="right")

        # 2. Mic device selection
        m2 = tk.Frame(card_mic, bg=palette.CARD_BG)
        m2.pack(fill="x", pady=8)
        m_box2 = tk.Frame(m2, bg=palette.CARD_BG)
        m_box2.pack(side="left", fill="x", expand=True)
        tk.Label(m_box2, text="Select Microphone Device", font=("Georgia", 12, "bold"), fg=palette.TEXT, bg=palette.CARD_BG).pack(anchor="w")
        tk.Label(m_box2, text="Choose which microphone input device to record.", font=("Segoe UI", 8), fg=palette.MUTED, bg=palette.CARD_BG).pack(anchor="w")

        right_mic = tk.Frame(m2, bg=palette.CARD_BG)
        right_mic.pack(side="right")
        self.mic_var = tk.StringVar(value="Default System Microphone")
        self.mic_combo = ttk.Combobox(right_mic, textvariable=self.mic_var, state="readonly", width=28)
        self.mic_combo.pack(side="left", padx=(0, 8))
        self.mic_combo.bind("<<ComboboxSelected>>", self.on_mic_selected)

        rescan_mic_btn = RoundedButton(
            right_mic, text="🔄", font=("Segoe UI", 9),
            fg=palette.TEXT, bg=palette.SECONDARY, activebackground=palette.CARD_HOVER, activeforeground=palette.TEXT,
            relief="flat", borderwidth=0, padx=8, pady=2, cursor="hand2", command=self.populate_audio_devices
        )
        rescan_mic_btn.pack(side="left")

        # 3. Mic Volume Slider
        m3 = tk.Frame(card_mic, bg=palette.CARD_BG)
        m3.pack(fill="x", pady=6)
        tk.Label(m3, text="Microphone Volume", font=("Segoe UI", 10, "bold"), fg=palette.TEXT, bg=palette.CARD_BG).pack(side="left")
        self.mic_vol_label = tk.Label(m3, text=f"{int(self.app.config.microphone_volume * 100)}%", font=("Segoe UI", 9, "bold"), fg=palette.LINK, bg=palette.CARD_BG)
        self.mic_vol_label.pack(side="right", padx=(8, 0))
        self.mic_vol_scale = ttk.Scale(
            m3, from_=0, to=150, orient="horizontal", length=160,
            command=self.on_mic_vol_changed
        )
        self.mic_vol_scale.set(self.app.config.microphone_volume * 100)
        self.mic_vol_scale.pack(side="right")

        # 4. Microphone Test & Live Level Meter
        m4 = tk.Frame(card_mic, bg=palette.CARD_BG)
        m4.pack(fill="x", pady=(10, 0))

        self.btn_test_mic = RoundedButton(
            m4, text="🎙️ Test Microphone", font=("Segoe UI", 9, "bold"),
            fg=palette.TEXT, bg=palette.ACCENT, activebackground=palette.ACCENT_HOVER, activeforeground=palette.TEXT,
            relief="flat", borderwidth=0, padx=12, pady=5, cursor="hand2", command=self.test_mic_action
        )
        self.btn_test_mic.pack(side="left", padx=(0, 12))

        meter_frame = tk.Frame(m4, bg="#10141d", highlightbackground=palette.CARD_BORDER, highlightthickness=1)
        meter_frame.pack(side="left", fill="x", expand=True)
        self.mic_level_canvas = tk.Canvas(meter_frame, height=12, bg="#10141d", highlightthickness=0)
        self.mic_level_canvas.pack(fill="x", padx=2, pady=2)
        self.mic_level_bar = self.mic_level_canvas.create_rectangle(0, 0, 0, 12, fill=palette.ACCENT, outline="")

        self.mic_status_label = tk.Label(card_mic, text="Click Test to check input audio level.", font=("Segoe UI", 8), fg=palette.MUTED, bg=palette.CARD_BG)
        self.mic_status_label.pack(anchor="w", pady=(6, 0))


    def populate_audio_devices(self):
        def scan():
            play = ["Default System Audio"] + list_playback_devices()
            mic = ["Default System Microphone"] + list_microphone_devices()
            self.events.put(("audio_devices", play, mic))
        threading.Thread(target=scan, daemon=True).start()
        self._audio_scan_started = True

    def _apply_audio_devices(self, play_devices, mic_devices):
        self.playback_combo["values"] = play_devices
        curr_play = self.app.config.playback_device
        if curr_play == "default" or not curr_play:
            self.playback_var.set("Default System Audio")
        else:
            match = next((d for d in play_devices if curr_play in d), None)
            self.playback_var.set(match if match else "Default System Audio")

        # Microphone devices
        self.mic_combo["values"] = mic_devices
        curr_mic = self.app.config.microphone_device
        if curr_mic == "default" or not curr_mic:
            self.mic_var.set("Default System Microphone")
        else:
            match = next((d for d in mic_devices if curr_mic in d), None)
            self.mic_var.set(match if match else "Default System Microphone")

    def on_toggle_game_audio(self, val: bool):
        self.app.config.set_audio_settings(game_audio_enabled=val)

    def on_playback_selected(self, event=None):
        val = self.playback_var.get()
        dev_id = "default" if val == "Default System Audio" else val
        self.app.config.set_audio_settings(playback_device=dev_id)

    def on_playback_vol_changed(self, value):
        val = int(float(value))
        if hasattr(self, "playback_vol_label"):
            self.playback_vol_label.configure(text=f"{val}%")
        self.app.config.set_audio_settings(playback_volume=round(val / 100.0, 2))

    def on_toggle_mic(self, val: bool):
        self.app.config.set_audio_settings(microphone_enabled=val)

    def on_mic_selected(self, event=None):
        val = self.mic_var.get()
        dev_id = "default" if val == "Default System Microphone" else val
        self.app.config.set_audio_settings(microphone_device=dev_id)

    def on_mic_vol_changed(self, value):
        val = int(float(value))
        if hasattr(self, "mic_vol_label"):
            self.mic_vol_label.configure(text=f"{val}%")
        self.app.config.set_audio_settings(microphone_volume=round(val / 100.0, 2))

    def test_mic_action(self):
        self.btn_test_mic.configure(state="disabled")
        self.mic_status_label.configure(text="Listening... speak into your microphone...")

        def work():
            dev_name = self.app.config.microphone_device
            level = test_microphone_level(dev_name, duration_sec=0.7)
            self.events.put(("mic_test_done", level))

        threading.Thread(target=work, daemon=True).start()

    def refresh_audio_view(self):
        self.toggle_game_audio.set_value(self.app.config.game_audio_enabled)
        self.toggle_mic.set_value(self.app.config.microphone_enabled)
        self.playback_vol_scale.set(self.app.config.playback_volume * 100)
        self.playback_vol_label.configure(text=f"{int(self.app.config.playback_volume * 100)}%")
        self.mic_vol_scale.set(self.app.config.microphone_volume * 100)
        self.mic_vol_label.configure(text=f"{int(self.app.config.microphone_volume * 100)}%")
        if not getattr(self, "_audio_scan_started", False):
            self.populate_audio_devices()

    # ---------------------------------------------------------
    # View 3: Dashboard / Recorder Status
    # ---------------------------------------------------------
    def build_dashboard_view(self):
        view = tk.Frame(self.view_container, bg=palette.BG, padx=28, pady=20)
        self.views["dashboard"] = view

        # Status Hero Card
        hero = tk.Frame(view, bg=palette.CARD_BG, highlightbackground=palette.CARD_BORDER, highlightthickness=1, padx=24, pady=20)
        hero.pack(fill="x", pady=(0, 18))

        tk.Label(hero, text="CAPTURE STATUS", font=("Segoe UI", 9, "bold"), fg=palette.MUTED, bg=palette.CARD_BG).pack(anchor="w")

        status_row = tk.Frame(hero, bg=palette.CARD_BG)
        status_row.pack(fill="x", pady=(4, 0))

        self.dash_status_text = tk.Label(status_row, text="Standby", font=("Segoe UI", 26, "bold"), fg=palette.TEXT, bg=palette.CARD_BG)
        self.dash_status_text.pack(side="left")

        # Radar animation canvas
        self.radar = tk.Canvas(status_row, width=40, height=40, bg=palette.CARD_BG, highlightthickness=0)
        self.radar.pack(side="right")
        for x, y, dx, dy in ((4, 4, 7, 7), (36, 4, -7, 7), (4, 36, 7, -7), (36, 36, -7, -7)):
            self.radar.create_line(x + dx, y, x, y, x, y + dy, fill=palette.MUTED, width=2)
        self.dash_dot = self.radar.create_oval(15, 15, 25, 25, fill=palette.MUTED, outline="")

        self.dash_detail = tk.Label(hero, text="Waiting for Valorant match activity.", font=("Segoe UI", 10), fg=palette.MUTED, bg=palette.CARD_BG)
        self.dash_detail.pack(anchor="w", pady=(4, 0))

        # Metrics Row
        tk.Frame(hero, bg=palette.CARD_BORDER, height=1).pack(fill="x", pady=16)
        metrics = tk.Frame(hero, bg=palette.CARD_BG)
        metrics.pack(fill="x")

        cfg = self.app.config
        metric_items = [
            ("Resolution", cfg.resolution.replace("x", " × ")),
            ("Frame rate", f"{cfg.fps} FPS"),
            ("Buffer", "Full match" if self.app.results.enabled else f"{cfg.buffer_duration_sec} sec"),
            ("Encoder", cfg.encoder),
            ("Microphone", "Enabled" if cfg.microphone_enabled else "Off"),
        ]
        for col, (caption, value) in enumerate(metric_items):
            metrics.columnconfigure(col, weight=1)
            tk.Label(metrics, text=caption, font=("Segoe UI", 9), fg=palette.MUTED, bg=palette.CARD_BG).grid(row=0, column=col, sticky="w")
            tk.Label(metrics, text=value, font=("Segoe UI", 10, "bold"), fg=palette.TEXT, bg=palette.CARD_BG).grid(row=1, column=col, sticky="w")

        # Auto clips Card
        auto_card = tk.Frame(view, bg=palette.CARD_BG, highlightbackground=palette.CARD_BORDER, highlightthickness=1, padx=24, pady=18)
        auto_card.pack(fill="x", pady=(0, 18))

        row_a = tk.Frame(auto_card, bg=palette.CARD_BG)
        row_a.pack(fill="x")
        tk.Label(row_a, text="Auto Clipping Mode", font=("Segoe UI", 11, "bold"), fg=palette.TEXT, bg=palette.CARD_BG).pack(side="left")
        mode_text = "Riot match results" if self.app.results.enabled else (
            "Personal kill feed" if cfg.detector_enabled else "Off"
        )
        tk.Label(row_a, text=mode_text, font=("Segoe UI", 10, "bold"), fg=palette.LINK, bg=palette.CARD_BG).pack(side="right")

        self.round_clips = tk.BooleanVar(self.root, value=cfg.auto_clip_round)
        cb = tk.Checkbutton(
            auto_card, text="Include round ending in kill clips", variable=self.round_clips,
            command=self.update_round_clips, bg=palette.CARD_BG, fg=palette.TEXT, selectcolor=palette.FIELD,
            activebackground=palette.CARD_BG, activeforeground=palette.ACCENT, font=("Segoe UI", 10),
            cursor="hand2", highlightthickness=0
        )
        cb.pack(anchor="w", pady=(10, 0))

        # Bottom Actions Row
        actions_row = tk.Frame(view, bg=palette.BG)
        actions_row.pack(fill="x", pady=(4, 0))

        self.dash_btn_start = RoundedButton(
            actions_row, text="▶ Start recording", font=("Segoe UI", 10, "bold"),
            fg=palette.TEXT, bg=palette.ACCENT, activebackground=palette.ACCENT_HOVER, activeforeground=palette.TEXT,
            relief="flat", borderwidth=0, padx=20, pady=10, cursor="hand2", command=self.start_capture
        )
        self.dash_btn_start.pack(side="left", padx=(0, 10))

        self.dash_btn_save = RoundedButton(
            actions_row, text="Save manual clip", font=("Segoe UI", 10, "bold"),
            fg=palette.TEXT, bg=palette.ACCENT, activebackground=palette.ACCENT_HOVER, activeforeground=palette.TEXT,
            relief="flat", borderwidth=0, padx=18, pady=10, cursor="hand2", command=self.save_clip
        )

        self.dash_btn_stop = RoundedButton(
            actions_row, text="⏹ Stop recording", font=("Segoe UI", 10, "bold"),
            fg=palette.STOP_TEXT, bg=palette.STOP_BG, activebackground=palette.STOP_HOVER, activeforeground=palette.STOP_ACTIVE,
            relief="flat", borderwidth=0, padx=18, pady=10, cursor="hand2", command=self.stop_capture
        )

        self.dash_btn_folder = RoundedButton(
            actions_row, text="📁 Open clips folder", font=("Segoe UI", 10, "bold"),
            fg=palette.TEXT, bg=palette.CARD_BG, activebackground=palette.CARD_HOVER, activeforeground=palette.TEXT,
            relief="flat", borderwidth=0, padx=16, pady=10, cursor="hand2", command=self.open_folder
        )
        self.dash_btn_folder.pack(side="left")

    # ---------------------------------------------------------
    # View 4: Settings
    # ---------------------------------------------------------
    def build_settings_view(self):
        view = tk.Frame(self.view_container, bg=palette.BG, padx=28, pady=20)
        self.views["settings"] = view

        tk.Label(view, text="Settings", font=("Georgia", 18, "bold"), fg=palette.TEXT, bg=palette.BG).pack(anchor="w", pady=(0, 16))

        card = tk.Frame(view, bg=palette.CARD_BG, highlightbackground=palette.CARD_BORDER, highlightthickness=1, padx=24, pady=20)
        card.pack(fill="x")

        self.dark_toggle = ctk.CTkSwitch(card, text="Dark mode", text_color=palette.TEXT,
            progress_color=palette.ACTIVE, command=self.toggle_dark_mode)
        self.dark_toggle.pack(anchor="w", pady=(0, 14))
        if palette.dark:
            self.dark_toggle.select()
        stretch = tk.BooleanVar(view, value=self.app.config.stretch_to_widescreen)
        self.stretch_var = stretch
        def save_stretch():
            self.app.config.set_preference("recorder", "stretch_to_widescreen", stretch.get())
            self.app.config.stretch_to_widescreen = self.app.cutter.stretch = stretch.get()
        tk.Checkbutton(card, text="Fill widescreen clips (16:9)", variable=stretch, command=save_stretch,
                       bg=palette.CARD_BG, fg=palette.TEXT, selectcolor=palette.FIELD,
                       activebackground=palette.CARD_BG, activeforeground=palette.TEXT).pack(anchor="w", pady=(0, 12))

        # Output folder
        tk.Label(card, text="Clips Output Directory", font=("Georgia", 12, "bold"), fg=palette.TEXT, bg=palette.CARD_BG).pack(anchor="w")
        dir_row = tk.Frame(card, bg=palette.CARD_BG)
        dir_row.pack(fill="x", pady=(4, 16))
        self.dir_entry = tk.Entry(dir_row, font=("Segoe UI", 9), fg=palette.TEXT, bg=palette.FIELD, relief="flat")
        self.dir_entry.insert(0, str(self.app.config.clips_output_dir))
        self.dir_entry.configure(state="readonly")
        self.dir_entry.pack(side="left", fill="x", expand=True, padx=(0, 10))

        browse_btn = RoundedButton(
            dir_row, text="Browse...", font=("Segoe UI", 9, "bold"),
            fg=palette.TEXT, bg=palette.SECONDARY, activebackground=palette.CARD_HOVER, activeforeground=palette.TEXT,
            relief="flat", borderwidth=0, padx=12, pady=4, cursor="hand2", command=self.browse_folder
        )
        browse_btn.pack(side="left")

        # Buffer Duration
        tk.Label(card, text="Buffer Duration", font=("Georgia", 12, "bold"), fg=palette.TEXT, bg=palette.CARD_BG).pack(anchor="w")
        tk.Label(card, text=f"{self.app.config.buffer_duration_sec} seconds rolling window", font=("Segoe UI", 9), fg=palette.MUTED, bg=palette.CARD_BG).pack(anchor="w", pady=(2, 16))

        # Hotkey Info
        tk.Label(card, text="Manual Save Hotkey", font=("Georgia", 12, "bold"), fg=palette.TEXT, bg=palette.CARD_BG).pack(anchor="w")
        tk.Label(card, text=f"[{self.app.config.hotkey_key}] - Save a manual clip while recording. Automatic highlights save separately at round end.", font=("Segoe UI", 9), fg=palette.MUTED, bg=palette.CARD_BG).pack(anchor="w", pady=(2, 0))

    def toggle_dark_mode(self):
        dark = bool(self.dark_toggle.get())
        if str(self.clip_player.btn_save_edits.cget("state")) == "disabled":
            self.dark_toggle.select() if palette.dark else self.dark_toggle.deselect()
            messagebox.showinfo("Appearance", "Finish the clip export before changing appearance.", parent=self.root)
            return
        self.app.config.set_preference("appearance", "dark_mode", dark)
        self.app.config.dark_mode = dark
        self.shell.destroy()
        self._library_snapshot = None
        self._camera_scan_started = self._audio_scan_started = False
        self._card_widgets = {}
        self._thumb_canvases = {}
        self.last_state = None
        self.build_interface("settings")

    def browse_folder(self):
        new_dir = filedialog.askdirectory(initialdir=str(self.app.config.clips_output_dir), parent=self.root)
        if new_dir:
            try:
                data = json.loads(self.app.config.config_path.read_text(encoding="utf-8"))
                data.setdefault("paths", {})["clips_output_dir"] = new_dir
                self.app.config.config_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
                self.app.config.clips_output_dir = Path(new_dir)
                self.dir_entry.configure(state="normal")
                self.dir_entry.delete(0, tk.END)
                self.dir_entry.insert(0, new_dir)
                self.dir_entry.configure(state="readonly")
                self.refresh_clips_grid()
            except Exception as exc:
                messagebox.showerror("Update Directory", str(exc), parent=self.root)

    # ---------------------------------------------------------
    # Actions & Periodic Refresh
    # ---------------------------------------------------------
    def update_round_clips(self):
        cfg = self.app.config
        try:
            cfg.set_auto_clip_round(self.round_clips.get())
        except (OSError, ValueError) as exc:
            self.round_clips.set(cfg.auto_clip_round)
            messagebox.showerror("Save auto clip setting", str(exc), parent=self.root)

    def open_folder(self):
        try:
            os.startfile(self.app.config.clips_output_dir)
        except OSError as exc:
            messagebox.showerror("Open clips folder", str(exc), parent=self.root)

    def _set_action_buttons_state(self, state: str):
        for btn in [
            getattr(self, "top_btn_start", None),
            getattr(self, "top_btn_save", None),
            getattr(self, "top_btn_stop", None),
            getattr(self, "dash_btn_start", None),
            getattr(self, "dash_btn_save", None),
            getattr(self, "dash_btn_stop", None),
        ]:
            if btn:
                try:
                    btn.configure(state=state)
                except Exception:
                    pass

    def start_capture(self):
        if self.busy:
            return
        self.busy = True
        self._set_action_buttons_state("disabled")
        self.notice = "Starting capture…"
        self.status_text.configure(text=self.notice)

        def work():
            try:
                self.app.manual_start_recording()
                result = "Recording started." if self.app.recorder.is_recording() else "Capture failed. Check encoder settings."
            except Exception as exc:
                result = str(exc)
            self.events.put(("action_done", result))

        threading.Thread(target=work, daemon=True).start()

    def stop_capture(self):
        if self.busy:
            return
        self.busy = True
        self._set_action_buttons_state("disabled")
        self.notice = "Stopping recording…"
        self.status_text.configure(text=self.notice)

        def work():
            try:
                self.app.manual_stop_recording()
                result = "Recording stopped. Standby."
            except Exception as exc:
                result = str(exc)
            self.events.put(("action_done", result))

        threading.Thread(target=work, daemon=True).start()

    def save_clip(self):
        if self.busy:
            return
        self.busy = True
        self._set_action_buttons_state("disabled")
        self.notice = "Saving clip…"
        self.status_text.configure(text=self.notice)

        def work():
            try:
                path = self.app.manual_save_clip(source="window")
                result = f"Saved {path.name}" if path else "No clip saved. Check buffer and try again."
            except Exception as exc:
                result = str(exc)
            self.events.put(("action_done", result))

        threading.Thread(target=work, daemon=True).start()

    def refresh(self):
        # Process async events
        while not self.events.empty():
            item = self.events.get()
            event_type = item[0]
            if event_type == "audio_devices":
                self._apply_audio_devices(item[1], item[2])
            elif event_type == "camera_devices":
                self._apply_cameras(item[1], item[2])
            elif event_type == "update_checked":
                self.checking_update = False
                self.check_button.configure(text="Check for update", state="normal")
                if self.manual_update:
                    if item[2]:
                        messagebox.showerror("Check for update", item[2], parent=self.root)
                    elif not item[1]:
                        messagebox.showinfo("Check for update", "You’re up to date.", parent=self.root)
                self.manual_update = False
                startup, self.startup_update = self.startup_update, False
                if item[1]:
                    self.pending_update = item[1]
                    self.check_button.configure(text="Restart to update", state="normal")
                    if startup and self.apply_startup_update():
                        return
            elif event_type == "action_done":
                self.notice = item[1]
                self.busy = False
                self._set_action_buttons_state("normal")
                if "Saved" in self.notice:
                    self.refresh_clips_grid()
            elif event_type == "camera_snapshot":
                ok, path = item[1], item[2]
                self.test_cam_btn.configure(state="normal")
                if ok and path.is_file():
                    try:
                        img = Image.open(path)
                        cfg = self.app.config
                        box_w = max(40, int((self.preview_w * cfg.overlay_width_percent) / 100.0))
                        box_h = max(30, int((self.preview_h * cfg.overlay_height_percent) / 100.0))
                        img = img.resize((box_w, box_h), Image.Resampling.LANCZOS)
                        self.camera_snapshot_photo = ImageTk.PhotoImage(img)
                        self.test_status_label.configure(text="Snapshot updated successfully!")
                        self.redraw_preview_canvas()
                    except Exception as exc:
                        self.test_status_label.configure(text=f"Error displaying snapshot: {exc}")
                else:
                    self.test_status_label.configure(text="Failed to capture camera snapshot.")
            elif event_type == "mic_test_done":
                level = item[1]
                if hasattr(self, "btn_test_mic"):
                    self.btn_test_mic.configure(state="normal")
                if hasattr(self, "mic_level_canvas") and hasattr(self, "mic_level_bar"):
                    c_w = self.mic_level_canvas.winfo_width() or 180
                    fill_w = max(2, int(c_w * min(1.0, level * 2.5)))
                    bar_color = palette.ACCENT if level > 0.02 else palette.MUTED
                    self.mic_level_canvas.coords(self.mic_level_bar, 0, 0, fill_w, 12)
                    self.mic_level_canvas.itemconfig(self.mic_level_bar, fill=bar_color)
                if hasattr(self, "mic_status_label"):
                    if level > 0.02:
                        self.mic_status_label.configure(text=f"Microphone detected sound! Peak: {int(level * 100)}%")
                    else:
                        self.mic_status_label.configure(text="Very quiet or no input detected. Speak louder or check mic device.")

        # Check background thumbnail worker
        while True:
            ready_thumb = self.thumbnails.get_ready()
            if not ready_thumb:
                break
            vpath, dur_str, thumbnail_ready = ready_thumb
            k = str(vpath.resolve())
            if k in self._clip_meta_cache and dur_str:
                self._clip_meta_cache[k]["duration_str"] = dur_str
            # Refresh grid if in library view
            canvas = getattr(self, "_thumb_canvases", {}).get(vpath)
            if canvas is not None and thumbnail_ready:
                photo = self.thumbnails.get_thumbnail(vpath, 280, 140)
                if photo:
                    canvas.delete("placeholder", "preview")
                    item = canvas.create_image(canvas.winfo_width() // 2, 70, image=photo, tags="preview")
                    canvas.tag_lower(item)
                    canvas.image = photo

        recording = self.app.recorder.is_recording()
        playback = self.app.recorder.playback
        mic_warning = playback.mic_warning if playback and recording else ""
        in_match = self.app.log_watcher.in_match
        log_status = self.app.log_watcher.log_status
        state = recording, in_match, log_status, self.notice, self.app.results.status, mic_warning

        if state != self.last_state:
            # Update Topbar Status Pill and Action Buttons
            if recording:
                self.status_canvas.itemconfigure(self.status_dot, fill=palette.ACCENT)
                self.status_text.configure(text="Recording: VALORANT", fg=palette.LINK)
                if hasattr(self, "top_btn_start"):
                    self.top_btn_start.pack_forget()
                if hasattr(self, "top_btn_save"):
                    self.top_btn_save.pack(side="left", padx=(0, 8))
                if hasattr(self, "top_btn_stop"):
                    self.top_btn_stop.pack(side="left")
            else:
                self.status_canvas.itemconfigure(self.status_dot, fill=palette.MUTED)
                self.status_text.configure(text=log_status or ("Match detected · recorder idle" if in_match else "Waiting for match"), fg=palette.MUTED)
                if hasattr(self, "top_btn_save"):
                    self.top_btn_save.pack_forget()
                if hasattr(self, "top_btn_stop"):
                    self.top_btn_stop.pack_forget()
                if hasattr(self, "top_btn_start"):
                    self.top_btn_start.pack(side="left")

            # Update Dashboard Status and Action Buttons
            if hasattr(self, "dash_status_text"):
                self.radar.itemconfigure(self.dash_dot, fill=palette.ACCENT if recording else palette.MUTED)
                self.dash_status_text.configure(
                    text="Recording" if recording else "Standby",
                    fg=palette.LINK if recording else palette.TEXT
                )
                detail_msg = self.notice or (f"{log_status} — {self.app.log_watcher.log_path}" if log_status else "") or mic_warning or self.app.results.status or (
                    "Your replay buffer is active. Kill highlights are auto-saved." if recording else "Waiting for match activity or click Start recording."
                )
                self.dash_detail.configure(text=detail_msg)

                if recording:
                    if hasattr(self, "dash_btn_start"):
                        self.dash_btn_start.pack_forget()
                    if hasattr(self, "dash_btn_save"):
                        self.dash_btn_save.pack(side="left", padx=(0, 10))
                    if hasattr(self, "dash_btn_stop"):
                        self.dash_btn_stop.pack(side="left", padx=(0, 10))
                    if hasattr(self, "dash_btn_folder"):
                        self.dash_btn_folder.pack(side="left")
                else:
                    if hasattr(self, "dash_btn_save"):
                        self.dash_btn_save.pack_forget()
                    if hasattr(self, "dash_btn_stop"):
                        self.dash_btn_stop.pack_forget()
                    if hasattr(self, "dash_btn_start"):
                        self.dash_btn_start.pack(side="left", padx=(0, 10))
                    if hasattr(self, "dash_btn_folder"):
                        self.dash_btn_folder.pack(side="left")

            self.last_state = state

        # Schedule next tick (1000ms normal, 5000ms minimized)
        delay = 1000 if self.root.state() == "normal" else 5000
        self.root.after(delay, self.refresh)

    def check_update(self, manual=False, *, startup=False):
        from .updates import stage_update
        bundle = os.environ.get("THORTCLIP_BUNDLE_TOOLS")
        if not bundle:
            if manual:
                import webbrowser
                webbrowser.open("https://github.com/rickyinnitdev/ThortClip/releases/latest")
            return
        if not manual:
            self.root.after(4 * 3600 * 1000, self.check_update)
        if self.pending_update:
            if manual:
                messagebox.showinfo("Check for update", "Update ready. Use Restart to update between matches.", parent=self.root)
            return
        self.startup_update |= startup
        self.manual_update |= manual
        if self.checking_update:
            return
        self.checking_update = True
        self.check_button.configure(text="Checking…")

        def work():
            try:
                channel = Path(bundle).parent / "release.json"
                path = stage_update(json.loads(channel.read_text(encoding="utf8")),
                                    Path(os.environ["LOCALAPPDATA"]) / "ThortClip" / "updates")
                self.events.put(("update_checked", path, ""))
            except Exception as exc:
                self.events.put(("update_checked", None, f"Could not check or download the update. {exc}"))

        threading.Thread(target=work, daemon=True).start()

    def apply_startup_update(self):
        if self.busy or self.active_view != "library" or not self.app._session_lock.acquire(blocking=False):
            return False
        try:
            if (self.app.recorder.is_recording() or self.app.log_watcher.in_match
                    or str(self.clip_player.btn_save_edits.cget("state")) == "disabled"
                    or (self.app.results.enabled and any(self.app.results.root.glob("*/ready.json")))):
                return False
            if not self.launch_update():
                return False
            self.app._running = False
        finally:
            self.app._session_lock.release()
        self.close()
        return True

    def restart_for_update(self):
        if self.busy or str(self.clip_player.btn_save_edits.cget("state")) == "disabled":
            messagebox.showinfo("Restart to update", "An action is in progress. Try again when it finishes.", parent=self.root)
            return
        if self.app.recorder.is_recording():
            if not messagebox.askyesno("Restart to update",
                    "Stop recording and restart ThortClip now? Capture will pause during the update.", parent=self.root):
                return
        if self.launch_update():
            self.close()

    def launch_update(self):
        try:
            subprocess.Popen([str(self.pending_update), "/update", str(os.getpid())],
                             creationflags=subprocess.CREATE_BREAKAWAY_FROM_JOB)
        except OSError as exc:
            messagebox.showerror("Update", str(exc), parent=self.root)
            return False
        return True

    def close(self):
        self.stop_live_preview()
        if hasattr(self, "clip_player"):
            self.clip_player.leave_player()
        self.root.withdraw()
        try:
            self.app.stop()
        finally:
            self.root.destroy()


def main():
    root = tk.Tk()
    try:
        contain_launcher()
        app = ThortClipApp()
        app.start_services()
        RecorderWindow(root, app)
    except Exception as exc:
        messagebox.showerror("ThortClip startup", str(exc), parent=root)
        root.destroy()
        return
    root.mainloop()


if __name__ == "__main__":
    main()
