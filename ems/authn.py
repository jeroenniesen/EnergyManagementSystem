from __future__ import annotations

import hashlib
import secrets

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

# Argon2id (argon2-cffi default). Params bounded for a Raspberry Pi 5.
_ph = PasswordHasher(time_cost=2, memory_cost=64 * 1024, parallelism=2)
# Precomputed hash so the missing-user login path does equal work (no timing oracle).
_DUMMY_HASH = _ph.hash("dummy-password-for-timing-equalization")


def hash_password(password: str) -> str:
    return _ph.hash(password)


def verify_password(encoded: str, password: str) -> bool:
    try:
        return _ph.verify(encoded, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def dummy_verify() -> None:
    """Constant-time-ish work for the user-not-found path."""
    try:
        _ph.verify(_DUMMY_HASH, "definitely-wrong")
    except Exception:
        pass


def new_token() -> str:
    return secrets.token_urlsafe(32)  # 256-bit


def hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
