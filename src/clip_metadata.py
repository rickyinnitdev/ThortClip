"""Paths for ThortClip clip metadata."""
import ctypes


def metadata_path(clip, *, create=False):
    folder = clip.parent / ".thortclip"
    if not create and not (folder / clip.with_suffix(".json").name).exists():
        legacy = clip.parent / ".medalrea" / clip.with_suffix(".json").name
        if legacy.is_file():
            return legacy
    if create and not folder.exists():
        folder.mkdir(exist_ok=True)
        kernel = ctypes.windll.kernel32
        attrs = kernel.GetFileAttributesW(str(folder))
        if attrs != -1:
            kernel.SetFileAttributesW(str(folder), attrs | 2)
    return folder / clip.with_suffix(".json").name
