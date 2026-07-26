# Release acceptance checklist

Run these checks before enabling production writes:

1. Create a scheduled backup and verify it without modifying it:
   `.venv/bin/python scripts/verify_backup.py <backup.sqlite>`
2. Confirm `/health/live` returns `alive` and `/health/ready` returns `ready` only after the
   database has opened and passed its integrity probe.
3. Restore a backup into a disposable database and run the verifier against the restored file.
4. Confirm a missing, zero-byte, or corrupt backup fails verification and does not get promoted.
5. Keep battery control in dry-run until the live battery behavior and power-loss/watchdog drills
   have been observed on the actual installation.
