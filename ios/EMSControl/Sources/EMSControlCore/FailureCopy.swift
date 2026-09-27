import Foundation

/// B-09 / #129 — emotionally complete "EMS unreachable" banner copy (parity with web #73
/// `labels.ts` `EMS_UNREACHABLE` + `formatLaatstBekend`).
///
/// Three lines only: what's wrong, failsafe (never a live mode claim), and "Laatst bekend hh:mm".
/// Does not say "the battery is safe" / "nothing changes" — the client cannot confirm AUTO.
public enum EMSUnreachableCopy {
    public static let message = "EMS is unreachable from this device."

    /// Failsafe intent only — not a live battery reading. No "network loss" claim: the client
    /// cannot tell EMS-down apart from a viewer-side network problem.
    public static let emsDoing =
        "In watch-only mode EMS never changes your battery. After a clean stop the battery returns "
        + "to its own self-use; if EMS itself is down, the last commanded mode stays until EMS is back."
}

/// Format the B-09 "last contact" line. `nil` → never reached (cold fail).
/// Uses the device's local calendar (same as web `formatLaatstBekend`).
public func formatLaatstBekend(_ date: Date?, calendar: Calendar = .current) -> String {
    guard let date else { return "Laatst bekend —" }
    let hour = calendar.component(.hour, from: date)
    let minute = calendar.component(.minute, from: date)
    return String(format: "Laatst bekend %02d:%02d", hour, minute)
}
