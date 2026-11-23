"""Paths for MedalRea clip metadata."""
import ctypes


def metadata_path(clip, *, create=False):
    folder = clip.parent / ".medalrea"
    if create and not folder.exists():
        folder.mkdir(exist_ok=True)
        kernel = ctypes.windll.kernel32
        attrs = kernel.GetFileAttributesW(str(folder))
        if attrs != -1:
            kernel.SetFileAttributesW(str(folder), attrs | 2)
    return folder / clip.with_suffix(".json").name
