import Foundation
import XCTest
@testable import EMSControlCore

final class APIClientTests: XCTestCase {
    func testAuthorizationHeaderUsesBearerToken() async throws {
        let transport = RecordingTransport(data: dashboardJSON(apiVersion: 1))
        let client = APIClient(baseURL: URL(string: "http://ems.local:8080")!, token: "abc123", transport: transport)

        _ = try await client.fetchDashboard()

        XCTAssertEqual(transport.lastRequest?.value(forHTTPHeaderField: "Authorization"), "Bearer abc123")
    }

    func testRejectsFutureDashboardAPIVersion() async {
        let client = APIClient(baseURL: URL(string: "http://ems.local:8080")!, transport: RecordingTransport(data: dashboardJSON(apiVersion: 99)))

        do {
            _ = try await client.fetchDashboard()
            XCTFail("expected incompatible server")
        } catch APIClientError.incompatibleServer(let version) {
            XCTAssertEqual(version, 99)
        } catch {
            XCTFail("unexpected error: \(error)")
        }
    }

    func testFetchFAQUsesExpectedPathAndAuthorizationHeader() async throws {
        let transport = RecordingTransport(data: faqJSON())
        let client = APIClient(baseURL: URL(string: "http://ems.local:8080")!, token: "abc123", transport: transport)

        let response = try await client.fetchFAQ()

        XCTAssertEqual(response.items.count, 1)
        XCTAssertEqual(transport.lastRequest?.url?.path, "/api/faq")
        XCTAssertEqual(transport.lastRequest?.httpMethod, "GET")
        XCTAssertEqual(transport.lastRequest?.value(forHTTPHeaderField: "Authorization"), "Bearer abc123")
    }

    func testFetchExplainerUsesExpectedPathAndAuthorizationHeader() async throws {
        let transport = RecordingTransport(data: explainerJSON())
        let client = APIClient(baseURL: URL(string: "http://ems.local:8080")!, token: "abc123", transport: transport)

        let response = try await client.fetchExplainer()

        XCTAssertEqual(response, ExplainerStatus(mode: "external_llm", active: true, language: "nl"))
        XCTAssertEqual(transport.lastRequest?.url?.path, "/api/explainer")
        XCTAssertEqual(transport.lastRequest?.httpMethod, "GET")
        XCTAssertEqual(transport.lastRequest?.value(forHTTPHeaderField: "Authorization"), "Bearer abc123")
    }

    func testSendChatUsesJSONBodyAndAuthorizationHeader() async throws {
        let transport = RecordingTransport(data: chatJSON())
        let client = APIClient(baseURL: URL(string: "http://ems.local:8080")!, token: "abc123", transport: transport)

        let response = try await client.sendChat(question: "Why is the battery charging?")

        XCTAssertEqual(response.answer, "Because prices are low.")
        XCTAssertEqual(transport.lastRequest?.url?.path, "/api/chat")
        XCTAssertEqual(transport.lastRequest?.httpMethod, "POST")
        XCTAssertEqual(transport.lastRequest?.value(forHTTPHeaderField: "Authorization"), "Bearer abc123")
        XCTAssertEqual(transport.lastRequest?.value(forHTTPHeaderField: "Content-Type"), "application/json")
        XCTAssertEqual(
            transport.lastRequest?.httpBody,
            try JSONEncoder.ems.encode(ChatRequest(question: "Why is the battery charging?"))
        )
    }
}

private final class RecordingTransport: HTTPTransport, @unchecked Sendable {
    var lastRequest: URLRequest?
    let data: Data

    init(data: Data) { self.data = data }

    func data(for request: URLRequest) async throws -> (Data, HTTPURLResponse) {
        lastRequest = request
        return (data, HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!)
    }
}

private func dashboardJSON(apiVersion: Int) -> Data {
    """
    {
      "api_version": \(apiVersion),
      "generated_at": "2026-06-29T12:00:00+00:00",
      "server_time": "2026-06-29T12:00:00+00:00",
      "server_name": "Home EMS",
      "cache_ttl_seconds": 10,
      "degraded_sections": [],
      "readiness": {},
      "status": {},
      "freshness": {},
      "strategy": {},
      "decision": {},
      "alerts": {},
      "battery": {},
      "charge_need": {},
      "savings": {},
      "energy_story": {},
      "ai_validation": {}
    }
    """.data(using: .utf8)!
}

private func faqJSON() -> Data {
    """
    {
      "ai_on": true,
      "items": [
        { "key": "plan", "question": "What is the plan?", "answer": "Charge before sunset." }
      ]
    }
    """.data(using: .utf8)!
}

private func explainerJSON() -> Data {
    """
    {
      "mode": "external_llm",
      "active": true,
      "language": "nl"
    }
    """.data(using: .utf8)!
}

private func chatJSON() -> Data {
    """
    {
      "answer": "Because prices are low.",
      "source": "faq"
    }
    """.data(using: .utf8)!
}
