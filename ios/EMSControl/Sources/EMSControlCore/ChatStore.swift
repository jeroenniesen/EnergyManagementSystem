import Foundation
import Observation

public enum ChatRole: Equatable {
    case user
    case assistant
}

public struct ChatMessage: Equatable, Identifiable {
    public let id = UUID()
    public let role: ChatRole
    public let text: String

    public init(role: ChatRole, text: String) {
        self.role = role
        self.text = text
    }
}

@MainActor
@Observable
public final class ChatStore {
    public var client: APIClient?
    public private(set) var messages: [ChatMessage] = []
    public private(set) var faqItems: [FAQItem] = []
    public private(set) var isBusy = false
    public private(set) var lastError: String?

    private let demoData: DemoDataStore

    public init(client: APIClient?, demoData: DemoDataStore = DemoDataStore()) {
        self.client = client
        self.demoData = demoData
    }

    public func loadFAQ() async {
        do {
            if let client {
                faqItems = try await client.fetchFAQ().items
            } else {
                try useDemoFAQ()
            }
            lastError = nil
        } catch {
            lastError = String(describing: error)
        }
    }

    public func useDemoFAQ() throws {
        faqItems = try demoData.faq().items
    }

    public func send(question: String) async {
        let trimmed = question.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty, !isBusy else { return }
        messages.append(ChatMessage(role: .user, text: trimmed))
        isBusy = true
        defer { isBusy = false }
        do {
            let response = try await (client?.sendChat(question: trimmed) ?? demoData.chatResponse())
            messages.append(ChatMessage(role: .assistant, text: response.answer))
            lastError = nil
        } catch {
            lastError = String(describing: error)
        }
    }

    public func clearSession() {
        messages.removeAll()
        faqItems.removeAll()
        lastError = nil
        isBusy = false
    }
}
