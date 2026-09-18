from __future__ import annotations

from ems.application.context import ApplicationContext, require_collaborator


class DiagnosticsService:
    """Assemble diagnostics without depending on HTTP or FastAPI."""

    def __init__(self, context: ApplicationContext):
        self.context = context

    async def get_snapshot(self) -> dict[str, object]:
        snapshot = require_collaborator(self.context.diagnostics_snapshot, "diagnostics_snapshot")
        return await snapshot(self.context.clock.now_utc())
