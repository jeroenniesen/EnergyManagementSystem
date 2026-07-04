import SwiftUI
import EMSControlCore

struct InsightsView: View {
    @Bindable var store: InsightsStore
    @Environment(\.colorScheme) private var colorScheme

    private var theme: EMSTheme {
        colorScheme == .dark ? .dark : .light
    }

    var body: some View {
        NavigationStack {
            ZStack {
                background

                ScrollView {
                    VStack(alignment: .leading, spacing: 16) {
                        header

                        if let error = store.errorMessage, store.report == nil {
                            MessagePanel(text: "Could not load this report. \(error)", systemImage: "wifi.exclamationmark", theme: theme)
                        } else if store.isLoading && store.report == nil {
                            ProgressView()
                                .frame(maxWidth: .infinity)
                                .padding(28)
                                .background(themeColor(theme.panel))
                                .clipShape(RoundedRectangle(cornerRadius: 12, style: .continuous))
                        } else if let report = store.report, !report.flows.hasData {
                            MessagePanel(text: "No energy recorded for this \(store.period.rawValue) yet.", systemImage: "chart.bar.xaxis", theme: theme)
                        } else if let report = store.report {
                            if let line = headline(for: report), !line.isEmpty {
                                Text(line)
                                    .font(.title3.weight(.semibold))
                                    .foregroundStyle(themeColor(theme.text))
                                    .fixedSize(horizontal: false, vertical: true)
                                    .padding(16)
                                    .background(themeColor(theme.panel))
                                    .clipShape(RoundedRectangle(cornerRadius: 12, style: .continuous))
                            }

                            ScoreExplanationGrid(scores: report.scores, theme: theme)

                            if !report.series.isEmpty {
                                EnergyBehaviorPanel(report: report, period: store.period, theme: theme)
                            }

                            FlowReportPanel(report: report, theme: theme)

                            if let finance = store.finance {
                                FinanceInsightsPanel(finance: finance, theme: theme)
                            }
                        }
                    }
                    .padding()
                }
            }
            .navigationTitle("Insights")
            .refreshable { await store.refresh() }
            .task(id: "\(store.client?.baseURL.absoluteString ?? "demo")-\(store.period.rawValue)-\(store.anchor)") {
                if store.client != nil, store.report == nil {
                    await store.refresh()
                }
            }
        }
    }

    private var header: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Your energy scores and where every kWh went.")
                .font(.subheadline)
                .foregroundStyle(themeColor(theme.muted))

            Picker("Reporting period", selection: Binding(
                get: { store.period },
                set: { period in Task { await store.setPeriod(period) } }
            )) {
                ForEach(InsightsPeriod.allCases, id: \.self) { period in
                    Text(period.title).tag(period)
                }
            }
            .pickerStyle(.segmented)

            HStack(spacing: 12) {
                Button {
                    Task { await store.movePeriod(direction: -1) }
                } label: {
                    Image(systemName: "chevron.left")
                        .frame(width: 34, height: 34)
                }
                .buttonStyle(.bordered)
                .accessibilityLabel("Previous period")

                Text(store.report?.label ?? store.anchor)
                    .font(.headline)
                    .foregroundStyle(themeColor(theme.text))
                    .frame(maxWidth: .infinity)

                Button {
                    Task { await store.movePeriod(direction: 1) }
                } label: {
                    Image(systemName: "chevron.right")
                        .frame(width: 34, height: 34)
                }
                .buttonStyle(.bordered)
                .disabled(store.report?.partial == true)
                .accessibilityLabel("Next period")
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

    private var background: some View {
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

private struct ScoreExplanationGrid: View {
    let scores: [ReportScore]
    let theme: EMSTheme

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Scores")
                .font(.headline)
                .foregroundStyle(themeColor(theme.text))

            ForEach(scores) { score in
                HStack(alignment: .center, spacing: 14) {
                    InsightsScoreRing(score: score, theme: theme)
                        .frame(width: 82)

                    VStack(alignment: .leading, spacing: 5) {
                        Text(score.label)
                            .font(.subheadline.weight(.semibold))
                            .foregroundStyle(themeColor(theme.text))
                        if let raw = rawText(score) {
                            Text(raw)
                                .font(.caption.weight(.semibold))
                                .foregroundStyle(themeColor(theme.accent))
                        }
                        Text(score.explanation ?? "No explanation available yet.")
                            .font(.caption)
                            .foregroundStyle(themeColor(theme.muted))
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
                .padding(12)
                .background(themeColor(theme.secondaryPanel))
                .clipShape(RoundedRectangle(cornerRadius: 10, style: .continuous))
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

private struct InsightsScoreRing: View {
    let score: ReportScore
    let theme: EMSTheme

    var body: some View {
        ZStack {
            Circle()
                .stroke(themeColor(theme.line), lineWidth: 8)
            Circle()
                .trim(from: 0, to: min(max((score.value ?? 0) / 100, 0), 1))
                .stroke(themeColor(color), style: StrokeStyle(lineWidth: 8, lineCap: .round))
                .rotationEffect(.degrees(-90))
            Text(score.value.map { "\($0.formatted(.number.precision(.fractionLength(0))))%" } ?? "--")
                .font(.caption.weight(.bold))
                .foregroundStyle(themeColor(theme.text))
        }
        .frame(width: 66, height: 66)
    }

    private var color: HexColor {
        guard let value = score.value else { return theme.muted }
        if value >= 75 { return theme.accent }
        if value >= 45 { return theme.amber }
        return theme.winter
    }
}

private struct EnergyBehaviorPanel: View {
    let report: ReportSnapshot
    let period: InsightsPeriod
    let theme: EMSTheme

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("How your energy behaved\(report.partial == true ? " so far" : "")")
                .font(.headline)
                .foregroundStyle(themeColor(theme.text))

            BehaviorChart(
                title: "Used by the home",
                buckets: report.series,
                series: [
                    ChartSeries(label: "House", color: theme.text, values: report.series.map(\.houseKwh)),
                    ChartSeries(label: "Car", color: theme.winter, values: report.series.map(\.carKwh))
                ],
                theme: theme
            )

            BehaviorChart(
                title: "Solar & grid",
                buckets: report.series,
                series: [
                    ChartSeries(label: "Solar", color: theme.amber, values: report.series.map(\.solarKwh)),
                    ChartSeries(label: "Grid in", color: theme.winter, values: report.series.map(\.gridImportKwh)),
                    ChartSeries(label: "Grid out", color: theme.muted, values: report.series.map { -$0.gridExportKwh })
                ],
                theme: theme
            )
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

private struct ChartSeries: Identifiable {
    let label: String
    let color: HexColor
    let values: [Double]
    var id: String { label }
}

private struct BehaviorChart: View {
    let title: String
    let buckets: [ReportSeriesBucket]
    let series: [ChartSeries]
    let theme: EMSTheme

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(title)
                .font(.caption.weight(.semibold))
                .foregroundStyle(themeColor(theme.muted))

            GeometryReader { proxy in
                ZStack {
                    grid(size: proxy.size)
                    ForEach(series) { item in
                        path(for: item.values, size: proxy.size)
                            .stroke(themeColor(item.color), style: StrokeStyle(lineWidth: 2.4, lineCap: .round, lineJoin: .round))
                    }
                }
            }
            .frame(height: 132)

            FlowLegend(items: series.map { ($0.label, $0.color) }, theme: theme)
        }
        .padding(12)
        .background(themeColor(theme.secondaryPanel))
        .clipShape(RoundedRectangle(cornerRadius: 10, style: .continuous))
    }

    private var domain: ClosedRange<Double> {
        let values = series.flatMap(\.values)
        let minValue = min(values.min() ?? 0, 0)
        let maxValue = max(values.max() ?? 1, 0.1)
        if minValue == maxValue { return 0 ... 1 }
        return minValue ... maxValue
    }

    private func grid(size: CGSize) -> some View {
        ZStack {
            ForEach([0.0, 0.5, 1.0], id: \.self) { ratio in
                Path { path in
                    let y = size.height * ratio
                    path.move(to: CGPoint(x: 0, y: y))
                    path.addLine(to: CGPoint(x: size.width, y: y))
                }
                .stroke(themeColor(theme.line), style: StrokeStyle(lineWidth: 1, dash: ratio == 0.5 ? [4, 4] : []))
            }
        }
    }

    private func path(for values: [Double], size: CGSize) -> Path {
        Path { path in
            guard values.count > 1 else { return }
            for (index, value) in values.enumerated() {
                let point = point(index: index, value: value, count: values.count, size: size)
                if index == 0 {
                    path.move(to: point)
                } else {
                    path.addLine(to: point)
                }
            }
        }
    }

    private func point(index: Int, value: Double, count: Int, size: CGSize) -> CGPoint {
        let x = Double(index) / Double(max(count - 1, 1)) * size.width
        let span = domain.upperBound - domain.lowerBound
        let y = (1 - ((value - domain.lowerBound) / span)) * size.height
        return CGPoint(x: x, y: y)
    }
}

private struct FlowReportPanel: View {
    let report: ReportSnapshot
    let theme: EMSTheme

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("Where your energy went\(report.partial == true ? " so far" : "")")
                .font(.headline)
                .foregroundStyle(themeColor(theme.text))

            LazyVGrid(columns: [GridItem(.flexible()), GridItem(.flexible())], spacing: 10) {
                FlowMetric(title: "Solar", value: kwh(report.flows.solarKwh), color: theme.amber, theme: theme)
                FlowMetric(title: "Grid", value: kwh(report.flows.gridImportKwh), color: theme.winter, theme: theme)
                FlowMetric(title: "Battery", value: kwh(report.flows.batteryDischargeKwh), color: theme.accent, theme: theme)
                FlowMetric(title: "House", value: kwh(report.flows.homeKwh), color: theme.text, theme: theme)
                FlowMetric(title: "Car", value: kwh(report.flows.carKwh), color: theme.winter, theme: theme)
                FlowMetric(title: "Exported", value: kwh(report.flows.gridExportKwh), color: theme.muted, theme: theme)
            }

            if report.flows.carGuardLeakKwh > 0.05 {
                Label("\(kwh(report.flows.carGuardLeakKwh)) went from the battery into the car.", systemImage: "exclamationmark.triangle")
                    .font(.caption)
                    .foregroundStyle(themeColor(theme.amber))
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

private struct FlowMetric: View {
    let title: String
    let value: String
    let color: HexColor
    let theme: EMSTheme

    var body: some View {
        HStack(spacing: 9) {
            Circle()
                .fill(themeColor(color))
                .frame(width: 9, height: 9)
            VStack(alignment: .leading, spacing: 3) {
                Text(title)
                    .font(.caption2.weight(.semibold))
                    .foregroundStyle(themeColor(theme.muted))
                Text(value)
                    .font(.subheadline.weight(.semibold))
                    .foregroundStyle(themeColor(theme.text))
            }
            Spacer(minLength: 0)
        }
        .padding(10)
        .background(themeColor(theme.secondaryPanel))
        .clipShape(RoundedRectangle(cornerRadius: 8, style: .continuous))
    }
}

private struct FinanceInsightsPanel: View {
    let finance: FinanceSnapshot
    let theme: EMSTheme

    var body: some View {
        if (finance.totals.daysWithData ?? 0) > 0 {
            VStack(alignment: .leading, spacing: 14) {
                Text("What it cost & saved\(finance.partial == true ? " so far" : "")")
                    .font(.headline)
                    .foregroundStyle(themeColor(theme.text))

                LazyVGrid(columns: [GridItem(.flexible()), GridItem(.flexible())], spacing: 10) {
                    FlowMetric(title: "Saved", value: euro(finance.totals.savedEur), color: theme.accent, theme: theme)
                    FlowMetric(title: "Grid cost", value: euro(finance.totals.gridCostEur), color: theme.winter, theme: theme)
                    FlowMetric(title: "Battery wear", value: euro(finance.totals.batteryCostEur), color: theme.amber, theme: theme)
                    FlowMetric(title: "Price days", value: "\(finance.totals.daysWithPrices ?? 0)/\(finance.totals.daysWithData ?? 0)", color: theme.muted, theme: theme)
                }

                if finance.days.filter(\.hasData).count > 1 {
                    SavedBars(days: finance.days, theme: theme)
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
}

private struct SavedBars: View {
    let days: [FinanceDay]
    let theme: EMSTheme

    var body: some View {
        GeometryReader { proxy in
            HStack(alignment: .center, spacing: 3) {
                ForEach(days) { day in
                    let value = day.savedEur ?? 0
                    RoundedRectangle(cornerRadius: 3, style: .continuous)
                        .fill(themeColor(value >= 0 ? theme.accent : theme.amber))
                        .frame(height: max(4, proxy.size.height * normalized(abs(value))))
                        .frame(maxHeight: .infinity, alignment: value >= 0 ? .bottom : .top)
                }
            }
        }
        .frame(height: 86)
        .padding(.top, 4)
    }

    private func normalized(_ value: Double) -> Double {
        let maxValue = max(days.compactMap(\.savedEur).map(abs).max() ?? 0, 0.01)
        return min(max(value / maxValue, 0), 1)
    }
}

private struct FlowLegend: View {
    let items: [(String, HexColor)]
    let theme: EMSTheme

    var body: some View {
        ViewThatFits(in: .horizontal) {
            HStack(spacing: 10) { content }
            VStack(alignment: .leading, spacing: 6) { content }
        }
    }

    private var content: some View {
        ForEach(items, id: \.0) { item in
            HStack(spacing: 5) {
                Circle().fill(themeColor(item.1)).frame(width: 7, height: 7)
                Text(item.0)
                    .font(.caption2)
                    .foregroundStyle(themeColor(theme.muted))
            }
        }
    }
}

private struct MessagePanel: View {
    let text: String
    let systemImage: String
    let theme: EMSTheme

    var body: some View {
        Label(text, systemImage: systemImage)
            .font(.subheadline)
            .foregroundStyle(themeColor(theme.muted))
            .fixedSize(horizontal: false, vertical: true)
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(16)
            .background(themeColor(theme.panel))
            .clipShape(RoundedRectangle(cornerRadius: 12, style: .continuous))
            .overlay {
                RoundedRectangle(cornerRadius: 12, style: .continuous)
                    .stroke(themeColor(theme.line), lineWidth: 1)
            }
    }
}

private func headline(for report: ReportSnapshot) -> String? {
    guard let selfSufficiency = report.flows.selfSufficiencyPct else { return nil }
    let co2 = report.scores.first { $0.key == "co2" }?.value
    let suffix = report.partial == true ? " so far" : ""
    if let co2 {
        return "You ran \(selfSufficiency.formatted(.number.precision(.fractionLength(0))))% on your own solar + battery and cut \(co2.formatted(.number.precision(.fractionLength(0))))% of a no-solar home's CO2\(suffix)."
    }
    return "You ran \(selfSufficiency.formatted(.number.precision(.fractionLength(0))))% on your own solar + battery\(suffix)."
}

private func rawText(_ score: ReportScore) -> String? {
    guard let raw = score.raw else { return nil }
    if score.unit == "kg" {
        return "\(raw.formatted(.number.precision(.fractionLength(1)))) kg CO2"
    }
    if score.unit == "EUR/kWh" || score.unit == "€/kWh" {
        return "\(raw.formatted(.currency(code: "EUR"))) / kWh avg import"
    }
    if let unit = score.unit, unit != "%" {
        return "\(raw.formatted(.number.precision(.fractionLength(1)))) \(unit)"
    }
    return nil
}

private func kwh(_ value: Double?) -> String {
    guard let value else { return "--" }
    return "\(value.formatted(.number.precision(.fractionLength(0...1)))) kWh"
}

private func euro(_ value: Double?) -> String {
    guard let value else { return "--" }
    return value.formatted(.currency(code: "EUR"))
}
