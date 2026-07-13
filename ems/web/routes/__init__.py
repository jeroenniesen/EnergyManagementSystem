"""Per-domain `APIRouter`s extracted from `create_app` (BACKLOG B-25, incremental slice).

Each module exposes `build_router(ctx: AppContext) -> APIRouter`; `create_app` builds the context
once and includes each router. Only self-contained domains live here so far — car, digest, notify,
export, accuracy — the control/plan/insights/finance/settings routes stay in `api.py` until B-46
extracts the control service + full app-context they depend on.
"""
