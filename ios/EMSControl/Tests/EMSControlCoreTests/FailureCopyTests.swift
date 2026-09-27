import Foundation
import XCTest
@testable import EMSControlCore

final class FailureCopyTests: XCTestCase {
    func testUnreachableCopyMatchesWebByteForByte() {
        // Exact parity with ems/web/frontend/src/labels.ts EMS_UNREACHABLE (#73 / #143).
        XCTAssertEqual(
            EMSUnreachableCopy.message,
            "EMS is unreachable from this device."
        )
        XCTAssertEqual(
            EMSUnreachableCopy.emsDoing,
            "In watch-only mode EMS never changes your battery. After a clean stop the battery returns "
                + "to its own self-use; if EMS itself is down, the last commanded mode stays until EMS is back."
        )

        let blob = "\(EMSUnreachableCopy.message) \(EMSUnreachableCopy.emsDoing)".lowercased()
        XCTAssertFalse(blob.contains("the battery is safe"))
        XCTAssertFalse(blob.contains("nothing changes"))
        XCTAssertFalse(blob.contains("home assistant"))
        XCTAssertFalse(blob.contains("network loss"))
        XCTAssertFalse(blob.contains("safe mode"))
    }

    func testFormatLaatstBekendIncludesFixedPhraseAndHhMm() {
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = TimeZone(secondsFromGMT: 0)!
        var components = DateComponents()
        components.year = 2026
        components.month = 9
        components.day = 27
        components.hour = 14
        components.minute = 5
        let date = calendar.date(from: components)!

        let line = formatLaatstBekend(date, calendar: calendar)
        XCTAssertEqual(line, "Laatst bekend 14:05")
    }

    func testFormatLaatstBekendAmsterdamLocalWallClock() {
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = TimeZone(identifier: "Europe/Amsterdam")!
        var components = DateComponents()
        components.year = 2026
        components.month = 9
        components.day = 27
        components.hour = 14
        components.minute = 5
        let date = calendar.date(from: components)!

        XCTAssertEqual(formatLaatstBekend(date, calendar: calendar), "Laatst bekend 14:05")
    }

    func testFormatLaatstBekendUnknownIsEmDash() {
        XCTAssertEqual(formatLaatstBekend(nil), "Laatst bekend —")
    }

    func testDashboardAlertDecodesB09Fields() throws {
        let json = """
        {
          "data_quality": "unsafe",
          "alerts": [
            {
              "key": "grid_stale",
              "severity": "critical",
              "message": "P1 meter delayed — EMS can't see your grid usage.",
              "safe": "EMS is directing the battery to self-use; that return is not yet confirmed.",
              "action": "Nothing needed — EMS retries automatically.",
              "ems_doing": "EMS actively commands the battery's own self-use and retries the P1 meter automatically."
            }
          ]
        }
        """.data(using: .utf8)!

        let snapshot = try JSONDecoder.ems.decode(AlertsSnapshot.self, from: json)
        XCTAssertEqual(snapshot.alerts.count, 1)
        let alert = try XCTUnwrap(snapshot.alerts.first)
        XCTAssertEqual(alert.key, "grid_stale")
        XCTAssertEqual(alert.severity, "critical")
        XCTAssertEqual(
            alert.emsDoing,
            "EMS actively commands the battery's own self-use and retries the P1 meter automatically."
        )
        XCTAssertEqual(
            alert.safe,
            "EMS is directing the battery to self-use; that return is not yet confirmed."
        )
        XCTAssertEqual(alert.action, "Nothing needed — EMS retries automatically.")
    }

    func testDashboardAlertToleratesMissingB09Fields() throws {
        let json = """
        {
          "data_quality": "complete",
          "alerts": [
            {"key": "dry_run_active", "severity": "info", "message": "Watch-only mode."}
          ]
        }
        """.data(using: .utf8)!

        let snapshot = try JSONDecoder.ems.decode(AlertsSnapshot.self, from: json)
        let alert = try XCTUnwrap(snapshot.alerts.first)
        XCTAssertNil(alert.safe)
        XCTAssertNil(alert.action)
        XCTAssertNil(alert.emsDoing)
    }
}

final class DashboardFailureStateTests: XCTestCase {
    func testStaleHidesLiveStatusCardAndAlerts() {
        let at = Date(timeIntervalSince1970: 1_750_000_000)
        let state = DashboardFailureState.evaluate(
            isStale: true,
            hasSnapshot: true,
            hasClient: true,
            authFailed: false,
            lastError: "boom",
            lastUpdatedAt: at
        )
        XCTAssertTrue(state.showUnreachableBanner)
        XCTAssertTrue(state.hideLiveStatusCard)
        XCTAssertTrue(state.hideServerAlerts)
        XCTAssertEqual(state.lastContactAt, at)
    }

    func testColdFailShowsBannerWithNilLastContact() {
        let state = DashboardFailureState.evaluate(
            isStale: false,
            hasSnapshot: false,
            hasClient: true,
            authFailed: false,
            lastError: "URLError(...)",
            lastUpdatedAt: nil
        )
        XCTAssertTrue(state.showUnreachableBanner)
        XCTAssertTrue(state.hideLiveStatusCard)
        XCTAssertTrue(state.hideServerAlerts)
        XCTAssertNil(state.lastContactAt)
        XCTAssertEqual(formatLaatstBekend(state.lastContactAt), "Laatst bekend —")
    }

    func testAuthFailureDoesNotShowUnreachableBanner() {
        let state = DashboardFailureState.evaluate(
            isStale: false,
            hasSnapshot: false,
            hasClient: false,
            authFailed: true,
            lastError: nil,
            lastUpdatedAt: nil
        )
        XCTAssertEqual(state, .reachable)
        XCTAssertFalse(state.showUnreachableBanner)
    }

    func testReachableWithSnapshotShowsLivePanels() {
        let at = Date()
        let state = DashboardFailureState.evaluate(
            isStale: false,
            hasSnapshot: true,
            hasClient: true,
            authFailed: false,
            lastError: nil,
            lastUpdatedAt: at
        )
        XCTAssertFalse(state.showUnreachableBanner)
        XCTAssertFalse(state.hideLiveStatusCard)
        XCTAssertFalse(state.hideServerAlerts)
        XCTAssertEqual(state.lastContactAt, at)
    }

    func testNoClientNoErrorIsReachableSignIn() {
        let state = DashboardFailureState.evaluate(
            isStale: false,
            hasSnapshot: false,
            hasClient: false,
            authFailed: false,
            lastError: nil,
            lastUpdatedAt: nil
        )
        XCTAssertFalse(state.showUnreachableBanner)
    }
}
