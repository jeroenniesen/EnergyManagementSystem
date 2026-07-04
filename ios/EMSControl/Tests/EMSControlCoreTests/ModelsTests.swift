import XCTest
import EMSControlCore

final class ModelsTests: XCTestCase {
    func testPublicModelsCanBeConstructedByExternalConsumers() {
        let faqItem = FAQItem(key: "battery-health", question: "What affects battery health?", answer: "Heat and deep discharge.")
        let faqResponse = FAQResponse(aiOn: true, items: [faqItem])
        let chatRequest = ChatRequest(question: "How can I reduce import costs tonight?")
        let chatResponse = ChatResponse(answer: "Charge after midnight.", source: "tariff-engine")
        let explainerStatus = ExplainerStatus(mode: "automatic", active: true, language: "en")

        XCTAssertEqual(faqItem.id, "battery-health")
        XCTAssertEqual(faqResponse.items, [faqItem])
        XCTAssertEqual(chatRequest.question, "How can I reduce import costs tonight?")
        XCTAssertEqual(chatResponse.source, "tariff-engine")
        XCTAssertEqual(explainerStatus.mode, "automatic")
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
