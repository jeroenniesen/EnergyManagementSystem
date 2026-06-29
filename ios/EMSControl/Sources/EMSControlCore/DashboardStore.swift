import Foundation
import Observation

@MainActor
@Observable
public final class DashboardStore {
    public var client: APIClient?
    public private(set) var snapshot: DashboardSnapshot?
    public private(set) var isLoading = false
    public private(set) var isStale = false
    public private(set) var lastError: String?

    private let demoData: DemoDataStore

    public init(client: APIClient?, demoData: DemoDataStore = DemoDataStore()) {
        self.client = client
        self.demoData = demoData
    }

    public func refresh() async {
        guard let client else { return }

        isLoading = true
        defer { isLoading = false }

        do {
            snapshot = try await client.fetchDashboard()
            isStale = false
            lastError = nil
        } catch {
            isStale = snapshot != nil
            lastError = String(describing: error)
        }
    }

    public func useDemo() throws {
        client = nil
        snapshot = try demoData.dashboardSnapshot()
        isStale = false
        lastError = nil
    }

    public func loadDemo() {
        do {
            try useDemo()
        } catch {
            client = nil
            snapshot = nil
            isStale = false
            lastError = String(describing: error)
        }
    }

    public func forgetServer() {
        client = nil
        snapshot = nil
        isStale = false
        lastError = nil
    }
}
