import SwiftUI
import EMSControlCore

struct AppShellView: View {
    @Environment(DashboardStore.self) private var dashboardStore
    @Environment(\.colorScheme) private var colorScheme
    @Environment(\.scenePhase) private var scenePhase
    @State private var chatStore = ChatStore(client: nil)
    @State private var insightsStore = InsightsStore(client: nil)
    @State private var activityStore = ActivityStore(client: nil)
    @State private var carStore = CarStore(client: nil)
    @State private var notificationsStore = NotificationsStore(client: nil)
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
                    DashboardView(notificationsStore: notificationsStore)
                        .tabItem { Label("Dashboard", systemImage: "bolt.horizontal.circle") }
                        .tag(AppTab.dashboard)
                    InsightsView(store: insightsStore)
                        .tabItem { Label("Insights", systemImage: "chart.xyaxis.line") }
                        .tag(AppTab.insights)
                    CarView(store: carStore)
                        .tabItem { Label("Car", systemImage: "car.fill") }
                        .tag(AppTab.car)
                    ActivityView(store: activityStore)
                        .tabItem { Label("Activity", systemImage: "clock.arrow.circlepath") }
                        .tag(AppTab.activity)
                    ChatView(store: chatStore)
                        .tabItem { Label("Chat", systemImage: "message") }
                        .tag(AppTab.chat)
                }
                .safeAreaInset(edge: .top, spacing: 0) {
                    if dashboardStore.demoSessionActive,
                       !ProcessInfo.processInfo.arguments.contains("-UITestingScreenshots") {
                        DemoModeBanner(theme: theme)
                    }
                }
            }
        }
        .tint(themeColor(theme.accent))
        .toolbarBackground(themeColor(theme.panel), for: .tabBar)
        .toolbarBackground(.visible, for: .tabBar)
        .task {
            // UI-screenshot / preview hook: land straight on the demo dashboard when launched
            // with EMS_UI_DEMO=1 in the environment. Never fires on a normal launch.
            if ProcessInfo.processInfo.arguments.contains("-UITestingReset") {
                dashboardStore.forgetServer()
            }
            if ProcessInfo.processInfo.environment["EMS_UI_DEMO"] == "1" {
                dashboardStore.loadDemo()
                return
            }
            dashboardStore.restoreSavedServer()
            if dashboardStore.snapshot == nil, dashboardStore.client == nil {
                dashboardStore.clearOrphanedWidgetDemoStateIfDisconnected()
            }
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
        .task(id: carSessionKey) {
            await syncCarSession()
        }
        .task(id: notificationsSessionKey) {
            await syncNotificationsSession()
        }
        .task(id: selectedTab) {
            if selectedTab == .insights, insightsStore.client != nil, insightsStore.report == nil {
                await insightsStore.refresh()
            }
            if selectedTab == .activity, activityStore.client != nil, activityStore.entries.isEmpty {
                await activityStore.refresh()
            }
            if selectedTab == .car, carStore.client != nil, !carStore.loaded {
                await carStore.refresh()
            }
        }
    }

    private var refreshLoopKey: String {
        "\(scenePhase)-\(dashboardStore.client?.baseURL.absoluteString ?? "none")"
    }

    private var chatSessionKey: String {
        let mode = dashboardStore.demoSessionActive ? "demo" : (dashboardStore.client == nil ? "disconnected" : "live")
        return "\(mode)-\(dashboardStore.client?.baseURL.absoluteString ?? "none")"
    }

    private var insightsSessionKey: String {
        let mode = dashboardStore.demoSessionActive ? "demo" : (dashboardStore.client == nil ? "disconnected" : "live")
        return "\(mode)-\(dashboardStore.client?.baseURL.absoluteString ?? "none")-\(dashboardStore.snapshot?.generatedAt.timeIntervalSince1970 ?? 0)"
    }

    private var activitySessionKey: String {
        let mode = dashboardStore.demoSessionActive ? "demo" : (dashboardStore.client == nil ? "disconnected" : "live")
        return "\(mode)-\(dashboardStore.client?.baseURL.absoluteString ?? "none")-\(dashboardStore.snapshot?.generatedAt.timeIntervalSince1970 ?? 0)"
    }

    private var carSessionKey: String {
        let mode = dashboardStore.demoSessionActive ? "demo" : (dashboardStore.client == nil ? "disconnected" : "live")
        return "\(mode)-\(dashboardStore.client?.baseURL.absoluteString ?? "none")"
    }

    private var notificationsSessionKey: String {
        let mode = dashboardStore.demoSessionActive ? "demo" : (dashboardStore.client == nil ? "disconnected" : "live")
        return "\(mode)-\(dashboardStore.client?.baseURL.absoluteString ?? "none")"
    }

    private func syncChatSession() async {
        if dashboardStore.demoSessionActive {
            await chatStore.updateSession(client: nil, mode: .demo)
        } else if let client = dashboardStore.client {
            await chatStore.updateSession(client: client, mode: .live)
        } else {
            await chatStore.updateSession(client: nil, mode: .disconnected)
        }
    }

    private func syncInsightsSession() async {
        if dashboardStore.demoSessionActive, let snapshot = dashboardStore.snapshot {
            insightsStore.setDemo(report: snapshot.report, finance: snapshot.finance)
        } else if let client = dashboardStore.client {
            insightsStore.setClient(client)
        } else {
            insightsStore.setClient(nil)
        }
    }

    private func syncActivitySession() async {
        if dashboardStore.demoSessionActive {
            activityStore.setDemo()
        } else if let client = dashboardStore.client {
            activityStore.setClient(client)
        } else {
            activityStore.setClient(nil)
        }
    }

    private func syncCarSession() async {
        if dashboardStore.demoSessionActive {
            carStore.setDemo()
        } else if let client = dashboardStore.client {
            carStore.setClient(client)
        } else {
            carStore.setClient(nil)
        }
    }

    private func syncNotificationsSession() async {
        if dashboardStore.demoSessionActive {
            notificationsStore.setDemo()
        } else if let client = dashboardStore.client {
            notificationsStore.setClient(client)
            // Dashboard-resident content with no tab-select hook: load once per connection here
            // (pull-to-refresh and opening the list keep it fresh afterwards).
            if !notificationsStore.loaded {
                await notificationsStore.refresh()
            }
        } else {
            notificationsStore.setClient(nil)
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
    case car
    case activity
    case chat
}

private struct DemoModeBanner: View {
    let theme: EMSTheme

    var body: some View {
        HStack(spacing: 8) {
            Image(systemName: "play.rectangle.on.rectangle")
            Text("Demo")
                .font(.subheadline.weight(.semibold))
            Text("— sample data only, no live EMS connection")
                .font(.caption)
                .lineLimit(2)
        }
        .foregroundStyle(themeColor(theme.background))
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.horizontal, 16)
        .padding(.vertical, 8)
        .background(themeColor(theme.amber))
        .accessibilityIdentifier("demo.mode.banner")
        .accessibilityLabel("Demo mode active")
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
