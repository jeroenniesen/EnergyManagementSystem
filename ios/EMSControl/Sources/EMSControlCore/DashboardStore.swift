import Foundation
import Observation

@MainActor
@Observable
public final class DashboardStore {
    public var client: APIClient?
    public private(set) var snapshot: MobileDashboardSnapshot?
    public private(set) var isLoading = false
    public private(set) var isStale = false
    public private(set) var lastError: String?
    public private(set) var nextRefreshAt: Date?
    /// Set when an API call is rejected with 401 (the session token expired or was revoked). Drives
    /// the "your session expired — log in again" prompt on the connection screen. Cleared on any
    /// successful login / demo / forget.
    public private(set) var authFailed = false

    private let demoData: DemoDataStore
    private let credentialStore: CredentialStore
    private let widgetConfig: AppGroupConfigStore
    private let transport: any HTTPTransport
    private let refreshFailureRetryDelay: TimeInterval = 15

    public init(
        client: APIClient?,
        demoData: DemoDataStore = DemoDataStore(),
        credentialStore: CredentialStore = KeychainCredentialStore(),
        widgetConfig: AppGroupConfigStore = AppGroupConfigStore(),
        transport: HTTPTransport = URLSessionTransport()
    ) {
        self.client = client
        self.demoData = demoData
        self.credentialStore = credentialStore
        self.widgetConfig = widgetConfig
        self.transport = transport
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
        } catch APIClientError.httpStatus(401) {
            // The session/token is no longer valid. Bounce back to the login screen rather than
            // showing stale data behind a dead token. Keep the saved server URL (do NOT forget it)
            // so re-login can prefill it; drop the live client + snapshot so AppShellView shows
            // ConnectionView with the expiry prompt.
            authFailed = true
            self.client = nil
            snapshot = nil
            nextRefreshAt = nil
            isStale = false
            lastError = nil
        } catch {
            isStale = snapshot != nil
            lastError = String(describing: error)
            nextRefreshAt = Date().addingTimeInterval(refreshFailureRetryDelay)
        }
    }

    /// Primary auth flow (spec §7): username/password login, then provision the dedicated per-device
    /// widget ACCESS token from the fresh session and route THAT (never the session token) to the
    /// widget. On success the app rides the session token; the widget rides the access token.
    ///
    /// - Throws: the underlying error when *login itself* fails (401 → `APIClientError.httpStatus(401)`,
    ///   or a transport `URLError`) so the caller can distinguish bad credentials from an unreachable
    ///   server. A failure to provision the widget token is non-fatal — the session login still
    ///   completes and the next login re-mints the widget token (that is why it is `replace:true`).
    public func login(baseURL: URL, username: String, password: String, deviceName: String) async throws {
        let session = try await APIClient(baseURL: baseURL, token: nil, transport: transport)
            .login(username: username, password: password)
        let liveClient = APIClient(baseURL: baseURL, token: session.token, transport: transport)

        var accessToken: String?
        do {
            let name = WidgetTokenName.make(deviceName: deviceName)
            accessToken = try await liveClient.provisionWidgetToken(name: name).token
        } catch {
            // Non-fatal: keep whatever widget token we already had (never the session token).
            accessToken = nil
        }

        // Interactive/session token → its own Keychain slot (the app rides it). Access token → the
        // separate widget slot + the app-group the widget reads. The session token is NEVER written
        // to the widget's app-group (spec §7 invariant).
        try credentialStore.saveLastBaseURL(baseURL)
        try credentialStore.saveToken(session.token, for: baseURL)
        if let accessToken {
            try? credentialStore.saveWidgetToken(accessToken, for: baseURL)
        }
        let widgetToken = accessToken ?? ((try? credentialStore.widgetToken(for: baseURL)) ?? nil)
        widgetConfig.save(WidgetServerConfig(baseURL: baseURL, token: widgetToken))

        authFailed = false
        client = liveClient
        await refresh()
    }

    public func shouldRefresh(now: Date = Date()) -> Bool {
        guard let nextRefreshAt else { return snapshot == nil && client != nil }
        return now >= nextRefreshAt
    }

    public func refreshWhenDue(now: Date = Date()) async {
        guard shouldRefresh(now: now) else { return }
        await refresh()
    }

    /// Fallback connect path for a MANUALLY-PASTED access token (machine-token users), not the
    /// primary password login. Here the pasted token IS a long-lived access token, so it is
    /// legitimately mirrored to the widget. The password-login path uses `login(...)` instead,
    /// which provisions a dedicated widget access token and never leaks the session token to the
    /// widget (spec §7).
    public func saveConnectedServer(_ client: APIClient) throws {
        authFailed = false
        try credentialStore.saveLastBaseURL(client.baseURL)
        if let token = client.token, !token.isEmpty {
            try credentialStore.saveToken(token, for: client.baseURL)
            try? credentialStore.saveWidgetToken(token, for: client.baseURL)
        }
        // Mirror {baseURL, token} into the shared app group so the home-screen widget can reach the
        // same server without its own onboarding (B-59). Best-effort: never fail a connect over it.
        widgetConfig.save(WidgetServerConfig(baseURL: client.baseURL, token: client.token))
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
        authFailed = false
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
            try? credentialStore.deleteWidgetToken(for: client.baseURL)
        }
        try? credentialStore.deleteLastBaseURL()
        // Drop the widget's shared config + cached render too, so it returns to "Open EMS to connect".
        widgetConfig.clear()
        WidgetSnapshotCache().clear()
        client = nil
        snapshot = nil
        nextRefreshAt = nil
        isStale = false
        lastError = nil
        authFailed = false
    }
}
