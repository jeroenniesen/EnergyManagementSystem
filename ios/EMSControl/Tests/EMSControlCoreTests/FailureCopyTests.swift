import Foundation
import XCTest
@testable import EMSControlCore

final class FailureCopyTests: XCTestCase {
    func testUnreachableCopyThreeLineContract() {
        XCTAssertTrue(EMSUnreachableCopy.message.localizedCaseInsensitiveContains("unreachable"))
        let doing = EMSUnreachableCopy.emsDoing.lowercased()
        XCTAssertTrue(doing.contains("watch-only"))
        XCTAssertTrue(doing.contains("self-use"))

        // Never over-claim failsafe without confirmed AUTO; no viewer-network claim.
        // Byte-parity with web labels.unreachable.test.ts / #73.
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
        XCTAssertTrue(line.contains("Laatst bekend"))
        XCTAssertEqual(line, "Laatst bekend 14:05")
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
