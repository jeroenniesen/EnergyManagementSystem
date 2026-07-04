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
}
