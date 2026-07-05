import Foundation

/// Shared ISO-8601 parsing for view-layer timestamp strings. The API mixes plain and
/// fractional-second timestamps for the same instant, so parse tolerantly. Kept in one place
/// instead of each chart/view rolling its own pair of `ISO8601DateFormatter`s.
public enum ISOTimestamp {
    public static func parse(_ string: String) -> Date? {
        plain.date(from: string) ?? fractional.date(from: string)
    }

    // ISO8601DateFormatter is configured once and only ever read (`date(from:)`), which Foundation
    // documents as thread-safe — so sharing the instances is safe despite the non-Sendable type.
    nonisolated(unsafe) private static let plain: ISO8601DateFormatter = {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime]
        return formatter
    }()

    nonisolated(unsafe) private static let fractional: ISO8601DateFormatter = {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return formatter
    }()
}
