import Foundation
import Observation

// Backs the dashboard's "Your week" digest card + notifications entry row and the pushed
// notifications list. Mirrors the web pairing: the header-bell feed (GET /api/notifications,
// POST /api/notifications/read — Notifications.tsx) and the weekly digest (GET /api/digest —
// WeekDigest.tsx). Reads are best-effort like the web polls: a failed refresh keeps the
// last-known feed on screen. The one write (mark-all-read) is optimistic exactly like the web
// bell — the dot clears immediately and a failed POST just leaves it to resync on next refresh.
@MainActor
@Observable
public final class NotificationsStore {
    // Matches the endpoint's default page; generous for a feed that is sparse by construction
    // (dedupe_key collapses repeats server-side).
    public static let feedLimit = 50

    public var client: APIClient?
    public private(set) var items: [NotificationItem] = []
    public private(set) var unread = 0
    public private(set) var digest: WeekDigest?
    public private(set) var isLoading = false
    public private(set) var loaded = false
    public private(set) var errorMessage: String?
    public private(set) var isDemo = false

    /// Identity of the server whose data is cached, so a switch to a different server/token wipes
    /// the previous server's data immediately (mirrors CarStore / ActivityStore / InsightsStore).
    private var serverKey: String

    public init(client: APIClient?) {
        self.client = client
        self.serverKey = Self.serverKey(client)
    }

    public func refresh() async {
        guard let client else { return }

        isLoading = true
        defer { isLoading = false }

        do {
            async let feed = client.fetchNotifications(limit: Self.feedLimit)
            // The digest is best-effort: an older backend without /api/digest (or a week the
            // server can't report on) must not blank the notifications feed — mirrors CarStore's
            // sessions handling. A failure keeps whatever digest was already shown.
            async let digestResult = client.fetchDigest()
            let response = try await feed
            items = response.items
            unread = response.unread
            digest = (try? await digestResult) ?? digest
            errorMessage = nil
            loaded = true
        } catch {
            // Keep the last-known feed visible (web-bell behaviour) and surface the error.
            errorMessage = String(describing: error)
        }
    }

    /// Load the digest for the week containing `week` (YYYY-MM-DD); nil = the server's default
    /// (last completed Mon-Sun week). A failure keeps the currently shown digest.
    public func loadDigest(week: String?) async {
        guard let client else { return }
        if let fresh = try? await client.fetchDigest(week: week) {
            digest = fresh
        }
    }

    /// Step to the previous/next week, mirroring the web WeekDigest's ‹ › stepper (which computes
    /// the adjacent Monday from the current label instead of a second round-trip).
    public func stepDigestWeek(direction: Int) async {
        guard let monday = digest?.mondayAnchor else { return }
        await loadDigest(week: WeekDigest.shiftWeek(monday, by: direction))
    }

    /// True when the ‹ › stepper can do anything: a live server plus a parseable week label.
    public var canStepDigestWeek: Bool {
        client != nil && digest?.mondayAnchor != nil
    }

    public func markAllRead() async {
        guard unread > 0 else { return }
        // Optimistic, like the web bell: the dot clears immediately; a failed POST leaves the
        // local state to resync on the next refresh (never rolls back to re-alarm the user).
        items = items.map { item in
            var next = item
            next.read = true
            return next
        }
        unread = 0
        guard let client else { return }  // demo / disconnected: keep the optimistic state
        if let fresh = try? await client.markNotificationsRead() {
            unread = fresh
        }
    }

    public func setClient(_ client: APIClient?) {
        let nextKey = Self.serverKey(client)
        let changed = nextKey != serverKey
        self.client = client
        serverKey = nextKey
        isDemo = false
        if changed {
            items = []
            unread = 0
            digest = nil
            errorMessage = nil
            loaded = false
        }
    }

    // Populate from coded demo fixtures (EMS_UI_DEMO=1) — no server to read/write.
    public func setDemo() {
        client = nil
        serverKey = "demo"
        isDemo = true
        items = NotificationItem.demoItems
        unread = NotificationItem.demoItems.filter { !$0.read }.count
        digest = .demo
        errorMessage = nil
        loaded = true
    }

    private static func serverKey(_ client: APIClient?) -> String {
        guard let client else { return "none" }
        return "\(client.baseURL.absoluteString)|\(client.token ?? "")"
    }
}
