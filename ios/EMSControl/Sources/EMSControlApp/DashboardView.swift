import SwiftUI
import EMSControlCore

struct DashboardView: View {
    @Environment(DashboardStore.self) private var dashboardStore
    private let theme = EMSTheme.dark

    var body: some View {
        NavigationStack {
            ZStack {
                themeColor(theme.background)
                    .ignoresSafeArea()

                ScrollView {
                    VStack(alignment: .leading, spacing: 16) {
                        if let snapshot = dashboardStore.snapshot {
                            HStack(alignment: .top, spacing: 12) {
                                VStack(alignment: .leading, spacing: 6) {
                                    Text(snapshot.serverName)
                                        .font(.headline)
                                        .foregroundStyle(themeColor(theme.text))
                                    Text(snapshot.isDemo ? "Demo data" : (dashboardStore.isStale ? "Stale feed" : "Live feed"))
                                        .font(.caption)
                                        .foregroundStyle(themeColor(theme.muted))
                                }
                                Spacer()
                                if snapshot.isDemo {
                                    Text("Demo")
                                        .font(.caption.bold())
                                        .padding(.horizontal, 10)
                                        .padding(.vertical, 5)
                                        .background(.thinMaterial)
                                        .clipShape(Capsule())
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

                            LazyVGrid(columns: [GridItem(.flexible()), GridItem(.flexible())], spacing: 12) {
                                DashboardCard(title: "Battery", value: snapshot.status.values["soc_pct"]?.displayValue ?? "--", theme: theme)
                                DashboardCard(title: "Mode", value: snapshot.decision.values["intent"]?.displayValue ?? "--", theme: theme)
                                DashboardCard(title: "Savings", value: snapshot.savings.values["today_eur"]?.displayValue ?? "--", theme: theme)
                                DashboardCard(title: "Plan", value: snapshot.energyStory.values["headline"]?.displayValue ?? "Open story", theme: theme)
                            }
                        }
                    }
                    .padding()
                }
            }
            .navigationTitle("Dashboard")
            .refreshable { await dashboardStore.refresh() }
        }
    }
}

private struct DashboardCard: View {
    let title: String
    let value: String
    let theme: EMSTheme

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(title)
                .font(.caption)
                .foregroundStyle(themeColor(theme.muted))
            Text(value)
                .font(.headline)
                .foregroundStyle(themeColor(theme.text))
                .lineLimit(2)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(16)
        .background(themeColor(theme.secondaryPanel))
        .clipShape(RoundedRectangle(cornerRadius: 14, style: .continuous))
        .overlay {
            RoundedRectangle(cornerRadius: 14, style: .continuous)
                .stroke(themeColor(theme.line), lineWidth: 1)
        }
    }
}

private extension JSONValue {
    var displayValue: String {
        switch self {
        case .string(let value):
            value
        case .number(let value):
            value.formatted()
        case .bool(let value):
            value ? "Yes" : "No"
        case .object, .array:
            "Details"
        case .null:
            "--"
        }
    }
}
