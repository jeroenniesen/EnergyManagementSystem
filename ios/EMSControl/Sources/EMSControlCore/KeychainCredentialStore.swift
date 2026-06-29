import Foundation
import Security

public enum KeychainCredentialError: Error, Equatable {
    case unexpectedStatus(OSStatus)
}

public protocol CredentialStore {
    func saveToken(_ token: String, for baseURL: URL) throws
    func token(for baseURL: URL) throws -> String?
    func deleteToken(for baseURL: URL) throws
}

public struct KeychainCredentialStore: CredentialStore {
    private let service: String

    public init(service: String = "com.jeroenniesen.emscontrol.web-token") {
        self.service = service
    }

    public func saveToken(_ token: String, for baseURL: URL) throws {
        let account = accountName(for: baseURL)
        try deleteToken(for: baseURL)

        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account,
            kSecAttrAccessible as String: kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly,
            kSecValueData as String: Data(token.utf8)
        ]

        let status = SecItemAdd(query as CFDictionary, nil)
        guard status == errSecSuccess else {
            throw KeychainCredentialError.unexpectedStatus(status)
        }
    }

    public func token(for baseURL: URL) throws -> String? {
        var query = baseQuery(for: baseURL)
        query[kSecReturnData as String] = true
        query[kSecMatchLimit as String] = kSecMatchLimitOne

        var result: CFTypeRef?
        let status = SecItemCopyMatching(query as CFDictionary, &result)
        if status == errSecItemNotFound { return nil }
        guard status == errSecSuccess else {
            throw KeychainCredentialError.unexpectedStatus(status)
        }
        guard let data = result as? Data else { return nil }
        return String(data: data, encoding: .utf8)
    }

    public func deleteToken(for baseURL: URL) throws {
        let status = SecItemDelete(baseQuery(for: baseURL) as CFDictionary)
        guard status == errSecSuccess || status == errSecItemNotFound else {
            throw KeychainCredentialError.unexpectedStatus(status)
        }
    }

    private func baseQuery(for baseURL: URL) -> [String: Any] {
        [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: accountName(for: baseURL)
        ]
    }

    private func accountName(for baseURL: URL) -> String {
        "\(baseURL.scheme ?? "http")://\(baseURL.host ?? ""):\(baseURL.port ?? defaultPort(for: baseURL))"
    }

    private func defaultPort(for baseURL: URL) -> Int {
        baseURL.scheme == "https" ? 443 : 80
    }
}
