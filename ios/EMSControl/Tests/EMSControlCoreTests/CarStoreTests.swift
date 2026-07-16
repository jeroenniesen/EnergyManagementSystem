import Foundation
import XCTest
@testable import EMSControlCore

@MainActor
final class CarStoreTests: XCTestCase {
    private let baseURL = URL(string: "http://ems.local:8080")!

    private func settingsJSON(
        hold: Bool = true, mode: String = "hold", watts: Int = 800
    ) -> Data {
        """
        {
          "values": {
            "control.hold_battery_when_car_charging": \(hold),
            "control.car_charging_battery_mode": "\(mode)",
            "control.car_discharge_w": \(watts),
            "ev.schedule": "{\\"mon\\":{\\"enabled\\":true,\\"min_pct\\":80,\\"ready_by\\":\\"07:30\\"}}"
          }
        }
        """.data(using: .utf8)!
    }

    private let sessionsJSON = """
    {"sessions": [{"start": "2026-06-30T23:00:00+02:00", "end": "2026-07-01T02:30:00+02:00",
      "kwh": 9.5, "avg_kw": 3.2, "peak_kw": 3.4}], "days": 14}
    """.data(using: .utf8)!

    func testRefreshLoadsModeScheduleAndSessions() async {
        let transport = CarStoreRoutingTransport(
            settingsData: settingsJSON(hold: false, mode: "match_home_load", watts: 1100),
            sessionsData: sessionsJSON
        )
        let store = CarStore(client: nil)
        store.setClient(APIClient(baseURL: baseURL, transport: transport))

        await store.refresh()

        XCTAssertTrue(store.loaded)
        XCTAssertEqual(store.mode, CarModeSettings(holdEnabled: false, mode: .matchHomeLoad, dischargeW: 1100))
        XCTAssertEqual(store.schedule["mon"], CarScheduleDay(enabled: true, minPct: 80, readyBy: "07:30"))
        XCTAssertEqual(store.sessions.count, 1)
        XCTAssertEqual(store.sessions.first?.avgKw, 3.2)
        XCTAssertNil(store.errorMessage)
    }

    func testSetModePostsModeAndWattsTogetherOptimistically() async {
        let transport = CarStoreRoutingTransport(settingsData: settingsJSON(), sessionsData: sessionsJSON)
        let store = CarStore(client: nil)
        store.setClient(APIClient(baseURL: baseURL, transport: transport))
        await store.refresh()

        await store.setMode(.staticDischarge)

        XCTAssertEqual(store.mode.mode, .staticDischarge)
        XCTAssertEqual(store.saveState, .saved)

        let body = try? JSONSerialization.jsonObject(with: transport.lastPostBody ?? Data()) as? [String: Any]
        XCTAssertEqual(body?["control.car_charging_battery_mode"] as? String, "static_discharge")
        XCTAssertNotNil(body?["control.car_discharge_w"], "watts must be saved alongside the mode")
    }

    func testFailedSaveRollsBackAndReportsError() async {
        let transport = CarStoreRoutingTransport(
            settingsData: settingsJSON(), sessionsData: sessionsJSON, postStatus: 401
        )
        let store = CarStore(client: nil)
        store.setClient(APIClient(baseURL: baseURL, transport: transport))
        await store.refresh()
        XCTAssertEqual(store.mode.mode, .hold)

        await store.setMode(.staticDischarge)

        XCTAssertEqual(store.mode.mode, .hold, "optimistic change is rolled back on failure")
        XCTAssertEqual(store.saveState, .error)
        XCTAssertNotNil(store.saveError)
    }

    func testUpdateScheduleDayPostsFullScheduleString() async {
        let transport = CarStoreRoutingTransport(settingsData: settingsJSON(), sessionsData: sessionsJSON)
        let store = CarStore(client: nil)
        store.setClient(APIClient(baseURL: baseURL, transport: transport))
        await store.refresh()

        await store.updateScheduleDay("tue") { $0.enabled = true; $0.minPct = 65 }

        XCTAssertEqual(store.schedule["tue"], CarScheduleDay(enabled: true, minPct: 65, readyBy: "07:30"))
        XCTAssertEqual(store.saveState, .saved)

        let body = try? JSONSerialization.jsonObject(with: transport.lastPostBody ?? Data()) as? [String: Any]
        let raw = body?["ev.schedule"] as? String
        XCTAssertNotNil(raw)
        XCTAssertEqual(CarSchedule.parse(raw ?? "")["tue"].minPct, 65)
    }

    func testSetDemoPopulatesCodedFixtures() {
        let store = CarStore(client: APIClient(baseURL: baseURL, transport: CarStoreRoutingTransport(
            settingsData: Data("{}".utf8), sessionsData: Data("{}".utf8)
        )))

        store.setDemo()

        XCTAssertNil(store.client)
        XCTAssertTrue(store.isDemo)
        XCTAssertTrue(store.loaded)
        XCTAssertEqual(store.mode, .demo)
        XCTAssertEqual(store.schedule, .demo)
        XCTAssertEqual(store.sessions, CarSession.demoSessions)
    }

    func testDemoWriteAppliesLocallyWithoutServer() async {
        let store = CarStore(client: nil)
        store.setDemo()

        await store.setMode(.matchHomeLoad)

        XCTAssertEqual(store.mode.mode, .matchHomeLoad)
        XCTAssertEqual(store.saveState, .saved)
    }

    func testSetClientWipesPreviousServerData() async {
        let transport = CarStoreRoutingTransport(settingsData: settingsJSON(), sessionsData: sessionsJSON)
        let store = CarStore(client: nil)
        store.setClient(APIClient(baseURL: baseURL, transport: transport))
        await store.refresh()
        XCTAssertTrue(store.loaded)

        store.setClient(APIClient(baseURL: URL(string: "http://other.local:8080")!, transport: transport))

        XCTAssertFalse(store.loaded)
        XCTAssertTrue(store.sessions.isEmpty)
        XCTAssertEqual(store.mode, .default)
    }
}

private final class CarStoreRoutingTransport: HTTPTransport, @unchecked Sendable {
    let settingsData: Data
    let sessionsData: Data
    let postStatus: Int
    private(set) var lastPostBody: Data?

    init(settingsData: Data, sessionsData: Data, postStatus: Int = 200) {
        self.settingsData = settingsData
        self.sessionsData = sessionsData
        self.postStatus = postStatus
    }

    func data(for request: URLRequest) async throws -> (Data, HTTPURLResponse) {
        let path = request.url?.path ?? ""
        let method = request.httpMethod ?? "GET"

        if method == "POST", path == "/api/settings" {
            lastPostBody = request.httpBody
            return (Data("{\"values\":{}}".utf8),
                    HTTPURLResponse(url: request.url!, statusCode: postStatus, httpVersion: nil, headerFields: nil)!)
        }

        let data: Data
        switch path {
        case "/api/settings": data = settingsData
        case "/api/car/sessions": data = sessionsData
        default: data = Data("{}".utf8)
        }
        return (data, HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!)
    }
}
