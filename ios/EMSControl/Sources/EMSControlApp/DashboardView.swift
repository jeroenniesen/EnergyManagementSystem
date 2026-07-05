import SwiftUI
import EMSControlCore

struct DashboardView: View {
    @Environment(DashboardStore.self) private var dashboardStore
    @Environment(\.colorScheme) private var colorScheme
    @State private var showsDetails = false

    private var theme: EMSTheme {
        colorScheme == .dark ? .dark : .light
    }

    var body: some View {
        NavigationStack {
            ZStack {
                dashboardBackground

                ScrollView {
                    VStack(alignment: .leading, spacing: 16) {
                        if let snapshot = dashboardStore.snapshot {
                            // 1. Status card
                            HomeStatePanel(snapshot: snapshot, isStale: dashboardStore.isStale, nextRefreshAt: dashboardStore.nextRefreshAt, theme: theme)

                            // Safety alerts stay at the top (conditional).
                            if !snapshot.alerts.alerts.isEmpty {
                                AlertsPanel(alerts: snapshot.alerts.alerts, theme: theme)
                            }

                            BatteryPlanPanel(plan: snapshot.batteryPlan, story: snapshot.energyStory, theme: theme)

                            // 2. Today so far (scores)
                            ScoreStrip(scores: snapshot.report.scores, theme: theme)

                            // 3. Plan tracks
                            EnergyGraphsPanel(story: snapshot.energyStory, theme: theme)

                            // 4. Battery
                            BatteryPanel(snapshot: snapshot, theme: theme)

                            // 5. Strategy
                            if let strategy = snapshot.strategy {
                                StrategyCard(strategy: strategy, theme: theme)
                            }

                            // Kept below the requested five (not named for removal).
                            EnergyStoryPanel(snapshot: snapshot, theme: theme)

                            FinancePanel(finance: snapshot.finance, savings: snapshot.savings, theme: theme)

                            DisclosureGroup(isExpanded: $showsDetails) {
                                DetailGrid(snapshot: snapshot, theme: theme)
                                    .padding(.top, 10)
                            } label: {
                                Label("Details", systemImage: "slider.horizontal.3")
                                    .font(.headline)
                                    .foregroundStyle(themeColor(theme.text))
                            }
                            .padding(16)
                            .background(themeColor(theme.panel))
                            .clipShape(RoundedRectangle(cornerRadius: 12, style: .continuous))
                            .overlay {
                                RoundedRectangle(cornerRadius: 12, style: .continuous)
                                    .stroke(themeColor(theme.line), lineWidth: 1)
                            }
                        }
                    }
                    .padding()
                }
            }
            .navigationTitle("Dashboard")
            .toolbar {
                if dashboardStore.snapshot != nil {
                    Button(dashboardStore.snapshot?.isDemo == true ? "Connect" : "Forget") {
                        dashboardStore.forgetServer()
                    }
                }
            }
            .refreshable { await dashboardStore.refresh() }
        }
    }

    private var dashboardBackground: some View {
        LinearGradient(
            colors: [
                themeColor(theme.background),
                themeColor(theme.secondaryPanel).opacity(colorScheme == .dark ? 0.72 : 0.55),
                themeColor(theme.background)
            ],
            startPoint: .topLeading,
            endPoint: .bottomTrailing
        )
        .ignoresSafeArea()
    }
}

private struct HomeStatePanel: View {
    let snapshot: MobileDashboardSnapshot
    let isStale: Bool
    let nextRefreshAt: Date?
    let theme: EMSTheme

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack(alignment: .top, spacing: 12) {
                VStack(alignment: .leading, spacing: 8) {
                    Text(snapshot.decision.homeState?.headline ?? "EMS is watching the home")
                        .font(.title3.weight(.semibold))
                        .foregroundStyle(themeColor(theme.text))
                        .fixedSize(horizontal: false, vertical: true)

                    Text(statusLine)
                        .font(.footnote)
                        .foregroundStyle(themeColor(theme.muted))
                }
                Spacer(minLength: 8)
                StatusBadge(text: badgeText, color: badgeColor, theme: theme)
            }

            HStack(spacing: 10) {
                MetricPill(title: "Battery %", value: percent(snapshot.status.socPct), theme: theme)
                MetricPill(title: "Mode", value: humanizeMode(snapshot.battery.currentMode ?? snapshot.decision.desiredMode), theme: theme)
                MetricPill(title: "Price", value: price(snapshot.energyStory.currentPriceEurPerKwh), theme: theme)
            }

            PriceContextBar(story: snapshot.energyStory, theme: theme)
        }
        .padding(18)
        .background(themeColor(theme.panel).opacity(0.94))
        .clipShape(RoundedRectangle(cornerRadius: 14, style: .continuous))
        .overlay {
            RoundedRectangle(cornerRadius: 14, style: .continuous)
                .stroke(themeColor(theme.line), lineWidth: 1)
        }
    }

    private var badgeText: String {
        if snapshot.isDemo { return "Demo" }
        if isStale { return "Stale" }
        if snapshot.decision.planValidation?.ok == false { return "Holding" }
        if snapshot.status.dryRun { return "Watch-only" }
        return "Live"
    }

    private var badgeColor: HexColor {
        if isStale { return theme.amber }
        if snapshot.decision.planValidation?.ok == false { return theme.amber }
        if snapshot.status.dryRun { return theme.winter }
        return theme.accent
    }

    private var statusLine: String {
        // The badge already says demo / live / watch-only, so leave devMode off this line.
        let refresh = nextRefreshAt.map { "next refresh \($0.formatted(date: .omitted, time: .shortened))" }
        return [snapshot.serverName, refresh].compactMap(\.self).joined(separator: " — ")
    }
}

// The "buy grid cheap" proof, at a glance: where the current price sits in today's range.
private struct PriceContextBar: View {
    let story: EnergyStorySnapshot
    let theme: EMSTheme

    private var prices: [Double] {
        (story.recent + story.slots).compactMap(\.eurPerKwh).filter { $0 > 0 }
    }

    var body: some View {
        if let now = story.currentPriceEurPerKwh, let lo = prices.min(), let hi = prices.max(), hi > lo {
            let pos = min(max((now - lo) / (hi - lo), 0), 1)
            VStack(alignment: .leading, spacing: 6) {
                HStack {
                    Text("Grid price now")
                        .font(.caption2.weight(.semibold))
                        .foregroundStyle(themeColor(theme.muted))
                    Spacer()
                    Text(band(pos))
                        .font(.caption2.weight(.semibold))
                        .foregroundStyle(themeColor(bandColor(pos)))
                }
                GeometryReader { geo in
                    ZStack(alignment: .leading) {
                        Capsule()
                            .fill(themeColor(theme.line))
                            .frame(height: 5)
                        Circle()
                            .fill(themeColor(bandColor(pos)))
                            .frame(width: 10, height: 10)
                            .offset(x: pos * (geo.size.width - 10))
                    }
                    .frame(maxHeight: .infinity, alignment: .center)
                }
                .frame(height: 12)
                HStack {
                    Text(euro(lo)).font(.caption2).foregroundStyle(themeColor(theme.muted))
                    Spacer()
                    Text(euro(hi)).font(.caption2).foregroundStyle(themeColor(theme.muted))
                }
            }
            .padding(.top, 4)
            .accessibilityElement(children: .ignore)
            .accessibilityLabel("Grid price now")
            .accessibilityValue("\(band(pos)). \(price(now)), in today's range \(euro(lo)) to \(euro(hi)).")
        }
    }

    private func band(_ pos: Double) -> String {
        if pos < 0.34 { return "Cheap" }
        if pos < 0.67 { return "Typical" }
        return "Pricey"
    }

    private func bandColor(_ pos: Double) -> HexColor {
        if pos < 0.34 { return theme.accent }
        if pos < 0.67 { return theme.muted }
        return theme.amber
    }
}

private struct BatteryPlanPanel: View {
    let plan: BatteryPlanSnapshot
    let story: EnergyStorySnapshot
    let theme: EMSTheme

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(alignment: .top, spacing: 10) {
                VStack(alignment: .leading, spacing: 6) {
                    Text("Battery plan")
                        .font(.caption.weight(.semibold))
                        .foregroundStyle(themeColor(theme.muted))
                    Text(compactHeadline)
                        .font(.headline.weight(.semibold))
                        .foregroundStyle(themeColor(theme.text))
                        .fixedSize(horizontal: false, vertical: true)
                }
                Spacer(minLength: 8)
                StatusBadge(text: statusLabel, color: statusColor, theme: theme)
            }

            Text(compactContext)
                .font(.footnote)
                .foregroundStyle(themeColor(theme.muted))
                .fixedSize(horizontal: false, vertical: true)

            ViewThatFits(in: .horizontal) {
                HStack(spacing: 8) { compactFacts }
                VStack(alignment: .leading, spacing: 8) { compactFacts }
            }

            if let warning = plan.warnings.first {
                Label(warning, systemImage: "exclamationmark.triangle")
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(themeColor(theme.amber))
                    .fixedSize(horizontal: false, vertical: true)
                    .padding(.horizontal, 10)
                    .padding(.vertical, 8)
                    .background(themeColor(theme.amber).opacity(0.13))
                    .clipShape(RoundedRectangle(cornerRadius: 8, style: .continuous))
            }

            NavigationLink {
                BatteryPlanDetailView(plan: plan, story: story, theme: theme)
            } label: {
                HStack(spacing: 8) {
                    Label("Plan details", systemImage: "chart.xyaxis.line")
                    Spacer(minLength: 8)
                    Image(systemName: "chevron.right")
                        .font(.caption.weight(.semibold))
                }
                .font(.footnote.weight(.semibold))
                .foregroundStyle(themeColor(theme.text))
                .padding(.horizontal, 12)
                .padding(.vertical, 10)
                .background(themeColor(theme.secondaryPanel))
                .clipShape(RoundedRectangle(cornerRadius: 8, style: .continuous))
            }
            .buttonStyle(.plain)
        }
        .padding(16)
        .background(themeColor(theme.panel))
        .clipShape(RoundedRectangle(cornerRadius: 12, style: .continuous))
        .overlay {
            RoundedRectangle(cornerRadius: 12, style: .continuous)
                .stroke(themeColor(theme.line), lineWidth: 1)
        }
        .accessibilityElement(children: .contain)
    }

    @ViewBuilder
    private var compactFacts: some View {
        BatteryPlanFact(title: "Now", value: actionLabel, color: statusColor, theme: theme)
        BatteryPlanFact(title: "Battery", value: batteryTargetLabel, color: theme.accent, theme: theme)
        BatteryPlanFact(title: "Top-up", value: topupLabel, color: theme.amber, theme: theme)
    }

    private var compactHeadline: String {
        switch plan.status {
        case "needs_topup":
            if let topup = plan.plannedGridTopupKwh, topup > 0.05 {
                return "EMS plans a \(kwh(topup)) grid top-up before sunset."
            }
            return "EMS plans a small grid top-up before sunset."
        case "behind_target":
            return "Battery is behind target; EMS is correcting it."
        case "data_stale":
            return "EMS is paused until fresh data returns."
        case "paused_safely":
            return "Battery automation is paused safely."
        default:
            return "Battery is following the plan."
        }
    }

    private var compactContext: String {
        let level = batteryTargetLabel
        let reason = plan.currentReason.trimmingCharacters(in: .whitespacesAndNewlines)
        if reason.isEmpty {
            return "\(level). Currently \(actionLabel.lowercased())."
        }
        return "\(level). Currently \(actionLabel.lowercased()): \(reason)"
    }

    private var batteryTargetLabel: String {
        if let current = plan.currentSocPct, let target = plan.targetSocPct {
            return "\(percent(current)) to \(percent(target))"
        }
        if let current = plan.currentSocPct {
            return percent(current)
        }
        return "--"
    }

    private var topupLabel: String {
        guard let topup = plan.plannedGridTopupKwh else {
            return "--"
        }
        if topup <= 0.05 {
            return "None"
        }
        return kwh(topup)
    }

    private var statusLabel: String {
        switch plan.status {
        case "on_track": "On track"
        case "needs_topup": "Top-up planned"
        case "behind_target": "Behind"
        case "paused_safely": "Paused"
        case "data_stale": "Data stale"
        default: plan.status.replacingOccurrences(of: "_", with: " ").capitalized
        }
    }

    private var statusColor: HexColor {
        switch plan.status {
        case "on_track": theme.accent
        case "needs_topup", "behind_target": theme.amber
        case "data_stale": theme.error
        default: theme.muted
        }
    }

    private var actionLabel: String {
        switch plan.currentAction {
        case "charge", "grid_charge": "Charge"
        case "solar_charge": "Solar charge"
        case "hold": "Hold"
        case "discharge": "Discharge"
        case "self_consumption", "self_consume": "Self-use"
        case "paused": "Paused"
        default: plan.currentAction.replacingOccurrences(of: "_", with: " ").capitalized
        }
    }
}

private struct BatteryPlanFact: View {
    let title: String
    let value: String
    let color: HexColor
    let theme: EMSTheme

    var body: some View {
        HStack(spacing: 6) {
            Circle()
                .fill(themeColor(color))
                .frame(width: 6, height: 6)
            VStack(alignment: .leading, spacing: 2) {
                Text(title)
                    .font(.caption2.weight(.semibold))
                    .foregroundStyle(themeColor(theme.muted))
                Text(value)
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(themeColor(theme.text))
                    .lineLimit(1)
                    .minimumScaleFactor(0.75)
            }
        }
        .padding(.horizontal, 10)
        .padding(.vertical, 8)
        .background(themeColor(theme.secondaryPanel))
        .clipShape(RoundedRectangle(cornerRadius: 8, style: .continuous))
    }
}

private struct BatteryPlanDetailView: View {
    let plan: BatteryPlanSnapshot
    let story: EnergyStorySnapshot
    let theme: EMSTheme

    var body: some View {
        ZStack {
            themeColor(theme.background).ignoresSafeArea()
            ScrollView {
                VStack(alignment: .leading, spacing: 16) {
                    VStack(alignment: .leading, spacing: 12) {
                        HStack(alignment: .top, spacing: 10) {
                            VStack(alignment: .leading, spacing: 6) {
                                Text("Battery plan")
                                    .font(.caption.weight(.semibold))
                                    .foregroundStyle(themeColor(theme.muted))
                                Text(headline)
                                    .font(.title3.weight(.semibold))
                                    .foregroundStyle(themeColor(theme.text))
                                    .fixedSize(horizontal: false, vertical: true)
                            }
                            Spacer(minLength: 8)
                            StatusBadge(text: statusLabel, color: statusColor, theme: theme)
                        }

                        Text(context)
                            .font(.footnote)
                            .foregroundStyle(themeColor(theme.muted))
                            .fixedSize(horizontal: false, vertical: true)

                        if !plan.summary.isEmpty, plan.summary != headline {
                            Text(plan.summary)
                                .font(.footnote.weight(.semibold))
                                .foregroundStyle(themeColor(theme.text))
                                .fixedSize(horizontal: false, vertical: true)
                        }
                    }
                    .padding(16)
                    .background(themeColor(theme.panel))
                    .clipShape(RoundedRectangle(cornerRadius: 12, style: .continuous))
                    .overlay {
                        RoundedRectangle(cornerRadius: 12, style: .continuous)
                            .stroke(themeColor(theme.line), lineWidth: 1)
                    }

                    if hasEnergyTotals {
                        LazyVGrid(columns: [GridItem(.flexible()), GridItem(.flexible())], spacing: 10) {
                            DetailCell(title: "Grid cost", value: euro(story.totals?.gridCostEur), theme: theme)
                            DetailCell(title: "Powered by you", value: percent(story.totals?.selfSufficiencyPct), theme: theme)
                            DetailCell(title: "From grid", value: kwh(story.totals?.importKwh), theme: theme)
                            DetailCell(title: "Solar", value: kwh(story.totals?.solarKwh), theme: theme)
                            DetailCell(title: "Battery in/out", value: batteryInOut, theme: theme)
                            DetailCell(title: "Top-up", value: topupLabel, theme: theme)
                        }
                    } else {
                        BatteryPlanEmptyNote(
                            icon: "clock.badge.exclamationmark",
                            title: "Energy totals unavailable",
                            message: "EMS still shows the current battery decision; cost and flow totals appear after the next complete strategy snapshot.",
                            theme: theme
                        )
                    }

                    VStack(alignment: .leading, spacing: 12) {
                        Text("Strategy graph")
                            .font(.headline)
                            .foregroundStyle(themeColor(theme.text))
                        BatteryPlanMiniChart(plan: plan, theme: theme, height: 240)
                    }
                    .padding(16)
                    .background(themeColor(theme.panel))
                    .clipShape(RoundedRectangle(cornerRadius: 12, style: .continuous))
                    .overlay {
                        RoundedRectangle(cornerRadius: 12, style: .continuous)
                            .stroke(themeColor(theme.line), lineWidth: 1)
                    }

                    VStack(alignment: .leading, spacing: 12) {
                        Text("What should happen")
                            .font(.headline)
                            .foregroundStyle(themeColor(theme.text))

                        ForEach(decisionRows) { row in
                            BatteryPlanDecisionRow(row: row, theme: theme)
                        }

                        ForEach(Array(plan.warnings.enumerated()), id: \.offset) { _, warning in
                            Label(warning, systemImage: "exclamationmark.triangle")
                                .font(.footnote.weight(.semibold))
                                .foregroundStyle(themeColor(theme.amber))
                                .fixedSize(horizontal: false, vertical: true)
                                .padding(.horizontal, 10)
                                .padding(.vertical, 8)
                                .background(themeColor(theme.amber).opacity(0.13))
                                .clipShape(RoundedRectangle(cornerRadius: 8, style: .continuous))
                        }

                        if plan.warnings.isEmpty {
                            BatteryPlanDecisionRow(
                                row: BatteryPlanDecision(
                                    title: "Warnings",
                                    value: "None",
                                    detail: "No active battery-plan warnings in the latest snapshot.",
                                    color: theme.accent,
                                    icon: "checkmark.circle.fill"
                                ),
                                theme: theme
                            )
                        }
                    }
                    .padding(16)
                    .background(themeColor(theme.panel))
                    .clipShape(RoundedRectangle(cornerRadius: 12, style: .continuous))
                    .overlay {
                        RoundedRectangle(cornerRadius: 12, style: .continuous)
                            .stroke(themeColor(theme.line), lineWidth: 1)
                    }
                }
                .padding()
            }
        }
        .navigationTitle("Battery plan")
        .navigationBarTitleDisplayMode(.inline)
    }

    private var headline: String {
        switch plan.status {
        case "needs_topup":
            if let topup = plan.plannedGridTopupKwh, topup > 0.05 {
                return "EMS plans a \(kwh(topup)) grid top-up before sunset."
            }
            return "EMS plans a grid top-up before sunset."
        case "behind_target":
            return "Battery is behind target; EMS is correcting it."
        case "data_stale":
            return "EMS is paused until fresh data returns."
        case "paused_safely":
            return "Battery automation is paused safely."
        default:
            return "Battery is following the plan."
        }
    }

    private var context: String {
        let level = batteryTargetLabel
        let reason = plan.currentReason.trimmingCharacters(in: .whitespacesAndNewlines)
        return reason.isEmpty ? level : "\(level). \(reason)"
    }

    private var batteryTargetLabel: String {
        if let current = plan.currentSocPct, let target = plan.targetSocPct {
            return "\(percent(current)) to \(percent(target))"
        }
        if let current = plan.currentSocPct {
            return percent(current)
        }
        return "Battery level unavailable"
    }

    private var batteryInOut: String {
        let charge = story.totals?.chargeKwh
        let discharge = story.totals?.dischargeKwh
        guard charge != nil || discharge != nil else { return "--" }
        return "\(kwh(charge))/\(kwh(discharge))"
    }

    private var topupLabel: String {
        guard let topup = plan.plannedGridTopupKwh else { return "--" }
        return topup <= 0.05 ? "None" : kwh(topup)
    }

    private var hasEnergyTotals: Bool {
        guard let totals = story.totals else { return false }
        return [
            totals.importKwh,
            totals.solarKwh,
            totals.chargeKwh,
            totals.dischargeKwh,
            totals.gridCostEur,
            totals.selfSufficiencyPct
        ].contains { $0 != nil }
    }

    private var hasGraphData: Bool {
        plan.graph.forecastSoc.count >= 2 || plan.graph.actualSoc.count >= 2
    }

    private var decisionRows: [BatteryPlanDecision] {
        var rows: [BatteryPlanDecision] = [
            BatteryPlanDecision(
                title: "Now",
                value: actionLabel,
                detail: plan.currentReason.isEmpty ? "EMS is keeping the battery within its safe operating band." : plan.currentReason,
                color: statusColor,
                icon: "bolt.fill"
            ),
            BatteryPlanDecision(
                title: "Reserve",
                value: percent(plan.reserveSocPct),
                detail: hasGraphData
                    ? "The forecast should stay above this floor, even while covering the evening peak."
                    : "EMS has a reserve floor, but the forecast curve needs fresh samples.",
                color: theme.muted,
                icon: "shield.checkered"
            )
        ]

        if let target = plan.targetSocPct {
            let deadline = formattedTime(plan.targetDeadline)
            rows.append(BatteryPlanDecision(
                title: "Night target",
                value: percent(target),
                detail: deadline.map { "EMS should reach this before \($0)." } ?? "EMS should reach this before the night window.",
                color: theme.amber,
                icon: "moon.stars.fill"
            ))
        }

        if let topup = plan.plannedGridTopupKwh, topup > 0.05 {
            rows.append(BatteryPlanDecision(
                title: "Grid top-up",
                value: kwh(topup),
                detail: "Charging should be concentrated in the cheapest useful price window.",
                color: theme.winter,
                icon: "arrow.down.to.line.compact"
            ))
        }

        return rows
    }

    private var statusLabel: String {
        switch plan.status {
        case "on_track": "On track"
        case "needs_topup": "Top-up planned"
        case "behind_target": "Behind"
        case "paused_safely": "Paused"
        case "data_stale": "Data stale"
        default: plan.status.replacingOccurrences(of: "_", with: " ").capitalized
        }
    }

    private var statusColor: HexColor {
        switch plan.status {
        case "on_track": theme.accent
        case "needs_topup", "behind_target": theme.amber
        case "data_stale": theme.error
        default: theme.muted
        }
    }

    private var actionLabel: String {
        switch plan.currentAction {
        case "charge", "grid_charge": "Charge"
        case "solar_charge": "Solar charge"
        case "hold": "Hold"
        case "discharge": "Discharge"
        case "self_consumption", "self_consume": "Self-use"
        case "paused": "Paused"
        default: plan.currentAction.replacingOccurrences(of: "_", with: " ").capitalized
        }
    }

    private func formattedTime(_ timestamp: String?) -> String? {
        guard let timestamp, let date = ISOTimestamp.parse(timestamp) else { return nil }
        return date.formatted(.dateTime.hour().minute())
    }
}

private struct BatteryPlanDecision: Identifiable {
    var id: String { title }
    let title: String
    let value: String
    let detail: String
    let color: HexColor
    let icon: String
}

private struct BatteryPlanDecisionRow: View {
    let row: BatteryPlanDecision
    let theme: EMSTheme

    var body: some View {
        HStack(alignment: .top, spacing: 10) {
            Image(systemName: row.icon)
                .font(.footnote.weight(.semibold))
                .foregroundStyle(themeColor(row.color))
                .frame(width: 20)
            VStack(alignment: .leading, spacing: 3) {
                HStack(alignment: .firstTextBaseline, spacing: 8) {
                    Text(row.title)
                        .font(.caption.weight(.semibold))
                        .foregroundStyle(themeColor(theme.muted))
                    Spacer(minLength: 8)
                    Text(row.value)
                        .font(.footnote.weight(.semibold))
                        .foregroundStyle(themeColor(theme.text))
                        .lineLimit(1)
                        .minimumScaleFactor(0.75)
                }
                Text(row.detail)
                    .font(.footnote)
                    .foregroundStyle(themeColor(theme.muted))
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        .padding(10)
        .background(themeColor(theme.secondaryPanel))
        .clipShape(RoundedRectangle(cornerRadius: 8, style: .continuous))
    }
}

private struct BatteryPlanEmptyNote: View {
    let icon: String
    let title: String
    let message: String
    let theme: EMSTheme

    var body: some View {
        HStack(alignment: .top, spacing: 10) {
            Image(systemName: icon)
                .font(.footnote.weight(.semibold))
                .foregroundStyle(themeColor(theme.muted))
                .frame(width: 20)
            VStack(alignment: .leading, spacing: 4) {
                Text(title)
                    .font(.footnote.weight(.semibold))
                    .foregroundStyle(themeColor(theme.text))
                Text(message)
                    .font(.footnote)
                    .foregroundStyle(themeColor(theme.muted))
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        .padding(12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(themeColor(theme.panel))
        .clipShape(RoundedRectangle(cornerRadius: 12, style: .continuous))
        .overlay {
            RoundedRectangle(cornerRadius: 12, style: .continuous)
                .stroke(themeColor(theme.line), lineWidth: 1)
        }
    }
}

private struct BatteryPlanMiniChart: View {
    let plan: BatteryPlanSnapshot
    let theme: EMSTheme
    var height: CGFloat = 150

    private var series: [BatteryPlanPoint] {
        plan.graph.forecastSoc.isEmpty ? plan.graph.actualSoc : plan.graph.forecastSoc
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            VStack(alignment: .leading, spacing: 3) {
                Text("Battery confidence")
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(themeColor(theme.muted))
                Text(plan.deviation.message)
                    .font(.caption2)
                    .foregroundStyle(themeColor(deviationColor))
                    .fixedSize(horizontal: false, vertical: true)
            }

            if series.count >= 2 || plan.graph.actualSoc.count >= 2 {
                GeometryReader { proxy in
                    ZStack(alignment: .bottomLeading) {
                        grid(size: proxy.size)
                        priceWindowBands(size: proxy.size)
                        actionStrip(size: proxy.size)
                        solarBars(size: proxy.size)
                        horizontalLine(value: plan.reserveSocPct, size: proxy.size, color: theme.muted, dashed: true)
                        if let target = plan.targetSocPct {
                            horizontalLine(value: target, size: proxy.size, color: theme.amber, dashed: true)
                        }
                        socLine(points: plan.graph.forecastSoc, size: proxy.size, color: theme.amber, dashed: true)
                        socLine(points: plan.graph.actualSoc, size: proxy.size, color: theme.accent, dashed: false)
                    }
                }
                .frame(height: height)
                .accessibilityElement(children: .ignore)
                .accessibilityLabel("Battery plan graph")
                .accessibilityValue(accessibilityValue)

                ViewThatFits(in: .horizontal) {
                    HStack(spacing: 12) {
                        chartLegend
                    }
                    VStack(alignment: .leading, spacing: 7) {
                        chartLegend
                    }
                }
            } else {
                Text("Plan graph appears once the EMS has enough fresh data.")
                    .font(.footnote)
                    .foregroundStyle(themeColor(theme.muted))
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }

    @ViewBuilder
    private var chartLegend: some View {
        LegendLine(label: "Actual", color: theme.accent, dashed: false, theme: theme)
        LegendLine(label: "Forecast", color: theme.amber, dashed: true, theme: theme)
        BatteryPlanLegendBlock(label: "Cheap", color: theme.accent, theme: theme)
        LegendLine(label: "Reserve", color: theme.muted, dashed: true, theme: theme)
    }

    private var deviationColor: HexColor {
        switch plan.deviation.status {
        case "behind_forecast": theme.amber
        case "missing": theme.muted
        default: theme.accent
        }
    }

    private var accessibilityValue: String {
        [
            plan.currentSocPct.map { "now \(Int($0)) percent" },
            plan.targetSocPct.map { "target \(Int($0)) percent" },
            "status \(plan.status.replacingOccurrences(of: "_", with: " "))"
        ]
        .compactMap(\.self)
        .joined(separator: ", ")
    }

    private func grid(size: CGSize) -> some View {
        ZStack {
            ForEach([0.0, 50.0, 100.0], id: \.self) { value in
                Path { path in
                    let y = y(value, size: size)
                    path.move(to: CGPoint(x: 0, y: y))
                    path.addLine(to: CGPoint(x: size.width, y: y))
                }
                .stroke(themeColor(theme.line), lineWidth: 1)
            }
        }
    }

    private func actionStrip(size: CGSize) -> some View {
        // Position each block by its real time span on the shared timestamp axis, so the coloured
        // strip lines up with the SoC curve and the price bands above it (not equal-width).
        ZStack(alignment: .topLeading) {
            ForEach(plan.graph.plannedActions) { block in
                if let x0 = x(block.start, size: size), let x1 = x(block.end, size: size) {
                    RoundedRectangle(cornerRadius: 2, style: .continuous)
                        .fill(themeColor(color(for: block.action)).opacity(0.82))
                        .frame(width: max(2, x1 - x0), height: 16)
                        .offset(x: x0, y: size.height - 16)
                }
            }
        }
        .frame(width: size.width, height: size.height, alignment: .topLeading)
    }

    private func priceWindowBands(size: CGSize) -> some View {
        let bands = plan.graph.priceWindows.compactMap { window -> (start: Double, width: Double)? in
            guard let start = x(window.start, size: size), let end = x(window.end, size: size) else {
                return nil
            }
            return (start, max(2, end - start))
        }

        return ZStack(alignment: .leading) {
            ForEach(Array(bands.enumerated()), id: \.offset) { _, band in
                RoundedRectangle(cornerRadius: 3, style: .continuous)
                    .fill(themeColor(theme.accent).opacity(0.12))
                    .frame(width: band.width, height: max(size.height - 22, 1))
                    .offset(x: band.start)
            }
        }
        .frame(width: size.width, height: size.height, alignment: .topLeading)
    }

    private func solarBars(size: CGSize) -> some View {
        // Place each solar bar at its timestamp (not evenly spaced) so the solar hump sits under the
        // clock time it belongs to and aligns with the cheap-price bands.
        let peak = max(plan.graph.solar.map(\.forecastW).max() ?? 0, 1)
        let plotH = max(size.height - 22, 1)
        let barW = solarBarWidth(size: size)
        return ZStack(alignment: .topLeading) {
            ForEach(plan.graph.solar) { point in
                if let x0 = x(point.ts, size: size) {
                    let h = max(2, plotH * 0.26 * min(max(point.forecastW / peak, 0), 1))
                    RoundedRectangle(cornerRadius: 2, style: .continuous)
                        .fill(themeColor(theme.amber).opacity(0.22))
                        .frame(width: barW, height: h)
                        .offset(x: x0, y: plotH - h)
                }
            }
        }
        .frame(width: size.width, height: size.height, alignment: .topLeading)
    }

    private func solarBarWidth(size: CGSize) -> Double {
        let xs = plan.graph.solar.compactMap { x($0.ts, size: size) }.sorted()
        guard xs.count > 1 else { return 4 }
        let minGap = zip(xs, xs.dropFirst()).map { $1 - $0 }.filter { $0 > 0 }.min() ?? 6
        return max(2, minGap * 0.7)
    }

    private func socLine(points: [BatteryPlanPoint], size: CGSize, color: HexColor, dashed: Bool) -> some View {
        // Break the line at gaps (missing SoC) and place every point on the shared TIMESTAMP axis,
        // so actual and forecast align in time with each other and with the price bands — instead
        // of each series being stretched across the full width by index (finding #2/#3).
        let runs = socRuns(points, size: size)
        return ZStack {
            ForEach(Array(runs.enumerated()), id: \.offset) { _, run in
                if run.count >= 2 {
                    Path { path in
                        path.move(to: run[0])
                        run.dropFirst().forEach { path.addLine(to: $0) }
                    }
                    .stroke(
                        themeColor(color),
                        style: StrokeStyle(lineWidth: 3, lineCap: .round, lineJoin: .round,
                                           dash: dashed ? [6, 5] : [])
                    )
                } else if let point = run.first {
                    // A lone point between gaps still shows, as a dot rather than a vanished segment.
                    Circle().fill(themeColor(color)).frame(width: 5, height: 5).position(point)
                }
            }
        }
    }

    private func socRuns(_ points: [BatteryPlanPoint], size: CGSize) -> [[CGPoint]] {
        var runs: [[CGPoint]] = []
        var cur: [CGPoint] = []
        for point in points {
            if let soc = point.socPct, let px = x(point.ts, size: size) {
                cur.append(CGPoint(x: px, y: y(soc, size: size)))
            } else if !cur.isEmpty {
                runs.append(cur)
                cur = []
            }
        }
        if !cur.isEmpty { runs.append(cur) }
        return runs
    }

    private func horizontalLine(value: Double, size: CGSize, color: HexColor, dashed: Bool) -> some View {
        Path { path in
            let y = y(value, size: size)
            path.move(to: CGPoint(x: 0, y: y))
            path.addLine(to: CGPoint(x: size.width, y: y))
        }
        .stroke(themeColor(color), style: StrokeStyle(lineWidth: 1.5, dash: dashed ? [5, 5] : []))
    }

    private func color(for action: String) -> HexColor {
        switch action {
        case "solar_charge": theme.accent
        case "grid_charge": theme.winter
        case "discharge": theme.amber
        case "self_consume", "self_consumption": theme.muted
        case "hold": theme.line
        default: theme.secondaryPanel
        }
    }

    private func x(_ timestamp: String, size: CGSize) -> Double? {
        guard let start = ISOTimestamp.parse(plan.windowStart),
              let end = ISOTimestamp.parse(plan.windowEnd),
              let current = ISOTimestamp.parse(timestamp),
              end > start else {
            return nil
        }
        let ratio = current.timeIntervalSince(start) / end.timeIntervalSince(start)
        return min(max(ratio, 0), 1) * size.width
    }

    private func y(_ value: Double, size: CGSize) -> Double {
        let plotHeight = max(size.height - 22, 1)
        return (1 - min(max(value, 0), 100) / 100) * plotHeight
    }

}

private struct BatteryPlanLegendBlock: View {
    let label: String
    let color: HexColor
    let theme: EMSTheme

    var body: some View {
        HStack(spacing: 5) {
            RoundedRectangle(cornerRadius: 2, style: .continuous)
                .fill(themeColor(color).opacity(0.32))
                .frame(width: 18, height: 9)

            Text(label)
                .font(.caption2)
                .foregroundStyle(themeColor(theme.muted))
        }
    }
}

private struct StrategyCard: View {
    let strategy: StrategySnapshot
    let theme: EMSTheme

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(alignment: .top, spacing: 12) {
                VStack(alignment: .leading, spacing: 4) {
                    Text("Strategy")
                        .font(.headline)
                        .foregroundStyle(themeColor(theme.text))
                    Text(humanizedMode)
                        .font(.subheadline.weight(.semibold))
                        .foregroundStyle(themeColor(theme.accent))
                }
                Spacer(minLength: 8)
                if strategy.auto {
                    StatusBadge(text: "Auto", color: theme.accent, theme: theme)
                }
            }

            Text(strategy.reason)
                .font(.subheadline)
                .foregroundStyle(themeColor(theme.text))
                .fixedSize(horizontal: false, vertical: true)

            if !strategy.summary.isEmpty {
                Text(strategy.summary)
                    .font(.footnote)
                    .foregroundStyle(themeColor(theme.muted))
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        .padding(16)
        .background(themeColor(theme.panel))
        .clipShape(RoundedRectangle(cornerRadius: 12, style: .continuous))
        .overlay {
            RoundedRectangle(cornerRadius: 12, style: .continuous)
                .stroke(themeColor(theme.line), lineWidth: 1)
        }
    }

    private var humanizedMode: String {
        switch strategy.active {
        case "summer":
            "Solar-first"
        case "winter":
            "Price-smart"
        default:
            strategy.active.capitalized
        }
    }
}

private struct ScoreStrip: View {
    let scores: [ReportScore]
    let theme: EMSTheme

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Today so far")
                .font(.caption.weight(.semibold))
                .foregroundStyle(themeColor(theme.muted))

            Text(verdict)
                .font(.title3.weight(.semibold))
                .foregroundStyle(themeColor(theme.text))
                .fixedSize(horizontal: false, vertical: true)

            LazyVGrid(columns: [GridItem(.flexible()), GridItem(.flexible()), GridItem(.flexible())], spacing: 10) {
                ForEach(scores.prefix(3)) { score in
                    ScoreRing(score: score, theme: theme)
                }
            }
        }
        .padding(16)
        .background(themeColor(theme.panel))
        .clipShape(RoundedRectangle(cornerRadius: 12, style: .continuous))
        .overlay {
            RoundedRectangle(cornerRadius: 12, style: .continuous)
                .stroke(themeColor(theme.line), lineWidth: 1)
        }
    }

    private func value(_ key: String) -> Double? {
        scores.first { $0.key == key }?.value
    }

    // One plain-language read on the day, tied to the two goals: use your own sun, and buy grid
    // power cheap. Self-consumption ≈ sun captured; best-price ≈ bought at the right times.
    private var verdict: String {
        guard let sun = value("self_consumption"), let price = value("best_price") else {
            return "Tracking how well today's energy is used."
        }
        switch min(sun, price) {
        case 80...:
            return "Using your energy well — mostly your own sun, and grid power bought at the cheapest times."
        case 50..<80:
            return "A solid energy day — some room to use more sun or buy cheaper."
        default:
            return "Room to do better — more power came from the grid at pricier times today."
        }
    }
}

private struct ScoreRing: View {
    let score: ReportScore
    let theme: EMSTheme

    var body: some View {
        VStack(spacing: 8) {
            ZStack {
                Circle()
                    .stroke(themeColor(theme.line), lineWidth: 7)
                Circle()
                    .trim(from: 0, to: progress)
                    .stroke(themeColor(theme.accent), style: StrokeStyle(lineWidth: 7, lineCap: .round))
                    .rotationEffect(.degrees(-90))
                Text(scoreText)
                    .font(.caption.weight(.bold))
                    .foregroundStyle(themeColor(theme.text))
                    .minimumScaleFactor(0.7)
            }
            .frame(width: 58, height: 58)

            VStack(spacing: 2) {
                Text(score.label)
                    .font(.caption2.weight(.semibold))
                    .foregroundStyle(themeColor(theme.text))
                Text(caption)
                    .font(.caption2)
                    .foregroundStyle(themeColor(theme.muted))
                    .minimumScaleFactor(0.85)
            }
            .multilineTextAlignment(.center)
            .lineLimit(2)
            .frame(minHeight: 30)
        }
        .frame(maxWidth: .infinity)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(score.label)
        .accessibilityValue("\(scoreAccessibilityValue). \(caption).")
    }

    // A short, always-fitting plain meaning under each ring (the full sentence lives in Insights).
    private var caption: String {
        switch score.key {
        case "self_consumption": "of your sun kept"
        case "co2": "less than no-solar"
        case "best_price": "in cheap hours"
        default: score.unit ?? ""
        }
    }

    private var progress: Double {
        min(max((score.value ?? 0) / 100, 0), 1)
    }

    private var scoreText: String {
        guard let value = score.value else { return "--" }
        return "\(value.formatted(.number.precision(.fractionLength(0))))%"
    }

    private var scoreAccessibilityValue: String {
        guard let value = score.value else { return "not available" }
        return "\(Int(value)) out of 100"
    }
}

private struct BatteryPanel: View {
    let snapshot: MobileDashboardSnapshot
    let theme: EMSTheme

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack {
                Label("Battery", systemImage: "battery.75percent")
                    .font(.headline)
                    .foregroundStyle(themeColor(theme.text))
                Spacer()
                Text(snapshot.decision.intent?.replacingOccurrences(of: "_", with: " ") ?? "automatic")
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(themeColor(theme.muted))
            }

            GeometryReader { proxy in
                ZStack(alignment: .leading) {
                    Capsule().fill(themeColor(theme.line))
                    Capsule().fill(themeColor(theme.accent))
                        .frame(width: proxy.size.width * fill)
                }
            }
            .frame(height: 14)

            HStack(spacing: 10) {
                MetricPill(title: "Battery", value: watts(snapshot.status.batteryPowerW), theme: theme)
                MetricPill(title: "Grid", value: watts(snapshot.status.gridPowerW), theme: theme)
                MetricPill(title: "Solar", value: watts(snapshot.status.solarPowerW), theme: theme)
            }
        }
        .padding(16)
        .background(themeColor(theme.panel))
        .clipShape(RoundedRectangle(cornerRadius: 12, style: .continuous))
        .overlay {
            RoundedRectangle(cornerRadius: 12, style: .continuous)
                .stroke(themeColor(theme.line), lineWidth: 1)
        }
    }

    private var fill: Double {
        min(max((snapshot.battery.aggregate?.socPct ?? snapshot.status.socPct ?? 0) / 100, 0), 1)
    }
}

private struct EnergyStoryPanel: View {
    let snapshot: MobileDashboardSnapshot
    let theme: EMSTheme

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Label("Energy story", systemImage: "sparkles")
                .font(.headline)
                .foregroundStyle(themeColor(theme.text))

            Text(snapshot.energyStory.headline ?? snapshot.chargeNeed.reason ?? "The plan is available once the EMS has enough data.")
                .font(.subheadline)
                .foregroundStyle(themeColor(theme.text))
                .fixedSize(horizontal: false, vertical: true)

            if let message = snapshot.energyStory.onTrack?.message ?? snapshot.energyStory.recentReview?.message {
                Text(message)
                    .font(.footnote)
                    .foregroundStyle(themeColor(theme.muted))
                    .fixedSize(horizontal: false, vertical: true)
            }

            if let markers = snapshot.energyStory.trustMarkers, !markers.isEmpty {
                FlowLayout(items: markers, theme: theme)
            }
        }
        .padding(16)
        .background(themeColor(theme.panel))
        .clipShape(RoundedRectangle(cornerRadius: 12, style: .continuous))
        .overlay {
            RoundedRectangle(cornerRadius: 12, style: .continuous)
                .stroke(themeColor(theme.line), lineWidth: 1)
        }
    }
}

private struct EnergyGraphsPanel: View {
    let story: EnergyStorySnapshot
    let theme: EMSTheme
    @State private var expanded = false

    private var timeline: [StorySlot] {
        story.recent + story.slots
    }

    private var priceAccessibilityValue: String {
        guard let price = story.currentPriceEurPerKwh else { return "not available" }
        return "currently \(price.formatted(.currency(code: "EUR"))) per kilowatt hour"
    }

    private var solarAccessibilityValue: String {
        let peak = timeline.map(\.solarW).max() ?? 0
        guard peak > 0 else { return "no solar production expected" }
        return "peak \(Int(peak)) watts"
    }

    var body: some View {
        if !timeline.isEmpty {
            DisclosureGroup(isExpanded: $expanded) {
                VStack(alignment: .leading, spacing: 14) {
                    BatteryForecastChart(story: story, slots: timeline, recentCount: story.recent.count, theme: theme)
                TrackLabel("Electricity price")
                BarTrack(
                    values: timeline.map { max($0.eurPerKwh ?? 0, 0) },
                    recentCount: story.recent.count,
                    fill: theme.accent,
                    actualFill: theme.winter,
                    theme: theme
                )
                .accessibilityElement(children: .ignore)
                .accessibilityLabel("Electricity price over time")
                .accessibilityValue(priceAccessibilityValue)

                TrackLabel("Battery actions")
                ActionTrack(slots: timeline, recentCount: story.recent.count, theme: theme)

                TrackLabel(story.recent.isEmpty ? "Solar forecast" : "Solar produced → forecast")
                BarTrack(
                    values: timeline.map { max($0.solarW, 0) },
                    recentCount: story.recent.count,
                    fill: theme.amber,
                    actualFill: theme.accent,
                    theme: theme
                )
                .accessibilityElement(children: .ignore)
                .accessibilityLabel(story.recent.isEmpty ? "Solar forecast over time" : "Solar produced and forecast over time")
                .accessibilityValue(solarAccessibilityValue)

                    ActionLegend(actions: uniqueActions(in: timeline), theme: theme)
                }
                .padding(.top, 12)
            } label: {
                Label("Plan tracks — the 24-hour plan", systemImage: "chart.xyaxis.line")
                    .font(.headline)
                    .foregroundStyle(themeColor(theme.text))
            }
            .tint(themeColor(theme.muted))
            .padding(16)
            .background(themeColor(theme.panel))
            .clipShape(RoundedRectangle(cornerRadius: 12, style: .continuous))
            .overlay {
                RoundedRectangle(cornerRadius: 12, style: .continuous)
                    .stroke(themeColor(theme.line), lineWidth: 1)
            }
        }
    }
}

private struct BatteryForecastChart: View {
    let story: EnergyStorySnapshot
    let slots: [StorySlot]
    let recentCount: Int
    let theme: EMSTheme

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Text("Battery level")
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(themeColor(theme.muted))
                Spacer()
                if let target = story.targetSocPct {
                    Text("\(target.formatted(.number.precision(.fractionLength(0))))% night target")
                        .font(.caption2.weight(.semibold))
                        .foregroundStyle(themeColor(theme.amber))
                }
            }

            GeometryReader { proxy in
                ZStack {
                    chartGrid(size: proxy.size)
                    targetLine(size: proxy.size)
                    reserveLine(size: proxy.size)
                    socLine(size: proxy.size)
                    nowDivider(size: proxy.size)
                }
            }
            .frame(height: 150)

            HStack(spacing: 12) {
                LegendLine(label: recentCount > 0 ? "Measured" : "Forecast", color: theme.accent, dashed: false, theme: theme)
                LegendLine(label: "Forecast", color: theme.amber, dashed: true, theme: theme)
                LegendLine(label: "Reserve", color: theme.muted, dashed: true, theme: theme)
            }
        }
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("Battery level forecast")
        .accessibilityValue(batteryForecastAccessibilityValue)
    }

    private var batteryForecastAccessibilityValue: String {
        guard let current = story.currentSocPct else { return "not available" }
        return "now \(Int(current))%"
    }

    private func chartGrid(size: CGSize) -> some View {
        ZStack {
            ForEach([0.0, 50.0, 100.0], id: \.self) { value in
                Path { path in
                    let y = y(value, size: size)
                    path.move(to: CGPoint(x: 0, y: y))
                    path.addLine(to: CGPoint(x: size.width, y: y))
                }
                .stroke(themeColor(theme.line), lineWidth: 1)
            }
        }
    }

    private func targetLine(size: CGSize) -> some View {
        Group {
            if let target = story.targetSocPct {
                dashedHorizontal(value: target, size: size, color: theme.amber)
            }
        }
    }

    private func reserveLine(size: CGSize) -> some View {
        dashedHorizontal(value: story.reserveSocPct ?? 10, size: size, color: theme.muted)
    }

    private func socLine(size: CGSize) -> some View {
        let points = slots.enumerated().compactMap { index, slot -> CGPoint? in
            guard let soc = slot.socPct else { return nil }
            return CGPoint(x: x(index, size: size), y: y(soc, size: size))
        }
        return ZStack {
            if points.count >= 2 {
                Path { path in
                    path.move(to: points[0])
                    points.dropFirst().forEach { path.addLine(to: $0) }
                }
                .stroke(themeColor(theme.amber), style: StrokeStyle(lineWidth: 3, lineCap: .round, lineJoin: .round, dash: [6, 5]))
            }
            if recentCount > 1 {
                let actualPoints = Array(points.prefix(recentCount))
                Path { path in
                    path.move(to: actualPoints[0])
                    actualPoints.dropFirst().forEach { path.addLine(to: $0) }
                }
                .stroke(themeColor(theme.accent), style: StrokeStyle(lineWidth: 3, lineCap: .round, lineJoin: .round))
            }
        }
    }

    private func nowDivider(size: CGSize) -> some View {
        Group {
            if recentCount > 0 && recentCount < slots.count {
                Path { path in
                    let x = x(recentCount - 1, size: size)
                    path.move(to: CGPoint(x: x, y: 0))
                    path.addLine(to: CGPoint(x: x, y: size.height))
                }
                .stroke(themeColor(theme.line), lineWidth: 1)
            }
        }
    }

    private func dashedHorizontal(value: Double, size: CGSize, color: HexColor) -> some View {
        Path { path in
            let y = y(value, size: size)
            path.move(to: CGPoint(x: 0, y: y))
            path.addLine(to: CGPoint(x: size.width, y: y))
        }
        .stroke(themeColor(color), style: StrokeStyle(lineWidth: 1.5, dash: [5, 5]))
    }

    private func x(_ index: Int, size: CGSize) -> Double {
        guard slots.count > 1 else { return 0 }
        return Double(index) / Double(slots.count - 1) * size.width
    }

    private func y(_ value: Double, size: CGSize) -> Double {
        (1 - min(max(value, 0), 100) / 100) * size.height
    }
}

private struct BarTrack: View {
    let values: [Double]
    let recentCount: Int
    let fill: HexColor
    let actualFill: HexColor
    let theme: EMSTheme

    var body: some View {
        GeometryReader { proxy in
            HStack(alignment: .bottom, spacing: 2) {
                ForEach(Array(values.enumerated()), id: \.offset) { index, value in
                    RoundedRectangle(cornerRadius: 2, style: .continuous)
                        .fill(themeColor(index < recentCount ? actualFill : fill).opacity(index < recentCount ? 0.95 : 0.72))
                        .frame(height: max(3, proxy.size.height * normalized(value)))
                }
            }
            .frame(maxHeight: .infinity, alignment: .bottom)
        }
        .frame(height: 50)
        .padding(.vertical, 4)
    }

    private func normalized(_ value: Double) -> Double {
        let maxValue = max(values.max() ?? 0, 0.01)
        return min(max(value / maxValue, 0), 1)
    }
}

private struct ActionTrack: View {
    let slots: [StorySlot]
    let recentCount: Int
    let theme: EMSTheme

    var body: some View {
        HStack(spacing: 2) {
            ForEach(Array(slots.enumerated()), id: \.offset) { index, slot in
                RoundedRectangle(cornerRadius: 2, style: .continuous)
                    .fill(themeColor(color(for: slot.action)).opacity(index < recentCount ? 1 : 0.82))
                    .frame(height: 24)
            }
        }
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("Battery actions over the last day")
        .accessibilityValue(actionsAccessibilityValue)
    }

    private var actionsAccessibilityValue: String {
        guard !slots.isEmpty else { return "no data available" }
        let counts = Dictionary(grouping: slots, by: \.action).mapValues(\.count)
        return counts
            .sorted { $0.value > $1.value }
            .map { "\(humanizedAction($0.key)) \($0.value)" }
            .joined(separator: ", ")
    }

    private func humanizedAction(_ action: String) -> String {
        switch action {
        case "solar_charge":
            "Charge from solar"
        case "grid_charge":
            "Charge from grid"
        case "discharge":
            "Power the house"
        case "self_consume":
            "Use solar first"
        case "hold":
            "Hold"
        case "idle":
            "Idle"
        default:
            action.replacingOccurrences(of: "_", with: " ")
        }
    }

    private func color(for action: String) -> HexColor {
        switch action {
        case "solar_charge":
            theme.accent
        case "grid_charge":
            theme.winter
        case "discharge":
            theme.amber
        case "self_consume":
            theme.muted
        case "hold":
            theme.line
        default:
            theme.secondaryPanel
        }
    }
}

private struct ActionLegend: View {
    let actions: [String]
    let theme: EMSTheme

    // A true legend: each row is the SAME colour swatch the Battery-actions bars use, next to its
    // meaning — so the stripes are readable. Two columns to stay compact; a thin outline keeps the
    // faint hold/idle swatches locatable on the dark panel.
    var body: some View {
        LazyVGrid(
            columns: [GridItem(.flexible(), alignment: .leading), GridItem(.flexible(), alignment: .leading)],
            alignment: .leading,
            spacing: 8
        ) {
            ForEach(actions, id: \.self) { action in
                HStack(spacing: 7) {
                    RoundedRectangle(cornerRadius: 3, style: .continuous)
                        .fill(themeColor(color(for: action)))
                        .frame(width: 14, height: 14)
                        .overlay {
                            RoundedRectangle(cornerRadius: 3, style: .continuous)
                                .stroke(themeColor(theme.muted).opacity(0.4), lineWidth: 1)
                        }
                    Text(label(for: action))
                        .font(.caption2)
                        .foregroundStyle(themeColor(theme.muted))
                        .lineLimit(1)
                        .minimumScaleFactor(0.8)
                    Spacer(minLength: 0)
                }
                .accessibilityElement(children: .ignore)
                .accessibilityLabel(label(for: action))
            }
        }
    }

    private func label(for action: String) -> String {
        switch action {
        case "solar_charge": "Charge from solar"
        case "grid_charge": "Charge from grid"
        case "discharge": "Power the house"
        case "self_consume": "Use solar first"
        case "hold": "Hold"
        case "idle": "Idle"
        default: action.replacingOccurrences(of: "_", with: " ")
        }
    }

    // Must match ActionTrack.color(for:) exactly, or the legend lies about the bars.
    private func color(for action: String) -> HexColor {
        switch action {
        case "solar_charge": theme.accent
        case "grid_charge": theme.winter
        case "discharge": theme.amber
        case "self_consume": theme.muted
        case "hold": theme.line
        default: theme.secondaryPanel
        }
    }
}

private struct TrackLabel: View {
    let text: String

    init(_ text: String) {
        self.text = text
    }

    var body: some View {
        Text(text)
            .font(.caption.weight(.semibold))
            .foregroundStyle(.secondary)
    }
}

private struct LegendLine: View {
    let label: String
    let color: HexColor
    let dashed: Bool
    let theme: EMSTheme

    var body: some View {
        HStack(spacing: 5) {
            Path { path in
                path.move(to: CGPoint(x: 0, y: 4))
                path.addLine(to: CGPoint(x: 18, y: 4))
            }
            .stroke(themeColor(color), style: StrokeStyle(lineWidth: 2, dash: dashed ? [4, 3] : []))
            .frame(width: 18, height: 8)

            Text(label)
                .font(.caption2)
                .foregroundStyle(themeColor(theme.muted))
        }
    }
}

private struct FinancePanel: View {
    let finance: FinanceSnapshot
    let savings: SavingsSnapshot
    let theme: EMSTheme

    var body: some View {
        HStack(spacing: 10) {
            MetricPill(title: "Saved", value: euro(finance.totals.savedEur ?? savings.todayEur), theme: theme)
            MetricPill(title: "Grid cost", value: euro(finance.totals.gridCostEur), theme: theme)
            MetricPill(title: "Import", value: kwh(finance.totals.gridImportKwh), theme: theme)
        }
        .padding(16)
        .background(themeColor(theme.panel))
        .clipShape(RoundedRectangle(cornerRadius: 12, style: .continuous))
        .overlay {
            RoundedRectangle(cornerRadius: 12, style: .continuous)
                .stroke(themeColor(theme.line), lineWidth: 1)
        }
    }
}

private struct AlertsPanel: View {
    let alerts: [DashboardAlert]
    let theme: EMSTheme

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            ForEach(orderedAlerts) { alert in
                Label(alert.message, systemImage: icon(for: alert.severity))
                    .font(.footnote)
                    .foregroundStyle(themeColor(color(for: alert.severity)))
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        .padding(14)
        .background(themeColor(theme.panel))
        .clipShape(RoundedRectangle(cornerRadius: 12, style: .continuous))
        .overlay {
            RoundedRectangle(cornerRadius: 12, style: .continuous)
                .stroke(themeColor(strokeColor).opacity(0.55), lineWidth: 1)
        }
    }

    private var orderedAlerts: [DashboardAlert] {
        alerts.sorted { rank(for: $0.severity) < rank(for: $1.severity) }
    }

    private var strokeColor: HexColor {
        if !alerts.filter({ $0.severity == "critical" }).isEmpty { return theme.error }
        if !alerts.filter({ $0.severity == "warning" }).isEmpty { return theme.amber }
        return theme.line
    }

    private func rank(for severity: String) -> Int {
        switch severity {
        case "critical":
            0
        case "warning":
            1
        default:
            2
        }
    }

    private func color(for severity: String) -> HexColor {
        switch severity {
        case "critical":
            theme.error
        case "warning":
            theme.amber
        default:
            theme.muted
        }
    }

    private func icon(for severity: String) -> String {
        switch severity {
        case "critical":
            "exclamationmark.octagon.fill"
        case "warning":
            "exclamationmark.triangle"
        default:
            "info.circle"
        }
    }
}

private struct DetailGrid: View {
    let snapshot: MobileDashboardSnapshot
    let theme: EMSTheme

    var body: some View {
        LazyVGrid(columns: [GridItem(.flexible()), GridItem(.flexible())], spacing: 10) {
            DetailCell(title: "House", value: watts(snapshot.status.houseLoadW), theme: theme)
            DetailCell(title: "Target %", value: percent(snapshot.chargeNeed.targetSocPct ?? snapshot.energyStory.targetSocPct), theme: theme)
            DetailCell(title: "Reserve", value: kwh(snapshot.chargeNeed.reserveKwh), theme: theme)
            DetailCell(title: "Data", value: snapshot.alerts.dataQuality ?? "unknown", theme: theme)
            DetailCell(title: "Towers", value: towers(snapshot.battery.aggregate), theme: theme)
            DetailCell(title: "Current mode", value: snapshot.battery.currentMode ?? "--", theme: theme)
        }
    }
}

private struct DetailCell: View {
    let title: String
    let value: String
    let theme: EMSTheme

    var body: some View {
        VStack(alignment: .leading, spacing: 5) {
            Text(title)
                .font(.caption2.weight(.semibold))
                .foregroundStyle(themeColor(theme.muted))
            Text(value)
                .font(.footnote.weight(.semibold))
                .foregroundStyle(themeColor(theme.text))
                .lineLimit(2)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(10)
        .background(themeColor(theme.secondaryPanel))
        .clipShape(RoundedRectangle(cornerRadius: 8, style: .continuous))
    }
}

private struct MetricPill: View {
    let title: String
    let value: String
    let theme: EMSTheme

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(title)
                .font(.caption2.weight(.semibold))
                .foregroundStyle(themeColor(theme.muted))
                .lineLimit(1)
                .minimumScaleFactor(0.7)
            Text(value)
                .font(.subheadline.weight(.semibold))
                .foregroundStyle(themeColor(theme.text))
                .lineLimit(1)
                .minimumScaleFactor(0.6)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.horizontal, 10)
        .padding(.vertical, 9)
        .background(themeColor(theme.secondaryPanel))
        .clipShape(RoundedRectangle(cornerRadius: 8, style: .continuous))
    }
}

private struct StatusBadge: View {
    let text: String
    let color: HexColor
    let theme: EMSTheme

    var body: some View {
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
}

private struct FlowLayout: View {
    let items: [String]
    let theme: EMSTheme

    var body: some View {
        ViewThatFits(in: .horizontal) {
            HStack(spacing: 8) { chips }
            VStack(alignment: .leading, spacing: 8) { chips }
        }
    }

    private var chips: some View {
        ForEach(items, id: \.self) { item in
            Text(item)
                .font(.caption2.weight(.semibold))
                .foregroundStyle(themeColor(theme.accent))
                .padding(.horizontal, 8)
                .padding(.vertical, 5)
                .background(themeColor(theme.accent).opacity(0.12))
                .clipShape(Capsule())
        }
    }
}

// Turn a raw battery-mode/intent code into a short homeowner word; empty/unknown → "Auto".
private func humanizeMode(_ raw: String?) -> String {
    guard let raw, !raw.isEmpty, raw != "--" else { return "Auto" }
    switch raw {
    case "self_consumption", "auto", "allow_self_consumption": return "Self-use"
    case "grid_charge", "grid_charge_to_target", "charge": return "Grid charge"
    case "discharge", "discharge_for_load": return "Discharging"
    case "hold", "hold_reserve": return "Holding"
    case "idle": return "Idle"
    default: return raw.replacingOccurrences(of: "_", with: " ").capitalized
    }
}

private func percent(_ value: Double?) -> String {
    guard let value else { return "--" }
    return "\(value.formatted(.number.precision(.fractionLength(0...1))))%"
}

private func watts(_ value: Double?) -> String {
    guard let value else { return "--" }
    return "\(value.formatted(.number.precision(.fractionLength(0)))) W"
}

private func kwh(_ value: Double?) -> String {
    guard let value else { return "--" }
    return "\(value.formatted(.number.precision(.fractionLength(0...1)))) kWh"
}

private func euro(_ value: Double?) -> String {
    guard let value else { return "--" }
    return value.formatted(.currency(code: "EUR"))
}

private func price(_ value: Double?) -> String {
    guard let value else { return "--" }
    return "\(value.formatted(.currency(code: "EUR")))/kWh"
}

private func towers(_ aggregate: BatteryAggregate?) -> String {
    guard let aggregate else { return "--" }
    if let online = aggregate.onlineTowers, let total = aggregate.totalTowers {
        return "\(online)/\(total) online"
    }
    if let online = aggregate.onlineTowers {
        return "\(online) online"
    }
    return "--"
}

private func uniqueActions(in slots: [StorySlot]) -> [String] {
    let preferred = ["solar_charge", "grid_charge", "discharge", "self_consume", "hold", "idle"]
    let present = Set(slots.map(\.action))
    return preferred.filter { present.contains($0) } + present.subtracting(preferred).sorted()
}
