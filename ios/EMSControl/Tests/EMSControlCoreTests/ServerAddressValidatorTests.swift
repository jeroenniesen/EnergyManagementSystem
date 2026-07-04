import Foundation
import XCTest
@testable import EMSControlCore

final class ServerAddressValidatorTests: XCTestCase {
    func testAcceptsLocalAndPrivateHosts() {
        let allowedHosts = [
            "http://localhost:8080",
            "http://ems",
            "http://ems-vpn",
            "http://home-ems",
            "http://127.0.0.1:8080",
            "http://192.168.1.10",
            "https://10.0.0.5",
            "https://172.16.4.20",
            "http://ems.local",
            "https://ems.internal",
            "http://ems-vpn"
        ]

        for host in allowedHosts {
            XCTAssertNoThrow(try ServerAddressValidator.validatedBaseURL(host), host)
        }
    }

    func testRejectsPublicInternetHosts() {
        let blockedHosts = [
            "http://example",
            "https://google",
            "https://example.com",
            "https://openai.com",
            "https://1.1.1.1",
            "https://8.8.8.8"
        ]

        for host in blockedHosts {
            XCTAssertThrowsError(try ServerAddressValidator.validatedBaseURL(host), host) { error in
                XCTAssertEqual(error as? ServerAddressValidationError, .publicHostNotAllowed)
            }
        }
    }
}
