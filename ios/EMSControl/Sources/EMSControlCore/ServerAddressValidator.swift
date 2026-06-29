import Foundation

public enum ServerAddressValidationError: Error, Equatable {
    case invalidURL
    case unsupportedScheme
    case missingHost
    case publicHostNotAllowed
}

extension ServerAddressValidationError: LocalizedError {
    public var errorDescription: String? {
        switch self {
        case .invalidURL:
            "Enter a valid server URL."
        case .unsupportedScheme:
            "Use an http:// or https:// server URL."
        case .missingHost:
            "Enter a server host name or IP address."
        case .publicHostNotAllowed:
            "Use a local, private-network, or VPN-style EMS host for this iteration."
        }
    }
}

public enum ServerAddressValidator {
    public static func validatedBaseURL(_ rawValue: String) throws -> URL {
        let trimmed = rawValue.trimmingCharacters(in: .whitespacesAndNewlines)
        guard let components = URLComponents(string: trimmed), let scheme = components.scheme?.lowercased() else {
            throw ServerAddressValidationError.invalidURL
        }
        guard scheme == "http" || scheme == "https" else {
            throw ServerAddressValidationError.unsupportedScheme
        }
        guard let host = components.host, !host.isEmpty else {
            throw ServerAddressValidationError.missingHost
        }
        guard isAllowedHost(host) else {
            throw ServerAddressValidationError.publicHostNotAllowed
        }
        guard let url = components.url else {
            throw ServerAddressValidationError.invalidURL
        }
        return url
    }

    public static func isAllowedHost(_ host: String) -> Bool {
        let normalizedHost = host.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        guard !normalizedHost.isEmpty else { return false }

        if normalizedHost == "localhost" || normalizedHost == "::1" {
            return true
        }
        if let octets = ipv4Octets(normalizedHost) {
            return isLoopbackIPv4(octets) || isRFC1918IPv4(octets)
        }
        if normalizedHost.hasSuffix(".local") {
            return true
        }
        if !normalizedHost.contains(".") {
            return true
        }

        let labels = normalizedHost.split(separator: ".").map(String.init)
        guard let suffix = labels.last else { return false }
        return privateHostSuffixes.contains(suffix)
    }

    private static let privateHostSuffixes: Set<String> = [
        "corp",
        "home",
        "internal",
        "intra",
        "lan",
        "localdomain",
        "private",
        "vpn"
    ]

    private static func ipv4Octets(_ host: String) -> [Int]? {
        let parts = host.split(separator: ".", omittingEmptySubsequences: false)
        guard parts.count == 4 else { return nil }

        let octets = parts.compactMap { Int($0) }
        guard octets.count == 4, octets.allSatisfy({ 0 ... 255 ~= $0 }) else {
            return nil
        }
        return octets
    }

    private static func isLoopbackIPv4(_ octets: [Int]) -> Bool {
        octets[0] == 127
    }

    private static func isRFC1918IPv4(_ octets: [Int]) -> Bool {
        switch (octets[0], octets[1]) {
        case (10, _):
            true
        case (172, 16 ... 31):
            true
        case (192, 168):
            true
        default:
            false
        }
    }
}
