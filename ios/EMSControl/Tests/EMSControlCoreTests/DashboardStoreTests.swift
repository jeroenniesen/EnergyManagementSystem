import Foundation
import XCTest
@testable import EMSControlCore

@MainActor
final class DashboardStoreTests: XCTestCase {
    func testRefreshKeepsStaleSnapshotAfterFailure() async throws {
        let good = DemoDataStore(bundle: .module)
        let store = DashboardStore(client: nil, demoData: good)
        try store.useDemo()
        let first = store.snapshot

        store.client = APIClient(baseURL: URL(string: "http://127.0.0.1:1")!, transport: FailingTransport())
        await store.refresh()

        XCTAssertEqual(store.snapshot, first)
        XCTAssertTrue(store.isStale)
    }

    func testRefreshFailureSetsRetryDeadline() async throws {
        let store = DashboardStore(
            client: APIClient(
                baseURL: URL(string: "http://ems.local:8080")!,
                transport: FailingTransport()
            )
        )

        await store.refresh()

        XCTAssertFalse(store.shouldRefresh())
        XCTAssertNotNil(store.nextRefreshAt)
    }

    func testForgetServerClearsSnapshot() throws {
        let store = DashboardStore(client: nil, demoData: DemoDataStore(bundle: .module))
        try store.useDemo()
        store.forgetServer()
        XCTAssertNil(store.snapshot)
        XCTAssertNil(store.nextRefreshAt)
    }

    func testRefreshRecordsNextRefreshFromServerTTL() async throws {
        let before = Date()
        let store = DashboardStore(
            client: APIClient(
                baseURL: URL(string: "http://ems.local:8080")!,
                transport: RoutingDashboardTransport()
            )
        )

        await store.refresh()

        XCTAssertEqual(store.snapshot?.decision.homeState?.headline, "Watching - the battery is running the house")
        XCTAssertEqual(store.snapshot?.status.socPct, 55.0)
        XCTAssertEqual(store.snapshot?.report.scores.count, 3)
        XCTAssertEqual(store.snapshot?.finance.totals.savedEur, 0.01)
        XCTAssertGreaterThanOrEqual(store.nextRefreshAt ?? .distantPast, before.addingTimeInterval(10))
    }

    func testForgetServerDeletesStoredTokenForLiveClient() throws {
        let credentials = RecordingCredentialStore()
        let url = URL(string: "http://ems.local:8080")!
        let store = DashboardStore(
            client: APIClient(baseURL: url, token: "secret"),
            credentialStore: credentials
        )

        store.forgetServer()

        XCTAssertEqual(credentials.deletedURLs, [url])
        XCTAssertTrue(credentials.deletedLastBaseURL)
        XCTAssertNil(store.client)
        XCTAssertNil(store.snapshot)
    }

    func testSaveAndRestoreConnectedServerUsesCredentialStore() throws {
        let credentials = RecordingCredentialStore()
        let url = URL(string: "http://ems.local:8080")!
        let client = APIClient(baseURL: url, token: "secret")
        let store = DashboardStore(client: client, credentialStore: credentials)

        try store.saveConnectedServer(client)
        store.forgetServer()
        credentials.deletedURLs.removeAll()
        credentials.deletedLastBaseURL = false
        store.restoreSavedServer()

        XCTAssertEqual(credentials.savedBaseURL, url)
        XCTAssertEqual(credentials.savedTokens[url], "secret")
        XCTAssertEqual(store.client?.baseURL, url)
        XCTAssertEqual(store.client?.token, "secret")
    }

    func testRefreshWhenDueSkipsBeforeDeadlineAndRefreshesAfter() async throws {
        let transport = RoutingDashboardTransport()
        let store = DashboardStore(
            client: APIClient(
                baseURL: URL(string: "http://ems.local:8080")!,
                transport: transport
            )
        )

        await store.refresh()
        await store.refreshWhenDue(now: Date())
        XCTAssertEqual(transport.dashboardRefreshCount, 1)

        await store.refreshWhenDue(now: Date().addingTimeInterval(11))
        XCTAssertEqual(transport.dashboardRefreshCount, 2)
    }

    func testUseDemoClearsLiveClient() throws {
        let store = DashboardStore(
            client: APIClient(baseURL: URL(string: "http://ems.local:8080")!),
            demoData: DemoDataStore(bundle: .module)
        )

        try store.useDemo()

        XCTAssertNil(store.client)
        XCTAssertTrue(store.snapshot?.isDemo == true)
    }

    func testLoadDemoRecordsErrorWhenDemoDataIsMissing() {
        let store = DashboardStore(
            client: APIClient(baseURL: URL(string: "http://ems.local:8080")!),
            demoData: DemoDataStore(bundle: Bundle(for: MissingBundleMarker.self))
        )

        store.loadDemo()

        XCTAssertNil(store.snapshot)
        XCTAssertNotNil(store.lastError)
        XCTAssertNil(store.client)
    }
}

private struct FailingTransport: HTTPTransport {
    func data(for request: URLRequest) async throws -> (Data, HTTPURLResponse) {
        throw URLError(.notConnectedToInternet)
    }
}

private final class RoutingDashboardTransport: HTTPTransport, @unchecked Sendable {
    private let lock = NSLock()
    private var requestedPaths: [String] = []
    var dashboardRefreshCount: Int {
        lock.withLock {
            requestedPaths.filter { $0 == "/api/status" }.count
        }
    }

    func data(for request: URLRequest) async throws -> (Data, HTTPURLResponse) {
        let path = request.url?.path ?? ""
        lock.withLock {
            requestedPaths.append(path)
        }
        let data: Data
        switch path {
        case "/api/status":
            data = statusJSON()
        case "/api/freshness":
            data = #"{"battery":"fresh","ev":"fresh","grid":"fresh","soc":"fresh","solar":"fresh"}"#.data(using: .utf8)!
        case "/api/decision":
            data = decisionJSON()
        case "/api/alerts":
            data = #"{"data_quality":"complete","alerts":[]}"#.data(using: .utf8)!
        case "/api/battery":
            data = #"{"current_mode":"auto","capabilities":null,"towers":[],"aggregate":null}"#.data(using: .utf8)!
        case "/api/charge-need":
            data = #"{"usable_kwh":10.8,"current_soc_pct":55.0,"current_kwh":5.94,"reserve_kwh":1.08,"target_kwh":9.51,"target_soc_pct":88.1,"deficit_kwh":3.57,"on_track":false,"reason":"Need more charge."}"#.data(using: .utf8)!
        case "/api/savings":
            data = #"{"today_eur":0.0}"#.data(using: .utf8)!
        case "/api/energy-story":
            data = energyStoryJSON()
        case "/api/report":
            data = reportJSON()
        case "/api/finance":
            data = financeJSON()
        default:
            data = #"{"detail":"unexpected path"}"#.data(using: .utf8)!
            return (data, HTTPURLResponse(url: request.url!, statusCode: 404, httpVersion: nil, headerFields: nil)!)
        }
        return (data, HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!)
    }
}

private final class RecordingCredentialStore: CredentialStore {
    var deletedURLs: [URL] = []
    var deletedLastBaseURL = false
    var savedBaseURL: URL?
    var savedTokens: [URL: String] = [:]

    func saveToken(_ token: String, for baseURL: URL) throws {
        savedTokens[baseURL] = token
    }

    func token(for baseURL: URL) throws -> String? {
        savedTokens[baseURL]
    }

    func deleteToken(for baseURL: URL) throws {
        deletedURLs.append(baseURL)
    }

    func saveLastBaseURL(_ baseURL: URL) throws {
        savedBaseURL = baseURL
    }

    func lastBaseURL() throws -> URL? {
        savedBaseURL
    }

    func deleteLastBaseURL() throws {
        deletedLastBaseURL = true
    }
}

private func statusJSON() -> Data {
    """
    {
      "dry_run": true,
      "dev_mode": "mock",
      "soc_pct": 55.0,
      "grid_power_w": 200.0,
      "solar_power_w": 0.0,
      "battery_power_w": 800.0,
      "house_load_w": 1000.0,
      "non_ev_load_w": 1000.0
    }
    """.data(using: .utf8)!
}

private func decisionJSON() -> Data {
    """
    {
      "intent": "allow_self_consumption",
      "desired_mode": "auto",
      "applied": false,
      "outcome": "dry_run",
      "reason": "dry-run: would set auto",
      "plan_reason": "running the house on the battery",
      "plan_reason_explained": "running the house on the battery",
      "override_active": false,
      "car_charging": false,
      "target_soc": null,
      "home_state": {
        "headline": "Watching - the battery is running the house",
        "tone": "watching",
        "simulated": true
      }
    }
    """.data(using: .utf8)!
}

private func energyStoryJSON() -> Data {
    """
    {
      "window": "next",
      "current_soc_pct": 55.0,
      "reserve_soc_pct": 10.0,
      "target_soc_pct": 88.1,
      "target_kwh": 9.5,
      "target_deadline": "2026-07-04T20:45:00+02:00",
      "current_price_eur_per_kwh": 0.12,
      "slots": [],
      "totals": {"import_kwh":0.0,"export_kwh":3.26,"solar_kwh":17.97,"charge_kwh":8.72,"grid_charge_kwh":0.0,"solar_charge_kwh":8.71,"discharge_kwh":5.16,"load_kwh":11.15,"grid_cost_eur":-0.62,"self_sufficiency_pct":100.0,"soc_start_pct":52.8,"soc_end_pct":81.2,"soc_min_pct":23.4,"soc_max_pct":100.0},
      "headline": "Next 24h - your solar fills the battery.",
      "trust_markers": ["Reserve respected"],
      "on_track": {"status":"ahead","actual_soc_pct":55.0,"target_soc_pct":88.1,"deficit_kwh":3.6,"message":"On track."},
      "recent_review": {"hours":3,"solar_actual_kwh":0.0,"solar_forecast_kwh":0.0,"solar_pct_of_forecast":null,"battery_charged_kwh":0.0,"battery_discharged_kwh":0.2,"message":"Last 3h: battery -0.2 kWh."}
    }
    """.data(using: .utf8)!
}

private func reportJSON() -> Data {
    """
    {
      "period": "day",
      "label": "2026-07-03",
      "partial": true,
      "flows": {"has_data": true, "self_sufficiency_pct": 80.0},
      "scores": [
        {"key": "self_consumption", "label": "Self-consumption", "value": 80.0, "raw": 80.0, "unit": "%", "explanation": "No solar this period."},
        {"key": "co2", "label": "CO2", "value": 80.0, "raw": 0.0, "unit": "kg", "explanation": "Avoided CO2."},
        {"key": "best_price", "label": "Best price", "value": 100.0, "raw": 0.12, "unit": "EUR/kWh", "explanation": "Prices were flat."}
      ],
      "series": []
    }
    """.data(using: .utf8)!
}

private func financeJSON() -> Data {
    """
    {
      "period": "day",
      "label": "2026-07-03",
      "partial": true,
      "days": [],
      "totals": {
        "grid_cost_eur": 0.01,
        "battery_cost_eur": 0.01,
        "saved_eur": 0.01,
        "grid_import_kwh": 0.05,
        "grid_export_kwh": 0.0,
        "days_with_prices": 1,
        "days_with_data": 1
      }
    }
    """.data(using: .utf8)!
}

private final class MissingBundleMarker {}
