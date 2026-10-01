import XCTest
@testable import EMSControlCore

final class DecisionReasonTests: XCTestCase {
    /// Same fixture shape as web `DecisionReasonDetails.test.ts`.
    private let reasonJSON = """
    {
      "chosen_window": {
        "start": "2026-09-30T01:00:00+00:00",
        "end": "2026-09-30T04:00:00+00:00",
        "intent": "grid_charge_to_target",
        "label": "cheap charge window",
        "eur_per_kwh_min": 0.05,
        "eur_per_kwh_max": 0.08
      },
      "rejected_alternative": {
        "intent": "allow_self_consumption",
        "reason": "self-consumption only — rejected in favour of the selected charge window",
        "window_start": null,
        "window_end": null
      },
      "expected_benefit": { "eur": 0.85, "summary": "Estimated net benefit ≈ €0.85." },
      "risk": { "margin_eur_per_kwh": 0.02, "summary": "Risk margin €0.020/kWh applied to break-even." },
      "safety_constraint": { "code": null, "message": null, "action": "proceed" },
      "gates": {
        "validator_code": null,
        "failsafe": false,
        "dwell": false,
        "cap_reached": false,
        "unconfirmed": false
      },
      "summary": "Charging in the cheap night window."
    }
    """.data(using: .utf8)!

    func testDecodesBatteryPlanReasonContract() throws {
        let reason = try JSONDecoder.ems.decode(DecisionReasonSnapshot.self, from: reasonJSON)

        XCTAssertEqual(reason.summary, "Charging in the cheap night window.")
        XCTAssertEqual(reason.chosenWindow?.label, "cheap charge window")
        XCTAssertEqual(reason.chosenWindow?.eurPerKwhMin, 0.05)
        XCTAssertEqual(
            reason.rejectedAlternative?.reason,
            "self-consumption only — rejected in favour of the selected charge window"
        )
        XCTAssertEqual(reason.expectedBenefit?.eur, 0.85)
        XCTAssertEqual(reason.risk?.marginEurPerKwh, 0.02)
        XCTAssertEqual(reason.safetyConstraint.action, "proceed")
        XCTAssertFalse(reason.gates.failsafe)
        XCTAssertNil(reason.gates.validatorCode)
    }

    func testDetailRowsMatchWebDecisionReasonDetailsFields() throws {
        let reason = try JSONDecoder.ems.decode(DecisionReasonSnapshot.self, from: reasonJSON)
        let rows = DecisionReasonFormatting.detailRows(from: reason)
        let byTitle = Dictionary(uniqueKeysWithValues: rows.map { ($0.title, $0.detail) })

        XCTAssertEqual(byTitle["Summary"], "Charging in the cheap night window.")
        XCTAssertTrue(byTitle["Chosen window"]?.contains("cheap charge window") == true)
        XCTAssertTrue(byTitle["Chosen window"]?.contains("from €0.05/kWh") == true)
        XCTAssertTrue(byTitle["Rejected alternative"]?.contains("self-consumption only") == true)
        XCTAssertTrue(byTitle["Expected benefit"]?.contains("€0.85") == true)
        XCTAssertTrue(byTitle["Risk"]?.contains("Risk margin") == true)
        XCTAssertEqual(byTitle["Safety"], "Proceed — no safety hold")
        XCTAssertNil(byTitle["Gates"])
    }

    func testSafetyPauseShowsFindingCodeAndFailsafeGate() {
        let base = DecisionReasonSnapshot.demoCharge
        let reason = DecisionReasonSnapshot(
            chosenWindow: base.chosenWindow,
            rejectedAlternative: base.rejectedAlternative,
            expectedBenefit: base.expectedBenefit,
            risk: base.risk,
            safetyConstraint: DecisionSafetyConstraint(
                code: "stale_inputs",
                message: "Critical inputs are stale.",
                action: "paused"
            ),
            gates: DecisionGateOutcomes(
                validatorCode: "stale_inputs",
                failsafe: true
            ),
            summary: base.summary
        )
        let rows = DecisionReasonFormatting.detailRows(from: reason)
        let byTitle = Dictionary(uniqueKeysWithValues: rows.map { ($0.title, $0.detail) })

        XCTAssertEqual(byTitle["Safety"], "Critical inputs are stale. (stale_inputs)")
        XCTAssertTrue(byTitle["Gates"]?.contains("validator: stale_inputs") == true)
        XCTAssertTrue(byTitle["Gates"]?.contains("failsafe") == true)
    }

    func testDwellAndCapDoNotClaimNoSafetyHold() {
        let reason = DecisionReasonSnapshot(
            safetyConstraint: DecisionSafetyConstraint(action: "proceed"),
            gates: DecisionGateOutcomes(dwell: true, capReached: true),
            summary: "Holding."
        )
        let rows = DecisionReasonFormatting.detailRows(from: reason)
        let byTitle = Dictionary(uniqueKeysWithValues: rows.map { ($0.title, $0.detail) })

        XCTAssertEqual(byTitle["Safety"], "Proceed — held by dwell, daily cap")
        XCTAssertNotEqual(byTitle["Safety"], "Proceed — no safety hold")
        XCTAssertTrue(byTitle["Gates"]?.contains("dwell") == true)
        XCTAssertTrue(byTitle["Gates"]?.contains("cap reached") == true)
    }

    func testNilReasonYieldsNoRows() {
        XCTAssertTrue(DecisionReasonFormatting.detailRows(from: nil).isEmpty)
    }

    func testBatteryPlanDecodesReasonWhenPresent() throws {
        let json = """
        {
          "status": "on_track",
          "summary": "Tonight is covered.",
          "current_action": "grid_charge",
          "current_reason": "Cheap night window.",
          "window_start": "2026-07-03T21:15:00+02:00",
          "window_end": "2026-07-03T21:45:00+02:00",
          "current_soc_pct": 55.0,
          "reserve_soc_pct": 10.0,
          "target_soc_pct": 88.1,
          "target_deadline": "2026-07-04T20:45:00+02:00",
          "planned_grid_topup_kwh": 0.0,
          "deviation": {"status": "ok", "message": "ok"},
          "warnings": [],
          "graph": {},
          "reason": {
            "chosen_window": {
              "start": "2026-09-30T01:00:00+00:00",
              "end": "2026-09-30T04:00:00+00:00",
              "intent": "grid_charge_to_target",
              "label": "cheap charge window",
              "eur_per_kwh_min": 0.05,
              "eur_per_kwh_max": 0.08
            },
            "rejected_alternative": null,
            "expected_benefit": { "eur": 0.85, "summary": "Estimated net benefit ≈ €0.85." },
            "risk": { "margin_eur_per_kwh": 0.02, "summary": "Risk margin." },
            "safety_constraint": { "code": null, "message": null, "action": "proceed" },
            "gates": {
              "validator_code": null,
              "failsafe": false,
              "dwell": false,
              "cap_reached": false,
              "unconfirmed": false
            },
            "summary": "Charging in the cheap night window."
          }
        }
        """.data(using: .utf8)!

        let plan = try JSONDecoder.ems.decode(BatteryPlanSnapshot.self, from: json)
        XCTAssertEqual(plan.reason?.summary, "Charging in the cheap night window.")
        XCTAssertEqual(plan.reason?.chosenWindow?.label, "cheap charge window")
        XCTAssertEqual(DecisionReasonFormatting.actionLabel(plan.currentAction), "Grid charge")
    }

    func testActionLabelMapsDischargeToSelfConsumptionNotDump() {
        XCTAssertEqual(DecisionReasonFormatting.actionLabel("discharge"), "Self-consumption")
        XCTAssertEqual(DecisionReasonFormatting.actionLabel("self_consume"), "Self-consumption")
        XCTAssertEqual(
            DecisionReasonFormatting.actionLabel("full_speed_discharge"),
            "Full-speed discharge"
        )
        XCTAssertNotEqual(DecisionReasonFormatting.actionLabel("discharge"), "Discharge")
    }

    func testBatteryPlanToleratesMissingReason() throws {
        let json = """
        {
          "status": "on_track",
          "summary": "Tonight is covered.",
          "current_action": "self_consumption",
          "current_reason": "Battery is following the current plan.",
          "window_start": "2026-07-03T21:15:00+02:00",
          "window_end": "2026-07-03T21:45:00+02:00",
          "current_soc_pct": 55.0,
          "reserve_soc_pct": 10.0,
          "target_soc_pct": 88.1,
          "target_deadline": null,
          "planned_grid_topup_kwh": 0.0,
          "deviation": {"status": "ok", "message": "ok"},
          "warnings": [],
          "graph": {}
        }
        """.data(using: .utf8)!

        let plan = try JSONDecoder.ems.decode(BatteryPlanSnapshot.self, from: json)
        XCTAssertNil(plan.reason)
        XCTAssertEqual(plan.status, "on_track")
    }

    func testDemoScenariosCarryStructuredReason() {
        for scenario in BatteryPlanSnapshot.demoScenarios {
            XCTAssertNotNil(scenario.reason, "demo \(scenario.status) should include reason")
            XCTAssertFalse(
                DecisionReasonFormatting.detailRows(from: scenario.reason).isEmpty,
                "demo \(scenario.status) should render reason rows"
            )
        }
    }
}
