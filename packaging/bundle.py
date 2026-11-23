"""Assemble an offline installer from a PyInstaller onedir build and Windows media tools."""
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

app, capture, probe, player, vlc, output = map(Path, sys.argv[1:])
tools = app / "tools"
tools.mkdir(exist_ok=True)
for binary in (capture, probe, player):
    shutil.copy2(binary, tools / binary.name)
video = tools / "vlc"
video.mkdir(exist_ok=True)
for name in ("libvlc.dll", "libvlccore.dll", "COPYING.txt", "AUTHORS.txt"):
    shutil.copy2(vlc / name, video / name)
shutil.copytree(vlc / "plugins", video / "plugins", dirs_exist_ok=True)
licenses = tools / "licenses"
licenses.mkdir(exist_ok=True)
(licenses / "FFmpeg.txt").write_text(subprocess.check_output([str(capture), "-L"], stderr=subprocess.STDOUT).decode(errors="replace"), encoding="utf8")
prefix = Path(sys.base_prefix)
if (prefix / "LICENSE.txt").exists():
    shutil.copy2(prefix / "LICENSE.txt", licenses / "Python.txt")
from importlib.metadata import distribution
for package in ("pillow","numpy","comtypes","dxcam","pyaudiowpatch","pyinstaller"):
    dist = distribution(package)
    for entry in dist.files or ():
        if ".dist-info/" in str(entry) and any(x in entry.name.upper() for x in ("LICENSE","COPYING","NOTICE")):
            shutil.copy2(dist.locate_file(entry), licenses / (package+"-"+entry.name))
output.parent.mkdir(exist_ok=True, parents=True)
with tempfile.TemporaryDirectory() as temp:
    payload = Path(temp) / "payload.zip"
    with zipfile.ZipFile(payload, "w", zipfile.ZIP_DEFLATED, compresslevel=3) as archive:
        for path in app.rglob("*"):
            if path.is_file():
                archive.write(path, path.relative_to(app))
    csc = Path(os.environ["WINDIR"]) / "Microsoft.NET/Framework64/v4.0.30319/csc.exe"
    subprocess.run([str(csc), "/nologo", "/target:winexe", "/platform:x64", f"/win32icon:{Path(__file__).resolve().parents[1] / 'src/assets/thotclip.ico'}",
                    "/reference:System.IO.Compression.dll", "/reference:System.IO.Compression.FileSystem.dll",
                    f"/resource:{payload},payload.zip", f"/out:{output}", str(Path(__file__).with_name("Setup.cs"))], check=True)
print(output, output.stat().st_size)
