import SwiftUI
import EMSControlCore

struct DashboardView: View {
    @Environment(DashboardStore.self) private var dashboardStore
    @Environment(\.colorScheme) private var colorScheme

    private var theme: EMSTheme {
        colorScheme == .dark ? .dark : .light
    }

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
                                    Text(statusLine(snapshot))
                                        .font(.caption)
                                        .foregroundStyle(themeColor(theme.muted))
                                    Text(snapshot.decision.values["intent"]?.displayValue ?? "Intent unavailable")
                                        .font(.title3.weight(.semibold))
                                        .foregroundStyle(themeColor(theme.text))
                                }
                                Spacer()
                                VStack(alignment: .trailing, spacing: 8) {
                                    badge(snapshot.isDemo ? "Demo" : (dashboardStore.isStale ? "Stale" : "Live"), color: snapshot.isDemo ? theme.amber : theme.accent)
                                    badge(snapshot.status.values["dry_run"]?.bool == true ? "Dry run" : "Control", color: snapshot.status.values["dry_run"]?.bool == true ? theme.winter : theme.accent)
                                    if !snapshot.degradedSections.isEmpty {
                                        badge("Degraded", color: theme.error)
                                    }
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

                            if !snapshot.degradedSections.isEmpty {
                                DashboardNotice(
                                    title: "Degraded sections",
                                    value: snapshot.degradedSections.joined(separator: ", "),
                                    theme: theme
                                )
                            }

                            if let alerts = alertsText(snapshot), !alerts.isEmpty {
                                DashboardNotice(title: "Alerts", value: alerts, theme: theme)
                            }

                            LazyVGrid(columns: [GridItem(.flexible()), GridItem(.flexible())], spacing: 12) {
                                DashboardCard(title: "Battery SoC", value: percent(snapshot.status.values["soc_pct"]), theme: theme)
                                DashboardCard(title: "Current price", value: price(snapshot), theme: theme)
                                DashboardCard(title: "Solar / flow", value: flow(snapshot), theme: theme)
                                DashboardCard(title: "Savings today", value: euro(snapshot.savings.values["today_eur"]), theme: theme)
                                DashboardCard(title: "Next plan", value: snapshot.decision.values["plan_reason"]?.displayValue ?? snapshot.energyStory.values["headline"]?.displayValue ?? "--", theme: theme)
                                DashboardCard(title: "Strategy", value: snapshot.strategy.values["mode"]?.displayValue ?? "--", theme: theme)
                                DashboardCard(title: "Readiness", value: readiness(snapshot), theme: theme)
                                DashboardCard(title: "Freshness", value: freshness(snapshot), theme: theme)
                                DashboardCard(title: "Battery towers", value: towers(snapshot), theme: theme)
                                DashboardCard(title: "AI validation", value: snapshot.aiValidation?.values["active"]?.displayValue ?? snapshot.aiValidation?.state?.rawValue ?? "Unavailable", theme: theme)
                            }
                        }
                    }
                    .padding()
                }
            }
            .navigationTitle("Dashboard")
            .toolbar {
                if dashboardStore.snapshot != nil {
                    Button(dashboardStore.snapshot?.isDemo == true ? "Connect to my EMS" : "Forget Server") {
                        dashboardStore.forgetServer()
                    }
                }
            }
            .refreshable { await dashboardStore.refresh() }
        }
    }

    private func statusLine(_ snapshot: DashboardSnapshot) -> String {
        let mode = snapshot.isDemo ? "Demo data" : (dashboardStore.isStale ? "Stale feed" : "Live feed")
        let next = dashboardStore.nextRefreshAt.map { "next refresh \($0.formatted(date: .omitted, time: .shortened))" }
        return [mode, next].compactMap(\.self).joined(separator: " - ")
    }

    private func badge(_ text: String, color: HexColor) -> some View {
        Text(text)
            .font(.caption.weight(.semibold))
            .foregroundStyle(themeColor(theme.text))
            .padding(.horizontal, 10)
            .padding(.vertical, 5)
            .background(themeColor(color).opacity(0.18))
            .clipShape(Capsule())
            .overlay {
                Capsule().stroke(themeColor(color), lineWidth: 1)
            }
    }

    private func percent(_ value: JSONValue?) -> String {
        guard let number = value?.number else { return value?.displayValue ?? "--" }
        return "\(number.formatted(.number.precision(.fractionLength(0...1))))%"
    }

    private func euro(_ value: JSONValue?) -> String {
        guard let number = value?.number else { return value?.displayValue ?? "--" }
        return number.formatted(.currency(code: "EUR"))
    }

    private func price(_ snapshot: DashboardSnapshot) -> String {
        if let value = snapshot.status.values["price_eur_per_kwh"] ?? snapshot.strategy.values["price_eur_per_kwh"] {
            return euro(value) + "/kWh"
        }
        return snapshot.status.values["price_level"]?.displayValue ?? "--"
    }

    private func flow(_ snapshot: DashboardSnapshot) -> String {
        let solar = watts(snapshot.status.values["solar_power_w"])
        let grid = watts(snapshot.status.values["grid_power_w"])
        return "Solar \(solar) / Grid \(grid)"
    }

    private func watts(_ value: JSONValue?) -> String {
        guard let number = value?.number else { return value?.displayValue ?? "--" }
        return "\(number.formatted(.number.precision(.fractionLength(0)))) W"
    }

    private func readiness(_ snapshot: DashboardSnapshot) -> String {
        if let ready = snapshot.readiness.values["dashboard_ready"]?.bool {
            return ready ? "Dashboard ready" : "Starting"
        }
        return snapshot.readiness.state?.rawValue ?? "--"
    }

    private func freshness(_ snapshot: DashboardSnapshot) -> String {
        let entries = ["battery", "prices", "forecast"].compactMap { key -> String? in
            guard let value = snapshot.freshness.values[key]?.displayValue else { return nil }
            return "\(key): \(value)"
        }
        return entries.isEmpty ? (snapshot.freshness.state?.rawValue ?? "--") : entries.joined(separator: ", ")
    }

    private func towers(_ snapshot: DashboardSnapshot) -> String {
        if let aggregate = snapshot.battery.values["aggregate"]?.object,
           let towers = aggregate["online_towers"]?.number {
            return "\(towers.formatted(.number.precision(.fractionLength(0)))) online"
        }
        return snapshot.battery.values["state"]?.displayValue ?? "--"
    }

    private func alertsText(_ snapshot: DashboardSnapshot) -> String? {
        guard let alerts = snapshot.alerts.values["alerts"]?.array else { return nil }
        if alerts.isEmpty { return nil }
        return alerts.map(\.displayValue).joined(separator: ", ")
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

private struct DashboardNotice: View {
    let title: String
    let value: String
    let theme: EMSTheme

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(title)
                .font(.subheadline.weight(.semibold))
                .foregroundStyle(themeColor(theme.text))
            Text(value)
                .font(.footnote)
                .foregroundStyle(themeColor(theme.muted))
        }
        .padding(16)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(themeColor(theme.panel))
        .clipShape(RoundedRectangle(cornerRadius: 14, style: .continuous))
        .overlay {
            RoundedRectangle(cornerRadius: 14, style: .continuous)
                .stroke(themeColor(theme.amber), lineWidth: 1)
        }
    }
}

private extension JSONValue {
    var number: Double? {
        if case .number(let value) = self { return value }
        return nil
    }

    var bool: Bool? {
        if case .bool(let value) = self { return value }
        return nil
    }

    var object: [String: JSONValue]? {
        if case .object(let value) = self { return value }
        return nil
    }

    var array: [JSONValue]? {
        if case .array(let value) = self { return value }
        return nil
    }

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
