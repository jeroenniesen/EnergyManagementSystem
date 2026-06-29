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
    public private(set) var nextRefreshAt: Date?

    private let demoData: DemoDataStore
    private let credentialStore: CredentialStore

    public init(
        client: APIClient?,
        demoData: DemoDataStore = DemoDataStore(),
        credentialStore: CredentialStore = KeychainCredentialStore()
    ) {
        self.client = client
        self.demoData = demoData
        self.credentialStore = credentialStore
    }

    public func refresh() async {
        guard let client else { return }

        isLoading = true
        defer { isLoading = false }

        do {
            let response = try await client.fetchDashboard()
            snapshot = response
            nextRefreshAt = response.generatedAt.addingTimeInterval(TimeInterval(response.cacheTTLSeconds))
            isStale = false
            lastError = nil
        } catch {
            isStale = snapshot != nil
            lastError = String(describing: error)
        }
    }

    public func shouldRefresh(now: Date = Date()) -> Bool {
        guard let nextRefreshAt else { return snapshot == nil && client != nil }
        return now >= nextRefreshAt
    }

    public func refreshWhenDue(now: Date = Date()) async {
        guard shouldRefresh(now: now) else { return }
        await refresh()
    }

    public func saveConnectedServer(_ client: APIClient) throws {
        try credentialStore.saveLastBaseURL(client.baseURL)
        if let token = client.token, !token.isEmpty {
            try credentialStore.saveToken(token, for: client.baseURL)
        }
    }

    public func restoreSavedServer() {
        guard client == nil,
              let baseURL = try? credentialStore.lastBaseURL()
        else { return }
        let token = try? credentialStore.token(for: baseURL)
        client = APIClient(baseURL: baseURL, token: token ?? nil)
    }

    public func useDemo() throws {
        client = nil
        snapshot = try demoData.dashboardSnapshot()
        if let snapshot {
            nextRefreshAt = snapshot.generatedAt.addingTimeInterval(TimeInterval(snapshot.cacheTTLSeconds))
        }
        isStale = false
        lastError = nil
    }

    public func loadDemo() {
        do {
            try useDemo()
        } catch {
            client = nil
            snapshot = nil
            nextRefreshAt = nil
            isStale = false
            lastError = String(describing: error)
        }
    }

    public func forgetServer() {
        if let client {
            try? credentialStore.deleteToken(for: client.baseURL)
        }
        try? credentialStore.deleteLastBaseURL()
        client = nil
        snapshot = nil
        nextRefreshAt = nil
        isStale = false
        lastError = nil
    }
}
