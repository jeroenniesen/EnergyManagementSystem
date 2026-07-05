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

public struct StrategySnapshot: Codable, Equatable, Sendable {
    public let mode: String
    public let active: String
    public let summary: String
    public let reason: String
    public let auto: Bool
    public let gridTopup: Bool
    public let maxTopupPrice: Double?

    public static let empty = StrategySnapshot(
        mode: "",
        active: "",
        summary: "",
        reason: "",
        auto: false,
        gridTopup: false,
        maxTopupPrice: nil
    )

    public init(
        mode: String,
        active: String,
        summary: String,
        reason: String,
        auto: Bool,
        gridTopup: Bool,
        maxTopupPrice: Double?
    ) {
        self.mode = mode
        self.active = active
        self.summary = summary
        self.reason = reason
        self.auto = auto
        self.gridTopup = gridTopup
        self.maxTopupPrice = maxTopupPrice
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
    public var batteryPlan: BatteryPlanSnapshot = .empty
    public let report: ReportSnapshot
    public let finance: FinanceSnapshot
    public let strategy: StrategySnapshot?

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
        batteryPlan: BatteryPlanSnapshot = .empty,
        report: ReportSnapshot,
        finance: FinanceSnapshot,
        strategy: StrategySnapshot? = nil
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
        self.batteryPlan = batteryPlan
        self.report = report
        self.finance = finance
        self.strategy = strategy
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
            finance: .empty,
            strategy: nil
        )
    }

    // `.convertFromSnakeCase` lower-cases every word after the first and capitalizes only its first
    // letter (e.g. "cache_ttl_seconds" -> "cacheTtlSeconds"), so it never lands on the fully
    // capitalized acronym spelling `cacheTTLSeconds` used below. Every other property in this file
    // follows the per-word-capitalize convention, so only this one case needs a manual mapping —
    // without it, decoding this struct directly (as the demo fixture does) throws `keyNotFound`.
    enum CodingKeys: String, CodingKey {
        case generatedAt
        case serverName
        case cacheTTLSeconds = "cacheTtlSeconds"
        case status
        case freshness
        case decision
        case alerts
        case battery
        case chargeNeed
        case savings
        case energyStory
        case batteryPlan
        case report
        case finance
        case strategy
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        self.init(
            generatedAt: try container.decode(Date.self, forKey: .generatedAt),
            serverName: try container.decode(String.self, forKey: .serverName),
            cacheTTLSeconds: try container.decode(Int.self, forKey: .cacheTTLSeconds),
            status: try container.decode(StatusSnapshot.self, forKey: .status),
            freshness: try container.decodeIfPresent(FreshnessSnapshot.self, forKey: .freshness) ?? .empty,
            decision: try container.decodeIfPresent(DecisionSnapshot.self, forKey: .decision) ?? .empty,
            alerts: try container.decodeIfPresent(AlertsSnapshot.self, forKey: .alerts) ?? .empty,
            battery: try container.decodeIfPresent(BatterySnapshot.self, forKey: .battery) ?? .empty,
            chargeNeed: try container.decodeIfPresent(ChargeNeedSnapshot.self, forKey: .chargeNeed) ?? .empty,
            savings: try container.decodeIfPresent(SavingsSnapshot.self, forKey: .savings) ?? .empty,
            energyStory: try container.decodeIfPresent(EnergyStorySnapshot.self, forKey: .energyStory) ?? .empty,
            batteryPlan: try container.decodeIfPresent(BatteryPlanSnapshot.self, forKey: .batteryPlan) ?? .empty,
            report: try container.decodeIfPresent(ReportSnapshot.self, forKey: .report) ?? .empty,
            finance: try container.decodeIfPresent(FinanceSnapshot.self, forKey: .finance) ?? .empty,
            strategy: try container.decodeIfPresent(StrategySnapshot.self, forKey: .strategy)
        )
    }

    public func encode(to encoder: Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        try container.encode(generatedAt, forKey: .generatedAt)
        try container.encode(serverName, forKey: .serverName)
        try container.encode(cacheTTLSeconds, forKey: .cacheTTLSeconds)
        try container.encode(status, forKey: .status)
        try container.encode(freshness, forKey: .freshness)
        try container.encode(decision, forKey: .decision)
        try container.encode(alerts, forKey: .alerts)
        try container.encode(battery, forKey: .battery)
        try container.encode(chargeNeed, forKey: .chargeNeed)
        try container.encode(savings, forKey: .savings)
        try container.encode(energyStory, forKey: .energyStory)
        try container.encode(batteryPlan, forKey: .batteryPlan)
        try container.encode(report, forKey: .report)
        try container.encode(finance, forKey: .finance)
        try container.encodeIfPresent(strategy, forKey: .strategy)
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

public struct ValidationFinding: Codable, Equatable, Sendable {
    public let severity: String
    public let code: String
    public let message: String
}

public struct PlanValidation: Codable, Equatable, Sendable {
    public let status: String
    public let ok: Bool
    public let findings: [ValidationFinding]
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
    public let planValidation: PlanValidation?
    public let explanationSource: String?

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
        homeState: nil,
        planValidation: nil,
        explanationSource: nil
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
        homeState: HomeState?,
        planValidation: PlanValidation? = nil,
        explanationSource: String? = nil
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
        self.planValidation = planValidation
        self.explanationSource = explanationSource
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
            homeState: nil,
            planValidation: nil,
            explanationSource: nil
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

public struct BatteryPlanSnapshot: Codable, Equatable, Sendable {
    public let status: String
    public let summary: String
    public let currentAction: String
    public let currentReason: String
    public let windowStart: String
    public let windowEnd: String
    public let currentSocPct: Double?
    public let reserveSocPct: Double
    public let targetSocPct: Double?
    public let targetDeadline: String?
    public let plannedGridTopupKwh: Double?
    public let deviation: BatteryPlanDeviation
    public let warnings: [String]
    public let graph: BatteryPlanGraph

    public init(
        status: String,
        summary: String,
        currentAction: String,
        currentReason: String,
        windowStart: String,
        windowEnd: String,
        currentSocPct: Double?,
        reserveSocPct: Double,
        targetSocPct: Double?,
        targetDeadline: String?,
        plannedGridTopupKwh: Double? = nil,
        deviation: BatteryPlanDeviation,
        warnings: [String],
        graph: BatteryPlanGraph
    ) {
        self.status = status
        self.summary = summary
        self.currentAction = currentAction
        self.currentReason = currentReason
        self.windowStart = windowStart
        self.windowEnd = windowEnd
        self.currentSocPct = currentSocPct
        self.reserveSocPct = reserveSocPct
        self.targetSocPct = targetSocPct
        self.targetDeadline = targetDeadline
        self.plannedGridTopupKwh = plannedGridTopupKwh
        self.deviation = deviation
        self.warnings = warnings
        self.graph = graph
    }

    public static let empty = BatteryPlanSnapshot(
        status: "paused_safely",
        summary: "No battery plan yet.",
        currentAction: "paused",
        currentReason: "Connect to an EMS server to see the live plan.",
        windowStart: "",
        windowEnd: "",
        currentSocPct: nil,
        reserveSocPct: 10,
        targetSocPct: nil,
        targetDeadline: nil,
        plannedGridTopupKwh: nil,
        deviation: BatteryPlanDeviation(status: "missing", message: "No data yet."),
        warnings: [],
        graph: BatteryPlanGraph.empty
    )

    public static let demoScenarios: [BatteryPlanSnapshot] = [
        BatteryPlanSnapshot(
            status: "on_track",
            summary: "Battery is on plan for the evening peak.",
            currentAction: "self_consumption",
            currentReason: "Solar covers the house now; EMS saves the battery for the pricey evening hours.",
            windowStart: "2026-07-05T12:00:00+02:00",
            windowEnd: "2026-07-06T12:00:00+02:00",
            currentSocPct: 63,
            reserveSocPct: 10,
            targetSocPct: 88,
            targetDeadline: "2026-07-05T22:00:00+02:00",
            plannedGridTopupKwh: 0,
            deviation: BatteryPlanDeviation(
                status: "ok",
                message: "On track — projected to reach the 88% night target.",
                actualSocPct: 63,
                targetSocPct: 88
            ),
            warnings: [],
            graph: .demoOnTrack
        ),
        BatteryPlanSnapshot(
            status: "behind_target",
            summary: "Battery is behind the night target.",
            currentAction: "grid_charge",
            currentReason: "EMS should use the cheapest grid window before sunset to recover the shortfall.",
            windowStart: "2026-07-05T12:00:00+02:00",
            windowEnd: "2026-07-06T12:00:00+02:00",
            currentSocPct: 51,
            reserveSocPct: 10,
            targetSocPct: 88,
            targetDeadline: "2026-07-05T22:00:00+02:00",
            plannedGridTopupKwh: 5.0,
            deviation: BatteryPlanDeviation(
                status: "behind_forecast",
                message: "Behind the 88% target — a grid top-up is planned before sunset.",
                actualSocPct: 51,
                targetSocPct: 88
            ),
            warnings: ["Behind the 88% target; expect a grid top-up in the cheapest window."],
            graph: .demoBehindTarget
        ),
        BatteryPlanSnapshot(
            status: "paused_safely",
            summary: "Battery automation is paused safely.",
            currentAction: "paused",
            currentReason: "EMS is missing fresh inputs, so it keeps the battery above reserve instead of changing modes.",
            windowStart: "2026-07-05T12:00:00+02:00",
            windowEnd: "2026-07-06T12:00:00+02:00",
            currentSocPct: 58,
            reserveSocPct: 10,
            targetSocPct: nil,
            targetDeadline: nil,
            plannedGridTopupKwh: nil,
            deviation: BatteryPlanDeviation(
                status: "missing",
                message: "Plan confidence is unavailable until fresh data returns.",
                actualSocPct: 58
            ),
            warnings: ["Live price or battery data is stale; EMS is holding safe mode."],
            graph: .demoPausedSafely
        )
    ]
}

public struct BatteryPlanDeviation: Codable, Equatable, Sendable {
    public let status: String          // "ok" | "behind_forecast" | "missing"
    public let message: String
    public let actualSocPct: Double?
    public let targetSocPct: Double?

    public init(
        status: String,
        message: String,
        actualSocPct: Double? = nil,
        targetSocPct: Double? = nil
    ) {
        self.status = status
        self.message = message
        self.actualSocPct = actualSocPct
        self.targetSocPct = targetSocPct
    }
}

public struct BatteryPlanGraph: Codable, Equatable, Sendable {
    public let forecastSoc: [BatteryPlanPoint]
    public let actualSoc: [BatteryPlanPoint]
    public let reserveLine: [BatteryPlanPoint]
    public let targetLine: [BatteryPlanPoint]
    public let plannedActions: [BatteryPlanActionBlock]
    public let priceWindows: [BatteryPlanPriceWindow]
    public let solar: [BatteryPlanSolarPoint]

    public init(
        forecastSoc: [BatteryPlanPoint],
        actualSoc: [BatteryPlanPoint],
        reserveLine: [BatteryPlanPoint],
        targetLine: [BatteryPlanPoint],
        plannedActions: [BatteryPlanActionBlock],
        priceWindows: [BatteryPlanPriceWindow],
        solar: [BatteryPlanSolarPoint]
    ) {
        self.forecastSoc = forecastSoc
        self.actualSoc = actualSoc
        self.reserveLine = reserveLine
        self.targetLine = targetLine
        self.plannedActions = plannedActions
        self.priceWindows = priceWindows
        self.solar = solar
    }

    // Tolerant decode: a partial graph (any sub-array missing from an older/degraded backend) must
    // degrade to empty arrays, NOT throw — otherwise it fails the whole MobileDashboardSnapshot
    // decode and blanks the dashboard instead of just the plan panel (finding #10).
    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        forecastSoc = try c.decodeIfPresent([BatteryPlanPoint].self, forKey: .forecastSoc) ?? []
        actualSoc = try c.decodeIfPresent([BatteryPlanPoint].self, forKey: .actualSoc) ?? []
        reserveLine = try c.decodeIfPresent([BatteryPlanPoint].self, forKey: .reserveLine) ?? []
        targetLine = try c.decodeIfPresent([BatteryPlanPoint].self, forKey: .targetLine) ?? []
        plannedActions = try c.decodeIfPresent([BatteryPlanActionBlock].self,
                                               forKey: .plannedActions) ?? []
        priceWindows = try c.decodeIfPresent([BatteryPlanPriceWindow].self,
                                             forKey: .priceWindows) ?? []
        solar = try c.decodeIfPresent([BatteryPlanSolarPoint].self, forKey: .solar) ?? []
    }

    public static let empty = BatteryPlanGraph(
        forecastSoc: [],
        actualSoc: [],
        reserveLine: [],
        targetLine: [],
        plannedActions: [],
        priceWindows: [],
        solar: []
    )

    public static let demoOnTrack = BatteryPlanGraph.demo(
        forecastSoc: [58, 63, 72, 88, 76, 44, 18, 14],
        actualSoc: [57, 61, 63],
        actions: ["self_consume", "solar_charge", "grid_charge", "hold", "discharge", "discharge", "idle", "solar_charge"],
        solar: [900, 1800, 2400, 1200, 100, 0, 0, 1400]
    )

    public static let demoBehindTarget = BatteryPlanGraph.demo(
        forecastSoc: [58, 63, 72, 88, 76, 44, 18, 14],
        actualSoc: [56, 53, 51],
        actions: ["self_consume", "hold", "grid_charge", "grid_charge", "discharge", "discharge", "idle", "solar_charge"],
        solar: [700, 1300, 1800, 900, 0, 0, 0, 1200]
    )

    public static let demoPausedSafely = BatteryPlanGraph.demo(
        forecastSoc: [],
        actualSoc: [58, 58, 57],
        actions: ["hold", "hold", "hold", "idle"],
        solar: [800, 1200, 400, 0]
    )

    private static func demo(
        forecastSoc: [Double],
        actualSoc: [Double],
        actions: [String],
        solar: [Double]
    ) -> BatteryPlanGraph {
        let stamps = [
            "2026-07-05T12:00:00+02:00",
            "2026-07-05T15:00:00+02:00",
            "2026-07-05T18:00:00+02:00",
            "2026-07-05T21:00:00+02:00",
            "2026-07-06T00:00:00+02:00",
            "2026-07-06T03:00:00+02:00",
            "2026-07-06T06:00:00+02:00",
            "2026-07-06T09:00:00+02:00"
        ]
        let usedStamps = Array(stamps.prefix(max(forecastSoc.count, actualSoc.count, solar.count, actions.count + 1)))
        let reserve = usedStamps.map { BatteryPlanPoint(ts: $0, socPct: 10) }
        let target = usedStamps.map { BatteryPlanPoint(ts: $0, socPct: 88) }
        let blocks = actions.enumerated().compactMap { index, action -> BatteryPlanActionBlock? in
            guard index + 1 < usedStamps.count else { return nil }
            return BatteryPlanActionBlock(start: usedStamps[index], end: usedStamps[index + 1], action: action)
        }

        return BatteryPlanGraph(
            forecastSoc: zip(usedStamps, forecastSoc).map { BatteryPlanPoint(ts: $0.0, socPct: $0.1) },
            actualSoc: zip(usedStamps, actualSoc).map { BatteryPlanPoint(ts: $0.0, socPct: $0.1) },
            reserveLine: reserve,
            targetLine: target,
            plannedActions: blocks,
            priceWindows: [
                BatteryPlanPriceWindow(
                    start: "2026-07-05T18:00:00+02:00",
                    end: "2026-07-05T21:00:00+02:00",
                    minEurPerKwh: 0.11,
                    maxEurPerKwh: 0.17
                )
            ],
            solar: zip(usedStamps, solar).map { BatteryPlanSolarPoint(ts: $0.0, forecastW: $0.1, actualW: nil) }
        )
    }
}

public struct BatteryPlanPoint: Codable, Equatable, Sendable, Identifiable {
    public let ts: String
    public let socPct: Double?

    public var id: String { ts }

    public init(ts: String, socPct: Double?) {
        self.ts = ts
        self.socPct = socPct
    }
}

public struct BatteryPlanActionBlock: Codable, Equatable, Sendable, Identifiable {
    public let start: String
    public let end: String
    public let action: String

    public var id: String { "\(start)-\(end)-\(action)" }

    public init(start: String, end: String, action: String) {
        self.start = start
        self.end = end
        self.action = action
    }
}

public struct BatteryPlanPriceWindow: Codable, Equatable, Sendable, Identifiable {
    public let start: String
    public let end: String
    public let minEurPerKwh: Double
    public let maxEurPerKwh: Double

    public var id: String { "\(start)-\(end)-\(maxEurPerKwh)" }

    public init(start: String, end: String, minEurPerKwh: Double, maxEurPerKwh: Double) {
        self.start = start
        self.end = end
        self.minEurPerKwh = minEurPerKwh
        self.maxEurPerKwh = maxEurPerKwh
    }
}

public struct BatteryPlanSolarPoint: Codable, Equatable, Sendable, Identifiable {
    public let ts: String
    public let forecastW: Double
    public let actualW: Double?

    public var id: String { ts }

    public init(ts: String, forecastW: Double, actualW: Double?) {
        self.ts = ts
        self.forecastW = forecastW
        self.actualW = actualW
    }
}

public struct ReportSnapshot: Codable, Equatable, Sendable {
    public let period: String?
    public let label: String?
    public let partial: Bool?
    public let flows: ReportFlows
    public let scores: [ReportScore]
    public let series: [ReportSeriesBucket]

    public static let empty = ReportSnapshot(period: nil, label: nil, partial: nil, flows: .empty, scores: [], series: [])

    public init(
        period: String?,
        label: String?,
        partial: Bool?,
        flows: ReportFlows,
        scores: [ReportScore],
        series: [ReportSeriesBucket]
    ) {
        self.period = period
        self.label = label
        self.partial = partial
        self.flows = flows
        self.scores = scores
        self.series = series
    }

    enum CodingKeys: String, CodingKey {
        case period
        case label
        case partial
        case flows
        case scores
        case series
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        period = try container.decodeIfPresent(String.self, forKey: .period)
        label = try container.decodeIfPresent(String.self, forKey: .label)
        partial = try container.decodeIfPresent(Bool.self, forKey: .partial)
        flows = try container.decodeIfPresent(ReportFlows.self, forKey: .flows) ?? .empty
        scores = try container.decodeIfPresent([ReportScore].self, forKey: .scores) ?? []
        series = try container.decodeIfPresent([ReportSeriesBucket].self, forKey: .series) ?? []
    }
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

public struct ReportFlows: Codable, Equatable, Sendable {
    public let hasData: Bool
    public let partial: Bool?
    public let solarKwh: Double
    public let gridImportKwh: Double
    public let gridExportKwh: Double
    public let batteryChargeKwh: Double
    public let batteryDischargeKwh: Double
    public let homeKwh: Double
    public let carKwh: Double
    public let carGuardLeakKwh: Double
    public let selfSufficiencyPct: Double?
    public let solarSelfConsumptionPct: Double?

    public static let empty = ReportFlows(
        hasData: false,
        partial: nil,
        solarKwh: 0,
        gridImportKwh: 0,
        gridExportKwh: 0,
        batteryChargeKwh: 0,
        batteryDischargeKwh: 0,
        homeKwh: 0,
        carKwh: 0,
        carGuardLeakKwh: 0,
        selfSufficiencyPct: nil,
        solarSelfConsumptionPct: nil
    )

    public init(
        hasData: Bool,
        partial: Bool?,
        solarKwh: Double,
        gridImportKwh: Double,
        gridExportKwh: Double,
        batteryChargeKwh: Double,
        batteryDischargeKwh: Double,
        homeKwh: Double,
        carKwh: Double,
        carGuardLeakKwh: Double,
        selfSufficiencyPct: Double?,
        solarSelfConsumptionPct: Double?
    ) {
        self.hasData = hasData
        self.partial = partial
        self.solarKwh = solarKwh
        self.gridImportKwh = gridImportKwh
        self.gridExportKwh = gridExportKwh
        self.batteryChargeKwh = batteryChargeKwh
        self.batteryDischargeKwh = batteryDischargeKwh
        self.homeKwh = homeKwh
        self.carKwh = carKwh
        self.carGuardLeakKwh = carGuardLeakKwh
        self.selfSufficiencyPct = selfSufficiencyPct
        self.solarSelfConsumptionPct = solarSelfConsumptionPct
    }

    enum CodingKeys: String, CodingKey {
        case hasData
        case partial
        case solarKwh
        case gridImportKwh
        case gridExportKwh
        case batteryChargeKwh
        case batteryDischargeKwh
        case homeKwh
        case carKwh
        case carGuardLeakKwh
        case selfSufficiencyPct
        case solarSelfConsumptionPct
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        hasData = try container.decodeIfPresent(Bool.self, forKey: .hasData) ?? false
        partial = try container.decodeIfPresent(Bool.self, forKey: .partial)
        solarKwh = try container.decodeIfPresent(Double.self, forKey: .solarKwh) ?? 0
        gridImportKwh = try container.decodeIfPresent(Double.self, forKey: .gridImportKwh) ?? 0
        gridExportKwh = try container.decodeIfPresent(Double.self, forKey: .gridExportKwh) ?? 0
        batteryChargeKwh = try container.decodeIfPresent(Double.self, forKey: .batteryChargeKwh) ?? 0
        batteryDischargeKwh = try container.decodeIfPresent(Double.self, forKey: .batteryDischargeKwh) ?? 0
        homeKwh = try container.decodeIfPresent(Double.self, forKey: .homeKwh) ?? 0
        carKwh = try container.decodeIfPresent(Double.self, forKey: .carKwh) ?? 0
        carGuardLeakKwh = try container.decodeIfPresent(Double.self, forKey: .carGuardLeakKwh) ?? 0
        selfSufficiencyPct = try container.decodeIfPresent(Double.self, forKey: .selfSufficiencyPct)
        solarSelfConsumptionPct = try container.decodeIfPresent(Double.self, forKey: .solarSelfConsumptionPct)
    }
}

public struct ReportSeriesBucket: Codable, Equatable, Identifiable, Sendable {
    public let start: String
    public let gridImportKwh: Double
    public let gridExportKwh: Double
    public let houseKwh: Double
    public let carKwh: Double
    public let solarKwh: Double
    public let samples: Int

    public var id: String { start }
}

public enum InsightsPeriod: String, CaseIterable, Codable, Equatable, Sendable {
    case day
    case week
    case month
    case year

    public var title: String {
        rawValue.prefix(1).uppercased() + rawValue.dropFirst()
    }

    public func shiftedAnchor(_ anchor: String, direction: Int, calendar: Calendar = .current) -> String {
        guard let date = Self.anchorFormatter.date(from: anchor) else { return anchor }
        let component: Calendar.Component
        let amount: Int
        switch self {
        case .day:
            component = .day
            amount = direction
        case .week:
            component = .day
            amount = direction * 7
        case .month:
            component = .month
            amount = direction
        case .year:
            component = .year
            amount = direction
        }
        return calendar.date(byAdding: component, value: amount, to: date)
            .map(Self.anchorFormatter.string(from:)) ?? anchor
    }

    public static func today(calendar: Calendar = .current) -> String {
        anchorFormatter.string(from: calendar.startOfDay(for: Date()))
    }

    private static let anchorFormatter: DateFormatter = {
        let formatter = DateFormatter()
        formatter.calendar = Calendar(identifier: .gregorian)
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.timeZone = .current
        formatter.dateFormat = "yyyy-MM-dd"
        return formatter
    }()
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

public struct AuditEntry: Codable, Equatable, Sendable, Identifiable {
    public let id: Int
    public let ts: String
    public let category: String
    public let summary: String

    public init(id: Int, ts: String, category: String, summary: String) {
        self.id = id
        self.ts = ts
        self.category = category
        self.summary = summary
    }
}

public struct AuditResponse: Codable, Equatable, Sendable {
    public let entries: [AuditEntry]

    public init(entries: [AuditEntry]) {
        self.entries = entries
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
