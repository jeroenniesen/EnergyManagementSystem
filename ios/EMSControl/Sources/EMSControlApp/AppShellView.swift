import SwiftUI
import EMSControlCore

struct AppShellView: View {
    @Environment(DashboardStore.self) private var dashboardStore
    @Environment(\.colorScheme) private var colorScheme
    @Environment(\.scenePhase) private var scenePhase
    @State private var chatStore = ChatStore(client: nil)

    private var theme: EMSTheme {
        colorScheme == .dark ? .dark : .light
    }

    var body: some View {
        ZStack {
            themeColor(theme.background)
                .ignoresSafeArea()

            if dashboardStore.snapshot == nil {
                ConnectionView()
            } else {
                TabView {
                    DashboardView()
                        .tabItem { Label("Dashboard", systemImage: "bolt.horizontal.circle") }
                    ChatView(store: chatStore)
                        .tabItem { Label("Chat", systemImage: "message") }
                }
            }
        }
        .tint(themeColor(theme.accent))
        .toolbarBackground(themeColor(theme.panel), for: .tabBar)
        .toolbarBackground(.visible, for: .tabBar)
        .task {
            dashboardStore.restoreSavedServer()
            await dashboardStore.refresh()
        }
        .task(id: refreshLoopKey) {
            await runRefreshLoop()
        }
        .task(id: chatSessionKey) {
            await syncChatSession()
        }
    }

    private var refreshLoopKey: String {
        "\(scenePhase)-\(dashboardStore.client?.baseURL.absoluteString ?? "none")"
    }

    private var chatSessionKey: String {
        let mode = dashboardStore.snapshot?.isDemo == true ? "demo" : (dashboardStore.client == nil ? "disconnected" : "live")
        return "\(mode)-\(dashboardStore.client?.baseURL.absoluteString ?? "none")"
    }

    private func syncChatSession() async {
        if dashboardStore.snapshot?.isDemo == true {
            await chatStore.updateSession(client: nil, mode: .demo)
        } else if let client = dashboardStore.client {
            await chatStore.updateSession(client: client, mode: .live)
        } else {
            await chatStore.updateSession(client: nil, mode: .disconnected)
        }
    }

    private func runRefreshLoop() async {
        guard scenePhase == .active else { return }
        while !Task.isCancelled {
            await dashboardStore.refreshWhenDue()
            let seconds = dashboardStore.nextRefreshAt.map { max(1, $0.timeIntervalSinceNow) } ?? 5
            let cappedSeconds = min(max(seconds, 1), 60)
            try? await Task.sleep(for: .seconds(cappedSeconds))
        }
    }
}

func themeColor(_ color: HexColor) -> Color {
    Color(hex: color.hex)
}

private extension Color {
    init(hex: String) {
        let hex = hex.trimmingCharacters(in: CharacterSet.alphanumerics.inverted)
        var value: UInt64 = 0
        Scanner(string: hex).scanHexInt64(&value)

        let red = Double((value >> 16) & 0xFF) / 255.0
        let green = Double((value >> 8) & 0xFF) / 255.0
        let blue = Double(value & 0xFF) / 255.0

        self.init(.sRGB, red: red, green: green, blue: blue, opacity: 1)
    }
}
