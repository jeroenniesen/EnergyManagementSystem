import XCTest
import EMSControlCore

final class ModelsTests: XCTestCase {
    func testPublicModelsCanBeConstructedByExternalConsumers() {
        let faqItem = FAQItem(key: "battery-health", question: "What affects battery health?", answer: "Heat and deep discharge.")
        let faqResponse = FAQResponse(aiOn: true, items: [faqItem])
        let chatRequest = ChatRequest(question: "How can I reduce import costs tonight?")
        let chatResponse = ChatResponse(answer: "Charge after midnight.", source: "tariff-engine")
        let explainerStatus = ExplainerStatus(mode: "automatic", active: true, language: "en")
        let planPoint = BatteryPlanPoint(ts: "2026-07-05T12:00:00+02:00", socPct: 64)
        let planGraph = BatteryPlanGraph(
            forecastSoc: [planPoint],
            actualSoc: [planPoint],
            reserveLine: [BatteryPlanPoint(ts: planPoint.ts, socPct: 10)],
            targetLine: [BatteryPlanPoint(ts: planPoint.ts, socPct: 88)],
            plannedActions: [
                BatteryPlanActionBlock(
                    start: "2026-07-05T12:00:00+02:00",
                    end: "2026-07-05T12:15:00+02:00",
                    action: "self_consume"
                )
            ],
            priceWindows: [
                BatteryPlanPriceWindow(
                    start: "2026-07-05T12:00:00+02:00",
                    end: "2026-07-05T12:15:00+02:00",
                    minEurPerKwh: 0.12,
                    maxEurPerKwh: 0.14
                )
            ],
            solar: [BatteryPlanSolarPoint(ts: planPoint.ts, forecastW: 1400, actualW: nil)]
        )
        let batteryPlan = BatteryPlanSnapshot(
            status: "on_track",
            summary: "Battery is on plan.",
            currentAction: "self_consume",
            currentReason: "Solar covers the home.",
            windowStart: "2026-07-05T12:00:00+02:00",
            windowEnd: "2026-07-06T12:00:00+02:00",
            currentSocPct: 64,
            reserveSocPct: 10,
            targetSocPct: 88,
            targetDeadline: "2026-07-05T22:00:00+02:00",
            plannedGridTopupKwh: 0,
            deviation: BatteryPlanDeviation(status: "ok", message: "Actual battery level is close to plan."),
            warnings: [],
            graph: planGraph
        )

        XCTAssertEqual(faqItem.id, "battery-health")
        XCTAssertEqual(faqResponse.items, [faqItem])
        XCTAssertEqual(chatRequest.question, "How can I reduce import costs tonight?")
        XCTAssertEqual(chatResponse.source, "tariff-engine")
        XCTAssertEqual(explainerStatus.mode, "automatic")
        XCTAssertEqual(batteryPlan.graph.priceWindows[0].minEurPerKwh, 0.12)
    }

    func testDashboardSnapshotDecodesVersionedContract() throws {
        let json = """
        {
          "api_version": 1,
          "generated_at": "2026-06-29T12:00:00+00:00",
          "server_time": "2026-06-29T12:00:01+00:00",
          "server_name": "Home EMS",
          "cache_ttl_seconds": 10,
          "degraded_sections": ["battery"],
          "readiness": {"dashboard_ready": true},
          "status": {"soc_pct": 64.0},
          "freshness": {},
          "strategy": {},
          "decision": {},
          "alerts": {"alerts": []},
          "battery": {"state": "degraded", "message": "Battery details are temporarily unavailable.", "updated_at": "2026-06-29T12:00:00+00:00"},
          "charge_need": {},
          "savings": {},
          "energy_story": {},
          "ai_validation": {"latest": null, "active": false}
        }
        """.data(using: .utf8)!

        let snapshot = try JSONDecoder.ems.decode(DashboardSnapshot.self, from: json)

        XCTAssertEqual(snapshot.apiVersion, 1)
        XCTAssertEqual(snapshot.serverName, "Home EMS")
        XCTAssertEqual(snapshot.cacheTTLSeconds, 10)
        XCTAssertEqual(snapshot.degradedSections, ["battery"])
        XCTAssertEqual(snapshot.battery.state, .degraded)
    }

    func testMobileDashboardSnapshotDecodesBatteryPlanWhenPresent() throws {
        let json = """
        {
          "generated_at": "2026-07-05T12:00:00+00:00",
          "server_name": "Home EMS",
          "cache_ttl_seconds": 10,
          "status": {
            "dry_run": false,
            "dev_mode": "live",
            "soc_pct": 64.0,
            "grid_power_w": 0.0,
            "solar_power_w": 1200.0,
            "battery_power_w": 200.0,
            "house_load_w": 900.0,
            "non_ev_load_w": 900.0
          },
          "battery_plan": {
            "status": "on_track",
            "summary": "Battery is on plan.",
            "current_action": "self_consume",
            "current_reason": "Solar covers the home.",
            "window_start": "2026-07-05T12:00:00+02:00",
            "window_end": "2026-07-06T12:00:00+02:00",
            "current_soc_pct": 64.0,
            "reserve_soc_pct": 10.0,
            "target_soc_pct": 88.0,
            "target_deadline": "2026-07-05T22:00:00+02:00",
            "planned_grid_topup_kwh": 2.5,
            "deviation": {"status": "ok", "message": "Actual battery level is close to plan."},
            "warnings": [],
            "graph": {
              "forecast_soc": [{"ts": "2026-07-05T12:00:00+02:00", "soc_pct": 64.0}],
              "actual_soc": [{"ts": "2026-07-05T12:00:00+02:00", "soc_pct": 64.0}],
              "reserve_line": [{"ts": "2026-07-05T12:00:00+02:00", "soc_pct": 10.0}],
              "target_line": [{"ts": "2026-07-05T12:00:00+02:00", "soc_pct": 88.0}],
              "planned_actions": [],
              "price_windows": [],
              "solar": []
            }
          }
        }
        """.data(using: .utf8)!

        let snapshot = try JSONDecoder.ems.decode(MobileDashboardSnapshot.self, from: json)

        XCTAssertEqual(snapshot.batteryPlan.status, "on_track")
        XCTAssertEqual(snapshot.batteryPlan.currentSocPct, 64.0)
        XCTAssertEqual(snapshot.batteryPlan.plannedGridTopupKwh, 2.5)
        XCTAssertEqual(snapshot.batteryPlan.graph.forecastSoc.count, 1)
    }

    func testBatteryPlanGraphToleratesMissingSubArrays() throws {
        // A partial graph (an older/degraded backend omits some arrays) must decode to empty
        // arrays, NOT throw — otherwise it would fail the whole MobileDashboardSnapshot decode and
        // blank the dashboard instead of just degrading the plan panel.
        let json = #"{"forecast_soc": [{"ts": "2026-07-05T12:00:00+02:00", "soc_pct": 64.0}]}"#
            .data(using: .utf8)!
        let graph = try JSONDecoder.ems.decode(BatteryPlanGraph.self, from: json)
        XCTAssertEqual(graph.forecastSoc.count, 1)
        XCTAssertTrue(graph.actualSoc.isEmpty)
        XCTAssertTrue(graph.plannedActions.isEmpty)
        XCTAssertTrue(graph.priceWindows.isEmpty)
        XCTAssertTrue(graph.solar.isEmpty)
    }

    func testReportSnapshotDecodesInsightsFlowsAndSeries() throws {
        let json = """
        {
          "period": "day",
          "label": "2026-07-03",
          "partial": true,
          "flows": {
            "has_data": true,
            "partial": true,
            "solar_kwh": 12.4,
            "grid_import_kwh": 2.1,
            "grid_export_kwh": 0.4,
            "battery_charge_kwh": 4.2,
            "battery_discharge_kwh": 3.8,
            "home_kwh": 9.6,
            "car_kwh": 1.2,
            "car_guard_leak_kwh": 0.0,
            "self_sufficiency_pct": 80.0,
            "solar_self_consumption_pct": 91.0
          },
          "scores": [
            {"key": "self_consumption", "label": "Self-consumption", "value": 80.0, "raw": 80.0, "unit": "%", "explanation": "Most load was local."}
          ],
          "series": [
            {"start": "2026-07-03T10:00:00+02:00", "grid_import_kwh": 0.2, "grid_export_kwh": 0.0, "house_kwh": 0.5, "car_kwh": 0.1, "solar_kwh": 0.8, "samples": 4}
          ]
        }
        """.data(using: .utf8)!

        let report = try JSONDecoder.ems.decode(ReportSnapshot.self, from: json)

        XCTAssertEqual(report.flows.hasData, true)
        XCTAssertEqual(report.flows.solarKwh, 12.4)
        XCTAssertEqual(report.flows.selfSufficiencyPct, 80.0)
        XCTAssertEqual(report.series.count, 1)
        XCTAssertEqual(report.series[0].houseKwh, 0.5)
        XCTAssertEqual(report.series[0].samples, 4)
    }

    func testFinanceSnapshotDecodesDaysAndTotals() throws {
        let json = """
        {
          "period": "week",
          "label": "2026-W27",
          "partial": false,
          "days": [
            {
              "day": "2026-07-01",
              "has_data": true,
              "price_coverage": 1.0,
              "grid_cost_eur": 1.20,
              "battery_cost_eur": 0.14,
              "baseline_cost_eur": 1.80,
              "saved_eur": 0.46,
              "grid_import_kwh": 5.2,
              "grid_export_kwh": 0.2
            }
          ],
          "totals": {
            "grid_cost_eur": 1.20,
            "battery_cost_eur": 0.14,
            "saved_eur": 0.46,
            "days_with_prices": 1,
            "days_with_data": 1
          }
        }
        """.data(using: .utf8)!

        let finance = try JSONDecoder.ems.decode(FinanceSnapshot.self, from: json)

        XCTAssertEqual(finance.days.count, 1)
        XCTAssertEqual(finance.days[0].savedEur, 0.46)
        XCTAssertEqual(finance.totals.daysWithData, 1)
        XCTAssertEqual(finance.totals.savedEur, 0.46)
    }
}
