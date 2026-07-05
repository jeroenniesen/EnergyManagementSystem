import Foundation
import XCTest
@testable import EMSControlCore

@MainActor
final class ActivityStoreTests: XCTestCase {
    func testRefreshLoadsEntriesAndSetsLastUpdated() async {
        let transport = AuditTransport()
        let client = APIClient(baseURL: URL(string: "http://ems.local:8080")!, transport: transport)
        let fixed = Date(timeIntervalSince1970: 1_780_000_000)
        let store = ActivityStore(client: client, now: { fixed })

        await store.refresh()

        XCTAssertEqual(store.entries.count, 1)
        XCTAssertEqual(store.lastUpdatedAt, fixed)
        XCTAssertFalse(store.isStale)
        XCTAssertNil(store.errorMessage)
    }

    func testFailureAfterDataSetsStaleAndKeepsEntries() async {
        let transport = AuditTransport()
        let client = APIClient(baseURL: URL(string: "http://ems.local:8080")!, transport: transport)
        let store = ActivityStore(client: client)
        await store.refresh()

        transport.shouldFail = true
        await store.refresh()

        XCTAssertTrue(store.isStale)
        XCTAssertEqual(store.entries.count, 1)   // old entries still visible
        XCTAssertNotNil(store.errorMessage)
    }

    func testSwitchingServersClearsEntriesImmediately() async {
        let transport = AuditTransport()
        let store = ActivityStore(client: APIClient(baseURL: URL(string: "http://ems-a.local:8080")!, transport: transport))
        await store.refresh()
        XCTAssertFalse(store.entries.isEmpty)

        store.setClient(APIClient(baseURL: URL(string: "http://ems-b.local:8080")!, transport: transport))

        XCTAssertTrue(store.entries.isEmpty)
        XCTAssertNil(store.errorMessage)
        XCTAssertNil(store.lastUpdatedAt)
        XCTAssertFalse(store.isStale)
    }

    func testDisconnectingClearsEntries() async {
        let transport = AuditTransport()
        let store = ActivityStore(client: APIClient(baseURL: URL(string: "http://ems.local:8080")!, transport: transport))
        await store.refresh()
        XCTAssertFalse(store.entries.isEmpty)

        store.setClient(nil)

        XCTAssertTrue(store.entries.isEmpty)
    }
}

private final class AuditTransport: HTTPTransport, @unchecked Sendable {
    var shouldFail = false

    func data(for request: URLRequest) async throws -> (Data, HTTPURLResponse) {
        if shouldFail {
            return (
                #"{"detail":"temporary failure"}"#.data(using: .utf8)!,
                HTTPURLResponse(url: request.url!, statusCode: 503, httpVersion: nil, headerFields: nil)!
            )
        }
        let body = #"{"entries":[{"id":1,"ts":"2026-07-04T10:00:00Z","category":"manual_override","summary":"Forced AUTO"}]}"#
        return (
            body.data(using: .utf8)!,
            HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!
        )
    }
}
