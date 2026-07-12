import SwiftUI
import WidgetKit
import EMSControlCore

// SwiftUI rendering for the EMS widget. Reuses EMSTheme from EMSControlCore for the palette and
// picks light/dark by the environment colour scheme. All state carries a text label (never a
// colour alone), and every view exposes a combined accessibility label.

// The `themeColor()` helper lives in the app target (private), so the extension has its own small
// hex → Color bridge here.
extension Color {
    init(emsHex hex: String) {
        let cleaned = hex.trimmingCharacters(in: CharacterSet.alphanumerics.inverted)
        var value: UInt64 = 0
        Scanner(string: cleaned).scanHexInt64(&value)
        let red = Double((value >> 16) & 0xFF) / 255.0
        let green = Double((value >> 8) & 0xFF) / 255.0
        let blue = Double(value & 0xFF) / 255.0
        self.init(.sRGB, red: red, green: green, blue: blue, opacity: 1)
    }

    init(_ color: HexColor) {
        self.init(emsHex: color.hex)
    }
}

struct EMSWidgetEntryView: View {
    @Environment(\.colorScheme) private var colorScheme
    @Environment(\.widgetFamily) private var family
    let entry: EMSEntry

    private var theme: EMSTheme { colorScheme == .dark ? .dark : .light }

    var body: some View {
        content
            .containerBackground(Color(theme.background), for: .widget)
    }

    @ViewBuilder
    private var content: some View {
        switch entry.content {
        case .needsSetup:
            MessageView(
                icon: "bolt.horizontal.circle",
                title: "Open EMS to connect",
                subtitle: family == .systemSmall ? nil : "Connect to your server in the app to see live status here.",
                theme: theme
            )
        case .unreachable:
            MessageView(
                icon: "wifi.exclamationmark",
                title: "Can't reach EMS",
                subtitle: family == .systemSmall ? nil : "The server didn't answer. It'll retry automatically.",
                theme: theme
            )
        case let .ready(data, stale):
            if family == .systemSmall {
                SmallStatusView(data: data, stale: stale, theme: theme)
            } else {
                MediumStatusView(data: data, stale: stale, theme: theme)
            }
        }
    }
}

// MARK: - Small

private struct SmallStatusView: View {
    let data: WidgetRenderData
    let stale: Bool
    let theme: EMSTheme

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            LiveBadge(verdict: data.verdict, stale: stale, theme: theme)
            Spacer(minLength: 0)
            HStack {
                Spacer(minLength: 0)
                SoCRing(socPct: data.socPct, theme: theme, diameter: 74)
                Spacer(minLength: 0)
            }
            Spacer(minLength: 0)
            Text(data.verdict.word)
                .font(.headline)
                .foregroundStyle(Color(theme.text))
                .lineLimit(1)
                .minimumScaleFactor(0.7)
            if stale {
                StaleFootnote(asOf: data.asOf, theme: theme)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(accessibilityLabel)
    }

    private var accessibilityLabel: String {
        let soc = data.socPct.map { "Battery \(Int($0.rounded())) percent" } ?? "Battery level unavailable"
        let live = data.verdict.live ? "live" : "watching"
        let staleNote = stale ? ", data \(staleAsOf(data.asOf))" : ""
        return "\(soc), \(data.verdict.word), \(live)\(staleNote)"
    }
}

// MARK: - Medium

private struct MediumStatusView: View {
    let data: WidgetRenderData
    let stale: Bool
    let theme: EMSTheme

    var body: some View {
        HStack(alignment: .top, spacing: 16) {
            VStack(alignment: .center, spacing: 8) {
                SoCRing(socPct: data.socPct, theme: theme, diameter: 82)
                Text(data.verdict.word)
                    .font(.subheadline.weight(.semibold))
                    .foregroundStyle(Color(theme.text))
                    .lineLimit(1)
                    .minimumScaleFactor(0.7)
            }
            .frame(width: 96)

            VStack(alignment: .leading, spacing: 8) {
                LiveBadge(verdict: data.verdict, stale: stale, theme: theme)

                if let headline = data.headline {
                    Text(headline)
                        .font(.footnote)
                        .foregroundStyle(Color(theme.text))
                        .lineLimit(3)
                        .fixedSize(horizontal: false, vertical: true)
                } else {
                    Text("EMS is watching your home.")
                        .font(.footnote)
                        .foregroundStyle(Color(theme.muted))
                }

                Spacer(minLength: 0)

                if let carLine = data.carLine {
                    Label(carLine, systemImage: "bolt.car.fill")
                        .font(.caption.weight(.semibold))
                        .foregroundStyle(Color(theme.accent))
                        .lineLimit(1)
                        .minimumScaleFactor(0.7)
                }

                if stale {
                    StaleFootnote(asOf: data.asOf, theme: theme)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        }
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(accessibilityLabel)
    }

    private var accessibilityLabel: String {
        let soc = data.socPct.map { "Battery \(Int($0.rounded())) percent" } ?? "Battery level unavailable"
        let live = data.verdict.live ? "live" : "watching"
        var parts = ["\(soc)", data.verdict.word, live]
        if let headline = data.headline { parts.append(headline) }
        if let carLine = data.carLine { parts.append(carLine) }
        if stale { parts.append("data \(staleAsOf(data.asOf))") }
        return parts.joined(separator: ", ")
    }
}

// MARK: - Shared pieces

private struct SoCRing: View {
    let socPct: Double?
    let theme: EMSTheme
    let diameter: CGFloat

    private var progress: Double { min(max((socPct ?? 0) / 100, 0), 1) }

    var body: some View {
        ZStack {
            Circle()
                .stroke(Color(theme.line), lineWidth: 8)
            Circle()
                .trim(from: 0, to: progress)
                .stroke(Color(theme.accent), style: StrokeStyle(lineWidth: 8, lineCap: .round))
                .rotationEffect(.degrees(-90))
            Text(socText)
                .font(.system(size: diameter * 0.3, weight: .bold, design: .rounded))
                .foregroundStyle(Color(theme.text))
                .minimumScaleFactor(0.6)
                .lineLimit(1)
        }
        .frame(width: diameter, height: diameter)
    }

    private var socText: String {
        guard let socPct else { return "--" }
        return "\(Int(socPct.rounded()))%"
    }
}

private struct LiveBadge: View {
    let verdict: WidgetVerdict
    let stale: Bool
    let theme: EMSTheme

    private var label: String {
        if stale { return "STALE" }
        return verdict.live ? "LIVE" : "WATCHING"
    }

    private var color: HexColor {
        if stale { return theme.amber }
        return verdict.live ? theme.accent : theme.winter
    }

    var body: some View {
        HStack(spacing: 5) {
            Circle()
                .fill(Color(color))
                .frame(width: 7, height: 7)
            Text(label)
                .font(.caption2.weight(.bold))
                .foregroundStyle(Color(color))
        }
    }
}

private struct MessageView: View {
    let icon: String
    let title: String
    let subtitle: String?
    let theme: EMSTheme

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Image(systemName: icon)
                .font(.title2)
                .foregroundStyle(Color(theme.accent))
            Text(title)
                .font(.headline)
                .foregroundStyle(Color(theme.text))
                .fixedSize(horizontal: false, vertical: true)
            if let subtitle {
                Text(subtitle)
                    .font(.footnote)
                    .foregroundStyle(Color(theme.muted))
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .leading)
        .accessibilityElement(children: .combine)
    }
}

private struct StaleFootnote: View {
    let asOf: Date
    let theme: EMSTheme

    var body: some View {
        Text(staleAsOf(asOf))
            .font(.caption2)
            .foregroundStyle(Color(theme.muted))
    }
}

private func staleAsOf(_ date: Date) -> String {
    "as of \(date.formatted(date: .omitted, time: .shortened))"
}
