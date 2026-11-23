"""Download verified installers from the release channel embedded by the build."""
import hashlib
import json
import os
import re
import tempfile
from pathlib import Path
from urllib.request import Request, urlopen

ASSET = "ThotClip-Setup-x64.exe"


def stage_update(channel, directory):
    repo, build = channel["repository"], channel["build"]
    if not re.fullmatch(r"[\w.-]+/[\w.-]+", repo):
        raise ValueError("Invalid release repository")
    request = Request(f"https://api.github.com/repos/{repo}/releases/latest",
                      headers={"User-Agent": "ThotClip-Updater", "Accept": "application/vnd.github+json"})
    with urlopen(request, timeout=15) as response:
        release = json.load(response)
    tag = release["tag_name"]
    match = re.fullmatch(r"build-(\d+)", tag)
    if not match or int(match[1]) <= build or release["draft"] or release["prerelease"]:
        return None
    asset = next(a for a in release["assets"] if a["name"] == ASSET)
    digest = asset.get("digest") or ""
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
        raise ValueError("Release asset has no SHA-256 digest")
    directory.mkdir(parents=True, exist_ok=True)
    dest = directory / f"{tag}-{ASSET}"
    if dest.exists():
        with dest.open("rb") as existing:
            if hashlib.file_digest(existing, "sha256").hexdigest() == digest[7:]:
                return dest
    tmp = None
    try:
        with tempfile.NamedTemporaryFile(dir=directory, delete=False) as out:
            tmp = Path(out.name)
            checksum = hashlib.sha256()
            with urlopen(f"https://github.com/{repo}/releases/download/{tag}/{ASSET}", timeout=45) as response:
                while block := response.read(1024 * 1024):
                    out.write(block)
                    checksum.update(block)
            if out.tell() != asset["size"] or checksum.hexdigest() != digest[7:]:
                raise ValueError("Update checksum mismatch")
        os.replace(tmp, dest)
        return dest
    finally:
        if tmp:
            tmp.unlink(missing_ok=True)
