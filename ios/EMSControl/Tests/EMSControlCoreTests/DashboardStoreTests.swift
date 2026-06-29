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

    func testForgetServerClearsSnapshot() throws {
        let store = DashboardStore(client: nil, demoData: DemoDataStore(bundle: .module))
        try store.useDemo()
        store.forgetServer()
        XCTAssertNil(store.snapshot)
        XCTAssertNil(store.nextRefreshAt)
    }

    func testRefreshRecordsNextRefreshFromServerTTL() async throws {
        let store = DashboardStore(
            client: APIClient(
                baseURL: URL(string: "http://ems.local:8080")!,
                transport: RecordingDashboardTransport(data: dashboardJSON(cacheTTLSeconds: 10))
            )
        )

        await store.refresh()

        XCTAssertEqual(
            store.nextRefreshAt,
            ISO8601DateFormatter().date(from: "2026-06-29T12:00:10+00:00")
        )
        XCTAssertFalse(store.shouldRefresh(now: ISO8601DateFormatter().date(from: "2026-06-29T12:00:09+00:00")!))
        XCTAssertTrue(store.shouldRefresh(now: ISO8601DateFormatter().date(from: "2026-06-29T12:00:10+00:00")!))
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
        XCTAssertNil(store.client)
        XCTAssertNil(store.snapshot)
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

private final class RecordingDashboardTransport: HTTPTransport, @unchecked Sendable {
    let data: Data

    init(data: Data) {
        self.data = data
    }

    func data(for request: URLRequest) async throws -> (Data, HTTPURLResponse) {
        (data, HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!)
    }
}

private final class RecordingCredentialStore: CredentialStore {
    var deletedURLs: [URL] = []

    func saveToken(_ token: String, for baseURL: URL) throws {}
    func token(for baseURL: URL) throws -> String? { nil }
    func deleteToken(for baseURL: URL) throws {
        deletedURLs.append(baseURL)
    }
}

private func dashboardJSON(cacheTTLSeconds: Int) -> Data {
    """
    {
      "api_version": 1,
      "generated_at": "2026-06-29T12:00:00+00:00",
      "server_time": "2026-06-29T12:00:00+00:00",
      "server_name": "Home EMS",
      "cache_ttl_seconds": \(cacheTTLSeconds),
      "degraded_sections": [],
      "readiness": {},
      "status": {},
      "freshness": {},
      "strategy": {},
      "decision": {},
      "alerts": {},
      "battery": {},
      "charge_need": {},
      "savings": {},
      "energy_story": {},
      "ai_validation": {}
    }
    """.data(using: .utf8)!
}

private final class MissingBundleMarker {}
