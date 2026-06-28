#!/usr/bin/env python
"""One-shot READ-ONLY live verification of the real devices. Never writes to anything.

Usage:
    export TIBBER_TOKEN='...'        # optional: verify prices
    export INDEVOLT_KEY='...'        # optional: verify battery SoC/power (HTTP Digest, user opend)
    uv run python scripts/verify_live.py

Checks the HomeWizard meters (no creds needed), Tibber prices (if TIBBER_TOKEN), and the Indevolt
battery (if reachable) — for the Indevolt it sweeps candidate GetData `config` values, with Digest
auth when a key is given, and reports which (if any) returns data. Prints a PASS/PARTIAL/FAIL line.
"""
from __future__ import annotations

import os

import httpx

P1, SOLAR, CAR = "192.168.50.92", "192.168.50.37", "192.168.50.98"
INDEVOLT = "192.168.50.53"
# Candidate GetData configs to try (the device returned {} for all of these unauthenticated).
CONFIG_CANDIDATES = ["all", "battery", "status", "data", "47005", "47005,47015,47016,47017"]


def _ok(label, detail):
    print(f"  [PASS] {label}: {detail}")
    return True


def _fail(label, detail):
    print(f"  [FAIL] {label}: {detail}")
    return False


def check_homewizard() -> bool:
    print("HomeWizard meters (read-only, no creds):")
    all_ok = True
    for name, ip in (("P1/grid", P1), ("solar", SOLAR), ("car/EV", CAR)):
        try:
            d = httpx.get(f"http://{ip}/api/v1/data", timeout=5).json()
            _ok(name, f"active_power_w={d.get('active_power_w')}")
        except Exception as exc:
            all_ok = _fail(name, f"{type(exc).__name__}: {exc}") and all_ok
    return all_ok


def check_tibber() -> bool:
    token = os.environ.get("TIBBER_TOKEN", "")
    print("Tibber prices:")
    if not token:
        print("  [SKIP] TIBBER_TOKEN not set")
        return False
    from ems.sources.tibber import TibberPriceSource

    slots = TibberPriceSource(token).slots()
    if slots:
        return _ok("prices", f"{len(slots)} 15-min slots; first {slots[0].eur_per_kwh} EUR/kWh")
    return _fail("prices", "no slots (token rejected or no subscription) — see logs")


def check_indevolt() -> bool:
    key = os.environ.get("INDEVOLT_KEY") or None
    print(f"Indevolt OpenData (read-only; key={'set' if key else 'NOT set'}):")
    auth = httpx.DigestAuth("opend", key) if key else None
    # Let the user pass the exact config/profile name they defined in the Indevolt app.
    candidates = ([os.environ["INDEVOLT_CONFIG"]] if os.environ.get("INDEVOLT_CONFIG") else []) \
        + CONFIG_CANDIDATES
    found = None
    for cfg in candidates:
        try:
            r = httpx.get(
                f"http://{INDEVOLT}:8080/rpc/Indevolt.GetData",
                params={"config": cfg}, auth=auth, timeout=5,
            )
            body = r.json() if r.headers.get("content-type", "").startswith("application") else {}
            if body:
                found = (cfg, body)
                break
        except Exception as exc:
            print(f"  config={cfg!r} -> {type(exc).__name__}: {exc}")
    if found:
        return _ok("battery", f"config={found[0]!r} returned keys {sorted(found[1])[:8]}")
    return _fail(
        "battery",
        "GetData empty for every config — enable OpenData data points in the app + supply a key",
    )


def main() -> None:
    print("=== EMS live device verification (READ-ONLY — no battery writes) ===\n")
    results = {
        "HomeWizard": check_homewizard(),
        "Tibber": check_tibber(),
        "Indevolt": check_indevolt(),
    }
    print("\n=== Summary ===")
    for name, ok in results.items():
        print(f"  {name}: {'working' if ok else 'NOT working (see above)'}")
    working = sum(results.values())
    verdict = "PASS" if working == 3 else ("PARTIAL" if working else "FAIL")
    print(f"\n{verdict}: {working}/3 device groups reading live. (Battery control stays OFF.)")


if __name__ == "__main__":
    main()
