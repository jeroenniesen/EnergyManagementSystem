import XCTest
@testable import EMSControlCore

@MainActor
final class ChatStoreTests: XCTestCase {
    func testLoadSessionUsesLiveClientWhenAvailable() async throws {
        let transport = ChatRecordingTransport(
            responses: [
                .json(
                    """
                    { "mode": "external_llm", "active": true, "language": "nl" }
                    """
                ),
                .json(
                    """
                    {
                      "ai_on": true,
                      "items": [
                        { "key": "plan", "question": "What is the plan?", "answer": "Charge before sunset." }
                      ]
                    }
                    """
                ),
            ]
        )
        let client = APIClient(
            baseURL: URL(string: "http://ems.local:8080")!,
            token: "secret",
            transport: transport
        )
        let store = ChatStore(client: nil, demoData: DemoDataStore(bundle: .module))

        await store.updateSession(client: client, mode: .live)

        XCTAssertEqual(store.explainerStatus, ExplainerStatus(mode: "external_llm", active: true, language: "nl"))
        XCTAssertEqual(store.faqItems.count, 1)
        XCTAssertFalse(store.isDemoMode)
        XCTAssertEqual(transport.requestedPaths, ["/api/explainer", "/api/faq"])
    }

    func testEmptyQuestionIsIgnored() async {
        let store = ChatStore(client: nil, demoData: DemoDataStore(bundle: .module))
        await store.send(question: "   ")
        XCTAssertTrue(store.messages.isEmpty)
    }

    func testDemoChatAddsQuestionAndAnswer() async throws {
        let store = ChatStore(client: nil, demoData: DemoDataStore(bundle: .module))
        await store.updateSession(client: nil, mode: .demo)
        await store.send(question: "What is the plan?")
        XCTAssertEqual(store.messages.count, 2)
        XCTAssertEqual(store.messages[0].role, .user)
        XCTAssertEqual(store.messages[1].role, .assistant)
    }

    func testSendQuestionIsIgnoredWhenExplainerInactive() async {
        let store = ChatStore(client: nil, demoData: DemoDataStore(bundle: .module))
        store.setExplainerStatusForTesting(ExplainerStatus(mode: "template", active: false, language: "en"))

        await store.send(question: "What is the plan?")

        XCTAssertTrue(store.messages.isEmpty)
        XCTAssertEqual(store.lastError, nil)
    }

    func testSessionChangeClearsMessagesAndFAQ() async throws {
        let firstTransport = ChatRecordingTransport(
            responses: [
                .json(#"{ "mode": "external_llm", "active": true, "language": "en" }"#),
                .json(#"{ "ai_on": true, "items": [{ "key": "faq-1", "question": "Q1", "answer": "A1" }] }"#),
                .json(#"{ "answer": "A1", "source": "faq" }"#),
            ]
        )
        let secondTransport = ChatRecordingTransport(
            responses: [
                .json(#"{ "mode": "template", "active": true, "language": "en" }"#),
                .json(#"{ "ai_on": true, "items": [{ "key": "faq-2", "question": "Q2", "answer": "A2" }] }"#),
            ]
        )
        let store = ChatStore(client: nil, demoData: DemoDataStore(bundle: .module))
        let firstClient = APIClient(baseURL: URL(string: "http://ems-a.local:8080")!, transport: firstTransport)
        let secondClient = APIClient(baseURL: URL(string: "http://ems-b.local:8080")!, transport: secondTransport)

        await store.updateSession(client: firstClient, mode: .live)
        await store.send(question: "First server question")
        XCTAssertEqual(store.messages.count, 2)
        XCTAssertEqual(store.faqItems.map(\.key), ["faq-1"])

        await store.updateSession(client: secondClient, mode: .live)

        XCTAssertTrue(store.messages.isEmpty)
        XCTAssertEqual(store.faqItems.map(\.key), ["faq-2"])
    }

    func testClearSessionRemovesMessagesAndFAQ() throws {
        let store = ChatStore(client: nil, demoData: DemoDataStore(bundle: .module))
        try store.useDemoFAQ()
        store.clearSession()
        XCTAssertTrue(store.messages.isEmpty)
        XCTAssertTrue(store.faqItems.isEmpty)
    }
}

private final class ChatRecordingTransport: HTTPTransport, @unchecked Sendable {
    enum Response {
        case json(String)
    }

    private(set) var requestedPaths: [String] = []
    private var responses: [Response]

    init(responses: [Response]) {
        self.responses = responses
    }

    func data(for request: URLRequest) async throws -> (Data, HTTPURLResponse) {
        requestedPaths.append(request.url?.path ?? "")
        guard !responses.isEmpty else {
            throw URLError(.badServerResponse)
        }
        let response = responses.removeFirst()
        let data: Data
        switch response {
        case .json(let body):
            data = Data(body.utf8)
        }
        return (data, HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!)
    }
}
