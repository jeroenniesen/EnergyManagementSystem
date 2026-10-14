import SwiftUI
import EMSControlCore

@main
struct EMSControlApp: App {
    @State private var dashboardStore = DashboardStore(client: nil)

    init() {
        WidgetTimelineReloader.install()
    }

    var body: some Scene {
        WindowGroup {
            AppShellView()
                .environment(dashboardStore)
        }
    }
}
