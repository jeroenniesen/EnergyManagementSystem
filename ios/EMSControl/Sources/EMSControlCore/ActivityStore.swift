import Foundation
import Observation

@MainActor
@Observable
public final class ActivityStore {
    public var client: APIClient?
    public private(set) var entries: [AuditEntry] = []
    public private(set) var isLoading = false
    public private(set) var errorMessage: String?

    public init(client: APIClient?) {
        self.client = client
    }

    public func refresh() async {
        guard let client else { return }

        isLoading = true
        defer { isLoading = false }

        do {
            entries = try await client.fetchAudit()
            errorMessage = nil
        } catch {
            errorMessage = String(describing: error)
        }
    }

    public func setClient(_ client: APIClient?) {
        self.client = client
        if client == nil {
            entries = []
            errorMessage = nil
        }
    }
}
