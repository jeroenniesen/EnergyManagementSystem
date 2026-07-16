import Foundation
import Observation

// Backs the Car tab's settings-driven sections: the "While the car charges" battery modes, the
// weekly schedule, and the detected-sessions history. The advisory charge-timing plan (the hero
// card) is read from the DashboardStore snapshot's carPlan — a single fetched-every-cycle source —
// so this store owns only what lives behind GET/POST /api/settings and GET /api/car/sessions.
//
// Writes follow the web's immediate-save idiom (Car.tsx): optimistically patch local state, POST
// only the changed key(s), roll back on failure. This is the app's first settings write; it is
// audit-logged server-side like every settings change.
@MainActor
@Observable
public final class CarStore {
    public enum SaveState: Equatable, Sendable {
        case idle
        case saving
        case saved
        case error
    }

    public var client: APIClient?
    public private(set) var mode: CarModeSettings = .default
    public private(set) var schedule: CarSchedule = .default
    public private(set) var sessions: [CarSession] = []
    public private(set) var isLoading = false
    public private(set) var loaded = false
    public private(set) var errorMessage: String?
    public private(set) var isDemo = false

    // Immediate-save feedback for the mode + schedule sections (mirrors Car.tsx's status/error).
    public private(set) var saveState: SaveState = .idle
    public private(set) var saveError: String?

    /// Identity of the server whose data is cached, so a switch to a different server/token wipes
    /// the previous server's data immediately (mirrors ActivityStore / InsightsStore).
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
            async let settings = client.fetchSettings()
            async let sessionsResult = client.fetchCarSessions()
            let values = try await settings
            mode = CarModeSettings.from(values: values)
            if let raw = values[CarSchedule.scheduleKey]?.string {
                schedule = CarSchedule.parse(raw)
            } else {
                schedule = .default
            }
            // Sessions are best-effort: the endpoint returns an empty list rather than erroring, so
            // a failure here (older backend) should not blank the mode/schedule sections.
            sessions = (try? await sessionsResult)?.sessions ?? []
            errorMessage = nil
            loaded = true
        } catch {
            errorMessage = String(describing: error)
        }
    }

    public func setClient(_ client: APIClient?) {
        let nextKey = Self.serverKey(client)
        let changed = nextKey != serverKey
        self.client = client
        serverKey = nextKey
        isDemo = false
        if changed {
            mode = .default
            schedule = .default
            sessions = []
            errorMessage = nil
            loaded = false
            saveState = .idle
            saveError = nil
        }
    }

    // Populate from coded demo fixtures (EMS_UI_DEMO=1) — no server to read/write.
    public func setDemo() {
        client = nil
        serverKey = "demo"
        isDemo = true
        mode = .demo
        schedule = .demo
        sessions = CarSession.demoSessions
        errorMessage = nil
        loaded = true
        saveState = .idle
        saveError = nil
    }

    // MARK: - Writes (optimistic patch → POST changed keys → roll back on failure)

    public func setHoldEnabled(_ next: Bool) async {
        let previous = mode
        mode.holdEnabled = next
        await save([CarModeSettings.holdKey: .bool(next)], rollback: previous)
    }

    public func setMode(_ next: CarChargingMode) async {
        guard next != mode.mode else { return }
        let previous = mode
        mode.mode = next
        var changes: [String: JSONValue] = [CarModeSettings.modeKey: .string(next.rawValue)]
        // Selecting the fixed-power mode saves its wattage alongside the mode in the SAME request,
        // so the two keys that define that behaviour are always persisted together (mirrors web).
        if next == .staticDischarge {
            changes[CarModeSettings.dischargeKey] = .number(Double(mode.dischargeW))
        }
        await save(changes, rollback: previous)
    }

    public func setDischargeW(_ next: Int) async {
        let clamped = CarModeSettings.clampWatts(next)
        guard clamped != mode.dischargeW else { return }
        let previous = mode
        mode.dischargeW = clamped
        await save([CarModeSettings.dischargeKey: .number(Double(clamped))], rollback: previous)
    }

    public func updateScheduleDay(_ day: String, _ patch: (inout CarScheduleDay) -> Void) async {
        let previous = schedule
        schedule = schedule.settingDay(day, patch)
        guard schedule != previous else { return }
        await save([CarSchedule.scheduleKey: .string(schedule.jsonString())], rollbackSchedule: previous)
    }

    // MARK: - Save helpers

    private func save(_ changes: [String: JSONValue], rollback: CarModeSettings) async {
        await post(changes) { [weak self] in self?.mode = rollback }
    }

    private func save(_ changes: [String: JSONValue], rollbackSchedule: CarSchedule) async {
        await post(changes) { [weak self] in self?.schedule = rollbackSchedule }
    }

    private func post(_ changes: [String: JSONValue], rollback: @escaping () -> Void) async {
        // Demo / disconnected: nothing to write to, so keep the optimistic patch and report saved.
        guard let client else {
            saveState = .saved
            saveError = nil
            return
        }
        saveState = .saving
        saveError = nil
        do {
            try await client.postSettings(changes)
            saveState = .saved
        } catch {
            rollback()
            saveError = (error as? LocalizedError)?.errorDescription
                ?? "Couldn't save — try again."
            saveState = .error
        }
    }

    private static func serverKey(_ client: APIClient?) -> String {
        guard let client else { return "none" }
        return "\(client.baseURL.absoluteString)|\(client.token ?? "")"
    }
}
