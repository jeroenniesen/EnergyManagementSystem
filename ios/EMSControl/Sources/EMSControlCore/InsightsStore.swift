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

    private let today: () -> String

    public init(
        client: APIClient?,
        period: InsightsPeriod = .day,
        anchor: String? = nil,
        today: @escaping () -> String = { InsightsPeriod.today() }
    ) {
        self.client = client
        self.period = period
        self.today = today
        self.anchor = anchor ?? today()
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
        } catch {
            errorMessage = String(describing: error)
        }
    }

    public func setClient(_ client: APIClient?) {
        self.client = client
        if client == nil {
            report = nil
            finance = nil
            errorMessage = nil
        }
    }

    public func setDemo(report: ReportSnapshot, finance: FinanceSnapshot) {
        client = nil
        self.report = report
        self.finance = finance
        period = InsightsPeriod(rawValue: report.period ?? "") ?? .day
        anchor = today()
        errorMessage = nil
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
}
