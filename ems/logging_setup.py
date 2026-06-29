"""Size-rotated logging for the 24/7 install (SPEC §11 / long-running review).

When EMS_LOG_FILE is set (the Mac LaunchAgent install), the app's logs go to a bounded, rotating
file so repeated device / Tibber / Forecast.Solar failures can't grow it without limit. Attached to
the `ems` logger (not root) so it survives uvicorn's own logging config. No-op when unset (dev /
foreground), leaving logging on the console as before."""
from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path


def configure_logging() -> bool:
    """Attach a RotatingFileHandler to the `ems` logger if EMS_LOG_FILE is set. Idempotent (a
    re-call won't stack handlers). Returns True if a file handler is now active, else False."""
    path = os.environ.get("EMS_LOG_FILE")
    log = logging.getLogger("ems")
    if not path:
        return False
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    max_bytes = int(float(os.environ.get("EMS_LOG_MAX_MB", "5")) * 1024 * 1024)
    backups = int(os.environ.get("EMS_LOG_BACKUPS", "5"))
    handler = RotatingFileHandler(path, maxBytes=max_bytes, backupCount=backups)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    log.setLevel(os.environ.get("EMS_LOG_LEVEL", "INFO").upper())
    log.handlers = [h for h in log.handlers if not isinstance(h, RotatingFileHandler)]  # idempotent
    log.addHandler(handler)
    return True
