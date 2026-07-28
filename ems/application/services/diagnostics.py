from __future__ import annotations

from ems.application.context import ApplicationContext


class DiagnosticsService:
    """Assemble diagnostics without depending on HTTP or FastAPI."""

    def __init__(self, context: ApplicationContext):
        self.context = context

    async def get_snapshot(self) -> dict[str, object]:
        return await self.context.runtime_state["diagnostics_snapshot"]()
