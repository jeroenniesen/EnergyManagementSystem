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

    var body: some View {
        FlowLayout(items: actions.map(label(for:)), theme: theme)
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
