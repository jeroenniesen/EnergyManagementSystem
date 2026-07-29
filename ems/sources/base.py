"""The Source port: any telemetry source returns a sign-normalised RawSample (SOLID, SPEC §13)."""
from __future__ import annotations

from .ports import Source

__all__ = ["Source"]
