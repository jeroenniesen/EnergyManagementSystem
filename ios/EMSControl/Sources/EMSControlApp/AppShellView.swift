import SwiftUI
import EMSControlCore

struct AppShellView: View {
    @Environment(DashboardStore.self) private var dashboardStore
    private let theme = EMSTheme.dark

    var body: some View {
        ZStack {
            themeColor(theme.background)
                .ignoresSafeArea()

            TabView {
                DashboardView()
                    .tabItem { Label("Dashboard", systemImage: "bolt.horizontal.circle") }
                Text("Chat")
                    .foregroundStyle(themeColor(theme.text))
                    .tabItem { Label("Chat", systemImage: "message") }
            }
        }
        .tint(themeColor(theme.accent))
        .preferredColorScheme(.dark)
        .toolbarBackground(themeColor(theme.panel), for: .tabBar)
        .toolbarBackground(.visible, for: .tabBar)
        .sheet(isPresented: .constant(dashboardStore.snapshot == nil)) {
            ConnectionView()
                .presentationBackground(themeColor(theme.background))
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
