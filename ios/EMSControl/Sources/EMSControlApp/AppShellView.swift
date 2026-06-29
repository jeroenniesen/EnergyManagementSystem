import SwiftUI
import EMSControlCore

struct AppShellView: View {
    @Environment(DashboardStore.self) private var dashboardStore

    var body: some View {
        TabView {
            DashboardView()
                .tabItem { Label("Dashboard", systemImage: "bolt.horizontal.circle") }
            Text("Chat")
                .tabItem { Label("Chat", systemImage: "message") }
        }
        .sheet(isPresented: .constant(dashboardStore.snapshot == nil)) {
            ConnectionView()
        }
    }
}
