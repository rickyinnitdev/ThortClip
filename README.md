# ThortClip

ThortClip is a local VALORANT highlight recorder for Windows x64. It reads the kill feed and saves one MP4 per round where it detects your kills, from the first kill through the last. Rounds with zero detected personal kills produce zero automatic clips. Press **F8** during recording to save a play manually. Automatic clipping works without a Riot account connection or API key.

**[Download ThortClip for Windows x64](https://github.com/rickyinnitdev/ThortClip/releases/latest/download/ThortClip-Setup-x64.exe)** · [All releases](https://github.com/rickyinnitdev/ThortClip/releases) · [GitHub Packages](https://github.com/rickyinnitdev/ThortClip/packages)

## Project status · Build 11

- **Automatic highlights:** In a 20-round competitive match on Lotus, the saved highlight counts added up to all 17 kills on the scoreboard. The library example below uses that match.
- **Recording and updates:** ThortClip recognizes a match already in progress when you open it. It checks encoder support using frames from the captured display, starts the optional webcam alongside gameplay capture, and lets you restart from the sidebar when an update is ready.
- **Window switching:** Build 11 disables Windows transition animations for the ThortClip window to address reports of a faded copy staying on screen after switching apps. One controlled Windows test passed 12 switch and minimize/restore checks. Confirmation during everyday play is still pending. [Build 11 release](https://github.com/rickyinnitdev/ThortClip/releases/tag/build-11)

## Record your first clip

1. Run **ThortClip-Setup-x64.exe** and open **ThortClip** from the Start menu. The installer includes Python, FFmpeg, and the media player.
2. Leave the app open and enter a match. **Capture Status** shows when recording is active. You can also choose **Start recording** to try it before a match.
3. Press **F8** during recording, or finish a round with a personal kill. Open **Clips Library** to play the highlight. Click a kill marker to jump to that moment, or choose **Open clip in folder** to select the MP4 in Explorer.

On a fresh install, clips are saved under **Videos → ThortClip**. Check **Settings → Clips Output Directory** for the actual location, especially after upgrading from an earlier version.

## Find your 1K clips

In **Clips Library**, select **All clips** to see single-kill rounds. **Multi-kills** shows only clips with two or more kills. Switching filters only changes what you see in the library. ThortClip still saves 1K rounds while the filter is selected.

Each automatic highlight covers one round. The badge tells you how many personal kills ThortClip detected in that round. For example, a match with three 1K rounds, four 2K rounds, and two 3K rounds produces **9 clips covering 17 kills**. The **Multi-kills** view shows six of those clips, covering 14 kills.

## Make it yours

| What you want | Where to find it |
| --- | --- |
| Record game sound and your voice | In **Audio Settings**, choose the playback and microphone devices, enable the inputs you want, and adjust their levels. |
| Include a webcam | In **Video Overlays**, select a camera and click **Start live preview**. Drag or resize the box, then enable the overlay for recordings. |
| Include the end of a round | In **Capture Status**, enable **Include round ending in kill clips**. This extends a highlight that already contains a kill. |
| Match a stretched game resolution | In **Settings**, enable **Fill widescreen clips (16:9)** for new exports. Turn it off to keep the capture’s original aspect ratio. |
| Switch to a dark interface | Toggle **Dark mode** in **Settings**. ThortClip remembers your choice. |
| Trim a clip | Open a clip in **Clips Library**, drag the trim handles, and choose **Save Edits** to save a separate copy. |

## Capture and updates

On first launch, the installer-created profile uses the primary display resolution and tests NVIDIA, AMD, and Intel H.264 encoders. If a hardware test succeeds, the profile uses 60 FPS. The software fallback uses 30 FPS.

The recorder keeps a rolling buffer of short segments. Standard highlights use stream copy; adding a webcam overlay requires video re-encoding. The detector looks for the local attacker and checks the spectator HUD to avoid crediting a teammate’s kill.

Click **Check for update** below Settings in the sidebar to check immediately. The installed app also checks GitHub Releases on launch and every four hours. When a newer installer has downloaded and passed its checksum check, the sidebar button changes to **Restart to update**. If capture is running, ThortClip asks before stopping it. Let an active clip export finish before restarting. Source checkouts launched with `run.bat` use Git for updates.

## Development

The desktop interface is in [src/gui.py](src/gui.py), capture and round handling in [src/main.py](src/main.py), and the local kill-feed detector in [src/kill_sources/screen_detector.py](src/kill_sources/screen_detector.py). The [Windows release workflow](.github/workflows/release.yml) specifies the build dependencies and packages the installer.

The installed profile lives at `%LOCALAPPDATA%\MedalRea\profile\config.json`. The legacy folder name preserves settings across upgrades. A source checkout uses a local `config.json` in the repository root. For recorder tests, use an isolated configuration and temporary output folders; starting capture can clear the configured segment directory.

For authorized post-match results, configure `MEDALREA_RIOT_API_KEY`, `MEDALREA_PLAYER_PUUID`, and `MEDALREA_VAL_REGION` for the approved application and opted-in player. With that integration enabled, pending match recordings are retained while the app waits for results.

## License

Copyright 2026 Morm Kimheng (rickyinnitdev). ThortClip’s own source code is licensed under **GPL-3.0-only**. Bundled third-party components retain their respective licenses. [Read the full license](LICENSE).
