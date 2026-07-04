import Foundation

public protocol HTTPTransport: Sendable {
    func data(for request: URLRequest) async throws -> (Data, HTTPURLResponse)
}

public struct URLSessionTransport: HTTPTransport, Sendable {
    public init() {}

    public func data(for request: URLRequest) async throws -> (Data, HTTPURLResponse) {
        let (data, response) = try await URLSession.shared.data(for: request)
        guard let http = response as? HTTPURLResponse else {
            throw APIClientError.invalidResponse
        }
        return (data, http)
    }
}

public enum APIClientError: Error, Equatable {
    case invalidResponse
    case httpStatus(Int)
    case incompatibleServer(Int)
}

public struct APIClient: Sendable {
    public let baseURL: URL
    public let token: String?
    private let transport: any HTTPTransport

    public init(baseURL: URL, token: String? = nil, transport: HTTPTransport = URLSessionTransport()) {
        self.baseURL = baseURL
        self.token = token
        self.transport = transport
    }

    public func fetchDashboard() async throws -> MobileDashboardSnapshot {
        async let statusResult = capture { try await fetchStatus() }
        async let freshnessResult = capture { try await fetchFreshness() }
        async let decisionResult = capture { try await fetchDecision() }
        async let alertsResult = capture { try await fetchAlerts() }
        async let batteryResult = capture { try await fetchBattery() }
        async let chargeNeedResult = capture { try await fetchChargeNeed() }
        async let savingsResult = capture { try await fetchSavings() }
        async let storyResult = capture { try await fetchEnergyStory() }
        async let reportResult = capture { try await fetchReport() }
        async let financeResult = capture { try await fetchFinance() }

        return try await MobileDashboardSnapshot(
            generatedAt: Date(),
            serverName: baseURL.host(percentEncoded: false) ?? baseURL.host() ?? "EMS",
            cacheTTLSeconds: 10,
            status: statusResult.get(),
            freshness: freshnessResult.optional ?? .empty,
            decision: decisionResult.optional ?? .empty,
            alerts: alertsResult.optional ?? .empty,
            battery: batteryResult.optional ?? .empty,
            chargeNeed: chargeNeedResult.optional ?? .empty,
            savings: savingsResult.optional ?? .empty,
            energyStory: storyResult.optional ?? .empty,
            report: reportResult.optional ?? .empty,
            finance: financeResult.optional ?? .empty
        )
    }

    public func fetchStatus() async throws -> StatusSnapshot {
        try await get("api/status", as: StatusSnapshot.self)
    }

    public func fetchFreshness() async throws -> FreshnessSnapshot {
        try await get("api/freshness", as: FreshnessSnapshot.self)
    }

    public func fetchDecision() async throws -> DecisionSnapshot {
        try await get("api/decision", as: DecisionSnapshot.self)
    }

    public func fetchAlerts() async throws -> AlertsSnapshot {
        try await get("api/alerts", as: AlertsSnapshot.self)
    }

    public func fetchBattery() async throws -> BatterySnapshot {
        try await get("api/battery", as: BatterySnapshot.self)
    }

    public func fetchChargeNeed() async throws -> ChargeNeedSnapshot {
        try await get("api/charge-need", as: ChargeNeedSnapshot.self)
    }

    public func fetchSavings() async throws -> SavingsSnapshot {
        try await get("api/savings", as: SavingsSnapshot.self)
    }

    public func fetchEnergyStory(window: String = "next") async throws -> EnergyStorySnapshot {
        try await get("api/energy-story?window=\(window)", as: EnergyStorySnapshot.self)
    }

    public func fetchReport(period: String = "day") async throws -> ReportSnapshot {
        try await get("api/report?period=\(period)", as: ReportSnapshot.self)
    }

    public func fetchFinance(period: String = "day") async throws -> FinanceSnapshot {
        try await get("api/finance?period=\(period)", as: FinanceSnapshot.self)
    }

    public func fetchLiveHealth() async throws -> HealthStatus {
        try await get("health/live", as: HealthStatus.self)
    }

    public func fetchReadyHealth() async throws -> HealthStatus {
        try await get("health/ready", as: HealthStatus.self)
    }

    public func fetchAuthStatus() async throws -> AuthStatus {
        try await get("api/auth", as: AuthStatus.self)
    }

    public func fetchExplainer() async throws -> ExplainerStatus {
        try await get("api/explainer", as: ExplainerStatus.self)
    }

    public func fetchFAQ() async throws -> FAQResponse {
        try await get("api/faq", as: FAQResponse.self)
    }

    public func sendChat(question: String) async throws -> ChatResponse {
        var request = URLRequest(url: baseURL.appending(path: "api/chat"))
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        if let token, !token.isEmpty {
            request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        }
        request.httpBody = try JSONEncoder.ems.encode(ChatRequest(question: question))
        let (data, response) = try await transport.data(for: request)
        guard (200 ..< 300).contains(response.statusCode) else {
            throw APIClientError.httpStatus(response.statusCode)
        }
        return try JSONDecoder.ems.decode(ChatResponse.self, from: data)
    }

    private func get<T: Decodable>(_ path: String, as type: T.Type) async throws -> T {
        var request = URLRequest(url: url(path))
        request.httpMethod = "GET"
        if let token, !token.isEmpty {
            request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        }
        let (data, response) = try await transport.data(for: request)
        guard (200 ..< 300).contains(response.statusCode) else {
            throw APIClientError.httpStatus(response.statusCode)
        }
        return try JSONDecoder.ems.decode(type, from: data)
    }

    private func url(_ path: String) -> URL {
        guard let question = path.firstIndex(of: "?") else {
            return baseURL.appending(path: path)
        }
        let endpoint = String(path[..<question])
        var components = URLComponents(url: baseURL.appending(path: endpoint), resolvingAgainstBaseURL: false)!
        components.percentEncodedQuery = String(path[path.index(after: question)...])
        return components.url!
    }

    private func capture<T: Sendable>(_ operation: @Sendable () async throws -> T) async -> Result<T, Error> {
        do {
            return .success(try await operation())
        } catch {
            return .failure(error)
        }
    }
}

private extension Result {
    var optional: Success? {
        if case let .success(value) = self { return value }
        return nil
    }
}
