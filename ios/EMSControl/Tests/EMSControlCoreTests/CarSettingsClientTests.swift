import Foundation
import XCTest
@testable import EMSControlCore

final class CarSettingsClientTests: XCTestCase {
    private let baseURL = URL(string: "http://ems.local:8080")!

    // MARK: - GET /api/settings

    func testFetchSettingsUsesPathAuthAndPreservesVerbatimKeys() async throws {
        let json = """
        {
          "schema": {"ignored": true},
          "values": {
            "control.hold_battery_when_car_charging": false,
            "control.car_charging_battery_mode": "static_discharge",
            "control.car_discharge_w": 900,
            "ev.schedule": "{\\"mon\\":{\\"enabled\\":true,\\"min_pct\\":80,\\"ready_by\\":\\"07:30\\"}}"
          }
        }
        """.data(using: .utf8)!
        let transport = CarSettingsRecordingTransport(data: json)
        let client = APIClient(baseURL: baseURL, token: "abc123", transport: transport)

        let values = try await client.fetchSettings()

        // The dotted, snake_case keys survive verbatim (no camelCase mangling).
        XCTAssertEqual(values["control.car_charging_battery_mode"]?.string, "static_discharge")
        XCTAssertEqual(values["control.hold_battery_when_car_charging"]?.bool, false)
        XCTAssertEqual(values["control.car_discharge_w"]?.number, 900)
        XCTAssertNotNil(values["ev.schedule"]?.string)

        XCTAssertEqual(transport.lastRequest?.url?.path, "/api/settings")
        XCTAssertEqual(transport.lastRequest?.httpMethod, "GET")
        XCTAssertEqual(transport.lastRequest?.value(forHTTPHeaderField: "Authorization"), "Bearer abc123")
    }

    func testFetchSettingsParsesIntoDomainTypes() async throws {
        let json = """
        {
          "values": {
            "control.hold_battery_when_car_charging": true,
            "control.car_charging_battery_mode": "match_home_load",
            "control.car_discharge_w": 1200,
            "ev.schedule": "{\\"tue\\":{\\"enabled\\":true,\\"min_pct\\":60,\\"ready_by\\":\\"06:00\\"}}"
          }
        }
        """.data(using: .utf8)!
        let client = APIClient(baseURL: baseURL, transport: CarSettingsRecordingTransport(data: json))

        let values = try await client.fetchSettings()
        let mode = CarModeSettings.from(values: values)
        let schedule = CarSchedule.parse(values[CarSchedule.scheduleKey]?.string ?? "")

        XCTAssertEqual(mode, CarModeSettings(holdEnabled: true, mode: .matchHomeLoad, dischargeW: 1200))
        XCTAssertEqual(schedule["tue"], CarScheduleDay(enabled: true, minPct: 60, readyBy: "06:00"))
    }

    // MARK: - POST /api/settings

    func testPostSettingsSendsChangedKeysVerbatimWithAuth() async throws {
        let transport = CarSettingsRecordingTransport(
            data: #"{"values":{},"restart_required":false}"#.data(using: .utf8)!
        )
        let client = APIClient(baseURL: baseURL, token: "abc123", transport: transport)

        try await client.postSettings([
            "control.car_charging_battery_mode": .string("static_discharge"),
            "control.car_discharge_w": .number(950),
        ])

        XCTAssertEqual(transport.lastRequest?.url?.path, "/api/settings")
        XCTAssertEqual(transport.lastRequest?.httpMethod, "POST")
        XCTAssertEqual(transport.lastRequest?.value(forHTTPHeaderField: "Content-Type"), "application/json")
        XCTAssertEqual(transport.lastRequest?.value(forHTTPHeaderField: "Authorization"), "Bearer abc123")

        let body = try XCTUnwrap(transport.lastRequest?.httpBody)
        let json = try JSONSerialization.jsonObject(with: body) as? [String: Any]
        XCTAssertEqual(json?["control.car_charging_battery_mode"] as? String, "static_discharge")
        XCTAssertEqual((json?["control.car_discharge_w"] as? NSNumber)?.doubleValue, 950)
    }

    func testPostSettingsEncodesScheduleAsJSONString() async throws {
        let transport = CarSettingsRecordingTransport(data: Data("{}".utf8))
        let client = APIClient(baseURL: baseURL, transport: transport)

        try await client.postSettings(["ev.schedule": .string(CarSchedule.demo.jsonString())])

        let body = try XCTUnwrap(transport.lastRequest?.httpBody)
        let json = try JSONSerialization.jsonObject(with: body) as? [String: Any]
        // The value is a JSON *string* (as the setting is stored), not a nested object.
        let scheduleRaw = try XCTUnwrap(json?["ev.schedule"] as? String)
        XCTAssertEqual(CarSchedule.parse(scheduleRaw), .demo)
    }

    func testPostSettingsSurfacesUnauthorized() async throws {
        let transport = CarSettingsStatusTransport(
            data: #"{"detail":"unauthorized"}"#.data(using: .utf8)!, statusCode: 401
        )
        let client = APIClient(baseURL: baseURL, transport: transport)

        do {
            try await client.postSettings(["control.car_discharge_w": .number(900)])
            XCTFail("Expected postSettings to throw on 401")
        } catch let error as APIClientError {
            XCTAssertEqual(error, .httpStatus(401))
        }
    }

    func testPostSettingsSurfacesValidationRejection() async throws {
        let transport = CarSettingsStatusTransport(
            data: #"{"detail":"invalid settings","errors":{}}"#.data(using: .utf8)!, statusCode: 422
        )
        let client = APIClient(baseURL: baseURL, transport: transport)

        do {
            try await client.postSettings(["control.car_discharge_w": .number(99)])
            XCTFail("Expected postSettings to throw on 422")
        } catch let error as APIClientError {
            XCTAssertEqual(error, .httpStatus(422))
        }
    }

    // MARK: - GET /api/car/sessions and /api/cars

    func testFetchCarSessionsUsesPathAndDaysQuery() async throws {
        let json = """
        {"sessions": [{"start": "2026-06-30T23:00:00+02:00", "end": "2026-07-01T02:30:00+02:00",
          "kwh": 9.5, "avg_kw": 3.2, "peak_kw": 3.4}], "days": 14}
        """.data(using: .utf8)!
        let transport = CarSettingsRecordingTransport(data: json)
        let client = APIClient(baseURL: baseURL, token: "abc123", transport: transport)

        let response = try await client.fetchCarSessions()

        XCTAssertEqual(response.days, 14)
        XCTAssertEqual(response.sessions.first?.avgKw, 3.2)
        XCTAssertEqual(transport.lastRequest?.url?.path, "/api/car/sessions")
        XCTAssertEqual(transport.lastRequest?.url?.query, "days=14")
        XCTAssertEqual(transport.lastRequest?.value(forHTTPHeaderField: "Authorization"), "Bearer abc123")
    }

    func testFetchCarsUsesPathAndDecodes() async throws {
        let json = """
        {"brands": ["Tesla"], "cars": [{"id": "tesla-model-y-long-range", "brand": "Tesla",
          "model": "Model Y Long Range", "battery_net_kwh": 75.0, "max_ac_kw": 11.0,
          "years": "2020-present"}]}
        """.data(using: .utf8)!
        let transport = CarSettingsRecordingTransport(data: json)
        let client = APIClient(baseURL: baseURL, transport: transport)

        let response = try await client.fetchCars()

        XCTAssertEqual(response.cars.first?.maxAcKw, 11.0)
        XCTAssertEqual(transport.lastRequest?.url?.path, "/api/cars")
        XCTAssertEqual(transport.lastRequest?.httpMethod, "GET")
    }
}

private final class CarSettingsRecordingTransport: HTTPTransport, @unchecked Sendable {
    var lastRequest: URLRequest?
    let data: Data

    init(data: Data) { self.data = data }

    func data(for request: URLRequest) async throws -> (Data, HTTPURLResponse) {
        lastRequest = request
        return (data, HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!)
    }
}

private final class CarSettingsStatusTransport: HTTPTransport, @unchecked Sendable {
    var lastRequest: URLRequest?
    let data: Data
    let statusCode: Int

    init(data: Data, statusCode: Int) {
        self.data = data
        self.statusCode = statusCode
    }

    func data(for request: URLRequest) async throws -> (Data, HTTPURLResponse) {
        lastRequest = request
        return (data, HTTPURLResponse(url: request.url!, statusCode: statusCode, httpVersion: nil, headerFields: nil)!)
    }
}
