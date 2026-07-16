import Foundation
import XCTest
@testable import EMSControlCore

@MainActor
final class NotificationsStoreTests: XCTestCase {
    private let baseURL = URL(string: "http://ems.local:8080")!

    private let feedJSON = """
    {
      "items": [
        {"id": 7, "ts": "2026-07-05T18:05:00+02:00", "key": "weekly_digest",
         "title": "Your week: saved €4.20", "body": "You saved €4.20 this week.",
         "confidence": null, "read": false, "delivered": ["in_app"],
         "dedupe_key": "digest:Week of 2026-06-29"},
        {"id": 6, "ts": "2026-07-04T03:00:00+02:00", "key": "backup_failed",
         "title": "Backup failed", "body": "Nightly backup did not complete.",
         "confidence": null, "read": true, "delivered": ["in_app"], "dedupe_key": null}
      ],
      "unread": 1
    }
    """.data(using: .utf8)!

    private let digestJSON = """
    {
      "week_label": "Week of 2026-06-22",
      "saved_eur": 3.41,
      "best_day": {"date": "2026-06-25", "saved_eur": 1.02},
      "self_sufficiency_pct": 74.2,
      "solar_kwh": 48.6,
      "co2_avoided_note": null,
      "actions": {"mode_switches": 5, "negative_soaks": 1, "overrides": 2},
      "tweak": null,
      "headline": "You saved €3.41 this week.",
      "days_measured": 7,
      "days_total": 7
    }
    """.data(using: .utf8)!

    private func makeStore(_ transport: NotificationsRoutingTransport) -> NotificationsStore {
        let store = NotificationsStore(client: nil)
        store.setClient(APIClient(baseURL: baseURL, transport: transport))
        return store
    }

    // MARK: - Refresh

    func testRefreshLoadsFeedAndDigest() async {
        let transport = NotificationsRoutingTransport(feedData: feedJSON, digestData: digestJSON)
        let store = makeStore(transport)

        await store.refresh()

        XCTAssertTrue(store.loaded)
        XCTAssertEqual(store.items.count, 2)
        XCTAssertEqual(store.unread, 1)
        XCTAssertEqual(store.digest?.weekLabel, "Week of 2026-06-22")
        XCTAssertNil(store.errorMessage)
        XCTAssertEqual(transport.lastFeedQuery, "limit=\(NotificationsStore.feedLimit)")
    }

    func testRefreshKeepsFeedWhenDigestEndpointMissing() async {
        // An older backend without /api/digest must not blank the notifications feed.
        let transport = NotificationsRoutingTransport(
            feedData: feedJSON, digestData: Data(), digestStatus: 404)
        let store = makeStore(transport)

        await store.refresh()

        XCTAssertTrue(store.loaded)
        XCTAssertEqual(store.items.count, 2)
        XCTAssertNil(store.digest)
        XCTAssertNil(store.errorMessage)
    }

    func testFailedRefreshKeepsLastKnownFeed() async {
        let transport = NotificationsRoutingTransport(feedData: feedJSON, digestData: digestJSON)
        let store = makeStore(transport)
        await store.refresh()
        XCTAssertEqual(store.items.count, 2)

        transport.feedStatus = 503
        await store.refresh()

        XCTAssertEqual(store.items.count, 2, "web-bell behaviour: last-known state stays visible")
        XCTAssertNotNil(store.errorMessage)
    }

    // MARK: - Mark all read (optimistic, like the web bell)

    func testMarkAllReadOptimisticallyClearsAndPostsAll() async throws {
        let transport = NotificationsRoutingTransport(feedData: feedJSON, digestData: digestJSON)
        let store = makeStore(transport)
        await store.refresh()
        XCTAssertEqual(store.unread, 1)

        await store.markAllRead()

        XCTAssertEqual(store.unread, 0)
        XCTAssertTrue(store.items.allSatisfy(\.read))
        let body = try JSONSerialization.jsonObject(
            with: XCTUnwrap(transport.lastPostBody)) as? [String: Any]
        XCTAssertEqual(body?["all"] as? Bool, true)
    }

    func testMarkAllReadFailureKeepsOptimisticState() async {
        let transport = NotificationsRoutingTransport(
            feedData: feedJSON, digestData: digestJSON, postStatus: 401)
        let store = makeStore(transport)
        await store.refresh()

        await store.markAllRead()

        // Never rolls back to re-alarm; the truth resyncs on the next refresh.
        XCTAssertEqual(store.unread, 0)
        XCTAssertTrue(store.items.allSatisfy(\.read))
    }

    func testMarkAllReadInDemoKeepsOptimisticState() async {
        let store = NotificationsStore(client: nil)
        store.setDemo()
        XCTAssertEqual(store.unread, 1)

        await store.markAllRead()

        XCTAssertEqual(store.unread, 0)
        XCTAssertTrue(store.items.allSatisfy(\.read))
    }

    // MARK: - Week stepping

    func testStepDigestWeekRequestsShiftedWeek() async {
        let transport = NotificationsRoutingTransport(feedData: feedJSON, digestData: digestJSON)
        let store = makeStore(transport)
        await store.refresh()
        XCTAssertTrue(store.canStepDigestWeek)

        await store.stepDigestWeek(direction: 1)

        XCTAssertEqual(transport.lastDigestQuery, "week=2026-06-29")
    }

    func testStepDigestWeekWithoutAnchorDoesNothing() async {
        let transport = NotificationsRoutingTransport(
            feedData: feedJSON, digestData: Data(), digestStatus: 404)
        let store = makeStore(transport)
        await store.refresh()
        XCTAssertFalse(store.canStepDigestWeek)

        await store.stepDigestWeek(direction: 1)

        XCTAssertNil(transport.lastDigestQuery)
    }

    // MARK: - Demo / server switching

    func testSetDemoPopulatesCodedFixtures() {
        let store = NotificationsStore(client: APIClient(
            baseURL: baseURL,
            transport: NotificationsRoutingTransport(feedData: Data(), digestData: Data())))

        store.setDemo()

        XCTAssertNil(store.client)
        XCTAssertTrue(store.isDemo)
        XCTAssertTrue(store.loaded)
        XCTAssertEqual(store.items, NotificationItem.demoItems)
        XCTAssertEqual(store.digest, .demo)
        XCTAssertEqual(store.unread, 1)
        XCTAssertFalse(store.canStepDigestWeek, "no server to fetch other weeks from")
    }

    func testSetClientWipesPreviousServerData() async {
        let transport = NotificationsRoutingTransport(feedData: feedJSON, digestData: digestJSON)
        let store = makeStore(transport)
        await store.refresh()
        XCTAssertTrue(store.loaded)

        store.setClient(APIClient(baseURL: URL(string: "http://other.local:8080")!, transport: transport))

        XCTAssertFalse(store.loaded)
        XCTAssertTrue(store.items.isEmpty)
        XCTAssertEqual(store.unread, 0)
        XCTAssertNil(store.digest)
    }

    // MARK: - APIClient request shapes

    func testFetchNotificationsPathQueryAndAuth() async throws {
        let transport = NotificationsRoutingTransport(feedData: feedJSON, digestData: digestJSON)
        let client = APIClient(baseURL: baseURL, token: "abc123", transport: transport)

        _ = try await client.fetchNotifications(limit: 10)

        XCTAssertEqual(transport.lastRequest?.url?.path, "/api/notifications")
        XCTAssertEqual(transport.lastRequest?.url?.query, "limit=10")
        XCTAssertEqual(transport.lastRequest?.httpMethod, "GET")
        XCTAssertEqual(transport.lastRequest?.value(forHTTPHeaderField: "Authorization"), "Bearer abc123")
    }

    func testMarkNotificationsReadWithIdsPostsIds() async throws {
        let transport = NotificationsRoutingTransport(feedData: feedJSON, digestData: digestJSON)
        let client = APIClient(baseURL: baseURL, token: "abc123", transport: transport)

        let unread = try await client.markNotificationsRead(ids: [3, 5])

        XCTAssertEqual(unread, 0)
        XCTAssertEqual(transport.lastRequest?.url?.path, "/api/notifications/read")
        XCTAssertEqual(transport.lastRequest?.httpMethod, "POST")
        XCTAssertEqual(transport.lastRequest?.value(forHTTPHeaderField: "Content-Type"), "application/json")
        XCTAssertEqual(transport.lastRequest?.value(forHTTPHeaderField: "Authorization"), "Bearer abc123")
        let body = try JSONSerialization.jsonObject(
            with: XCTUnwrap(transport.lastPostBody)) as? [String: Any]
        XCTAssertEqual(body?["ids"] as? [Int], [3, 5])
        XCTAssertNil(body?["all"])
    }

    func testFetchDigestDefaultAndWeekQuery() async throws {
        let transport = NotificationsRoutingTransport(feedData: feedJSON, digestData: digestJSON)
        let client = APIClient(baseURL: baseURL, transport: transport)

        _ = try await client.fetchDigest()
        XCTAssertEqual(transport.lastRequest?.url?.path, "/api/digest")
        XCTAssertNil(transport.lastRequest?.url?.query)

        _ = try await client.fetchDigest(week: "2026-06-15")
        XCTAssertEqual(transport.lastRequest?.url?.path, "/api/digest")
        XCTAssertEqual(transport.lastRequest?.url?.query, "week=2026-06-15")
    }
}

// Routes the three notification/digest endpoints; everything else 404s. Mutable knobs let a test
// flip an endpoint to failure mid-flight (mirrors CarStoreRoutingTransport).
private final class NotificationsRoutingTransport: HTTPTransport, @unchecked Sendable {
    let feedData: Data
    let digestData: Data
    var feedStatus: Int
    var digestStatus: Int
    let postStatus: Int
    private(set) var lastRequest: URLRequest?
    private(set) var lastPostBody: Data?
    private(set) var lastFeedQuery: String?
    private(set) var lastDigestQuery: String?

    init(
        feedData: Data, digestData: Data,
        feedStatus: Int = 200, digestStatus: Int = 200, postStatus: Int = 200
    ) {
        self.feedData = feedData
        self.digestData = digestData
        self.feedStatus = feedStatus
        self.digestStatus = digestStatus
        self.postStatus = postStatus
    }

    func data(for request: URLRequest) async throws -> (Data, HTTPURLResponse) {
        lastRequest = request
        let path = request.url?.path ?? ""
        let method = request.httpMethod ?? "GET"

        func respond(_ data: Data, _ status: Int) -> (Data, HTTPURLResponse) {
            (data, HTTPURLResponse(url: request.url!, statusCode: status, httpVersion: nil, headerFields: nil)!)
        }

        if method == "POST", path == "/api/notifications/read" {
            lastPostBody = request.httpBody
            return respond(Data(#"{"unread": 0}"#.utf8), postStatus)
        }
        switch path {
        case "/api/notifications":
            lastFeedQuery = request.url?.query
            return respond(feedData, feedStatus)
        case "/api/digest":
            lastDigestQuery = request.url?.query
            return respond(digestData, digestStatus)
        default:
            return respond(Data(#"{"detail":"unexpected path"}"#.utf8), 404)
        }
    }
}
