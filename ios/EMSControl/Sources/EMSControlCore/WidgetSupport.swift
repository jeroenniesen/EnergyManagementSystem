import Foundation

// Shared, Foundation-only support for the home-screen widget (BACKLOG B-59). Everything here is
// deliberately UI-framework-free so it (a) links cleanly into the WidgetKit app extension and
// (b) stays unit-testable in EMSControlCoreTests without WidgetKit/SwiftUI. The widget target
// reuses APIClient + the API models directly; this file only adds the app-group bridge and the
// pure render-derivation helpers.
//
// NOTE (Live Activity is OUT OF SCOPE for v1): a live car-charge Activity needs push-to-start via
// APNs (a server + push key), so it is intentionally not implemented here. See README.

// MARK: - App-group bridge

/// The minimal server config the widget needs to reach the EMS: base URL + optional bearer token.
///
/// The token is mirrored into app-group UserDefaults (not the Keychain). The app writes it on a
/// successful connect; the widget only ever reads it. As of auth slice 5 the widget token is
/// minted READ-ONLY (tier "view"), so an app-group default — on a trusted LAN, over plain
/// http:// — is an acceptable tradeoff; a shared keychain-access-group entitlement is
/// deliberately not required. (Keychain *sharing* would need that entitlement on both targets;
/// scoping the token to read-only removes the reason to add it.)
public struct WidgetServerConfig: Codable, Equatable, Sendable {
    public let baseURL: URL
    public let token: String?

    public init(baseURL: URL, token: String?) {
        self.baseURL = baseURL
        self.token = token
    }
}

/// Read/write access to the shared app-group config. Not `Sendable` (wraps `UserDefaults`), so
/// callers construct it locally where they use it rather than passing it across actors.
public struct AppGroupConfigStore {
    /// Must match the App Group entitlement on both the app and widget targets (project.yml).
    public static let appGroupID = "group.com.jeroenniesen.emscontrol"

    private static let baseURLKey = "widget.base_url"
    private static let tokenKey = "widget.token"

    private let defaults: UserDefaults

    public init(appGroupID: String = AppGroupConfigStore.appGroupID) {
        // A valid suite name never collides with the app's own domain; fall back defensively so a
        // misconfigured group id degrades to "no shared config" instead of trapping.
        self.defaults = UserDefaults(suiteName: appGroupID) ?? .standard
    }

    /// Test seam: inject a throwaway suite so tests never touch the real shared container.
    init(defaults: UserDefaults) {
        self.defaults = defaults
    }

    public func save(_ config: WidgetServerConfig) {
        defaults.set(config.baseURL.absoluteString, forKey: Self.baseURLKey)
        if let token = config.token, !token.isEmpty {
            defaults.set(token, forKey: Self.tokenKey)
        } else {
            defaults.removeObject(forKey: Self.tokenKey)
        }
    }

    public func load() -> WidgetServerConfig? {
        guard let raw = defaults.string(forKey: Self.baseURLKey),
              let url = URL(string: raw)
        else { return nil }
        let token = defaults.string(forKey: Self.tokenKey)
        return WidgetServerConfig(baseURL: url, token: token?.isEmpty == true ? nil : token)
    }

    public func clear() {
        defaults.removeObject(forKey: Self.baseURLKey)
        defaults.removeObject(forKey: Self.tokenKey)
    }
}

// MARK: - Widget access-token name

/// Builds the `name` under which the per-device widget access token is minted/replaced
/// (`POST /api/auth/tokens {name, replace:true}`, spec §7). The name is the server-side identity of
/// the token, so it must be **stable per device** — every login re-mints under the same name,
/// atomically revoking the previous one. Foundation-only + pure so it is unit-testable without
/// UIKit; the app passes in `UIDevice.current.name`.
public enum WidgetTokenName {
    public static let prefix = "iOS widget"

    /// `"iOS widget · <sanitized device name>"`. The device name is trimmed, has control characters
    /// and interior whitespace collapsed to single spaces, and is capped so a pathological name
    /// can't bloat the token label. Falls back to "iPhone" when the platform yields nothing usable.
    public static func make(deviceName: String) -> String {
        "\(prefix) · \(sanitize(deviceName))"
    }

    static func sanitize(_ raw: String, maxLength: Int = 40) -> String {
        let collapsed = raw
            .components(separatedBy: .whitespacesAndNewlines.union(.controlCharacters))
            .filter { !$0.isEmpty }
            .joined(separator: " ")
        let trimmed = collapsed.isEmpty ? "iPhone" : collapsed
        guard trimmed.count > maxLength else { return trimmed }
        return String(trimmed.prefix(maxLength)).trimmingCharacters(in: .whitespaces)
    }
}

// MARK: - Render model

/// Verdict shown on the widget: a short homeowner word plus whether the EMS is armed (`live`) or
/// only watching (dry-run). `live == false` renders as the "WATCHING" state; `true` as "LIVE".
public struct WidgetVerdict: Codable, Equatable, Sendable {
    public let word: String
    public let live: Bool

    public init(word: String, live: Bool) {
        self.word = word
        self.live = live
    }
}

public enum WidgetVerdictBuilder {
    /// Map a battery mode / planner intent code to the homeowner-facing verdict word. Prefers the
    /// physical `mode` (from /api/battery, available to every widget size) and falls back to the
    /// planner `intent` (only fetched for the medium size). Mirrors the app's `humanizeMode`.
    public static func word(mode: String?, intent: String? = nil) -> String {
        let raw = firstNonEmpty(mode, intent)
        guard let raw, raw != "--" else { return "Auto" }
        switch raw {
        case "charge", "grid_charge", "grid_charge_to_target", "solar_charge":
            return "Charging"
        case "self_consumption", "auto", "allow_self_consumption":
            return "Self-use"
        case "hold", "hold_reserve":
            return "Holding"
        case "discharge", "discharge_for_load":
            return "Discharging"
        case "idle":
            return "Idle"
        default:
            return raw.replacingOccurrences(of: "_", with: " ").capitalized
        }
    }

    public static func make(dryRun: Bool, mode: String?, intent: String? = nil) -> WidgetVerdict {
        WidgetVerdict(word: word(mode: mode, intent: intent), live: !dryRun)
    }

    private static func firstNonEmpty(_ values: String?...) -> String? {
        for value in values {
            if let value, !value.isEmpty { return value }
        }
        return nil
    }
}

public enum WidgetCarLine {
    /// One-line summary of the next planned car-charge window, e.g. "Car: Mon 23:00 · 9.5 kWh".
    /// Returns `nil` when the EV feature is off or no window is planned, so the medium widget can
    /// simply omit the row. Timezone/locale are injectable for deterministic tests.
    public static func text(
        from carPlan: CarPlanSnapshot,
        timeZone: TimeZone = .current,
        locale: Locale = Locale(identifier: "en_US")
    ) -> String? {
        guard carPlan.enabled,
              let window = carPlan.plan?.windows.first,
              let start = window.start,
              let date = ISOTimestamp.parse(start)
        else { return nil }

        let formatter = DateFormatter()
        formatter.locale = locale
        formatter.timeZone = timeZone
        formatter.dateFormat = "EEE HH:mm"
        let when = formatter.string(from: date)

        let kwh = window.batteryKwh ?? carPlan.plan?.totalPlannedKwh
        if let kwh {
            let value = kwh.formatted(.number.precision(.fractionLength(0 ... 1)).locale(locale))
            return "Car: \(when) · \(value) kWh"
        }
        return "Car: \(when)"
    }
}

/// Render-ready snapshot the widget draws from. It is also the shape cached in the app group so a
/// failed refresh can fall back to the last good data (shown "as of HH:mm").
public struct WidgetRenderData: Codable, Equatable, Sendable {
    public let socPct: Double?
    public let verdict: WidgetVerdict
    public let headline: String?
    public let carLine: String?
    public let asOf: Date

    public init(
        socPct: Double?,
        verdict: WidgetVerdict,
        headline: String?,
        carLine: String?,
        asOf: Date
    ) {
        self.socPct = socPct
        self.verdict = verdict
        self.headline = headline
        self.carLine = carLine
        self.asOf = asOf
    }

    /// Gallery / placeholder sample so Xcode previews and the widget picker have plausible content.
    public static let sample = WidgetRenderData(
        socPct: 63,
        verdict: WidgetVerdict(word: "Self-use", live: true),
        headline: "Running the house on your own sun — saving the battery for the evening peak.",
        carLine: "Car: Mon 23:00 · 9.5 kWh",
        asOf: Date(timeIntervalSince1970: 1_783_000_000)
    )
}

/// Persists the last good `WidgetRenderData` in the app group for graceful stale rendering.
/// Separate from `AppGroupConfigStore` so config and cache clear independently.
public struct WidgetSnapshotCache {
    private static let key = "widget.last_render"
    private let defaults: UserDefaults

    public init(appGroupID: String = AppGroupConfigStore.appGroupID) {
        self.defaults = UserDefaults(suiteName: appGroupID) ?? .standard
    }

    init(defaults: UserDefaults) {
        self.defaults = defaults
    }

    public func save(_ data: WidgetRenderData) {
        if let encoded = try? JSONEncoder().encode(data) {
            defaults.set(encoded, forKey: Self.key)
        }
    }

    public func load() -> WidgetRenderData? {
        guard let data = defaults.data(forKey: Self.key) else { return nil }
        return try? JSONDecoder().decode(WidgetRenderData.self, from: data)
    }

    public func clear() {
        defaults.removeObject(forKey: Self.key)
    }
}
