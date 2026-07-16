"""Notification outbox (BACKLOG B-20): an in-app surface (the `notifications` table, via
`HistoryStore`) plus an optional ntfy.sh (or self-hosted) push channel — real phone pushes with no
APNs/cloud account: point `notify.ntfy_url` at ntfy.sh or a self-hosted instance, pick a topic, and
subscribe to it in the ntfy app.

This module is the RAILS, proven end-to-end by exactly one source (a failing scheduled backup, see
`ems/web/api.py` `_run_backup`). Which conditions deserve a push ("detectors") is a separate,
later task — `Notifier.send()` is a plain API any future source can call.

Fail-safe like every other optional channel in this codebase (see `ems/sources/carbon.py`):
`send()` NEVER raises. A misconfigured/unreachable ntfy target, or even a store failure, is
logged and swallowed — a notification hiccup must never take down the caller (e.g. the
maintenance loop)."""
from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from ems.storage.history import HistoryStore

_log = logging.getLogger("ems.notify")
_NTFY_TIMEOUT_SECONDS = 5.0

# (target_url, body_bytes, headers) -> None. Raises on transport/HTTP error — send() catches it.
PostFn = Callable[[str, bytes, dict], None]


def _default_post(url: str, data: bytes, headers: dict) -> None:
    import httpx

    r = httpx.post(url, content=data, headers=headers, timeout=_NTFY_TIMEOUT_SECONDS)
    r.raise_for_status()


class Notifier:
    """`send()` always stores (dedupe-aware) via the history store; when `notify.ntfy_url` +
    `notify.ntfy_topic` are both set, it also POSTs the title+body to `<ntfy_url>/<topic>`.

    `settings` is read LIVE on every call (a plain dict such as the app's in-memory
    `settings_cache`, or a zero-arg callable returning one) so a just-saved ntfy config applies to
    the very next send — no restart, mirroring the explainer/site settings wiring in `api.py`."""

    def __init__(
        self,
        store: HistoryStore,
        settings: dict[str, Any] | Callable[[], dict[str, Any]],
        *,
        post: PostFn | None = None,
    ) -> None:
        self.store = store
        self._settings = settings
        self._post = post or _default_post

    def _cfg(self) -> dict[str, Any]:
        return self._settings() if callable(self._settings) else self._settings

    async def send(
        self, key: str, title: str, body: str, *,
        confidence: str | None = None, dedupe_key: str | None = None,
        push_even_if_store_fails: bool = False,
    ) -> int | None:
        """Store + (optionally) push one notification. Returns the new row id, or None if it was
        deduped (already sent — see `HistoryStore.add_notification`) or if storing itself failed.
        Never raises.

        `push_even_if_store_fails` (targeted, B-49): normally a failed in-app store write short-
        circuits the push too. But the `store_unhealthy` alert exists PRECISELY because the history
        store is wedged — so for it we still attempt the ntfy push (ntfy needs no store), so the
        operator gets word even though the in-app copy couldn't be written. The delivered-channel
        update is skipped in that case (there's no row to update)."""
        row_id: int | None = None
        stored = True
        try:
            row_id = await self.store.add_notification(
                datetime.now(UTC).isoformat(), key, title, body,
                confidence=confidence, dedupe_key=dedupe_key,
            )
        except Exception as exc:
            _log.warning("notification store failed (non-fatal): %s: %s", type(exc).__name__, exc)
            stored = False
            if not push_even_if_store_fails:
                return None
            # else: fall through and still try ntfy — the dead store is what we're alerting about
        if stored and row_id is None:
            return None  # deduped — already sent for this dedupe_key, skip the push too
        cfg = self._cfg()
        url = str(cfg.get("notify.ntfy_url") or "").strip()
        topic = str(cfg.get("notify.ntfy_topic") or "").strip()
        if not url or not topic:
            return row_id
        try:
            import asyncio

            target = f"{url.rstrip('/')}/{topic}"
            await asyncio.to_thread(self._post, target, body.encode(), {"Title": title})
            if row_id is not None:  # skip when the store write failed (no row to mark delivered)
                await self.store.set_notification_delivered(row_id, ["in_app", "ntfy"])
        except Exception as exc:
            _log.warning("ntfy push failed (non-fatal): %s: %s", type(exc).__name__, exc)
        return row_id
