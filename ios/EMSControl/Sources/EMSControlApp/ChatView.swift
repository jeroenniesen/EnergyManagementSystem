import SwiftUI
import EMSControlCore

struct ChatView: View {
    @Environment(DashboardStore.self) private var dashboardStore
    @Environment(\.colorScheme) private var colorScheme

    let store: ChatStore

    @State private var input = ""

    private var theme: EMSTheme {
        colorScheme == .dark ? .dark : .light
    }

    private var sessionContext: ChatScreenSessionContext {
        if let client = dashboardStore.client {
            return .live(client)
        }
        if dashboardStore.snapshot?.isDemo == true {
            return .demo
        }
        return .disconnected
    }

    private var sessionSignature: String {
        switch sessionContext {
        case .live(let client):
            "live:\(client.baseURL.absoluteString)|\(client.token ?? "")"
        case .demo:
            "demo"
        case .disconnected:
            "disconnected"
        }
    }

    var body: some View {
        NavigationStack {
            ZStack {
                themeColor(theme.background)
                    .ignoresSafeArea()

                VStack(spacing: 16) {
                    headerCard

                    ScrollView {
                        VStack(alignment: .leading, spacing: 12) {
                            if let error = store.lastError, !error.isEmpty {
                                statusCard(
                                    title: "Chat unavailable",
                                    detail: error,
                                    accent: theme.error
                                )
                            }

                            if !store.faqItems.isEmpty {
                                sectionCard(title: "Quick answers") {
                                    VStack(spacing: 10) {
                                        ForEach(store.faqItems) { item in
                                            VStack(alignment: .leading, spacing: 6) {
                                                Text(item.question)
                                                    .font(.subheadline.weight(.semibold))
                                                    .foregroundStyle(themeColor(theme.text))
                                                    .frame(maxWidth: .infinity, alignment: .leading)
                                                Text(item.answer)
                                                    .font(.subheadline)
                                                    .foregroundStyle(themeColor(theme.muted))
                                                    .frame(maxWidth: .infinity, alignment: .leading)
                                            }
                                            .padding(14)
                                            .background(themeColor(theme.secondaryPanel))
                                            .clipShape(RoundedRectangle(cornerRadius: 14, style: .continuous))
                                            .overlay {
                                                RoundedRectangle(cornerRadius: 14, style: .continuous)
                                                    .stroke(themeColor(theme.line), lineWidth: 1)
                                            }
                                        }
                                    }
                                }
                            }

                            sectionCard(title: "Conversation") {
                                if store.messages.isEmpty {
                                    Text(emptyStateText)
                                        .font(.subheadline)
                                        .foregroundStyle(themeColor(theme.muted))
                                        .frame(maxWidth: .infinity, alignment: .leading)
                                } else {
                                    VStack(spacing: 10) {
                                        ForEach(store.messages) { message in
                                            ChatBubble(message: message, theme: theme)
                                        }
                                    }
                                }
                            }
                        }
                        .padding(.horizontal, 20)
                        .padding(.bottom, 8)
                    }

                    composerCard
                }
                .padding(.top, 16)
            }
            .navigationTitle("Chat")
            .task(id: sessionSignature) {
                await synchronizeStore()
            }
        }
    }

    private var headerCard: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(alignment: .top, spacing: 12) {
                VStack(alignment: .leading, spacing: 6) {
                    Text(headerTitle)
                        .font(.headline)
                        .foregroundStyle(themeColor(theme.text))
                    Text(headerSubtitle)
                        .font(.caption)
                        .foregroundStyle(themeColor(theme.muted))
                }
                Spacer()
                if store.isDemoMode {
                    badge("Demo", color: theme.amber)
                } else if store.canSendFreeform {
                    badge("AI active", color: theme.accent)
                } else {
                    badge("FAQ only", color: theme.winter)
                }
            }

            HStack(spacing: 8) {
                badge("Mode: \(modeLabel)", color: theme.winter)
                badge("Language: \(languageLabel)", color: theme.accent)
            }
        }
        .padding(20)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(themeColor(theme.panel))
        .clipShape(RoundedRectangle(cornerRadius: 18, style: .continuous))
        .overlay {
            RoundedRectangle(cornerRadius: 18, style: .continuous)
                .stroke(themeColor(theme.line), lineWidth: 1)
        }
        .padding(.horizontal, 20)
    }

    private var composerCard: some View {
        VStack(alignment: .leading, spacing: 10) {
            if !store.canSendFreeform {
                Text(disabledReason)
                    .font(.footnote)
                    .foregroundStyle(themeColor(theme.muted))
            }

            HStack(spacing: 12) {
                TextField("Ask the EMS why", text: $input, axis: .vertical)
                    .textInputAutocapitalization(.sentences)
                    .autocorrectionDisabled(false)
                    .disabled(!store.canSendFreeform || store.isBusy)
                    .padding(14)
                    .background(themeColor(theme.secondaryPanel))
                    .foregroundStyle(themeColor(store.canSendFreeform ? theme.text : theme.muted))
                    .clipShape(RoundedRectangle(cornerRadius: 12, style: .continuous))
                    .overlay {
                        RoundedRectangle(cornerRadius: 12, style: .continuous)
                            .stroke(themeColor(store.canSendFreeform ? theme.line : theme.winter), lineWidth: 1)
                    }

                Button {
                    let question = input
                    input = ""
                    Task { await store.send(question: question) }
                } label: {
                    Image(systemName: "paperplane.fill")
                        .font(.headline)
                        .frame(width: 44, height: 44)
                }
                .buttonStyle(ChatSendButtonStyle(theme: theme))
                .disabled(!store.canSendFreeform || store.isBusy || input.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
            }
        }
        .padding(20)
        .background(themeColor(theme.panel))
        .clipShape(RoundedRectangle(cornerRadius: 18, style: .continuous))
        .overlay {
            RoundedRectangle(cornerRadius: 18, style: .continuous)
                .stroke(themeColor(theme.line), lineWidth: 1)
        }
        .padding(.horizontal, 20)
        .padding(.bottom, 16)
    }

    private var headerTitle: String {
        switch sessionContext {
        case .live:
            dashboardStore.snapshot?.serverName ?? "Connected EMS"
        case .demo:
            "Demo EMS"
        case .disconnected:
            "Connect to your EMS"
        }
    }

    private var headerSubtitle: String {
        switch sessionContext {
        case .live:
            store.canSendFreeform ? "Live grounded chat is available." : "Live FAQ is available. Free-form chat is disabled."
        case .demo:
            "Synthetic answers for review and offline validation."
        case .disconnected:
            "Open Demo mode or connect to a live server."
        }
    }

    private var modeLabel: String {
        store.explainerStatus?.mode.replacingOccurrences(of: "_", with: " ").capitalized ?? "Unavailable"
    }

    private var languageLabel: String {
        guard let language = store.explainerStatus?.language else { return "Unavailable" }
        switch language.lowercased() {
        case "nl":
            return "Dutch"
        case "en":
            return "English"
        default:
            return language.uppercased()
        }
    }

    private var disabledReason: String {
        switch sessionContext {
        case .disconnected:
            return "Connect to a server or open Demo mode to ask free-form questions."
        case .demo:
            return ""
        case .live:
            return "Free-form chat follows the server explainer state and is currently disabled."
        }
    }

    private var emptyStateText: String {
        switch sessionContext {
        case .disconnected:
            "Conversation starts after you connect or open Demo mode."
        case .demo:
            "Demo chat stays in memory for this session only."
        case .live:
            "Ask why the EMS is charging, holding, or waiting."
        }
    }

    @ViewBuilder
    private func sectionCard<Content: View>(title: String, @ViewBuilder content: () -> Content) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            Text(title)
                .font(.headline)
                .foregroundStyle(themeColor(theme.text))
            content()
        }
        .padding(18)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(themeColor(theme.panel))
        .clipShape(RoundedRectangle(cornerRadius: 18, style: .continuous))
        .overlay {
            RoundedRectangle(cornerRadius: 18, style: .continuous)
                .stroke(themeColor(theme.line), lineWidth: 1)
        }
    }

    @ViewBuilder
    private func statusCard(title: String, detail: String, accent: HexColor) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(title)
                .font(.subheadline.weight(.semibold))
                .foregroundStyle(themeColor(theme.text))
            Text(detail)
                .font(.footnote)
                .foregroundStyle(themeColor(theme.muted))
        }
        .padding(16)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(themeColor(theme.panel))
        .clipShape(RoundedRectangle(cornerRadius: 14, style: .continuous))
        .overlay {
            RoundedRectangle(cornerRadius: 14, style: .continuous)
                .stroke(themeColor(accent), lineWidth: 1)
        }
    }

    private func badge(_ text: String, color: HexColor) -> some View {
        Text(text)
            .font(.caption.weight(.semibold))
            .foregroundStyle(themeColor(theme.text))
            .padding(.horizontal, 10)
            .padding(.vertical, 6)
            .background(themeColor(color).opacity(0.18))
            .clipShape(Capsule())
            .overlay {
                Capsule()
                    .stroke(themeColor(color), lineWidth: 1)
            }
    }

    private func synchronizeStore() async {
        switch sessionContext {
        case .live(let client):
            await store.updateSession(client: client, mode: .live)
        case .demo:
            await store.updateSession(client: nil, mode: .demo)
        case .disconnected:
            await store.updateSession(client: nil, mode: .disconnected)
        }
    }
}

private struct ChatBubble: View {
    let message: ChatMessage
    let theme: EMSTheme

    var body: some View {
        HStack {
            if message.role == .assistant { Spacer(minLength: 36) }
            Text(message.text)
                .font(.subheadline)
                .foregroundStyle(themeColor(message.role == .user ? theme.background : theme.text))
                .padding(14)
                .frame(maxWidth: .infinity, alignment: message.role == .user ? .trailing : .leading)
                .background(themeColor(message.role == .user ? theme.accent : theme.secondaryPanel))
                .clipShape(RoundedRectangle(cornerRadius: 16, style: .continuous))
                .overlay {
                    RoundedRectangle(cornerRadius: 16, style: .continuous)
                        .stroke(themeColor(message.role == .user ? theme.accent : theme.line), lineWidth: 1)
                }
            if message.role == .user { Spacer(minLength: 36) }
        }
    }
}

private struct ChatSendButtonStyle: ButtonStyle {
    let theme: EMSTheme

    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .background(themeColor(theme.accent).opacity(configuration.isPressed ? 0.82 : 1))
            .foregroundStyle(themeColor(theme.background))
            .clipShape(RoundedRectangle(cornerRadius: 12, style: .continuous))
    }
}

private enum ChatScreenSessionContext {
    case disconnected
    case demo
    case live(APIClient)
}
