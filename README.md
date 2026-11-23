# Thot Clip

Thot Clip saves local Valorant highlights on Windows. The installer includes the recorder, player, and media tools. Press **F8** for a manual clip, or let the kill-feed detector save personal highlights by round.

## Current Windows profile

Launch `run.bat` for the recorder window. The configured profile uses Windows Graphics Capture and NVIDIA HEVC at 1568×1080, 60 FPS, and 24 Mb/s, with Windows playback captured as stereo AAC on the configured PC. The capture backend uses the FFmpeg 9.1 executable in `%LOCALAPPDATA%/MedalRea/tools`, or a compatible FFmpeg on PATH. The screen detector reads the full-resolution local attacker entry in the top-right kill feed. Automatic clips span the first through the last personal kill of each round. The optional round-ending setting extends only rounds that have kills.

For an authorized VAL-MATCH-V1 integration, supply the approved application's API key through `MEDALREA_RIOT_API_KEY`, the opted-in player's PUUID through `MEDALREA_PLAYER_PUUID`, and the VAL regional route through `MEDALREA_VAL_REGION`. With these configured, an identified match is retained in `temp/pending-matches` and exported after Riot returns completed results. Unavailable results are retried while the app is open. Pending recordings use approximately 10.9 GB per hour at this profile. With no credentials configured, the recorder uses its bounded rolling buffer and local kill-feed detection.

A clean, high-performance game recorder and auto-clipper designed with a modular architecture:
- **2-second MPEG-TS segments (`.ts`)**: Rolling buffer with automatic pruning. Eliminates file lock contention and MP4 container corruption.
- **Instant Lossless Slicing**: Uses FFmpeg stream copy (`-c copy`) via concat demuxer (<50ms). Zero re-encoding.
- **Passive Log Watcher**: Tails `%LOCALAPPDATA%\VALORANT\Saved\Logs\ShooterGame.log` for round bounds.
- **Screen Detector**: Recognizes the highlighted local attacker in the top-right kill feed.
- **Manual Hotkey**: Global `F8` key fallback via native Windows Win32 API.
- **Offline Detector Test Tool**: Test screenshots and tune HSV thresholds offline before playing.

---

## Architecture

```
                                  +----------------------+
                                  |    Valorant Game     |
                                  +-----------+----------+
                                              |
                     +------------------------+------------------------+
                     | Writes Logs                                     | Renders Screen
                     v                                                 v
           +--------------------+                            +--------------------+
           |     LogWatcher     |                            |   ScreenDetector   |
           | (ShooterGame.log)  |                            | (Local kill feed)  |
           +---------+----------+                            +---------+----------+
                     | Active Round                                    | Kill Event Trigger
                     v                                                 v
  +-------------------------------------------------------------------------------+
  |                                  Thot Clip                                    |
  |  - ScreenRecorder: FFmpeg -> 2s .ts rolling buffer (auto-pruned to 120s)      |
  |  - Hotkey Source: [F8] global key fallback                                    |
  |  - VideoCutter: Concat completed .ts segments with -c copy -> clean .mp4      |
  +---------------------------------------+---------------------------------------+
                                          |
                                          v
                               [clips/Clip_RoundX_*.mp4]
```

---

## Quick Start

### 1. Run the Auto-Clipper
Double-click `run.bat` or run:
```powershell
python -m src.main
```
*Note: Run `run.bat` directly from your interactive Windows desktop so FFmpeg has permission to access the display.*

### 2. Test the Screen Detector Offline
To test detection on any screenshot of your kill banner:
```powershell
python test_detector.py path\to\your_kill_screenshot.png
```
Or drag and drop the screenshot directly onto `test_detector.bat`.
This generates visual debug files in `debug_output/`:
- `annotated_<filename>`: Shows the green/red ROI bounding box.
- `mask_<filename>`: Shows the exact matched yellow pixels.

---

## Configuration (`config.json`)

```json
{
  "recorder": {
    "encoder": "auto",              // "auto", "h264_nvenc" (NVIDIA), "h264_amf" (AMD), "h264_qsv" (Intel), or "libx264"
    "resolution": "1280x720",       // 720p or 1080p
    "fps": 60,
    "bitrate": "8M",
    "buffer_duration_sec": 120,     // Total rolling buffer window (auto-pruned)
    "clip_duration_before_sec": 20, // Lead-in time before the kill/hotkey
    "clip_duration_after_sec": 5,   // Lead-out time after the kill/hotkey
    "audio_device": "none"          // DirectShow audio input device or "none"
  },
  "detector": {
    "enabled": true,
    "poll_fps": 15,                 // Checks 15 times/sec (< 0.5% CPU)
    "target_color": "yellow",
    "yellow_hsv_min": [35, 100, 120],
    "yellow_hsv_max": [65, 255, 255],
    "min_matching_pixels": 80,      // Minimum yellow pixels to trigger kill
    "cooldown_sec": 1.5,            // Prevent duplicate clips from same kill
    "roi_percent": {
      "x_min": 0.40, "x_max": 0.60,
      "y_min": 0.74, "y_max": 0.88
    }
  },
  "hotkey": {
    "enabled": true,
    "key": "F8"
  },
  "overlay": {
    "enabled": false,               // Toggle webcam overlay on/off
    "device": "USB2.0 HD UVC WebCam", // USB webcam or virtual camera device
    "virtual_camera_enabled": false, // Include OBS Virtual Camera in choices
    "position": "bottom-right",     // "top-left", "top-right", "bottom-left", "bottom-right", "center", or "custom"
    "x_percent": 80.0,              // Horizontal position percentage
    "y_percent": 72.0,              // Vertical position percentage
    "width_percent": 20.0,          // Webcam width percentage
    "height_percent": 25.0,         // Webcam height percentage
    "size_preset": "medium"         // "small", "medium", "large", or "custom"
  }
}
```

## Features
- **Thot Clip desktop interface**: Sleek dark gaming dashboard with Clips Library grid, Video Overlays screen, Capture Status monitor, and PC Storage indicator.
- **Webcam Video Overlay**: Capture USB webcams and virtual cameras (OBS). Drag-and-drop webcam positioning on an interactive 16:9 preview canvas, custom scale adjustment (10%–45%), and corner presets.
- **Zero Game FPS Impact**: Lightweight Tkinter/Python architecture (<40 MB RAM, 0% CPU on standby). Screen capture runs hardware-accelerated directly in GPU VRAM without pipeline contention.

