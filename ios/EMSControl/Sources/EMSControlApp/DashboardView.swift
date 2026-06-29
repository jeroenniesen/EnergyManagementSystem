import SwiftUI
import EMSControlCore

struct DashboardView: View {
    @Environment(DashboardStore.self) private var dashboardStore

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 16) {
                    if let snapshot = dashboardStore.snapshot {
                        HStack {
                            VStack(alignment: .leading) {
                                Text(snapshot.serverName)
                                    .font(.headline)
                                Text(snapshot.isDemo ? "Demo data" : (dashboardStore.isStale ? "Stale" : "Live"))
                                    .font(.caption)
                                    .foregroundStyle(.secondary)
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
                        LazyVGrid(columns: [GridItem(.flexible()), GridItem(.flexible())], spacing: 12) {
                            DashboardCard(title: "Battery", value: snapshot.status.values["soc_pct"]?.displayValue ?? "--")
                            DashboardCard(title: "Mode", value: snapshot.decision.values["intent"]?.displayValue ?? "--")
                            DashboardCard(title: "Savings", value: snapshot.savings.values["today_eur"]?.displayValue ?? "--")
                            DashboardCard(title: "Plan", value: snapshot.energyStory.values["headline"]?.displayValue ?? "Open story")
                        }
                    }
                }
                .padding()
            }
            .navigationTitle("Dashboard")
            .refreshable { await dashboardStore.refresh() }
        }
    }
}

private struct DashboardCard: View {
    let title: String
    let value: String

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(title).font(.caption).foregroundStyle(.secondary)
            Text(value).font(.headline).lineLimit(2)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding()
        .background(.regularMaterial)
        .clipShape(RoundedRectangle(cornerRadius: 12, style: .continuous))
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
