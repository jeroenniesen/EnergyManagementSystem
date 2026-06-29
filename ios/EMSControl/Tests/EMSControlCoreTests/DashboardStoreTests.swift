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

private final class MissingBundleMarker {}
