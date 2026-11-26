# ThortClip

ThortClip is a local VALORANT highlight recorder for Windows x64. It reads the kill feed and saves one MP4 per round where it detects your kills, from the first kill through the last. It skips rounds with no detected personal kills. Press **F8** during recording to save a play manually. Automatic clipping works without a Riot account connection or API key.

**[Download ThortClip for Windows x64](https://github.com/rickyinnitdev/ThortClip/releases/latest/download/ThortClip-Setup-x64.exe)** · [All releases](https://github.com/rickyinnitdev/ThortClip/releases) · [GitHub Packages](https://github.com/rickyinnitdev/ThortClip/packages)

## Record your first clip

1. Run **ThortClip-Setup-x64.exe** and open **ThortClip** from the Start menu. The installer includes Python, FFmpeg, and the media player.
2. Leave the app open and enter a match. **Capture Status** shows when recording is active. You can also choose **Start recording** to try it before a match.
3. Press **F8** during recording, or finish a round with a personal kill. Open **Clips Library** to play the highlight. Click a kill marker to jump to that moment, or choose **Open clip in folder** to select the MP4 in Explorer.

On a fresh install, clips are saved under **Videos → ThortClip**. Check **Settings → Clips Output Directory** for the actual location, especially after upgrading from an earlier version.

## Browse your highlights

Open **Clips Library** and select **All clips** to see every saved highlight, including single-kill rounds. Each automatic clip has a badge showing how many of your kills ThortClip detected in that round. Select **Multi-kills** to show rounds with two or more detected kills. ThortClip keeps saving single-kill highlights while this filter is active.

## Customize your recordings

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

The installed profile lives at `%LOCALAPPDATA%\ThortClip\profile\config.json`. On upgrade, the launcher copies an existing profile on first use. A source checkout uses a local `config.json` in the repository root. For recorder tests, use an isolated configuration and temporary output folders; starting capture can clear the configured segment directory.

For authorized post-match results, configure `THORTCLIP_RIOT_API_KEY`, `THORTCLIP_PLAYER_PUUID`, and `THORTCLIP_VAL_REGION` for the approved application and opted-in player. With that integration enabled, pending match recordings are retained while the app waits for results.

## License

Copyright 2026 Morm Kimheng (rickyinnitdev). ThortClip’s own source code is licensed under **GPL-3.0-only**. Bundled third-party components retain their respective licenses. [Read the full license](LICENSE).
