import XCTest
@testable import EMSControlCore

final class ServerDiscoveryTests: XCTestCase {
    func testManualURLNormalizesTrailingSlash() throws {
        let discovery = ServerDiscovery()

        let url = try discovery.normalizedManualURL("http://ems.local:8080/")

        XCTAssertEqual(url.absoluteString, "http://ems.local:8080")
    }

    func testManualURLRejectsEmbeddedCredentials() {
        XCTAssertThrowsError(
            try ServerDiscovery().normalizedManualURL("http://user:pass@ems.local:8080")
        ) { error in
            XCTAssertEqual(error as? ServerDiscoveryError, .tokenNotAllowed)
        }
    }

    func testManualURLRejectsTokenQuery() {
        XCTAssertThrowsError(
            try ServerDiscovery().normalizedManualURL("http://ems.local:8080?token=secret")
        ) { error in
            XCTAssertEqual(error as? ServerDiscoveryError, .tokenNotAllowed)
        }
    }

    func testQRPayloadParsesURLAndLabelWithoutToken() throws {
        let payload = try ServerDiscovery().parsePairingPayload(
            #"{"base_url":"http://ems.local:8080","server_label":"Home EMS"}"#
        )

        XCTAssertEqual(payload.baseURL.absoluteString, "http://ems.local:8080")
        XCTAssertEqual(payload.serverLabel, "Home EMS")
    }

    func testQRPayloadRejectsEmbeddedTokenField() {
        XCTAssertThrowsError(
            try ServerDiscovery().parsePairingPayload(#"{"base_url":"http://ems.local:8080","token":"secret"}"#)
        ) { error in
            XCTAssertEqual(error as? ServerDiscoveryError, .tokenNotAllowed)
        }
    }

    func testQRPayloadRejectsTokenInURLQuery() {
        XCTAssertThrowsError(
            try ServerDiscovery().parsePairingPayload(
                #"{"base_url":"http://ems.local:8080?token=secret","server_label":"Home EMS"}"#
            )
        ) { error in
            XCTAssertEqual(error as? ServerDiscoveryError, .tokenNotAllowed)
        }
    }

    func testQRPayloadRejectsUnexpectedFields() {
        XCTAssertThrowsError(
            try ServerDiscovery().parsePairingPayload(
                #"{"base_url":"http://ems.local:8080","server_label":"Home EMS","foo":"bar"}"#
            )
        ) { error in
            XCTAssertEqual(error as? ServerDiscoveryError, .invalidPayload)
        }
    }
}
