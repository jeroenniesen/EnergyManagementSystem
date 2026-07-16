import Foundation

// Domain types for the notification outbox (GET /api/notifications — the web header bell,
// ems/web/routes/notify.py + Notifications.tsx) and the weekly digest (GET /api/digest — the web
// "Your week" panel, ems/web/routes/digest.py + ems/digest.py + WeekDigest.tsx). All parsing is
// tolerant (mirrors CarPlanBody / BatteryPlanGraph): a missing or null field degrades to a
// sensible default instead of throwing, so an older backend never blanks the dashboard.

// MARK: - Notification outbox (B-20)

// One outbox row, exactly as HistoryStore.notifications_between returns it: `read` is a bool,
// `delivered` a list of channels (["in_app"] or ["in_app", "ntfy"]), `confidence`/`dedupe_key`
// nullable. `ts` stays a string (UTC-ISO) like AuditEntry.ts; views parse it via ISOTimestamp.
public struct NotificationItem: Codable, Equatable, Identifiable, Sendable {
    public let id: Int
    public let ts: String
    public let key: String
    public let title: String
    public let body: String
    public let confidence: String?
    public var read: Bool
    public let delivered: [String]
    public let dedupeKey: String?

    public init(
        id: Int, ts: String, key: String, title: String, body: String,
        confidence: String? = nil, read: Bool = false, delivered: [String] = ["in_app"],
        dedupeKey: String? = nil
    ) {
        self.id = id
        self.ts = ts
        self.key = key
        self.title = title
        self.body = body
        self.confidence = confidence
        self.read = read
        self.delivered = delivered
        self.dedupeKey = dedupeKey
    }

    enum CodingKeys: String, CodingKey {
        case id, ts, key, title, body, confidence, read, delivered, dedupeKey
    }

    // Tolerant decode: only `id` is required (it anchors Identifiable and mark-as-read);
    // everything else falls back to an empty/absent default rather than throwing.
    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decode(Int.self, forKey: .id)
        ts = try c.decodeIfPresent(String.self, forKey: .ts) ?? ""
        key = try c.decodeIfPresent(String.self, forKey: .key) ?? ""
        title = try c.decodeIfPresent(String.self, forKey: .title) ?? ""
        body = try c.decodeIfPresent(String.self, forKey: .body) ?? ""
        confidence = try c.decodeIfPresent(String.self, forKey: .confidence)
        read = try c.decodeIfPresent(Bool.self, forKey: .read) ?? false
        delivered = try c.decodeIfPresent([String].self, forKey: .delivered) ?? []
        dedupeKey = try c.decodeIfPresent(String.self, forKey: .dedupeKey)
    }

    // Coded demo fixtures (EMS_UI_DEMO=1), stamped in the same window as the demo dashboard.
    // Keys mirror real senders: the Sunday digest push and the proven backup-failure alert.
    public static let demoItems: [NotificationItem] = [
        NotificationItem(
            id: 2, ts: "2026-07-05T18:05:00+02:00", key: "weekly_digest",
            title: "Your week: saved €4.20",
            body: "You saved €4.20 this week, ran 78% self-sufficient and the panels made "
                + "52.4 kWh. Steady week — settings look right.",
            read: false, delivered: ["in_app"], dedupeKey: "digest:Week of 2026-06-29"
        ),
        NotificationItem(
            id: 1, ts: "2026-07-04T03:10:00+02:00", key: "backup_failed",
            title: "Backup failed",
            body: "Last night's scheduled database backup did not complete.",
            read: true, delivered: ["in_app", "ntfy"], dedupeKey: "backup_failed:2026-07-04"
        ),
    ]
}

// GET /api/notifications: the newest-first feed + the unread count for the bell dot.
public struct NotificationsResponse: Codable, Equatable, Sendable {
    public let items: [NotificationItem]
    public let unread: Int

    public static let empty = NotificationsResponse(items: [], unread: 0)

    public init(items: [NotificationItem], unread: Int) {
        self.items = items
        self.unread = unread
    }

    enum CodingKeys: String, CodingKey {
        case items, unread
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        items = try c.decodeIfPresent([NotificationItem].self, forKey: .items) ?? []
        unread = try c.decodeIfPresent(Int.self, forKey: .unread) ?? 0
    }
}

// MARK: - Weekly digest (B-58 "the Sunday read")

public struct DigestBestDay: Codable, Equatable, Sendable {
    public let date: String
    public let savedEur: Double

    public init(date: String, savedEur: Double) {
        self.date = date
        self.savedEur = savedEur
    }
}

// What the system DID this week, counted from the audit trail (ems/digest.py _count_actions).
public struct DigestActions: Codable, Equatable, Sendable {
    public let modeSwitches: Int
    public let negativeSoaks: Int
    public let overrides: Int

    public static let zero = DigestActions(modeSwitches: 0, negativeSoaks: 0, overrides: 0)

    public init(modeSwitches: Int, negativeSoaks: Int, overrides: Int) {
        self.modeSwitches = modeSwitches
        self.negativeSoaks = negativeSoaks
        self.overrides = overrides
    }

    enum CodingKeys: String, CodingKey {
        case modeSwitches, negativeSoaks, overrides
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        modeSwitches = try c.decodeIfPresent(Int.self, forKey: .modeSwitches) ?? 0
        negativeSoaks = try c.decodeIfPresent(Int.self, forKey: .negativeSoaks) ?? 0
        overrides = try c.decodeIfPresent(Int.self, forKey: .overrides) ?? 0
    }
}

// The /api/digest response (ems/digest.py build_digest): what you saved, what the system did,
// one suggested tweak. A number the week can't honestly measure arrives as null and STAYS nil
// here — the view shows "--" rather than a fabricated €0 (same honesty rule as finance).
public struct WeekDigest: Codable, Equatable, Sendable {
    public let weekLabel: String
    public let savedEur: Double?
    public let bestDay: DigestBestDay?
    public let selfSufficiencyPct: Double?
    public let solarKwh: Double
    public let co2AvoidedNote: String?
    public let actions: DigestActions
    public let tweak: String?
    public let headline: String
    public let daysMeasured: Int
    public let daysTotal: Int

    public init(
        weekLabel: String, savedEur: Double?, bestDay: DigestBestDay?,
        selfSufficiencyPct: Double?, solarKwh: Double, co2AvoidedNote: String?,
        actions: DigestActions, tweak: String?, headline: String,
        daysMeasured: Int, daysTotal: Int
    ) {
        self.weekLabel = weekLabel
        self.savedEur = savedEur
        self.bestDay = bestDay
        self.selfSufficiencyPct = selfSufficiencyPct
        self.solarKwh = solarKwh
        self.co2AvoidedNote = co2AvoidedNote
        self.actions = actions
        self.tweak = tweak
        self.headline = headline
        self.daysMeasured = daysMeasured
        self.daysTotal = daysTotal
    }

    enum CodingKeys: String, CodingKey {
        case weekLabel, savedEur, bestDay, selfSufficiencyPct, solarKwh, co2AvoidedNote
        case actions, tweak, headline, daysMeasured, daysTotal
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        weekLabel = try c.decodeIfPresent(String.self, forKey: .weekLabel) ?? ""
        savedEur = try c.decodeIfPresent(Double.self, forKey: .savedEur)
        bestDay = try c.decodeIfPresent(DigestBestDay.self, forKey: .bestDay)
        selfSufficiencyPct = try c.decodeIfPresent(Double.self, forKey: .selfSufficiencyPct)
        solarKwh = try c.decodeIfPresent(Double.self, forKey: .solarKwh) ?? 0
        co2AvoidedNote = try c.decodeIfPresent(String.self, forKey: .co2AvoidedNote)
        actions = try c.decodeIfPresent(DigestActions.self, forKey: .actions) ?? .zero
        tweak = try c.decodeIfPresent(String.self, forKey: .tweak)
        headline = try c.decodeIfPresent(String.self, forKey: .headline) ?? ""
        daysMeasured = try c.decodeIfPresent(Int.self, forKey: .daysMeasured) ?? 0
        daysTotal = try c.decodeIfPresent(Int.self, forKey: .daysTotal) ?? 0
    }

    /// Days without data yet (drives the web's "N of M days measured" coverage line).
    public var partial: Bool { daysTotal > 0 && daysMeasured < daysTotal }

    /// The web fact-card total: mode switches + manual overrides ("battery adjustments").
    public var adjustmentsTotal: Int { actions.modeSwitches + actions.overrides }

    /// The trailing YYYY-MM-DD in a "Week of YYYY-MM-DD" label — mirrors WeekDigest.tsx's
    /// `mondayOf` (and ems/digest.py's own `_MONDAY_RE`) so the ‹ › stepper can compute the
    /// adjacent week without a second round-trip just to learn the date.
    public var mondayAnchor: String? { Self.monday(of: weekLabel) }

    public static func monday(of label: String) -> String? {
        guard let range = label.range(of: #"\d{4}-\d{2}-\d{2}\s*$"#, options: .regularExpression)
        else { return nil }
        return String(label[range]).trimmingCharacters(in: .whitespaces)
    }

    /// Shift a YYYY-MM-DD Monday by whole weeks (mirrors WeekDigest.tsx `shiftWeek`). An
    /// unparseable input comes back unchanged so a caller can never lose its anchor.
    public static func shiftWeek(_ monday: String, by direction: Int) -> String {
        guard let date = anchorFormatter.date(from: monday),
              let shifted = weekCalendar.date(byAdding: .day, value: 7 * direction, to: date)
        else { return monday }
        return anchorFormatter.string(from: shifted)
    }

    private static let weekCalendar = Calendar(identifier: .gregorian)

    // DateFormatter is configured once and only ever read, which Foundation documents as
    // thread-safe — same justification as ISOTimestamp's shared formatters.
    nonisolated(unsafe) private static let anchorFormatter: DateFormatter = {
        let formatter = DateFormatter()
        formatter.calendar = Calendar(identifier: .gregorian)
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.timeZone = .current
        formatter.dateFormat = "yyyy-MM-dd"
        return formatter
    }()

    // Coded demo fixture (EMS_UI_DEMO=1) — a calm, fully-measured week matching demoItems' digest.
    public static let demo = WeekDigest(
        weekLabel: "Week of 2026-06-29",
        savedEur: 4.20,
        bestDay: DigestBestDay(date: "2026-07-02", savedEur: 1.10),
        selfSufficiencyPct: 78,
        solarKwh: 52.4,
        co2AvoidedNote: "Avoided 62% of a no-solar home's CO₂ this week.",
        actions: DigestActions(modeSwitches: 6, negativeSoaks: 1, overrides: 0),
        tweak: nil,
        headline: "You saved €4.20 this week, ran 78% self-sufficient and the panels made "
            + "52.4 kWh. Steady week — settings look right.",
        daysMeasured: 7,
        daysTotal: 7
    )
}
