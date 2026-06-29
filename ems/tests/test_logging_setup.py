"""Size-rotated logging for the 24/7 install: EMS_LOG_FILE attaches a bounded RotatingFileHandler
to the `ems` logger; unset is a no-op (console logging, as in dev)."""
import logging
from logging.handlers import RotatingFileHandler

from ems.logging_setup import configure_logging


def _ems_file_handlers():
    return [h for h in logging.getLogger("ems").handlers if isinstance(h, RotatingFileHandler)]


def test_rotating_handler_attached_when_env_set(tmp_path, monkeypatch):
    log = logging.getLogger("ems")
    saved = list(log.handlers)
    try:
        log.handlers = [h for h in saved if not isinstance(h, RotatingFileHandler)]
        monkeypatch.setenv("EMS_LOG_FILE", str(tmp_path / "server.log"))
        monkeypatch.setenv("EMS_LOG_MAX_MB", "1")
        monkeypatch.setenv("EMS_LOG_BACKUPS", "2")
        assert configure_logging() is True
        handlers = _ems_file_handlers()
        assert len(handlers) == 1
        assert handlers[0].maxBytes == 1024 * 1024 and handlers[0].backupCount == 2
        # Idempotent — a second call (e.g. re-import) must not stack a second file handler.
        configure_logging()
        assert len(_ems_file_handlers()) == 1
    finally:
        log.handlers = saved


def test_no_file_handler_without_env(monkeypatch):
    monkeypatch.delenv("EMS_LOG_FILE", raising=False)
    log = logging.getLogger("ems")
    saved = list(log.handlers)
    try:
        log.handlers = [h for h in saved if not isinstance(h, RotatingFileHandler)]
        assert configure_logging() is False
        assert _ems_file_handlers() == []
    finally:
        log.handlers = saved
