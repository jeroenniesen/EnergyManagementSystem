import XCTest
@testable import EMSControlCore

@MainActor
final class ChatStoreTests: XCTestCase {
    func testEmptyQuestionIsIgnored() async {
        let store = ChatStore(client: nil, demoData: DemoDataStore(bundle: .module))
        await store.send(question: "   ")
        XCTAssertTrue(store.messages.isEmpty)
    }

    func testDemoChatAddsQuestionAndAnswer() async throws {
        let store = ChatStore(client: nil, demoData: DemoDataStore(bundle: .module))
        try store.useDemoFAQ()
        await store.send(question: "What is the plan?")
        XCTAssertEqual(store.messages.count, 2)
        XCTAssertEqual(store.messages[0].role, .user)
        XCTAssertEqual(store.messages[1].role, .assistant)
    }

    func testClearSessionRemovesMessagesAndFAQ() throws {
        let store = ChatStore(client: nil, demoData: DemoDataStore(bundle: .module))
        try store.useDemoFAQ()
        store.clearSession()
        XCTAssertTrue(store.messages.isEmpty)
        XCTAssertTrue(store.faqItems.isEmpty)
    }
}
