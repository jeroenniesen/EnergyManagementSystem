import Foundation
import Observation

@MainActor
@Observable
public final class InsightsStore {
    public var client: APIClient?
    public private(set) var period: InsightsPeriod
    public private(set) var anchor: String
    public private(set) var report: ReportSnapshot?
    public private(set) var finance: FinanceSnapshot?
    public private(set) var isLoading = false
    public private(set) var errorMessage: String?
    /// When the last successful load completed. Drives the "last updated" / stale label.
    public private(set) var lastUpdatedAt: Date?
    /// True when a refresh failed but previously-loaded data is still on screen.
    public private(set) var isStale = false

    private let today: () -> String
    private let now: () -> Date
    /// Identity of the server whose data is currently cached, so a switch to a *different* server
    /// (or token) wipes the previous server's data immediately — never show server A's numbers
    /// under server B. Mirrors ChatStore.sessionKey.
    private var serverKey: String

    public init(
        client: APIClient?,
        period: InsightsPeriod = .day,
        anchor: String? = nil,
        today: @escaping () -> String = { InsightsPeriod.today() },
        now: @escaping () -> Date = { Date() }
    ) {
        self.client = client
        self.period = period
        self.today = today
        self.now = now
        self.anchor = anchor ?? today()
        self.serverKey = Self.serverKey(client)
    }

    public func refresh() async {
        guard let client else { return }

        isLoading = true
        defer { isLoading = false }

        do {
            async let report = client.fetchReport(period: period, anchor: anchor)
            async let finance = client.fetchFinance(period: period, anchor: anchor)
            self.report = try await report
            self.finance = try await finance
            errorMessage = nil
            lastUpdatedAt = now()
            isStale = false
        } catch {
            errorMessage = String(describing: error)
            // Old data stays visible; flag it stale so the UI can warn instead of lying.
            isStale = report != nil || finance != nil
        }
    }

    public func setClient(_ client: APIClient?) {
        let nextKey = Self.serverKey(client)
        let changed = nextKey != serverKey
        self.client = client
        serverKey = nextKey
        // Wipe cached data whenever the server identity changes (incl. → nil / demo → live).
        if changed {
            report = nil
            finance = nil
            errorMessage = nil
            lastUpdatedAt = nil
            isStale = false
        }
    }

    public func setDemo(report: ReportSnapshot, finance: FinanceSnapshot) {
        client = nil
        serverKey = "demo"
        self.report = report
        self.finance = finance
        period = InsightsPeriod(rawValue: report.period ?? "") ?? .day
        anchor = today()
        errorMessage = nil
        lastUpdatedAt = nil
        isStale = false
    }

    public func setPeriod(_ period: InsightsPeriod) async {
        self.period = period
        anchor = today()
        await refresh()
    }

    public func movePeriod(direction: Int) async {
        anchor = period.shiftedAnchor(anchor, direction: direction)
        await refresh()
    }

    private static func serverKey(_ client: APIClient?) -> String {
        guard let client else { return "none" }
        return "\(client.baseURL.absoluteString)|\(client.token ?? "")"
    }
}
