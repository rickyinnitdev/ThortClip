import json
import os
import sys
from pathlib import Path

root = Path(sys.executable).parent
tools = root / "tools"
os.environ["PATH"] = str(tools) + os.pathsep + os.environ["PATH"]
os.environ["MEDALREA_BUNDLE_TOOLS"] = str(tools)
data = Path(os.environ["LOCALAPPDATA"]) / "MedalRea" / "profile"
data.mkdir(parents=True, exist_ok=True)
os.chdir(data)
from src.config import detect_encoder
from src.recorder import get_primary_display_info
from src.gui import main

if not Path("config.json").exists():
    w, h, _ = get_primary_display_info()
    encoder = detect_encoder()
    settings = {
        "recorder": {"capture_method": "gfxcapture", "encoder": encoder,
                     "resolution": f"{w}x{h}", "fps": 30 if encoder == "libx264" else 60,
                     "stretch_to_widescreen": True, "audio_device": "system", "auto_clip_round": False},
        "paths": {"clips_output_dir": str(Path.home() / "Videos" / "Thot Clip")},
        "overlay": {"enabled": False, "device": ""}
    }
    Path("config.json").write_text(json.dumps(settings, indent=2), encoding="utf8")
main()
