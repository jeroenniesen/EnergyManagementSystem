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

public enum SectionState: String, Codable, Equatable {
    case ok
    case stale
    case degraded
    case unavailable
}

public struct FlexibleSection: Codable, Equatable {
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

public struct DashboardSnapshot: Codable, Equatable {
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

public struct FAQItem: Codable, Equatable, Identifiable {
    public let key: String
    public let question: String
    public let answer: String
    public var id: String { key }
}

public struct FAQResponse: Codable, Equatable {
    public let aiOn: Bool
    public let items: [FAQItem]
}

public struct ChatRequest: Codable, Equatable {
    public let question: String
}

public struct ChatResponse: Codable, Equatable {
    public let answer: String
    public let source: String
}

public struct ExplainerStatus: Codable, Equatable {
    public let mode: String
    public let active: Bool
    public let language: String
}

public enum JSONValue: Codable, Equatable {
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

    public init(from decoder: Decoder) throws {
        let container = try decoder.singleValueContainer()
        if container.decodeNil() { self = .null }
        else if let value = try? container.decode(Bool.self) { self = .bool(value) }
        else if let value = try? container.decode(Double.self) { self = .number(value) }
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

public struct DynamicCodingKey: CodingKey {
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
