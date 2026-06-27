"""The Source port: any telemetry source returns a sign-normalised RawSample (SOLID, SPEC §13)."""
from __future__ import annotations

from typing import Protocol

from ems.domain import RawSample


class Source(Protocol):
    def read(self) -> RawSample: ...
