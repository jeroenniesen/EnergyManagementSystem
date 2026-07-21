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

extension APIClientError: LocalizedError {
    public var errorDescription: String? {
        switch self {
        case .invalidResponse:
            "The server sent a response the app couldn't read."
        case let .httpStatus(code):
            switch code {
            case 401, 403:
                "Access denied (HTTP \(code)) — check your access token."
            default:
                "The server returned an error (HTTP \(code))."
            }
        case let .incompatibleServer(version):
            "This app isn't compatible with the EMS server (API v\(version)). Update the app or the server."
        }
    }
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
        async let batteryPlanResult = capture { try await fetchBatteryPlan() }
        async let reportResult = capture { try await fetchReport() }
        async let financeResult = capture { try await fetchFinance() }
        async let strategyResult = capture { try await fetchStrategy() }
        async let carPlanResult = capture { try await fetchCarPlan() }

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
            batteryPlan: batteryPlanResult.optional ?? .empty,
            report: reportResult.optional ?? .empty,
            finance: financeResult.optional ?? .empty,
            strategy: strategyResult.optional,
            carPlan: carPlanResult.optional ?? .empty
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

    public func fetchStrategy() async throws -> StrategySnapshot {
        try await get("api/strategy", as: StrategySnapshot.self)
    }

    public func fetchCarPlan() async throws -> CarPlanSnapshot {
        try await get("api/car/plan", as: CarPlanSnapshot.self)
    }

    public func fetchEnergyStory(window: String = "next") async throws -> EnergyStorySnapshot {
        try await get("api/energy-story?window=\(window)", as: EnergyStorySnapshot.self)
    }

    public func fetchBatteryPlan() async throws -> BatteryPlanSnapshot {
        try await get("api/battery-plan", as: BatteryPlanSnapshot.self)
    }

    public func fetchReport(period: InsightsPeriod = .day, anchor: String? = nil) async throws -> ReportSnapshot {
        try await get(insightsPath("api/report", period: period, anchor: anchor), as: ReportSnapshot.self)
    }

    public func fetchFinance(period: InsightsPeriod = .day, anchor: String? = nil) async throws -> FinanceSnapshot {
        try await get(insightsPath("api/finance", period: period, anchor: anchor), as: FinanceSnapshot.self)
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

    // Username/password login (POST /api/auth/login). This endpoint is auth-exempt, so the request
    // carries NO Authorization header — a leftover/expired token must not interfere. A 401 (generic
    // "invalid credentials") surfaces as APIClientError.httpStatus(401); a transport failure throws
    // the underlying URLError so the caller can tell "wrong password" from "server unreachable".
    public func login(username: String, password: String) async throws -> LoginResponse {
        var request = URLRequest(url: baseURL.appending(path: "api/auth/login"))
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try JSONEncoder.ems.encode(LoginRequest(username: username, password: password))
        let (data, response) = try await transport.data(for: request)
        guard (200 ..< 300).contains(response.statusCode) else {
            throw APIClientError.httpStatus(response.statusCode)
        }
        return try JSONDecoder.ems.decode(LoginResponse.self, from: data)
    }

    // Mint (or, with replace:true, atomically revoke-and-remint by name) the dedicated per-device
    // ACCESS token the home-screen widget rides (POST /api/auth/tokens). MUST be called with a
    // SESSION bearer (this APIClient's token). The raw value is returned exactly once — persist it
    // immediately; a lost copy is recovered by re-minting on the next login (that is why the widget
    // token is provisioned with replace:true). See spec §7.
    public func provisionWidgetToken(name: String) async throws -> TokenProvisionResponse {
        var request = URLRequest(url: baseURL.appending(path: "api/auth/tokens"))
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        if let token, !token.isEmpty {
            request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        }
        request.httpBody = try JSONEncoder.ems.encode(TokenProvisionRequest(name: name, replace: true))
        let (data, response) = try await transport.data(for: request)
        guard (200 ..< 300).contains(response.statusCode) else {
            throw APIClientError.httpStatus(response.statusCode)
        }
        return try JSONDecoder.ems.decode(TokenProvisionResponse.self, from: data)
    }

    public func fetchFAQ() async throws -> FAQResponse {
        try await get("api/faq", as: FAQResponse.self)
    }

    public func fetchAudit(limit: Int = 100) async throws -> [AuditEntry] {
        try await get("api/audit?limit=\(limit)", as: AuditResponse.self).entries
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

    // Set the manual car-SoC anchor. POSTs {"pct": n} exactly like sendChat and returns the fresh
    // SoC estimate the server echoes back ({"soc": ...}); auth (401) surfaces as APIClientError.
    public func setCarSoc(pct: Int) async throws -> CarSoc? {
        var request = URLRequest(url: baseURL.appending(path: "api/car/soc"))
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        if let token, !token.isEmpty {
            request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        }
        request.httpBody = try JSONEncoder.ems.encode(CarSocRequest(pct: pct))
        let (data, response) = try await transport.data(for: request)
        guard (200 ..< 300).contains(response.statusCode) else {
            throw APIClientError.httpStatus(response.statusCode)
        }
        return try JSONDecoder.ems.decode(CarSocResponse.self, from: data).soc
    }

    // Detected EV charging sessions (GET /api/car/sessions), newest-first, for the Car tab's
    // history table. Read-only: the server returns an empty list (never an error) when there is no
    // history or no charging in the window.
    public func fetchCarSessions(days: Int = 14) async throws -> CarSessionsResponse {
        try await get("api/car/sessions?days=\(days)", as: CarSessionsResponse.self)
    }

    // Static car database (GET /api/cars) for the picker — read-only and cacheable.
    public func fetchCars() async throws -> CarsResponse {
        try await get("api/cars", as: CarsResponse.self)
    }

    // The notification-outbox feed (GET /api/notifications, B-20 — the web header bell): most
    // recent notifications newest-first plus the unread count. Read-only.
    public func fetchNotifications(limit: Int = 50) async throws -> NotificationsResponse {
        try await get("api/notifications?limit=\(limit)", as: NotificationsResponse.self)
    }

    // Mark outbox notifications read (POST /api/notifications/read — the web bell's "Mark all
    // read"). `ids == nil` marks everything unread ({"all": true}); otherwise exactly those ids.
    // Returns the server's fresh unread count. Follows the setCarSoc POST pattern; auth (401)
    // surfaces as APIClientError like every other write.
    public func markNotificationsRead(ids: [Int]? = nil) async throws -> Int {
        var request = URLRequest(url: baseURL.appending(path: "api/notifications/read"))
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        if let token, !token.isEmpty {
            request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        }
        // JSONSerialization keeps the ids as plain integers (a JSONValue round-trip via Double
        // could re-encode them as floats), matching the endpoint's `[int(i) for i in raw_ids]`.
        let payload: [String: Any] = ids.map { ["ids": $0] } ?? ["all": true]
        request.httpBody = try JSONSerialization.data(withJSONObject: payload)
        let (data, response) = try await transport.data(for: request)
        guard (200 ..< 300).contains(response.statusCode) else {
            throw APIClientError.httpStatus(response.statusCode)
        }
        return try JSONDecoder.ems.decode(UnreadResponse.self, from: data).unread
    }

    // The weekly digest (GET /api/digest, B-58 "the Sunday read"). `week` (YYYY-MM-DD, any date
    // inside the desired week) anchors a specific week; nil = the server's default, the last
    // COMPLETED Mon-Sun week. Read-only.
    public func fetchDigest(week: String? = nil) async throws -> WeekDigest {
        if let week, !week.isEmpty {
            return try await get("api/digest?week=\(week)", as: WeekDigest.self)
        }
        return try await get("api/digest", as: WeekDigest.self)
    }

    // Effective settings (GET /api/settings) — the `values` map only (the Car tab renders fixed
    // native controls, not the schema-driven form). Decoded with a PLAIN decoder (not `.ems`) so
    // the dotted, snake_case setting keys ("control.car_charging_battery_mode", "ev.schedule") are
    // preserved verbatim — `.convertFromSnakeCase` would mangle them into camelCase and break
    // key lookups. Auth (401) surfaces as APIClientError like every other request.
    public func fetchSettings() async throws -> [String: JSONValue] {
        var request = URLRequest(url: url("api/settings"))
        request.httpMethod = "GET"
        if let token, !token.isEmpty {
            request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        }
        let (data, response) = try await transport.data(for: request)
        guard (200 ..< 300).contains(response.statusCode) else {
            throw APIClientError.httpStatus(response.statusCode)
        }
        return try JSONDecoder().decode(SettingsEnvelope.self, from: data).values
    }

    // Save only the changed setting keys (POST /api/settings) — the app's first settings write,
    // following setCarSoc's POST pattern (bearer token rides automatically; audit-logged
    // server-side). Encoded with a PLAIN encoder so the literal keys are sent verbatim (no
    // snake-case conversion). Non-2xx (401 unauthorised, 422 rejected) surfaces as APIClientError.
    public func postSettings(_ changes: [String: JSONValue]) async throws {
        var request = URLRequest(url: baseURL.appending(path: "api/settings"))
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        if let token, !token.isEmpty {
            request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        }
        request.httpBody = try JSONEncoder().encode(changes)
        let (_, response) = try await transport.data(for: request)
        guard (200 ..< 300).contains(response.statusCode) else {
            throw APIClientError.httpStatus(response.statusCode)
        }
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

    private func insightsPath(_ endpoint: String, period: InsightsPeriod, anchor: String?) -> String {
        var query = "period=\(period.rawValue)"
        if let anchor, !anchor.isEmpty {
            query += "&date=\(anchor)"
        }
        return "\(endpoint)?\(query)"
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

private struct LoginRequest: Encodable {
    let username: String
    let password: String
}

private struct TokenProvisionRequest: Encodable {
    let name: String
    let replace: Bool
}

private struct CarSocRequest: Encodable {
    let pct: Int
}

private struct CarSocResponse: Decodable {
    let soc: CarSoc?
}

// POST /api/notifications/read echoes the fresh unread count back ({"unread": n}).
private struct UnreadResponse: Decodable {
    let unread: Int
}

// GET /api/settings returns {schema, values}; the Car tab only needs `values`. Decoded with a
// plain decoder so the dictionary keys stay verbatim (see fetchSettings).
private struct SettingsEnvelope: Decodable {
    let values: [String: JSONValue]
}
