"""Verify an EMS SQLite backup without modifying it."""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path


def verify(path: str | Path) -> bool:
    backup = Path(path)
    if not backup.is_file() or backup.stat().st_size == 0:
        return False
    try:
        with sqlite3.connect(f"file:{backup}?mode=ro", uri=True, timeout=2.0) as db:
            row = db.execute("PRAGMA integrity_check").fetchone()
        return bool(row and row[0] == "ok")
    except (OSError, sqlite3.DatabaseError):
        return False


if __name__ == "__main__":
    if len(sys.argv) != 2 or not verify(sys.argv[1]):
        raise SystemExit(1)
