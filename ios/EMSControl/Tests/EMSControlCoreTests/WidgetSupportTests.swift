import Foundation
import XCTest
@testable import EMSControlCore

// Covers the Foundation-only widget helpers (B-59) that the WidgetKit extension reuses: the
// app-group config/cache bridge and the pure render derivations (verdict word, LIVE/WATCHING,
// car-window line). The widget's SwiftUI itself is verified by build.
final class WidgetSupportTests: XCTestCase {
    private var suiteName = ""
    private var defaults: UserDefaults!

    override func setUp() {
        super.setUp()
        suiteName = "test.widget.\(UUID().uuidString)"
        defaults = UserDefaults(suiteName: suiteName)
    }

    override func tearDown() {
        defaults.removePersistentDomain(forName: suiteName)
        defaults = nil
        super.tearDown()
    }

    // MARK: - App-group config

    func testConfigRoundTripWithToken() {
        let store = AppGroupConfigStore(defaults: defaults)
        let config = WidgetServerConfig(baseURL: URL(string: "http://192.168.1.20:8080")!, token: "secret")
        store.save(config)

        let loaded = store.load()
        XCTAssertEqual(loaded?.baseURL, config.baseURL)
        XCTAssertEqual(loaded?.token, "secret")
    }

    func testConfigWithoutTokenClearsToken() {
        let store = AppGroupConfigStore(defaults: defaults)
        store.save(WidgetServerConfig(baseURL: URL(string: "http://ems.local:8080")!, token: "old"))
        store.save(WidgetServerConfig(baseURL: URL(string: "http://ems.local:8080")!, token: nil))

        let loaded = store.load()
        XCTAssertNotNil(loaded)
        XCTAssertNil(loaded?.token)
    }

    func testConfigLoadNilWhenEmpty() {
        XCTAssertNil(AppGroupConfigStore(defaults: defaults).load())
    }

    func testConfigClear() {
        let store = AppGroupConfigStore(defaults: defaults)
        store.save(WidgetServerConfig(baseURL: URL(string: "http://ems.local:8080")!, token: "t"))
        store.clear()
        XCTAssertNil(store.load())
    }

    // MARK: - Snapshot cache

    func testSnapshotCacheRoundTrip() {
        let cache = WidgetSnapshotCache(defaults: defaults)
        let data = WidgetRenderData(
            socPct: 72,
            verdict: WidgetVerdict(word: "Charging", live: true),
            headline: "Topping up cheaply.",
            carLine: "Car: Mon 02:00 · 34.5 kWh",
            asOf: Date(timeIntervalSince1970: 1_783_000_000)
        )
        cache.save(data)
        XCTAssertEqual(cache.load(), data)
        cache.clear()
        XCTAssertNil(cache.load())
    }

    // MARK: - Widget access-token name

    func testWidgetTokenNamePrefixesAndKeepsDeviceName() {
        XCTAssertEqual(WidgetTokenName.make(deviceName: "Jeroen's iPhone"), "iOS widget · Jeroen's iPhone")
    }

    func testWidgetTokenNameCollapsesWhitespaceAndControlChars() {
        XCTAssertEqual(WidgetTokenName.sanitize("  Jeroen's\tiPhone \n"), "Jeroen's iPhone")
    }

    func testWidgetTokenNameFallsBackWhenEmpty() {
        XCTAssertEqual(WidgetTokenName.sanitize("   "), "iPhone")
        XCTAssertEqual(WidgetTokenName.make(deviceName: ""), "iOS widget · iPhone")
    }

    func testWidgetTokenNameCapsLength() {
        let long = String(repeating: "A", count: 100)
        XCTAssertEqual(WidgetTokenName.sanitize(long).count, 40)
    }

    // MARK: - Verdict

    func testVerdictWordMapping() {
        XCTAssertEqual(WidgetVerdictBuilder.word(mode: "grid_charge"), "Charging")
        XCTAssertEqual(WidgetVerdictBuilder.word(mode: "solar_charge"), "Charging")
        XCTAssertEqual(WidgetVerdictBuilder.word(mode: "self_consumption"), "Self-use")
        XCTAssertEqual(WidgetVerdictBuilder.word(mode: "auto"), "Self-use")
        XCTAssertEqual(WidgetVerdictBuilder.word(mode: "hold"), "Holding")
        XCTAssertEqual(WidgetVerdictBuilder.word(mode: "hold_reserve"), "Holding")
        XCTAssertEqual(WidgetVerdictBuilder.word(mode: "discharge"), "Discharging")
        XCTAssertEqual(WidgetVerdictBuilder.word(mode: "idle"), "Idle")
        XCTAssertEqual(WidgetVerdictBuilder.word(mode: nil), "Auto")
        XCTAssertEqual(WidgetVerdictBuilder.word(mode: "--"), "Auto")
    }

    func testVerdictFallsBackToIntentThenTitleCases() {
        XCTAssertEqual(WidgetVerdictBuilder.word(mode: nil, intent: "grid_charge_to_target"), "Charging")
        XCTAssertEqual(WidgetVerdictBuilder.word(mode: "", intent: "discharge_for_load"), "Discharging")
        XCTAssertEqual(WidgetVerdictBuilder.word(mode: "some_new_mode"), "Some New Mode")
    }

    func testVerdictLiveVsWatching() {
        XCTAssertTrue(WidgetVerdictBuilder.make(dryRun: false, mode: "hold").live)
        XCTAssertFalse(WidgetVerdictBuilder.make(dryRun: true, mode: "hold").live)
    }

    // MARK: - Car line

    private func decodeCarPlan(_ json: String) throws -> CarPlanSnapshot {
        try JSONDecoder.ems.decode(CarPlanSnapshot.self, from: Data(json.utf8))
    }

    func testCarLineFormatsNextWindow() throws {
        let plan = try decodeCarPlan(#"""
        {
          "enabled": true,
          "plan": {
            "windows": [
              {"start": "2026-07-13T02:00:00+02:00", "end": "2026-07-13T05:30:00+02:00", "battery_kwh": 34.5}
            ]
          }
        }
        """#)
        // 2026-07-13 is a Monday; pin the timezone so the formatted local time is deterministic.
        let line = WidgetCarLine.text(from: plan, timeZone: TimeZone(identifier: "Europe/Amsterdam")!)
        XCTAssertEqual(line, "Car: Mon 02:00 · 34.5 kWh")
    }

    func testCarLineFallsBackToTotalPlannedKwh() throws {
        let plan = try decodeCarPlan(#"""
        {
          "enabled": true,
          "plan": {
            "windows": [{"start": "2026-07-13T23:00:00+02:00", "end": "2026-07-14T01:00:00+02:00"}],
            "total_planned_kwh": 9.5
          }
        }
        """#)
        let line = WidgetCarLine.text(from: plan, timeZone: TimeZone(identifier: "Europe/Amsterdam")!)
        XCTAssertEqual(line, "Car: Mon 23:00 · 9.5 kWh")
    }

    func testCarLineNilWhenDisabledOrEmpty() throws {
        XCTAssertNil(WidgetCarLine.text(from: .empty))
        let enabledNoWindows = try decodeCarPlan(#"{"enabled": true, "plan": {"advice": "x"}}"#)
        XCTAssertNil(WidgetCarLine.text(from: enabledNoWindows))
        // Enabled + windows but the feature disabled at the flag level must still be nil.
        let disabledWithWindow = try decodeCarPlan(#"""
        {"enabled": false, "plan": {"windows": [{"start": "2026-07-13T02:00:00+02:00", "battery_kwh": 5}]}}
        """#)
        XCTAssertNil(WidgetCarLine.text(from: disabledWithWindow))
    }
}
