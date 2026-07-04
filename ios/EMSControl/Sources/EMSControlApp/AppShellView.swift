import SwiftUI
import EMSControlCore

struct AppShellView: View {
    @Environment(DashboardStore.self) private var dashboardStore
    @Environment(\.colorScheme) private var colorScheme
    @Environment(\.scenePhase) private var scenePhase
    @State private var chatStore = ChatStore(client: nil)
    @State private var insightsStore = InsightsStore(client: nil)
    @State private var activityStore = ActivityStore(client: nil)
    @State private var selectedTab: AppTab = .dashboard

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
                TabView(selection: $selectedTab) {
                    DashboardView()
                        .tabItem { Label("Dashboard", systemImage: "bolt.horizontal.circle") }
                        .tag(AppTab.dashboard)
                    InsightsView(store: insightsStore)
                        .tabItem { Label("Insights", systemImage: "chart.xyaxis.line") }
                        .tag(AppTab.insights)
                    ActivityView(store: activityStore)
                        .tabItem { Label("Activity", systemImage: "clock.arrow.circlepath") }
                        .tag(AppTab.activity)
                    ChatView(store: chatStore)
                        .tabItem { Label("Chat", systemImage: "message") }
                        .tag(AppTab.chat)
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
        .task(id: insightsSessionKey) {
            await syncInsightsSession()
        }
        .task(id: activitySessionKey) {
            await syncActivitySession()
        }
        .task(id: selectedTab) {
            if selectedTab == .insights, insightsStore.client != nil, insightsStore.report == nil {
                await insightsStore.refresh()
            }
            if selectedTab == .activity, activityStore.client != nil, activityStore.entries.isEmpty {
                await activityStore.refresh()
            }
        }
    }

    private var refreshLoopKey: String {
        "\(scenePhase)-\(dashboardStore.client?.baseURL.absoluteString ?? "none")"
    }

    private var chatSessionKey: String {
        let mode = dashboardStore.snapshot?.isDemo == true ? "demo" : (dashboardStore.client == nil ? "disconnected" : "live")
        return "\(mode)-\(dashboardStore.client?.baseURL.absoluteString ?? "none")"
    }

    private var insightsSessionKey: String {
        let mode = dashboardStore.snapshot?.isDemo == true ? "demo" : (dashboardStore.client == nil ? "disconnected" : "live")
        return "\(mode)-\(dashboardStore.client?.baseURL.absoluteString ?? "none")-\(dashboardStore.snapshot?.generatedAt.timeIntervalSince1970 ?? 0)"
    }

    private var activitySessionKey: String {
        let mode = dashboardStore.snapshot?.isDemo == true ? "demo" : (dashboardStore.client == nil ? "disconnected" : "live")
        return "\(mode)-\(dashboardStore.client?.baseURL.absoluteString ?? "none")-\(dashboardStore.snapshot?.generatedAt.timeIntervalSince1970 ?? 0)"
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

    private func syncInsightsSession() async {
        if let snapshot = dashboardStore.snapshot, snapshot.isDemo {
            insightsStore.setDemo(report: snapshot.report, finance: snapshot.finance)
        } else if let client = dashboardStore.client {
            insightsStore.setClient(client)
        } else {
            insightsStore.setClient(nil)
        }
    }

    private func syncActivitySession() async {
        if dashboardStore.snapshot?.isDemo == true {
            // Activity has no demo fixture data — show the empty state in demo mode.
            activityStore.setClient(nil)
        } else if let client = dashboardStore.client {
            activityStore.setClient(client)
        } else {
            activityStore.setClient(nil)
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

private enum AppTab {
    case dashboard
    case insights
    case activity
    case chat
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
