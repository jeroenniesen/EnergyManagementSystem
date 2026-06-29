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

    public func fetchDashboard() async throws -> DashboardSnapshot {
        var request = URLRequest(url: baseURL.appending(path: "api/dashboard"))
        request.httpMethod = "GET"
        if let token, !token.isEmpty {
            request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        }

        let (data, response) = try await transport.data(for: request)
        guard (200 ..< 300).contains(response.statusCode) else {
            throw APIClientError.httpStatus(response.statusCode)
        }

        let snapshot = try JSONDecoder.ems.decode(DashboardSnapshot.self, from: data)
        guard snapshot.apiVersion <= 1 else {
            throw APIClientError.incompatibleServer(snapshot.apiVersion)
        }
        return snapshot
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
        var request = URLRequest(url: baseURL.appending(path: path))
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
}
