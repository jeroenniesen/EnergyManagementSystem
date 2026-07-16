import Foundation
import XCTest
@testable import EMSControlCore

final class NotificationsModelsTests: XCTestCase {
    // MARK: - Decode: notifications feed

    func testNotificationsResponseDecodesFeed() throws {
        let json = """
        {
          "items": [
            {"id": 7, "ts": "2026-07-05T18:05:00+02:00", "key": "weekly_digest",
             "title": "Your week: saved €4.20", "body": "You saved €4.20 this week.",
             "confidence": null, "read": false, "delivered": ["in_app", "ntfy"],
             "dedupe_key": "digest:Week of 2026-06-29"},
            {"id": 6, "ts": "2026-07-04T03:00:00+02:00", "key": "backup_failed",
             "title": "Backup failed", "body": "Nightly backup did not complete.",
             "confidence": "high", "read": true, "delivered": ["in_app"], "dedupe_key": null}
          ],
          "unread": 1
        }
        """.data(using: .utf8)!

        let response = try JSONDecoder.ems.decode(NotificationsResponse.self, from: json)

        XCTAssertEqual(response.unread, 1)
        XCTAssertEqual(response.items.count, 2)
        let first = try XCTUnwrap(response.items.first)
        XCTAssertEqual(first.id, 7)
        XCTAssertEqual(first.key, "weekly_digest")
        XCTAssertFalse(first.read)
        XCTAssertEqual(first.delivered, ["in_app", "ntfy"])
        XCTAssertEqual(first.dedupeKey, "digest:Week of 2026-06-29")
        XCTAssertNil(first.confidence)
        let second = try XCTUnwrap(response.items.last)
        XCTAssertTrue(second.read)
        XCTAssertEqual(second.confidence, "high")
        XCTAssertNil(second.dedupeKey)
    }

    func testNotificationItemToleratesMissingOptionalFields() throws {
        let json = #"{"items": [{"id": 1}], "unread": 0}"#.data(using: .utf8)!

        let response = try JSONDecoder.ems.decode(NotificationsResponse.self, from: json)

        let item = try XCTUnwrap(response.items.first)
        XCTAssertEqual(item.id, 1)
        XCTAssertEqual(item.ts, "")
        XCTAssertEqual(item.title, "")
        XCTAssertEqual(item.body, "")
        XCTAssertFalse(item.read)
        XCTAssertEqual(item.delivered, [])
        XCTAssertNil(item.confidence)
        XCTAssertNil(item.dedupeKey)
    }

    func testNotificationsResponseToleratesEmptyObject() throws {
        let response = try JSONDecoder.ems.decode(
            NotificationsResponse.self, from: Data("{}".utf8))
        XCTAssertTrue(response.items.isEmpty)
        XCTAssertEqual(response.unread, 0)
    }

    // MARK: - Decode: weekly digest

    func testWeekDigestDecodesFullShape() throws {
        let json = """
        {
          "week_label": "Week of 2026-06-22",
          "saved_eur": 3.41,
          "best_day": {"date": "2026-06-25", "saved_eur": 1.02},
          "self_sufficiency_pct": 74.2,
          "solar_kwh": 48.6,
          "co2_avoided_note": "Avoided 62% of a no-solar home's CO2.",
          "actions": {"mode_switches": 5, "negative_soaks": 1, "overrides": 2},
          "tweak": "Apply the advisor suggestion.",
          "headline": "You saved €3.41 this week.",
          "days_measured": 6,
          "days_total": 7
        }
        """.data(using: .utf8)!

        let digest = try JSONDecoder.ems.decode(WeekDigest.self, from: json)

        XCTAssertEqual(digest.weekLabel, "Week of 2026-06-22")
        XCTAssertEqual(digest.savedEur, 3.41)
        XCTAssertEqual(digest.bestDay, DigestBestDay(date: "2026-06-25", savedEur: 1.02))
        XCTAssertEqual(digest.selfSufficiencyPct, 74.2)
        XCTAssertEqual(digest.solarKwh, 48.6)
        XCTAssertEqual(digest.co2AvoidedNote, "Avoided 62% of a no-solar home's CO2.")
        XCTAssertEqual(digest.actions, DigestActions(modeSwitches: 5, negativeSoaks: 1, overrides: 2))
        XCTAssertEqual(digest.tweak, "Apply the advisor suggestion.")
        XCTAssertEqual(digest.daysMeasured, 6)
        XCTAssertEqual(digest.daysTotal, 7)
        XCTAssertTrue(digest.partial)
        XCTAssertEqual(digest.adjustmentsTotal, 7)
        XCTAssertEqual(digest.mondayAnchor, "2026-06-22")
    }

    func testWeekDigestToleratesNulls() throws {
        // A week with no priced days: build_digest sends null figures, never a fake €0.
        let json = """
        {
          "week_label": "Week of 2026-06-29",
          "saved_eur": null,
          "best_day": null,
          "self_sufficiency_pct": null,
          "solar_kwh": 0.0,
          "co2_avoided_note": null,
          "actions": {"mode_switches": 0, "negative_soaks": 0, "overrides": 0},
          "tweak": null,
          "headline": "No priced days yet this week.",
          "days_measured": 0,
          "days_total": 7
        }
        """.data(using: .utf8)!

        let digest = try JSONDecoder.ems.decode(WeekDigest.self, from: json)

        XCTAssertNil(digest.savedEur)
        XCTAssertNil(digest.bestDay)
        XCTAssertNil(digest.selfSufficiencyPct)
        XCTAssertNil(digest.tweak)
        XCTAssertEqual(digest.adjustmentsTotal, 0)
        XCTAssertTrue(digest.partial)
    }

    func testWeekDigestToleratesMissingFields() throws {
        let json = #"{"week_label": "Week of 2026-06-29", "headline": "h"}"#.data(using: .utf8)!

        let digest = try JSONDecoder.ems.decode(WeekDigest.self, from: json)

        XCTAssertEqual(digest.actions, .zero)
        XCTAssertEqual(digest.solarKwh, 0)
        XCTAssertEqual(digest.daysTotal, 0)
        XCTAssertFalse(digest.partial, "no day counts must not read as a partial week")
    }

    // MARK: - Week label helpers (mirror WeekDigest.tsx mondayOf/shiftWeek)

    func testMondayOfLabel() {
        XCTAssertEqual(WeekDigest.monday(of: "Week of 2026-06-29"), "2026-06-29")
        XCTAssertEqual(WeekDigest.monday(of: "2026-06-29"), "2026-06-29")
        XCTAssertNil(WeekDigest.monday(of: "Week of unknown"))
        XCTAssertNil(WeekDigest.monday(of: ""))
        XCTAssertNil(WeekDigest.monday(of: "2026-06-29 was a Monday"), "date must be trailing")
    }

    func testShiftWeekMovesWholeWeeks() {
        XCTAssertEqual(WeekDigest.shiftWeek("2026-06-29", by: 1), "2026-07-06")
        XCTAssertEqual(WeekDigest.shiftWeek("2026-06-29", by: -1), "2026-06-22")
        // Across a month/year boundary.
        XCTAssertEqual(WeekDigest.shiftWeek("2025-12-29", by: 1), "2026-01-05")
        // Unparseable input comes back unchanged so callers never lose their anchor.
        XCTAssertEqual(WeekDigest.shiftWeek("garbage", by: 1), "garbage")
    }

    // MARK: - Demo fixtures

    func testDemoFixturesAreConsistent() {
        XCTAssertFalse(NotificationItem.demoItems.isEmpty)
        XCTAssertEqual(NotificationItem.demoItems.filter { !$0.read }.count, 1)
        XCTAssertFalse(WeekDigest.demo.headline.isEmpty)
        XCTAssertEqual(WeekDigest.demo.mondayAnchor, "2026-06-29")
        XCTAssertFalse(WeekDigest.demo.partial)
    }
}
