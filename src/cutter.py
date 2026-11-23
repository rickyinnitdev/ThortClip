import tempfile
import time
import subprocess
from pathlib import Path
from typing import List, Optional, Dict, Any

from .camera_manager import get_ffmpeg_binary

class VideoCutter:
    """Concatenates completed .ts segments into a pristine .mp4 clip with optional webcam overlay."""

    def __init__(self, output_dir: Path):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def cut_from_segments(
        self,
        segments: List[Path],
        clip_name: Optional[str] = None,
        round_number: Optional[int] = None,
        kill_count: Optional[int] = None,
        cam_segments: Optional[List[Path]] = None,
        overlay_config: Optional[Dict[str, Any]] = None,
        screen_resolution: str = "1568x1080",
        bitrate: str = "24M",
        encoder: str = "hevc_nvenc",
        cam_offset: float = 0.0,
    ) -> Optional[Path]:
        if not segments:
            print("[Cutter] Warning: No completed segments available to cut.")
            return None

        if not clip_name:
            ts_str = time.strftime("%Y%m%d_%H%M%S") + f"_{time.time_ns() % 1000000000:09d}"
            prefix = "Clip"
            if kill_count is not None:
                prefix = f"{kill_count}K"
            round_str = f"_Round{round_number}" if round_number is not None else ""
            clip_name = f"{prefix}{round_str}_{ts_str}.mp4"

        output_path = self.output_dir / clip_name
        concat_txt = None
        cam_concat_txt = None
        pending = None
        binary = get_ffmpeg_binary()

        # Check if overlay is enabled and webcam segments exist
        use_overlay = bool(
            overlay_config
            and overlay_config.get("enabled")
            and cam_segments
        )

        try:
            with tempfile.NamedTemporaryFile(mode="w", dir=self.output_dir, delete=False,
                                             suffix=".txt", encoding="utf-8") as f:
                concat_txt = Path(f.name)
                for seg in segments:
                    clean_path = str(seg.resolve()).replace("\\", "/")
                    f.write(f"file '{clean_path}'\n")

            pending = concat_txt.with_suffix(".partial.mp4")

            if use_overlay:
                with tempfile.NamedTemporaryFile(mode="w", dir=self.output_dir, delete=False,
                                                 suffix="_cam.txt", encoding="utf-8") as f_cam:
                    cam_concat_txt = Path(f_cam.name)
                    for seg in cam_segments:
                        clean_path = str(seg.resolve()).replace("\\", "/")
                        f_cam.write(f"file '{clean_path}'\n")

                try:
                    parts = screen_resolution.lower().split("x")
                    sw, sh = int(parts[0]), int(parts[1])
                except Exception:
                    sw, sh = 1568, 1080

                width_pct = float(overlay_config.get("width_percent", 20.0))
                height_pct = float(overlay_config.get("height_percent", 25.0))
                cam_w = max(120, round(sw * width_pct / 100))
                cam_h = max(90, round(sh * height_pct / 100))
                cam_w -= (cam_w % 2)
                cam_h -= (cam_h % 2)

                preset = overlay_config.get("position", "bottom-right")
                margin = 24
                if preset == "top-left":
                    pos_x = margin
                    pos_y = margin
                elif preset == "top-right":
                    pos_x = sw - cam_w - margin
                    pos_y = margin
                elif preset == "bottom-left":
                    pos_x = margin
                    pos_y = sh - cam_h - margin
                elif preset == "bottom-right":
                    pos_x = sw - cam_w - margin
                    pos_y = sh - cam_h - margin
                elif preset == "center":
                    pos_x = (sw - cam_w) // 2
                    pos_y = (sh - cam_h) // 2
                else:
                    pos_x = round(sw * float(overlay_config.get("x_percent", 80.0)) / 100)
                    pos_y = round(sh * float(overlay_config.get("y_percent", 72.0)) / 100)

                pos_x = max(0, min(pos_x, sw - cam_w))
                pos_y = max(0, min(pos_y, sh - cam_h))

                filter_complex = (f"[1:v]trim=start={max(0, -cam_offset):.6f},setpts=PTS-STARTPTS+{max(0, cam_offset):.6f}/TB,"
                                  f"scale={cam_w}:{cam_h}[cam];[0:v][cam]overlay={pos_x}:{pos_y}:eof_action=pass[outv]")
                cmd = [
                    binary,
                    "-y",
                    "-f", "concat",
                    "-safe", "0",
                    "-i", str(concat_txt),
                    "-f", "concat",
                    "-safe", "0",
                    "-i", str(cam_concat_txt),
                    "-filter_complex", filter_complex,
                    "-map", "[outv]",
                    "-map", "0:a?",
                ]
                if encoder in ("hevc_nvenc", "h264_nvenc"):
                    cmd += ["-c:v", encoder, "-preset", "p1", "-tune", "ull", "-b:v", bitrate]
                else:
                    cmd += ["-c:v", "libx264", "-preset", "ultrafast", "-tune", "zerolatency", "-b:v", bitrate]

                cmd += [
                    "-c:a", "copy",
                    "-movflags", "+faststart",
                    str(pending),
                ]
            else:
                cmd = [
                    binary,
                    "-y",
                    "-f", "concat",
                    "-safe", "0",
                    "-i", str(concat_txt),
                    "-c", "copy",
                    "-movflags", "+faststart",
                    str(pending),
                ]

            start_t = time.time()
            res = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            elapsed = time.time() - start_t

            if res.returncode == 0 and pending.is_file():
                pending.replace(output_path)
                mode_str = "with webcam overlay" if use_overlay else "stream copy"
                print(f"[Cutter] Saved {len(segments)*2}s clip ({mode_str}) in {elapsed:.2f}s -> {output_path.name}")
                return output_path
            else:
                # If overlay cut failed, fall back to stream-copy
                if use_overlay:
                    print(f"[Cutter] Overlay cut failed ({res.stderr.decode(errors='ignore')[-150:]}), falling back to stream copy...")
                    cmd_fallback = [
                        binary, "-y", "-f", "concat", "-safe", "0",
                        "-i", str(concat_txt), "-c", "copy", "-movflags", "+faststart", str(pending)
                    ]
                    res2 = subprocess.run(cmd_fallback, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                          creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                    if res2.returncode == 0 and pending.is_file():
                        pending.replace(output_path)
                        print(f"[Cutter] Saved fallback clip in {time.time()-start_t:.2f}s -> {output_path.name}")
                        return output_path
                err_snippet = res.stderr.decode(errors="ignore")[-200:]
                print(f"[Cutter] Concat failed: {err_snippet}")
                return None
        except Exception as e:
            print(f"[Cutter] Error cutting clip: {e}")
            return None
        finally:
            try:
                if pending:
                    pending.unlink(missing_ok=True)
                if concat_txt:
                    concat_txt.unlink(missing_ok=True)
                if cam_concat_txt:
                    cam_concat_txt.unlink(missing_ok=True)
            except Exception:
                pass
