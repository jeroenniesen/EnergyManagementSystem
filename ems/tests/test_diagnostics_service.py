import asyncio

from ems.application.context import ApplicationContext
from ems.application.services.diagnostics import DiagnosticsService


def test_diagnostics_service_returns_snapshot_without_http():
    async def snapshot():
        return {"overall": "ok", "checks": []}

    svc = DiagnosticsService(ApplicationContext(source=None,
        runtime_state={"diagnostics_snapshot": snapshot}))
    assert asyncio.run(svc.get_snapshot()) == {"overall": "ok", "checks": []}
