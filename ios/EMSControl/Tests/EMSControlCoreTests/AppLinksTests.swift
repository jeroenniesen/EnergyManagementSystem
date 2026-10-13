import XCTest
@testable import EMSControlCore

final class AppLinksTests: XCTestCase {
    func testDefaultsAreHTTPSWithHost() {
        XCTAssertEqual(AppLinks.defaultPrivacyPolicyURL.scheme, "https")
        XCTAssertNotNil(AppLinks.defaultPrivacyPolicyURL.host)
        XCTAssertEqual(AppLinks.defaultSupportURL.scheme, "https")
        XCTAssertNotNil(AppLinks.defaultSupportURL.host)
    }

    func testInfoPlistOverrideWins() {
        let info: [String: Any] = [
            AppLinks.privacyPolicyInfoKey: "https://example.com/privacy",
            AppLinks.supportInfoKey: "https://example.com/support",
        ]
        XCTAssertEqual(
            AppLinks.url(
                forInfoKey: AppLinks.privacyPolicyInfoKey,
                fallback: AppLinks.defaultPrivacyPolicyURL,
                info: info
            ).absoluteString,
            "https://example.com/privacy"
        )
        XCTAssertEqual(
            AppLinks.url(
                forInfoKey: AppLinks.supportInfoKey,
                fallback: AppLinks.defaultSupportURL,
                info: info
            ).absoluteString,
            "https://example.com/support"
        )
    }

    func testInvalidOverrideFallsBack() {
        XCTAssertEqual(
            AppLinks.url(
                forInfoKey: AppLinks.privacyPolicyInfoKey,
                fallback: AppLinks.defaultPrivacyPolicyURL,
                info: [AppLinks.privacyPolicyInfoKey: "file:///etc/passwd"]
            ),
            AppLinks.defaultPrivacyPolicyURL
        )
        XCTAssertEqual(
            AppLinks.url(
                forInfoKey: AppLinks.supportInfoKey,
                fallback: AppLinks.defaultSupportURL,
                info: [AppLinks.supportInfoKey: "not a url"]
            ),
            AppLinks.defaultSupportURL
        )
    }

    func testEmptyOverrideFallsBack() {
        XCTAssertEqual(
            AppLinks.url(
                forInfoKey: AppLinks.privacyPolicyInfoKey,
                fallback: AppLinks.defaultPrivacyPolicyURL,
                info: [AppLinks.privacyPolicyInfoKey: "   "]
            ),
            AppLinks.defaultPrivacyPolicyURL
        )
        XCTAssertEqual(
            AppLinks.url(
                forInfoKey: AppLinks.supportInfoKey,
                fallback: AppLinks.defaultSupportURL,
                info: [:]
            ),
            AppLinks.defaultSupportURL
        )
    }
}
