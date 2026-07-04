import SwiftUI
import EMSControlCore

struct ActivityView: View {
    let store: ActivityStore
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

                        if let error = store.errorMessage, store.entries.isEmpty {
                            MessagePanel(text: "Could not load activity. \(error)", systemImage: "wifi.exclamationmark", theme: theme)
                        } else if store.isLoading && store.entries.isEmpty {
                            ProgressView()
                                .frame(maxWidth: .infinity)
                                .padding(28)
                                .background(themeColor(theme.panel))
                                .clipShape(RoundedRectangle(cornerRadius: 12, style: .continuous))
                        } else if store.entries.isEmpty {
                            MessagePanel(text: "No activity recorded yet.", systemImage: "clock.arrow.circlepath", theme: theme)
                        } else {
                            VStack(spacing: 10) {
                                ForEach(store.entries) { entry in
                                    ActivityRow(entry: entry, theme: theme)
                                }
                            }
                        }
                    }
                    .padding()
                }
            }
            .navigationTitle("Activity")
            .refreshable { await store.refresh() }
        }
    }

    private var header: some View {
        Text("What the EMS has done recently.")
            .font(.subheadline)
            .foregroundStyle(themeColor(theme.muted))
            .frame(maxWidth: .infinity, alignment: .leading)
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

private struct ActivityRow: View {
    let entry: AuditEntry
    let theme: EMSTheme

    var body: some View {
        HStack(alignment: .top, spacing: 12) {
            Circle()
                .fill(themeColor(accent))
                .frame(width: 9, height: 9)
                .padding(.top, 5)

            VStack(alignment: .leading, spacing: 4) {
                Text(entry.summary)
                    .font(.subheadline)
                    .foregroundStyle(themeColor(theme.text))
                    .fixedSize(horizontal: false, vertical: true)

                HStack(spacing: 8) {
                    Text(categoryLabel)
                        .font(.caption2.weight(.semibold))
                        .foregroundStyle(themeColor(accent))
                    Text(timestampLabel)
                        .font(.caption2)
                        .foregroundStyle(themeColor(theme.muted))
                }
            }

            Spacer(minLength: 0)
        }
        .padding(12)
        .background(themeColor(theme.secondaryPanel))
        .clipShape(RoundedRectangle(cornerRadius: 10, style: .continuous))
    }

    private var categoryLabel: String {
        switch entry.category {
        case "battery_decision":
            "Battery decision"
        case "manual_override":
            "Manual override"
        case "config_change":
            "Config change"
        case "ai_validation":
            "AI validation"
        default:
            entry.category.replacingOccurrences(of: "_", with: " ").capitalized
        }
    }

    private var timestampLabel: String {
        guard let date = ISO8601DateFormatter().date(from: entry.ts) else { return entry.ts }
        return date.formatted(date: .abbreviated, time: .shortened)
    }

    private var accent: HexColor {
        switch entry.category {
        case "battery_decision":
            theme.accent
        case "manual_override":
            theme.amber
        case "ai_validation":
            theme.winter
        default:
            theme.muted
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
