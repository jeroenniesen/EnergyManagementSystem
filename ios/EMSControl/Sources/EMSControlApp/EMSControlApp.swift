import SwiftUI
import EMSControlCore

@main
struct EMSControlApp: App {
    @State private var dashboardStore = DashboardStore(client: nil)

    var body: some Scene {
        WindowGroup {
            AppShellView()
                .environment(dashboardStore)
        }
    }
}
