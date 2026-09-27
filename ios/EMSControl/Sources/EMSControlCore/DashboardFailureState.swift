import Foundation

/// B-09 / #129 — what the dashboard should show when EMS is unreachable (parity with web #73/#143).
///
/// Web hides the hero and server alerts under the outage banner so a stale "Nothing needed" /
/// live-looking Mode pill never sits under it. Cold fail (saved client, no snapshot yet) uses
/// the same banner with `Laatst bekend —`.
public struct DashboardFailureState: Equatable, Sendable {
    public let showUnreachableBanner: Bool
    /// Hide `HomeStatePanel` (headline + Battery % / Mode / Price) while unreachable.
    public let hideLiveStatusCard: Bool
    /// Hide `/api/alerts` rows while unreachable so they are not presented as live.
    public let hideServerAlerts: Bool
    /// Drives `formatLaatstBekend`. Nil → cold fail → "Laatst bekend —".
    public let lastContactAt: Date?

    public init(
        showUnreachableBanner: Bool,
        hideLiveStatusCard: Bool,
        hideServerAlerts: Bool,
        lastContactAt: Date?
    ) {
        self.showUnreachableBanner = showUnreachableBanner
        self.hideLiveStatusCard = hideLiveStatusCard
        self.hideServerAlerts = hideServerAlerts
        self.lastContactAt = lastContactAt
    }

    public static let reachable = DashboardFailureState(
        showUnreachableBanner: false,
        hideLiveStatusCard: false,
        hideServerAlerts: false,
        lastContactAt: nil
    )

    /// Derive presentation from store flags.
    /// - Stale: prior snapshot kept after a failed refresh.
    /// - Cold unreachable: client present (e.g. restored saved server), no snapshot, non-auth error.
    public static func evaluate(
        isStale: Bool,
        hasSnapshot: Bool,
        hasClient: Bool,
        authFailed: Bool,
        lastError: String?,
        lastUpdatedAt: Date?
    ) -> DashboardFailureState {
        if isStale, hasSnapshot {
            return DashboardFailureState(
                showUnreachableBanner: true,
                hideLiveStatusCard: true,
                hideServerAlerts: true,
                lastContactAt: lastUpdatedAt
            )
        }
        if hasClient, !hasSnapshot, !authFailed, lastError != nil {
            return DashboardFailureState(
                showUnreachableBanner: true,
                hideLiveStatusCard: true,
                hideServerAlerts: true,
                lastContactAt: nil
            )
        }
        return DashboardFailureState(
            showUnreachableBanner: false,
            hideLiveStatusCard: false,
            hideServerAlerts: false,
            lastContactAt: lastUpdatedAt
        )
    }
}
