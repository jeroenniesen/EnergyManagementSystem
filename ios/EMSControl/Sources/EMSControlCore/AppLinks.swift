import Foundation

/// Configurable privacy / support URLs for App Review links (#257).
///
/// Defaults ship in Info.plist (`EMSPrivacyPolicyURL`, `EMSSupportURL`) and can be overridden
/// per build without code changes. Fallbacks keep the app usable when keys are missing.
public enum AppLinks {
    public static let privacyPolicyInfoKey = "EMSPrivacyPolicyURL"
    public static let supportInfoKey = "EMSSupportURL"

    /// Public privacy-policy page (hosted from `docs/privacy-policy.md` / #258).
    public static let defaultPrivacyPolicyURL = URL(
        string: "https://github.com/jeroenniesen/EnergyManagementSystem/blob/main/docs/privacy-policy.md"
    )!

    /// Where homeowners can ask for help (GitHub Issues for this open-source app).
    public static let defaultSupportURL = URL(
        string: "https://github.com/jeroenniesen/EnergyManagementSystem/issues/new/choose"
    )!

    public static func privacyPolicyURL(bundle: Bundle = .main) -> URL {
        url(forInfoKey: privacyPolicyInfoKey, fallback: defaultPrivacyPolicyURL, info: bundle.infoDictionary)
    }

    public static func supportURL(bundle: Bundle = .main) -> URL {
        url(forInfoKey: supportInfoKey, fallback: defaultSupportURL, info: bundle.infoDictionary)
    }

    /// Resolve a http(s) URL from an Info.plist dictionary; ignore empty / non-http schemes.
    public static func url(
        forInfoKey key: String,
        fallback: URL,
        info: [String: Any]?
    ) -> URL {
        guard let raw = info?[key] as? String else {
            return fallback
        }
        let trimmed = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty,
              let parsed = URL(string: trimmed),
              let scheme = parsed.scheme?.lowercased(),
              scheme == "http" || scheme == "https",
              parsed.host != nil
        else {
            return fallback
        }
        return parsed
    }
}
