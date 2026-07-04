import Foundation

public extension JSONDecoder {
    static var ems: JSONDecoder {
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        decoder.dateDecodingStrategy = .iso8601
        return decoder
    }
}

public extension JSONEncoder {
    static var ems: JSONEncoder {
        let encoder = JSONEncoder()
        encoder.keyEncodingStrategy = .convertToSnakeCase
        encoder.dateEncodingStrategy = .iso8601
        return encoder
    }
}

public enum SectionState: String, Codable, Equatable, Sendable {
    case ok
    case stale
    case degraded
    case unavailable
}

public struct FlexibleSection: Codable, Equatable, Sendable {
    public let state: SectionState?
    public let message: String?
    public let updatedAt: Date?
    public let values: [String: JSONValue]

    public init(state: SectionState? = nil, message: String? = nil, updatedAt: Date? = nil, values: [String: JSONValue] = [:]) {
        self.state = state
        self.message = message
        self.updatedAt = updatedAt
        self.values = values
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: DynamicCodingKey.self)
        var values: [String: JSONValue] = [:]
        for key in container.allKeys {
            values[key.stringValue] = try container.decode(JSONValue.self, forKey: key)
        }
        self.values = values
        self.state = values["state"]?.string.flatMap(SectionState.init(rawValue:))
        self.message = values["message"]?.string
        if let raw = values["updated_at"]?.string ?? values["updatedAt"]?.string {
            self.updatedAt = ISO8601DateFormatter().date(from: raw)
        } else {
            self.updatedAt = nil
        }
    }
}

public struct DashboardSnapshot: Codable, Equatable, Sendable {
    public let apiVersion: Int
    public let generatedAt: Date
    public let serverTime: Date
    public let serverName: String
    public let cacheTTLSeconds: Int
    public let degradedSections: [String]
    public let readiness: FlexibleSection
    public let status: FlexibleSection
    public let freshness: FlexibleSection
    public let strategy: FlexibleSection
    public let decision: FlexibleSection
    public let alerts: FlexibleSection
    public let battery: FlexibleSection
    public let chargeNeed: FlexibleSection
    public let savings: FlexibleSection
    public let energyStory: FlexibleSection
    public let aiValidation: FlexibleSection?

    public var isDemo: Bool { serverName.lowercased().contains("demo") }

    enum CodingKeys: String, CodingKey {
        case apiVersion
        case generatedAt
        case serverTime
        case serverName
        case cacheTtlSeconds
        case degradedSections
        case readiness
        case status
        case freshness
        case strategy
        case decision
        case alerts
        case battery
        case chargeNeed
        case savings
        case energyStory
        case aiValidation
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        apiVersion = try container.decode(Int.self, forKey: .apiVersion)
        generatedAt = try container.decode(Date.self, forKey: .generatedAt)
        serverTime = try container.decode(Date.self, forKey: .serverTime)
        serverName = try container.decode(String.self, forKey: .serverName)
        cacheTTLSeconds = try container.decode(Int.self, forKey: .cacheTtlSeconds)
        degradedSections = try container.decode([String].self, forKey: .degradedSections)
        readiness = try container.decode(FlexibleSection.self, forKey: .readiness)
        status = try container.decode(FlexibleSection.self, forKey: .status)
        freshness = try container.decode(FlexibleSection.self, forKey: .freshness)
        strategy = try container.decode(FlexibleSection.self, forKey: .strategy)
        decision = try container.decode(FlexibleSection.self, forKey: .decision)
        alerts = try container.decode(FlexibleSection.self, forKey: .alerts)
        battery = try container.decode(FlexibleSection.self, forKey: .battery)
        chargeNeed = try container.decode(FlexibleSection.self, forKey: .chargeNeed)
        savings = try container.decode(FlexibleSection.self, forKey: .savings)
        energyStory = try container.decode(FlexibleSection.self, forKey: .energyStory)
        aiValidation = try container.decodeIfPresent(FlexibleSection.self, forKey: .aiValidation)
    }

    public func encode(to encoder: Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        try container.encode(apiVersion, forKey: .apiVersion)
        try container.encode(generatedAt, forKey: .generatedAt)
        try container.encode(serverTime, forKey: .serverTime)
        try container.encode(serverName, forKey: .serverName)
        try container.encode(cacheTTLSeconds, forKey: .cacheTtlSeconds)
        try container.encode(degradedSections, forKey: .degradedSections)
        try container.encode(readiness, forKey: .readiness)
        try container.encode(status, forKey: .status)
        try container.encode(freshness, forKey: .freshness)
        try container.encode(strategy, forKey: .strategy)
        try container.encode(decision, forKey: .decision)
        try container.encode(alerts, forKey: .alerts)
        try container.encode(battery, forKey: .battery)
        try container.encode(chargeNeed, forKey: .chargeNeed)
        try container.encode(savings, forKey: .savings)
        try container.encode(energyStory, forKey: .energyStory)
        try container.encodeIfPresent(aiValidation, forKey: .aiValidation)
    }
}

public struct MobileDashboardSnapshot: Codable, Equatable, Sendable {
    public let generatedAt: Date
    public let serverName: String
    public let cacheTTLSeconds: Int
    public let status: StatusSnapshot
    public let freshness: FreshnessSnapshot
    public let decision: DecisionSnapshot
    public let alerts: AlertsSnapshot
    public let battery: BatterySnapshot
    public let chargeNeed: ChargeNeedSnapshot
    public let savings: SavingsSnapshot
    public let energyStory: EnergyStorySnapshot
    public let report: ReportSnapshot
    public let finance: FinanceSnapshot

    public var isDemo: Bool { serverName.lowercased().contains("demo") }
    public var degradedSections: [String] {
        var sections: [String] = []
        if alerts.dataQuality != "complete" { sections.append("data quality") }
        if battery.aggregate == nil && battery.towers.isEmpty { sections.append("battery details") }
        return sections
    }

    public init(
        generatedAt: Date,
        serverName: String,
        cacheTTLSeconds: Int,
        status: StatusSnapshot,
        freshness: FreshnessSnapshot,
        decision: DecisionSnapshot,
        alerts: AlertsSnapshot,
        battery: BatterySnapshot,
        chargeNeed: ChargeNeedSnapshot,
        savings: SavingsSnapshot,
        energyStory: EnergyStorySnapshot,
        report: ReportSnapshot,
        finance: FinanceSnapshot
    ) {
        self.generatedAt = generatedAt
        self.serverName = serverName
        self.cacheTTLSeconds = cacheTTLSeconds
        self.status = status
        self.freshness = freshness
        self.decision = decision
        self.alerts = alerts
        self.battery = battery
        self.chargeNeed = chargeNeed
        self.savings = savings
        self.energyStory = energyStory
        self.report = report
        self.finance = finance
    }

    public init(legacy snapshot: DashboardSnapshot) {
        self.init(
            generatedAt: snapshot.generatedAt,
            serverName: snapshot.serverName,
            cacheTTLSeconds: snapshot.cacheTTLSeconds,
            status: StatusSnapshot(section: snapshot.status),
            freshness: FreshnessSnapshot(values: snapshot.freshness.values.compactMapValues(\.string)),
            decision: DecisionSnapshot(section: snapshot.decision),
            alerts: AlertsSnapshot(section: snapshot.alerts),
            battery: BatterySnapshot(section: snapshot.battery),
            chargeNeed: ChargeNeedSnapshot(section: snapshot.chargeNeed),
            savings: SavingsSnapshot(todayEur: snapshot.savings.values["today_eur"]?.number),
            energyStory: EnergyStorySnapshot(section: snapshot.energyStory),
            report: .empty,
            finance: .empty
        )
    }
}

public struct StatusSnapshot: Codable, Equatable, Sendable {
    public let dryRun: Bool
    public let devMode: String
    public let socPct: Double?
    public let gridPowerW: Double?
    public let solarPowerW: Double?
    public let batteryPowerW: Double?
    public let houseLoadW: Double?
    public let nonEvLoadW: Double?

    public init(
        dryRun: Bool,
        devMode: String,
        socPct: Double?,
        gridPowerW: Double?,
        solarPowerW: Double?,
        batteryPowerW: Double?,
        houseLoadW: Double?,
        nonEvLoadW: Double?
    ) {
        self.dryRun = dryRun
        self.devMode = devMode
        self.socPct = socPct
        self.gridPowerW = gridPowerW
        self.solarPowerW = solarPowerW
        self.batteryPowerW = batteryPowerW
        self.houseLoadW = houseLoadW
        self.nonEvLoadW = nonEvLoadW
    }

    init(section: FlexibleSection) {
        self.init(
            dryRun: section.values["dry_run"]?.bool ?? false,
            devMode: section.values["dev_mode"]?.string ?? "unknown",
            socPct: section.values["soc_pct"]?.number,
            gridPowerW: section.values["grid_power_w"]?.number,
            solarPowerW: section.values["solar_power_w"]?.number,
            batteryPowerW: section.values["battery_power_w"]?.number,
            houseLoadW: section.values["house_load_w"]?.number,
            nonEvLoadW: section.values["non_ev_load_w"]?.number
        )
    }
}

public struct FreshnessSnapshot: Codable, Equatable, Sendable {
    public let values: [String: String]
    public static let empty = FreshnessSnapshot(values: [:])

    public init(values: [String: String]) {
        self.values = values
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: DynamicCodingKey.self)
        var values: [String: String] = [:]
        for key in container.allKeys {
            values[key.stringValue] = try container.decode(String.self, forKey: key)
        }
        self.values = values
    }
}

public struct DecisionSnapshot: Codable, Equatable, Sendable {
    public let intent: String?
    public let desiredMode: String?
    public let applied: Bool
    public let outcome: String?
    public let reason: String?
    public let planReason: String?
    public let planReasonExplained: String?
    public let overrideActive: Bool
    public let carCharging: Bool?
    public let targetSoc: Double?
    public let homeState: HomeState?

    public static let empty = DecisionSnapshot(
        intent: nil,
        desiredMode: nil,
        applied: false,
        outcome: nil,
        reason: nil,
        planReason: nil,
        planReasonExplained: nil,
        overrideActive: false,
        carCharging: nil,
        targetSoc: nil,
        homeState: nil
    )

    public init(
        intent: String?,
        desiredMode: String?,
        applied: Bool,
        outcome: String?,
        reason: String?,
        planReason: String?,
        planReasonExplained: String?,
        overrideActive: Bool,
        carCharging: Bool?,
        targetSoc: Double?,
        homeState: HomeState?
    ) {
        self.intent = intent
        self.desiredMode = desiredMode
        self.applied = applied
        self.outcome = outcome
        self.reason = reason
        self.planReason = planReason
        self.planReasonExplained = planReasonExplained
        self.overrideActive = overrideActive
        self.carCharging = carCharging
        self.targetSoc = targetSoc
        self.homeState = homeState
    }

    init(section: FlexibleSection) {
        self.init(
            intent: section.values["intent"]?.string,
            desiredMode: section.values["desired_mode"]?.string,
            applied: section.values["applied"]?.bool ?? false,
            outcome: section.values["outcome"]?.string,
            reason: section.values["reason"]?.string,
            planReason: section.values["plan_reason"]?.string,
            planReasonExplained: section.values["plan_reason_explained"]?.string,
            overrideActive: section.values["override_active"]?.bool ?? false,
            carCharging: section.values["car_charging"]?.bool,
            targetSoc: section.values["target_soc"]?.number,
            homeState: nil
        )
    }
}

public struct HomeState: Codable, Equatable, Sendable {
    public let headline: String
    public let tone: String
    public let simulated: Bool
}

public struct AlertsSnapshot: Codable, Equatable, Sendable {
    public let dataQuality: String?
    public let alerts: [DashboardAlert]
    public static let empty = AlertsSnapshot(dataQuality: nil, alerts: [])

    public init(dataQuality: String?, alerts: [DashboardAlert]) {
        self.dataQuality = dataQuality
        self.alerts = alerts
    }

    init(section: FlexibleSection) {
        self.init(dataQuality: section.values["data_quality"]?.string, alerts: [])
    }
}

public struct DashboardAlert: Codable, Equatable, Identifiable, Sendable {
    public let key: String
    public let severity: String
    public let message: String
    public var id: String { key }
}

public struct BatterySnapshot: Codable, Equatable, Sendable {
    public let currentMode: String?
    public let capabilities: BatteryCapabilities?
    public let towers: [BatteryTower]
    public let aggregate: BatteryAggregate?
    public static let empty = BatterySnapshot(currentMode: nil, capabilities: nil, towers: [], aggregate: nil)

    public init(
        currentMode: String?,
        capabilities: BatteryCapabilities?,
        towers: [BatteryTower],
        aggregate: BatteryAggregate?
    ) {
        self.currentMode = currentMode
        self.capabilities = capabilities
        self.towers = towers
        self.aggregate = aggregate
    }

    init(section: FlexibleSection) {
        let aggregateValues = section.values["aggregate"]?.object
        self.init(
            currentMode: section.values["current_mode"]?.string ?? section.values["state"]?.string,
            capabilities: nil,
            towers: [],
            aggregate: aggregateValues.map {
                BatteryAggregate(
                    socPct: $0["soc_pct"]?.number,
                    powerW: $0["power_w"]?.number,
                    capacityKwh: $0["capacity_kwh"]?.number,
                    onlineTowers: $0["online_towers"]?.number.map(Int.init),
                    totalTowers: $0["total_towers"]?.number.map(Int.init)
                )
            }
        )
    }
}

public struct BatteryCapabilities: Codable, Equatable, Sendable {
    public let services: [String]
    public let energyModeOptions: [String]
    public let hasStandby: Bool
    public let hasGridChargeSwitch: Bool
    public let p1Paired: Bool
    public let maxChargeW: Double?
    public let maxDischargeW: Double?
}

public struct BatteryTower: Codable, Equatable, Identifiable, Sendable {
    public let ip: String
    public let role: String?
    public let socPct: Double?
    public let powerW: Double?
    public let capacityKwh: Double?
    public let online: Bool
    public let mode: String?
    public var id: String { ip }
}

public struct BatteryAggregate: Codable, Equatable, Sendable {
    public let socPct: Double?
    public let powerW: Double?
    public let capacityKwh: Double?
    public let onlineTowers: Int?
    public let totalTowers: Int?
}

public struct ChargeNeedSnapshot: Codable, Equatable, Sendable {
    public let usableKwh: Double?
    public let currentSocPct: Double?
    public let currentKwh: Double?
    public let reserveKwh: Double?
    public let targetKwh: Double?
    public let targetSocPct: Double?
    public let deficitKwh: Double?
    public let onTrack: Bool?
    public let reason: String?

    public static let empty = ChargeNeedSnapshot(
        usableKwh: nil,
        currentSocPct: nil,
        currentKwh: nil,
        reserveKwh: nil,
        targetKwh: nil,
        targetSocPct: nil,
        deficitKwh: nil,
        onTrack: nil,
        reason: nil
    )

    public init(
        usableKwh: Double?,
        currentSocPct: Double?,
        currentKwh: Double?,
        reserveKwh: Double?,
        targetKwh: Double?,
        targetSocPct: Double?,
        deficitKwh: Double?,
        onTrack: Bool?,
        reason: String?
    ) {
        self.usableKwh = usableKwh
        self.currentSocPct = currentSocPct
        self.currentKwh = currentKwh
        self.reserveKwh = reserveKwh
        self.targetKwh = targetKwh
        self.targetSocPct = targetSocPct
        self.deficitKwh = deficitKwh
        self.onTrack = onTrack
        self.reason = reason
    }

    init(section: FlexibleSection) {
        usableKwh = section.values["usable_kwh"]?.number
        currentSocPct = section.values["current_soc_pct"]?.number
        currentKwh = section.values["current_kwh"]?.number
        reserveKwh = section.values["reserve_kwh"]?.number
        targetKwh = section.values["target_kwh"]?.number
        targetSocPct = section.values["target_soc_pct"]?.number
        deficitKwh = section.values["deficit_kwh"]?.number
        onTrack = section.values["on_track"]?.bool
        reason = section.values["reason"]?.string
    }
}

public struct SavingsSnapshot: Codable, Equatable, Sendable {
    public let todayEur: Double?
    public static let empty = SavingsSnapshot(todayEur: nil)
}

public struct EnergyStorySnapshot: Codable, Equatable, Sendable {
    public let window: String?
    public let now: String?
    public let currentSocPct: Double?
    public let reserveSocPct: Double?
    public let targetSocPct: Double?
    public let targetKwh: Double?
    public let targetDeadline: String?
    public let currentPriceEurPerKwh: Double?
    public let headline: String?
    public let trustMarkers: [String]?
    public let totals: EnergyStoryTotals?
    public let slots: [StorySlot]
    public let recent: [StorySlot]
    public let recentHours: Double?
    public let onTrack: StoryOnTrack?
    public let recentReview: StoryRecentReview?

    public static let empty = EnergyStorySnapshot(
        window: nil,
        now: nil,
        currentSocPct: nil,
        reserveSocPct: nil,
        targetSocPct: nil,
        targetKwh: nil,
        targetDeadline: nil,
        currentPriceEurPerKwh: nil,
        headline: nil,
        trustMarkers: nil,
        totals: nil,
        slots: [],
        recent: [],
        recentHours: nil,
        onTrack: nil,
        recentReview: nil
    )

    public init(
        window: String?,
        now: String?,
        currentSocPct: Double?,
        reserveSocPct: Double?,
        targetSocPct: Double?,
        targetKwh: Double?,
        targetDeadline: String?,
        currentPriceEurPerKwh: Double?,
        headline: String?,
        trustMarkers: [String]?,
        totals: EnergyStoryTotals?,
        slots: [StorySlot],
        recent: [StorySlot],
        recentHours: Double?,
        onTrack: StoryOnTrack?,
        recentReview: StoryRecentReview?
    ) {
        self.window = window
        self.now = now
        self.currentSocPct = currentSocPct
        self.reserveSocPct = reserveSocPct
        self.targetSocPct = targetSocPct
        self.targetKwh = targetKwh
        self.targetDeadline = targetDeadline
        self.currentPriceEurPerKwh = currentPriceEurPerKwh
        self.headline = headline
        self.trustMarkers = trustMarkers
        self.totals = totals
        self.slots = slots
        self.recent = recent
        self.recentHours = recentHours
        self.onTrack = onTrack
        self.recentReview = recentReview
    }

    init(section: FlexibleSection) {
        window = section.values["window"]?.string
        now = section.values["now"]?.string
        currentSocPct = section.values["current_soc_pct"]?.number
        reserveSocPct = section.values["reserve_soc_pct"]?.number
        targetSocPct = section.values["target_soc_pct"]?.number
        targetKwh = section.values["target_kwh"]?.number
        targetDeadline = section.values["target_deadline"]?.string
        currentPriceEurPerKwh = section.values["current_price_eur_per_kwh"]?.number
        headline = section.values["headline"]?.string
        trustMarkers = section.values["trust_markers"]?.array?.compactMap(\.string)
        totals = nil
        slots = []
        recent = []
        recentHours = section.values["recent_hours"]?.number
        onTrack = nil
        recentReview = nil
    }
}

public struct StorySlot: Codable, Equatable, Identifiable, Sendable {
    public let start: String
    public let socPct: Double?
    public let gridW: Double
    public let solarW: Double
    public let batteryW: Double
    public let loadW: Double
    public let eurPerKwh: Double?
    public let action: String

    public var id: String { "\(start)-\(action)" }
}

public struct EnergyStoryTotals: Codable, Equatable, Sendable {
    public let importKwh: Double?
    public let exportKwh: Double?
    public let solarKwh: Double?
    public let chargeKwh: Double?
    public let gridChargeKwh: Double?
    public let solarChargeKwh: Double?
    public let dischargeKwh: Double?
    public let loadKwh: Double?
    public let gridCostEur: Double?
    public let selfSufficiencyPct: Double?
    public let socStartPct: Double?
    public let socEndPct: Double?
    public let socMinPct: Double?
    public let socMaxPct: Double?
}

public struct StoryOnTrack: Codable, Equatable, Sendable {
    public let status: String?
    public let actualSocPct: Double?
    public let targetSocPct: Double?
    public let deficitKwh: Double?
    public let message: String?
}

public struct StoryRecentReview: Codable, Equatable, Sendable {
    public let hours: Double?
    public let solarActualKwh: Double?
    public let solarForecastKwh: Double?
    public let solarPctOfForecast: Double?
    public let batteryChargedKwh: Double?
    public let batteryDischargedKwh: Double?
    public let message: String?
}

public struct ReportSnapshot: Codable, Equatable, Sendable {
    public let period: String?
    public let label: String?
    public let partial: Bool?
    public let flows: [String: JSONValue]
    public let scores: [ReportScore]

    public static let empty = ReportSnapshot(period: nil, label: nil, partial: nil, flows: [:], scores: [])
}

public struct ReportScore: Codable, Equatable, Identifiable, Sendable {
    public let key: String
    public let label: String
    public let value: Double?
    public let raw: Double?
    public let unit: String?
    public let explanation: String?
    public var id: String { key }
}

public struct FinanceSnapshot: Codable, Equatable, Sendable {
    public let period: String?
    public let label: String?
    public let partial: Bool?
    public let days: [FinanceDay]
    public let totals: FinanceTotals

    public static let empty = FinanceSnapshot(
        period: nil,
        label: nil,
        partial: nil,
        days: [],
        totals: FinanceTotals(
            gridCostEur: nil,
            batteryCostEur: nil,
            savedEur: nil,
            gridImportKwh: nil,
            gridExportKwh: nil,
            daysWithPrices: nil,
            daysWithData: nil
        )
    )
}

public struct FinanceDay: Codable, Equatable, Identifiable, Sendable {
    public let day: String
    public let hasData: Bool
    public let priceCoverage: Double?
    public let gridCostEur: Double?
    public let batteryCostEur: Double?
    public let baselineCostEur: Double?
    public let savedEur: Double?
    public let gridImportKwh: Double?
    public let gridExportKwh: Double?
    public let batteryChargeKwh: Double?
    public let batteryDischargeKwh: Double?
    public var id: String { day }
}

public struct FinanceTotals: Codable, Equatable, Sendable {
    public let gridCostEur: Double?
    public let batteryCostEur: Double?
    public let savedEur: Double?
    public let gridImportKwh: Double?
    public let gridExportKwh: Double?
    public let daysWithPrices: Int?
    public let daysWithData: Int?
}

public struct FAQItem: Codable, Equatable, Identifiable, Sendable {
    public let key: String
    public let question: String
    public let answer: String
    public var id: String { key }

    public init(key: String, question: String, answer: String) {
        self.key = key
        self.question = question
        self.answer = answer
    }
}

public struct FAQResponse: Codable, Equatable, Sendable {
    public let aiOn: Bool
    public let items: [FAQItem]

    public init(aiOn: Bool, items: [FAQItem]) {
        self.aiOn = aiOn
        self.items = items
    }
}

public struct ChatRequest: Codable, Equatable, Sendable {
    public let question: String

    public init(question: String) {
        self.question = question
    }
}

public struct ChatResponse: Codable, Equatable, Sendable {
    public let answer: String
    public let source: String

    public init(answer: String, source: String) {
        self.answer = answer
        self.source = source
    }
}

public struct ExplainerStatus: Codable, Equatable, Sendable {
    public let mode: String
    public let active: Bool
    public let language: String

    public init(mode: String, active: Bool, language: String) {
        self.mode = mode
        self.active = active
        self.language = language
    }
}

public struct AuthStatus: Codable, Equatable, Sendable {
    public let required: Bool
    public let authenticated: Bool

    public init(required: Bool, authenticated: Bool) {
        self.required = required
        self.authenticated = authenticated
    }
}

public struct HealthStatus: Codable, Equatable, Sendable {
    public let status: String

    public init(status: String) {
        self.status = status
    }
}

public enum JSONValue: Codable, Equatable, Sendable {
    case string(String)
    case number(Double)
    case bool(Bool)
    case object([String: JSONValue])
    case array([JSONValue])
    case null

    public var string: String? {
        if case let .string(value) = self { return value }
        return nil
    }

    public var number: Double? {
        if case let .number(value) = self { return value }
        return nil
    }

    public var bool: Bool? {
        if case let .bool(value) = self { return value }
        return nil
    }

    public var object: [String: JSONValue]? {
        if case let .object(value) = self { return value }
        return nil
    }

    public var array: [JSONValue]? {
        if case let .array(value) = self { return value }
        return nil
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.singleValueContainer()
        if container.decodeNil() { self = .null }
        else if let value = try? container.decode(Double.self) { self = .number(value) }
        else if let value = try? container.decode(Bool.self) { self = .bool(value) }
        else if let value = try? container.decode(String.self) { self = .string(value) }
        else if let value = try? container.decode([String: JSONValue].self) { self = .object(value) }
        else { self = .array(try container.decode([JSONValue].self)) }
    }

    public func encode(to encoder: Encoder) throws {
        var container = encoder.singleValueContainer()
        switch self {
        case .string(let value): try container.encode(value)
        case .number(let value): try container.encode(value)
        case .bool(let value): try container.encode(value)
        case .object(let value): try container.encode(value)
        case .array(let value): try container.encode(value)
        case .null: try container.encodeNil()
        }
    }
}

public struct DynamicCodingKey: CodingKey, Sendable {
    public let stringValue: String
    public let intValue: Int?

    public init(stringValue: String) {
        self.stringValue = stringValue
        self.intValue = nil
    }

    public init?(intValue: Int) {
        self.stringValue = "\(intValue)"
        self.intValue = intValue
    }
}
