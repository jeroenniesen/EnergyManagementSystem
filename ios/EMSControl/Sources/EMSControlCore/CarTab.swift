import Foundation

// Domain types for the Car tab's three settings-backed sections — "While the car charges" battery
// modes, the weekly schedule, and the detected-sessions history. These mirror the web Car view
// (ems/web/frontend/src/Car.tsx + ev.tsx) and the backend defaults in ems/settings.py /
// ems/ev_schedule.py, so a partial or missing GET /api/settings response degrades to exactly the
// backend's own default shape rather than throwing. All parsing lives here (not in the view) so it
// is unit-testable without SwiftUI.

// MARK: - "While the car charges" battery modes

// The home BATTERY's behaviour during a charging session (control.car_charging_battery_mode) — a
// different concern from the advisory charge-timing plan. Raw values match ems/control/car_mode.py.
public enum CarChargingMode: String, CaseIterable, Equatable, Sendable {
    case hold
    case staticDischarge = "static_discharge"
    case matchHomeLoad = "match_home_load"

    // Plain-language card title, same wording as the web radio cards (Car.tsx CAR_MODE_TITLE).
    public var title: String {
        switch self {
        case .hold: "Hold the battery"
        case .staticDischarge: "Help with a fixed power"
        case .matchHomeLoad: "Cover the house automatically"
        }
    }

    // Base description; static_discharge's physics detail is appended by the view once the wattage
    // is known, and match_home_load fills in the live "~N W" from the freshest house-load reading.
    public func detail(houseLoadW: Double?) -> String {
        switch self {
        case .hold:
            return "The battery pauses; solar and grid cover the house and car. Safest, and the default."
        case .staticDischarge:
            return "Discharges at the fixed wattage below while the car charges."
        case .matchHomeLoad:
            if let houseLoadW {
                return "The battery quietly covers the home's predicted use (~\(Int(houseLoadW.rounded())) W "
                    + "right now) so the car charges purely on grid and solar."
            }
            return "The battery quietly covers the home's predicted use so the car charges purely on "
                + "grid and solar."
        }
    }
}

// The three-setting state the Car tab's mode section reads/writes together. `dischargeW` is stored
// as watts (server default 800). Mirrors Car.tsx CAR_MODE_DEFAULTS + the min/max/step bounds.
public struct CarModeSettings: Equatable, Sendable {
    public var holdEnabled: Bool
    public var mode: CarChargingMode
    public var dischargeW: Int

    public static let `default` = CarModeSettings(holdEnabled: true, mode: .hold, dischargeW: 800)
    public static let demo = CarModeSettings(holdEnabled: true, mode: .hold, dischargeW: 800)

    public static let minW = 100
    public static let maxW = 5000
    public static let stepW = 50

    // Settings keys owned by this section (ems/settings.py control.* group).
    public static let holdKey = "control.hold_battery_when_car_charging"
    public static let modeKey = "control.car_charging_battery_mode"
    public static let dischargeKey = "control.car_discharge_w"

    public init(holdEnabled: Bool, mode: CarChargingMode, dischargeW: Int) {
        self.holdEnabled = holdEnabled
        self.mode = mode
        self.dischargeW = dischargeW
    }

    // Build from the effective-settings map (GET /api/settings `values`), filtering only the three
    // keys we own; anything missing/wrong-typed falls back to the backend default (never throws).
    public static func from(values: [String: JSONValue]) -> CarModeSettings {
        let hold = values[holdKey]?.bool ?? CarModeSettings.default.holdEnabled
        let mode = values[modeKey]?.string.flatMap(CarChargingMode.init(rawValue:))
            ?? CarModeSettings.default.mode
        let watts = values[dischargeKey]?.number.map { Int($0.rounded()) }
            ?? CarModeSettings.default.dischargeW
        return CarModeSettings(
            holdEnabled: hold,
            mode: mode,
            dischargeW: clampWatts(watts)
        )
    }

    public static func clampWatts(_ value: Int) -> Int {
        let stepped = Int((Double(value) / Double(stepW)).rounded()) * stepW
        return max(minW, min(maxW, stepped))
    }
}

// MARK: - Weekly schedule (ev.schedule)

// One day of the weekly minimum-charge schedule. Mirrors ems/ev_schedule.py's canonical shape.
public struct CarScheduleDay: Equatable, Sendable {
    public var enabled: Bool
    public var minPct: Int
    public var readyBy: String  // "HH:MM"

    public static let `default` = CarScheduleDay(enabled: false, minPct: 80, readyBy: "07:30")

    public init(enabled: Bool, minPct: Int, readyBy: String) {
        self.enabled = enabled
        self.minPct = minPct
        self.readyBy = readyBy
    }
}

// The 7-day schedule. `ev.schedule` is stored server-side as a JSON *string*, so this parses from /
// serialises to that string. Parsing is maximally tolerant (mirrors ev_schedule.parse_schedule and
// ev.tsx parseScheduleClient): any garbage collapses to the default shape rather than throwing.
public struct CarSchedule: Equatable, Sendable {
    public static let dayOrder = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
    public static let dayLabel: [String: String] = [
        "mon": "Monday", "tue": "Tuesday", "wed": "Wednesday", "thu": "Thursday",
        "fri": "Friday", "sat": "Saturday", "sun": "Sunday",
    ]
    public static let scheduleKey = "ev.schedule"

    public var days: [String: CarScheduleDay]

    public init(days: [String: CarScheduleDay]) {
        self.days = days
    }

    public static let `default` = CarSchedule(
        days: Dictionary(uniqueKeysWithValues: dayOrder.map { ($0, CarScheduleDay.default) })
    )

    // Demo fixture: mirrors the demo-dashboard.json car_plan.schedule (weekdays on, weekend off).
    public static let demo = CarSchedule(days: [
        "mon": CarScheduleDay(enabled: true, minPct: 80, readyBy: "07:30"),
        "tue": CarScheduleDay(enabled: true, minPct: 80, readyBy: "07:30"),
        "wed": CarScheduleDay(enabled: true, minPct: 80, readyBy: "07:30"),
        "thu": CarScheduleDay(enabled: true, minPct: 80, readyBy: "07:30"),
        "fri": CarScheduleDay(enabled: true, minPct: 80, readyBy: "07:30"),
        "sat": CarScheduleDay(enabled: false, minPct: 80, readyBy: "09:00"),
        "sun": CarScheduleDay(enabled: false, minPct: 80, readyBy: "09:00"),
    ])

    public subscript(_ day: String) -> CarScheduleDay {
        days[day] ?? .default
    }

    public var enabledDayCount: Int {
        Self.dayOrder.filter { self[$0].enabled }.count
    }

    private static let timePattern = try! NSRegularExpression(pattern: "^([01]\\d|2[0-3]):([0-5]\\d)$")

    public static func validTime(_ value: String) -> Bool {
        let range = NSRange(value.startIndex..<value.endIndex, in: value)
        return timePattern.firstMatch(in: value, range: range) != nil
    }

    public static func parse(_ raw: String) -> CarSchedule {
        guard
            let data = raw.data(using: .utf8),
            let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any]
        else {
            return .default
        }
        var out = CarSchedule.default.days
        for day in dayOrder {
            guard let rawDay = obj[day] as? [String: Any] else { continue }
            let enabled = (rawDay["enabled"] as? Bool) ?? false
            let minPct: Int
            if let num = rawDay["min_pct"] as? NSNumber {
                minPct = max(0, min(100, num.intValue))
            } else {
                minPct = 80
            }
            let readyByRaw = rawDay["ready_by"] as? String
            let readyBy = (readyByRaw.map(validTime) == true) ? readyByRaw! : "07:30"
            out[day] = CarScheduleDay(enabled: enabled, minPct: minPct, readyBy: readyBy)
        }
        return CarSchedule(days: out)
    }

    // Serialise back to the JSON string the `ev.schedule` setting stores. Sorted keys keep the
    // output deterministic (round-trip tests) and `min_pct` stays an integer via JSONSerialization.
    public func jsonString() -> String {
        var obj: [String: Any] = [:]
        for day in Self.dayOrder {
            let d = self[day]
            obj[day] = ["enabled": d.enabled, "min_pct": d.minPct, "ready_by": d.readyBy]
        }
        guard
            let data = try? JSONSerialization.data(withJSONObject: obj, options: [.sortedKeys]),
            let string = String(data: data, encoding: .utf8)
        else {
            return "{}"
        }
        return string
    }

    public func settingDay(_ day: String, _ patch: (inout CarScheduleDay) -> Void) -> CarSchedule {
        var next = days
        var day0 = self[day]
        patch(&day0)
        next[day] = day0
        return CarSchedule(days: next)
    }
}

// MARK: - Detected charging sessions (GET /api/car/sessions)

public struct CarSession: Codable, Equatable, Identifiable, Sendable {
    public let start: String
    public let end: String
    public let kwh: Double
    public let avgKw: Double
    public let peakKw: Double

    public var id: String { start }

    public init(start: String, end: String, kwh: Double, avgKw: Double, peakKw: Double) {
        self.start = start
        self.end = end
        self.kwh = kwh
        self.avgKw = avgKw
        self.peakKw = peakKw
    }

    // Coded demo fixture (EMS_UI_DEMO=1) — a couple of realistic overnight sessions.
    public static let demoSessions: [CarSession] = [
        CarSession(
            start: "2026-06-30T23:00:00+02:00", end: "2026-07-01T02:30:00+02:00",
            kwh: 9.5, avgKw: 3.2, peakKw: 3.4
        ),
        CarSession(
            start: "2026-06-28T01:00:00+02:00", end: "2026-06-28T04:15:00+02:00",
            kwh: 18.8, avgKw: 7.0, peakKw: 11.0
        ),
    ]
}

public struct CarSessionsResponse: Codable, Equatable, Sendable {
    public let sessions: [CarSession]
    public let days: Int

    public init(sessions: [CarSession], days: Int) {
        self.sessions = sessions
        self.days = days
    }
}

// MARK: - Car database (GET /api/cars)

public struct CarModel: Codable, Equatable, Identifiable, Sendable {
    public let id: String
    public let brand: String
    public let model: String
    public let batteryNetKwh: Double
    public let maxAcKw: Double
    public let years: String

    public init(
        id: String, brand: String, model: String,
        batteryNetKwh: Double, maxAcKw: Double, years: String
    ) {
        self.id = id
        self.brand = brand
        self.model = model
        self.batteryNetKwh = batteryNetKwh
        self.maxAcKw = maxAcKw
        self.years = years
    }
}

public struct CarsResponse: Codable, Equatable, Sendable {
    public let brands: [String]
    public let cars: [CarModel]

    public init(brands: [String], cars: [CarModel]) {
        self.brands = brands
        self.cars = cars
    }
}
