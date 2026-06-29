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
}
