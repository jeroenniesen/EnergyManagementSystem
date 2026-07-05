import Foundation
import Observation

@MainActor
@Observable
public final class ActivityStore {
    public var client: APIClient?
    public private(set) var entries: [AuditEntry] = []
    public private(set) var isLoading = false
    public private(set) var errorMessage: String?
    /// When the last successful load completed. Drives the "last updated" / stale label.
    public private(set) var lastUpdatedAt: Date?
    /// True when a refresh failed but previously-loaded entries are still on screen.
    public private(set) var isStale = false

    private let now: () -> Date
    /// Identity of the server whose entries are cached; a switch to a different server/token wipes
    /// the previous server's activity immediately (mirrors ChatStore.sessionKey / InsightsStore).
    private var serverKey: String

    public init(client: APIClient?, now: @escaping () -> Date = { Date() }) {
        self.client = client
        self.now = now
        self.serverKey = Self.serverKey(client)
    }

    public func refresh() async {
        guard let client else { return }

        isLoading = true
        defer { isLoading = false }

        do {
            entries = try await client.fetchAudit()
            errorMessage = nil
            lastUpdatedAt = now()
            isStale = false
        } catch {
            errorMessage = String(describing: error)
            // Old entries stay visible; flag them stale so the UI can warn.
            isStale = !entries.isEmpty
        }
    }

    public func setClient(_ client: APIClient?) {
        let nextKey = Self.serverKey(client)
        let changed = nextKey != serverKey
        self.client = client
        serverKey = nextKey
        if changed {
            entries = []
            errorMessage = nil
            lastUpdatedAt = nil
            isStale = false
        }
    }

    private static func serverKey(_ client: APIClient?) -> String {
        guard let client else { return "none" }
        return "\(client.baseURL.absoluteString)|\(client.token ?? "")"
    }
}
