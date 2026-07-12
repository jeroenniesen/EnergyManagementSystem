import Foundation
import XCTest
@testable import EMSControlCore

// GET /api/car/plan is progressive (see ems/tests/test_car_plan_api.py): `enabled:false`,
// `needs_anchor`, `needs_schedule`, and the full plan. All four must decode tolerantly.
final class CarPlanSnapshotTests: XCTestCase {
    private func decode(_ json: String) throws -> CarPlanSnapshot {
        try JSONDecoder.ems.decode(CarPlanSnapshot.self, from: Data(json.utf8))
    }

    func testDisabledStateDecodesToEnabledFalse() throws {
        let plan = try decode(#"{"enabled": false, "plan": null, "soc": null}"#)

        XCTAssertFalse(plan.enabled)
        XCTAssertNil(plan.plan)
        XCTAssertNil(plan.soc)
        XCTAssertNil(plan.needsAnchor)
        XCTAssertNil(plan.needsSchedule)
        XCTAssertNil(plan.car)
        XCTAssertNil(plan.effectiveKw)
        XCTAssertNil(plan.carMeterConfigured)
    }

    func testNeedsAnchorState() throws {
        let plan = try decode(
            #"{"enabled": true, "plan": null, "soc": null, "needs_anchor": true, "car_meter_configured": false}"#
        )

        XCTAssertTrue(plan.enabled)
        XCTAssertEqual(plan.needsAnchor, true)
        XCTAssertNil(plan.soc)
        XCTAssertNil(plan.plan)
        XCTAssertNil(plan.needsSchedule)
        XCTAssertEqual(plan.carMeterConfigured, false)
    }

    func testNeedsScheduleStateCarriesSocButNoPlan() throws {
        let json = """
        {
          "enabled": true,
          "needs_schedule": true,
          "plan": null,
          "soc": {
            "soc_pct": 55.0,
            "anchor_pct": 55,
            "anchor_ts": "2026-07-12T09:00:00+00:00",
            "added_kwh": 0.0,
            "sessions_since_anchor": 0,
            "age_hours": 3.5,
            "stale": false
          }
        }
        """
        let plan = try decode(json)

        XCTAssertTrue(plan.enabled)
        XCTAssertEqual(plan.needsSchedule, true)
        XCTAssertNil(plan.plan)
        XCTAssertNil(plan.needsAnchor)
        XCTAssertEqual(plan.soc?.anchorPct, 55)
        XCTAssertEqual(plan.soc?.socPct, 55.0)
        XCTAssertEqual(plan.soc?.stale, false)
        XCTAssertEqual(plan.soc?.ageHours, 3.5)
    }

    func testFullPlanState() throws {
        let json = """
        {
          "enabled": true,
          "car_meter_configured": true,
          "effective_kw": 11.0,
          "soc": {
            "soc_pct": 20.0,
            "anchor_pct": 20.0,
            "anchor_ts": "2026-07-12T06:00:00+00:00",
            "added_kwh": 0.0,
            "sessions_since_anchor": 0,
            "age_hours": 80.0,
            "stale": true
          },
          "plan": {
            "soc": 20.0,
            "deadlines": [
              {
                "ready_by": "2026-07-13T07:30:00+02:00",
                "min_pct": 80,
                "required_kwh": 34.5,
                "planned_kwh": 34.5,
                "pending_kwh": 34.5,
                "shortfall_kwh": 0.0,
                "already_met": false,
                "feasible": true
              }
            ],
            "slots": [
              {
                "start": "2026-07-13T02:00:00+02:00",
                "kw": 11.0,
                "ac_kwh": 2.75,
                "battery_kwh": 2.48,
                "eur_per_kwh_effective": 0.11,
                "est_cost_eur": 0.30,
                "solar_surplus": false,
                "for_deadline": "2026-07-13T07:30:00+02:00"
              }
            ],
            "windows": [
              {
                "start": "2026-07-13T02:00:00+02:00",
                "end": "2026-07-13T05:30:00+02:00",
                "ac_kwh": 38.3,
                "battery_kwh": 34.5,
                "est_cost_eur": 4.05,
                "solar_share_pct": 25,
                "reason": "Cheapest slots to reach 80% by Mon 07:30 — 25% overlaps expected solar surplus."
              }
            ],
            "advice": "Plug in Mon 02:00–05:30 (34.5 kWh, ≈ €4.05) to reach 80% by Mon 07:30.",
            "negative_price_hint": "Prices go negative Tue 13:00–14:30 — you would be PAID to top up beyond the weekly minimum.",
            "total_est_cost_eur": 4.05,
            "total_planned_kwh": 34.5
          },
          "schedule": {
            "mon": { "enabled": true, "min_pct": 80, "ready_by": "07:30" }
          },
          "car": {
            "id": "tesla-model-y-long-range",
            "brand": "Tesla",
            "model": "Model Y Long Range",
            "battery_net_kwh": 75.0,
            "max_ac_kw": 11.0,
            "years": "2020–present"
          }
        }
        """
        let plan = try decode(json)

        XCTAssertTrue(plan.enabled)
        XCTAssertNil(plan.needsAnchor)
        XCTAssertNil(plan.needsSchedule)
        XCTAssertEqual(plan.effectiveKw, 11.0)
        XCTAssertEqual(plan.carMeterConfigured, true)
        XCTAssertEqual(plan.soc?.socPct, 20.0)
        XCTAssertEqual(plan.soc?.stale, true)

        XCTAssertEqual(plan.car?.id, "tesla-model-y-long-range")
        XCTAssertEqual(plan.car?.maxAcKw, 11.0)
        XCTAssertEqual(plan.car?.batteryNetKwh, 75.0)

        let body = try XCTUnwrap(plan.plan)
        XCTAssertEqual(body.soc, 20.0)
        XCTAssertEqual(body.totalPlannedKwh, 34.5)
        XCTAssertEqual(body.totalEstCostEur, 4.05)
        XCTAssertEqual(body.advice, "Plug in Mon 02:00–05:30 (34.5 kWh, ≈ €4.05) to reach 80% by Mon 07:30.")
        XCTAssertEqual(
            body.negativePriceHint,
            "Prices go negative Tue 13:00–14:30 — you would be PAID to top up beyond the weekly minimum."
        )

        let deadline = try XCTUnwrap(body.deadlines.first)
        XCTAssertEqual(deadline.minPct, 80)
        XCTAssertEqual(deadline.requiredKwh, 34.5)
        XCTAssertEqual(deadline.alreadyMet, false)
        XCTAssertEqual(deadline.feasible, true)
        XCTAssertEqual(deadline.readyBy, "2026-07-13T07:30:00+02:00")

        let window = try XCTUnwrap(body.windows.first)
        XCTAssertEqual(window.batteryKwh, 34.5)
        XCTAssertEqual(window.estCostEur, 4.05)
        XCTAssertEqual(window.solarSharePct, 25)
        XCTAssertEqual(window.start, "2026-07-13T02:00:00+02:00")
        XCTAssertEqual(window.end, "2026-07-13T05:30:00+02:00")
    }

    func testEmptyFallbackIsDisabled() {
        XCTAssertFalse(CarPlanSnapshot.empty.enabled)
        XCTAssertNil(CarPlanSnapshot.empty.plan)
    }

    func testTolerantDecodeWhenPlanArraysMissing() throws {
        // A degraded backend that omits deadlines/windows must degrade to empty arrays, not throw.
        let plan = try decode(#"{"enabled": true, "plan": {"soc": 40.0, "advice": "…"}, "soc": null}"#)
        XCTAssertEqual(plan.plan?.deadlines, [])
        XCTAssertEqual(plan.plan?.windows, [])
        XCTAssertEqual(plan.plan?.soc, 40.0)
    }
}
