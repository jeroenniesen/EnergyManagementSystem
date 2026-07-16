import Foundation
import XCTest
@testable import EMSControlCore

final class CarTabModelsTests: XCTestCase {
    // MARK: - Decode: sessions

    func testCarSessionsResponseDecodesSnakeCaseFields() throws {
        let json = """
        {
          "sessions": [
            {"start": "2026-06-30T23:00:00+02:00", "end": "2026-07-01T02:30:00+02:00",
             "kwh": 9.5, "avg_kw": 3.2, "peak_kw": 3.4}
          ],
          "days": 14
        }
        """.data(using: .utf8)!

        let response = try JSONDecoder.ems.decode(CarSessionsResponse.self, from: json)

        XCTAssertEqual(response.days, 14)
        XCTAssertEqual(response.sessions.count, 1)
        let session = try XCTUnwrap(response.sessions.first)
        XCTAssertEqual(session.kwh, 9.5)
        XCTAssertEqual(session.avgKw, 3.2)
        XCTAssertEqual(session.peakKw, 3.4)
        XCTAssertEqual(session.id, "2026-06-30T23:00:00+02:00")
    }

    func testCarSessionsResponseDecodesEmpty() throws {
        let json = #"{"sessions": [], "days": 30}"#.data(using: .utf8)!
        let response = try JSONDecoder.ems.decode(CarSessionsResponse.self, from: json)
        XCTAssertTrue(response.sessions.isEmpty)
        XCTAssertEqual(response.days, 30)
    }

    // MARK: - Decode: cars

    func testCarsResponseDecodesSnakeCaseFields() throws {
        let json = """
        {
          "brands": ["Tesla", "Volkswagen"],
          "cars": [
            {"id": "tesla-model-y-long-range", "brand": "Tesla", "model": "Model Y Long Range",
             "battery_net_kwh": 75.0, "max_ac_kw": 11.0, "years": "2020-present"}
          ]
        }
        """.data(using: .utf8)!

        let response = try JSONDecoder.ems.decode(CarsResponse.self, from: json)

        XCTAssertEqual(response.brands, ["Tesla", "Volkswagen"])
        XCTAssertEqual(response.cars.count, 1)
        let car = try XCTUnwrap(response.cars.first)
        XCTAssertEqual(car.id, "tesla-model-y-long-range")
        XCTAssertEqual(car.batteryNetKwh, 75.0)
        XCTAssertEqual(car.maxAcKw, 11.0)
    }

    // MARK: - CarModeSettings.from

    func testCarModeSettingsFromFullValues() {
        let values: [String: JSONValue] = [
            CarModeSettings.holdKey: .bool(false),
            CarModeSettings.modeKey: .string("static_discharge"),
            CarModeSettings.dischargeKey: .number(1200),
        ]
        let mode = CarModeSettings.from(values: values)
        XCTAssertFalse(mode.holdEnabled)
        XCTAssertEqual(mode.mode, .staticDischarge)
        XCTAssertEqual(mode.dischargeW, 1200)
    }

    func testCarModeSettingsFromMissingKeysFallsBackToDefaults() {
        let mode = CarModeSettings.from(values: [:])
        XCTAssertEqual(mode, .default)
        XCTAssertTrue(mode.holdEnabled)
        XCTAssertEqual(mode.mode, .hold)
        XCTAssertEqual(mode.dischargeW, 800)
    }

    func testCarModeSettingsFromWrongTypesFallsBackToDefaults() {
        let values: [String: JSONValue] = [
            CarModeSettings.holdKey: .string("yes"),
            CarModeSettings.modeKey: .string("nonsense"),
            CarModeSettings.dischargeKey: .string("lots"),
        ]
        let mode = CarModeSettings.from(values: values)
        XCTAssertEqual(mode, .default)
    }

    func testCarModeSettingsClampsWattsToStepAndBounds() {
        XCTAssertEqual(CarModeSettings.clampWatts(823), 800)   // snapped to nearest 50
        XCTAssertEqual(CarModeSettings.clampWatts(40), 100)    // below min
        XCTAssertEqual(CarModeSettings.clampWatts(9000), 5000) // above max
    }

    // MARK: - CarChargingMode copy

    func testCarChargingModeMatchHomeLoadIncludesLiveWatts() {
        XCTAssertTrue(CarChargingMode.matchHomeLoad.detail(houseLoadW: 640).contains("~640 W"))
        XCTAssertFalse(CarChargingMode.matchHomeLoad.detail(houseLoadW: nil).contains("~"))
    }

    // MARK: - CarSchedule parse / serialise

    func testScheduleRoundTripDefault() {
        let parsed = CarSchedule.parse(CarSchedule.default.jsonString())
        XCTAssertEqual(parsed, .default)
    }

    func testScheduleRoundTripDemo() {
        let parsed = CarSchedule.parse(CarSchedule.demo.jsonString())
        XCTAssertEqual(parsed, .demo)
        XCTAssertEqual(parsed.enabledDayCount, 5)
    }

    func testScheduleParseGarbageFallsBackToDefault() {
        XCTAssertEqual(CarSchedule.parse("not json at all"), .default)
        XCTAssertEqual(CarSchedule.parse("[]"), .default)
        XCTAssertEqual(CarSchedule.parse(""), .default)
    }

    func testScheduleParsePartialDayKeepsOthersDefault() {
        let raw = #"{"mon": {"enabled": true, "min_pct": 55, "ready_by": "06:15"}}"#
        let schedule = CarSchedule.parse(raw)
        XCTAssertEqual(schedule["mon"], CarScheduleDay(enabled: true, minPct: 55, readyBy: "06:15"))
        XCTAssertEqual(schedule["tue"], .default)
        XCTAssertEqual(schedule["sun"], .default)
    }

    func testScheduleParseClampsMinPctAndRejectsBadTime() {
        let raw = #"{"wed": {"enabled": true, "min_pct": 150, "ready_by": "99:99"}}"#
        let schedule = CarSchedule.parse(raw)
        XCTAssertEqual(schedule["wed"].minPct, 100)     // clamped
        XCTAssertEqual(schedule["wed"].readyBy, "07:30") // invalid → default
    }

    func testScheduleSettingDayIsImmutableAndTargeted() {
        let updated = CarSchedule.default.settingDay("fri") { $0.enabled = true; $0.minPct = 70 }
        XCTAssertEqual(updated["fri"], CarScheduleDay(enabled: true, minPct: 70, readyBy: "07:30"))
        XCTAssertEqual(CarSchedule.default["fri"], .default) // original untouched
    }

    func testScheduleValidTime() {
        XCTAssertTrue(CarSchedule.validTime("07:30"))
        XCTAssertTrue(CarSchedule.validTime("23:59"))
        XCTAssertFalse(CarSchedule.validTime("24:00"))
        XCTAssertFalse(CarSchedule.validTime("7:5"))
        XCTAssertFalse(CarSchedule.validTime("noon"))
    }
}
