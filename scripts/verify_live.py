#!/usr/bin/env python
"""One-shot READ-ONLY live verification of the real devices. Never writes to anything.

Usage (point it at YOUR devices via env — no addresses are committed):
    export P1_IP=... SOLAR_IP=... CAR_IP=... INDEVOLT_IP=...   # your HomeWizard / Indevolt LAN IPs
    export TIBBER_TOKEN='...'        # optional: verify prices
    export INDEVOLT_KEY='...'        # optional: verify battery SoC/power (HTTP Digest)
    uv run python scripts/verify_live.py

Checks the HomeWizard meters (no creds needed), Tibber prices (if TIBBER_TOKEN), and the Indevolt
battery (if reachable) — for the Indevolt it sweeps candidate GetData `config` values, with Digest
auth when a key is given, and reports which (if any) returns data. Prints a PASS/PARTIAL/FAIL line.
"""
from __future__ import annotations

import os

import httpx

# Device addresses come from the environment — none are hard-coded/committed. The 192.0.2.x defaults
# are RFC 5737 TEST-NET placeholders (not a real network) so an unconfigured run fails cleanly.
P1 = os.environ.get("P1_IP", "192.0.2.10")
SOLAR = os.environ.get("SOLAR_IP", "192.0.2.11")
CAR = os.environ.get("CAR_IP", "192.0.2.12")
INDEVOLT = os.environ.get("INDEVOLT_IP", "192.0.2.20")


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
    # Read-only: keyless POST GetData?config={"t":[keys]}. 6002=SoC, 6000=power, 6001=state.
    print("Indevolt battery (read-only):")
    from ems.sources.indevolt import IndevoltReadClient

    try:
        power, soc = IndevoltReadClient(INDEVOLT).read_power_soc()
        return _ok("battery", f"SoC {soc:.0f}% · power {power:.0f} W (+discharge/-charge)")
    except Exception as exc:
        return _fail("battery", f"{type(exc).__name__}: {exc}")


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
