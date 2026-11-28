"""Small local error log for support reports."""
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import re
import threading


log = logging.getLogger("thortclip.support")
log.addHandler(logging.NullHandler())
log.propagate = False
LOG_PATH = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "ThortClip" / "support-errors.log"


def start():
    if any(isinstance(handler, RotatingFileHandler) for handler in log.handlers):
        return
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(LOG_PATH, maxBytes=64 * 1024, backupCount=1, encoding="utf-8")
    except OSError:
        return
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s [%(threadName)s] %(message)s"))
    log.addHandler(handler)
    previous_hook = threading.excepthook

    def log_thread_error(args):
        if args.exc_type is not SystemExit:
            log.error("Background task failed", exc_info=(args.exc_type, args.exc_value, args.exc_traceback))
        previous_hook(args)

    threading.excepthook = log_thread_error


def recent_errors():
    try:
        with LOG_PATH.open("rb") as file:
            size = file.seek(0, os.SEEK_END)
            file.seek(max(0, size - 16 * 1024))
            report = file.read(16 * 1024).decode("utf-8", errors="replace").strip()
    except FileNotFoundError:
        return ""
    for location in {str(Path.home()), str(Path.home()).replace("\\", "/")}:
        report = re.sub(re.escape(location), lambda _: "%USERPROFILE%", report, flags=re.IGNORECASE)
    return report
