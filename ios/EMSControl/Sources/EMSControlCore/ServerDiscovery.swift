import Foundation

public struct PairingPayload: Equatable {
    public let baseURL: URL
    public let serverLabel: String?

    public init(baseURL: URL, serverLabel: String?) {
        self.baseURL = baseURL
        self.serverLabel = serverLabel
    }
}

public enum ServerDiscoveryError: Error, Equatable {
    case invalidPayload
    case invalidURL
    case tokenNotAllowed
}

extension ServerDiscoveryError: LocalizedError {
    public var errorDescription: String? {
        switch self {
        case .invalidPayload:
            "Use a valid EMS pairing payload."
        case .invalidURL:
            "Use a valid EMS server URL."
        case .tokenNotAllowed:
            "Pairing payloads cannot include tokens."
        }
    }
}

public struct ServerDiscovery {
    public init() {}

    public func normalizedManualURL(_ input: String) throws -> URL {
        let trimmed = input.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else {
            throw ServerDiscoveryError.invalidURL
        }

        guard let components = URLComponents(string: trimmed) else {
            throw ServerDiscoveryError.invalidURL
        }

        try rejectEmbeddedToken(in: components)

        guard let validatedURL = try? ServerAddressValidator.validatedBaseURL(trimmed),
              var normalizedComponents = URLComponents(url: validatedURL, resolvingAgainstBaseURL: false)
        else {
            throw ServerDiscoveryError.invalidURL
        }

        if normalizedComponents.path == "/" {
            normalizedComponents.path = ""
        }

        if normalizedComponents.percentEncodedQuery?.isEmpty == true {
            normalizedComponents.percentEncodedQuery = nil
        }

        guard let url = normalizedComponents.url else {
            throw ServerDiscoveryError.invalidURL
        }

        return url
    }

    public func parsePairingPayload(_ raw: String) throws -> PairingPayload {
        struct RawPayload: Decodable {
            let baseURL: String
            let serverLabel: String?
            let token: String?
            let accessToken: String?
            let apiKey: String?

            enum CodingKeys: String, CodingKey {
                case baseURL = "base_url"
                case serverLabel = "server_label"
                case token
                case accessToken = "access_token"
                case apiKey = "api_key"
            }
        }

        let data = Data(raw.utf8)
        let payload: RawPayload
        do {
            payload = try JSONDecoder().decode(RawPayload.self, from: data)
        } catch {
            throw ServerDiscoveryError.invalidPayload
        }

        guard payload.token == nil, payload.accessToken == nil, payload.apiKey == nil else {
            throw ServerDiscoveryError.tokenNotAllowed
        }

        return PairingPayload(
            baseURL: try normalizedManualURL(payload.baseURL),
            serverLabel: payload.serverLabel?.trimmingCharacters(in: .whitespacesAndNewlines).nilIfEmpty
        )
    }

    private func rejectEmbeddedToken(in components: URLComponents) throws {
        if components.user != nil || components.password != nil {
            throw ServerDiscoveryError.tokenNotAllowed
        }

        let forbiddenQueryNames: Set<String> = [
            "access_token",
            "api_key",
            "apikey",
            "token"
        ]

        let queryItems = components.queryItems ?? []
        if queryItems.contains(where: { forbiddenQueryNames.contains($0.name.lowercased()) }) {
            throw ServerDiscoveryError.tokenNotAllowed
        }
    }
}

private extension String {
    var nilIfEmpty: String? {
        isEmpty ? nil : self
    }
}
