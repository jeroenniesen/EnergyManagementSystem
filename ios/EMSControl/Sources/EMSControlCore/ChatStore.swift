import Foundation
import Observation

public enum ChatRole: Equatable {
    case user
    case assistant
}

public enum ChatSessionMode: Equatable {
    case disconnected
    case demo
    case live
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
    public private(set) var explainerStatus: ExplainerStatus?
    public private(set) var sessionMode: ChatSessionMode = .disconnected
    public private(set) var isBusy = false
    public private(set) var lastError: String?

    public var isDemoMode: Bool { sessionMode == .demo }
    public var canSendFreeform: Bool { explainerStatus?.active == true }

    private let demoData: DemoDataStore
    private var currentSessionKey: String?
    /// Bumped on session changes so a late live FAQ/explainer fetch cannot overwrite demo.
    private var sessionGeneration = 0

    public init(client: APIClient?, demoData: DemoDataStore = DemoDataStore()) {
        self.client = client
        self.demoData = demoData
        if client != nil {
            sessionMode = .live
            currentSessionKey = Self.sessionKey(client: client, mode: .live)
        }
    }

    public func updateSession(client: APIClient?, mode: ChatSessionMode) async {
        let nextSessionKey = Self.sessionKey(client: client, mode: mode)
        let didChangeSession = nextSessionKey != currentSessionKey || mode != sessionMode

        self.client = client
        sessionMode = mode

        if didChangeSession {
            sessionGeneration &+= 1
            clearSession()
            currentSessionKey = nextSessionKey
        }

        switch mode {
        case .disconnected:
            explainerStatus = nil
            lastError = nil
        case .demo:
            await loadDemoSession()
        case .live:
            guard client != nil else {
                explainerStatus = nil
                lastError = nil
                return
            }
            await loadLiveSession()
        }
    }

    public func loadFAQ() async {
        switch sessionMode {
        case .demo:
            await loadDemoSession()
        case .live:
            await loadLiveSession()
        case .disconnected:
            clearSession()
            explainerStatus = nil
            lastError = nil
        }
    }

    public func loadExplainer() async {
        let generation = sessionGeneration
        let mode = sessionMode
        do {
            if let client, mode == .live {
                let status = try await client.fetchExplainer()
                guard generation == sessionGeneration, sessionMode == .live else { return }
                explainerStatus = status
            } else {
                explainerStatus = try demoData.explainerStatus()
            }
            lastError = nil
        } catch {
            guard generation == sessionGeneration else { return }
            lastError = String(describing: error)
        }
    }

    public func useDemoFAQ() throws {
        faqItems = try demoData.faq().items
    }

    public func send(question: String) async {
        let trimmed = question.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty, !isBusy, canSendFreeform else { return }
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
        explainerStatus = nil
        lastError = nil
        isBusy = false
    }

    func setExplainerStatusForTesting(_ status: ExplainerStatus?) {
        explainerStatus = status
    }

    private func loadDemoSession() async {
        do {
            explainerStatus = try demoData.explainerStatus()
            faqItems = try demoData.faq().items
            lastError = nil
        } catch {
            lastError = String(describing: error)
        }
    }

    private func loadLiveSession() async {
        guard let client else { return }
        let generation = sessionGeneration

        do {
            let status = try await client.fetchExplainer()
            let faq = try await client.fetchFAQ().items
            guard generation == sessionGeneration, sessionMode == .live else { return }
            explainerStatus = status
            faqItems = faq
            lastError = nil
        } catch {
            guard generation == sessionGeneration, sessionMode == .live else { return }
            lastError = String(describing: error)
        }
    }

    private static func sessionKey(client: APIClient?, mode: ChatSessionMode) -> String {
        switch mode {
        case .disconnected:
            return "disconnected"
        case .demo:
            return "demo"
        case .live:
            guard let client else { return "live:none" }
            return "live:\(client.baseURL.absoluteString)|\(client.token ?? "")"
        }
    }
}
