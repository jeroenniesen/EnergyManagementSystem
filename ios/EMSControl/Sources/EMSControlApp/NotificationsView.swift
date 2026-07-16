import SwiftUI
import EMSControlCore

// iOS parity for the web's notification bell (Notifications.tsx) + weekly digest (WeekDigest.tsx):
// the Dashboard shows the "Your week" digest card and a notifications entry row; tapping the row
// pushes the full recent-notifications list with mark-all-read. Backed by NotificationsStore.

// MARK: - "Your week" digest card (Dashboard)

// Mirrors the web WeekDigest panel's hierarchy: headline sentence, hero saved-€, three facts
// (self-sufficient %, solar kWh, battery adjustments), best day, one tweak, coverage note, and
// the same ‹ › week stepper. Numbers the week can't measure show "--", never a fabricated €0.
struct WeekDigestPanel: View {
    let store: NotificationsStore
    let theme: EMSTheme

    var body: some View {
        if let digest = store.digest {
            VStack(alignment: .leading, spacing: 12) {
                HStack(alignment: .firstTextBaseline, spacing: 8) {
                    Text("Your week")
                        .font(.headline)
                        .foregroundStyle(themeColor(theme.text))
                    Spacer(minLength: 8)
                    weekStepper(digest)
                }

                if !digest.headline.isEmpty {
                    Text(digest.headline)
                        .font(.subheadline)
                        .foregroundStyle(themeColor(theme.text))
                        .fixedSize(horizontal: false, vertical: true)
                }

                HStack(alignment: .firstTextBaseline, spacing: 6) {
                    Text(euro(digest.savedEur))
                        .font(.title2.weight(.bold))
                        .foregroundStyle(themeColor(heroColor(digest)))
                    Text("saved this week")
                        .font(.caption)
                        .foregroundStyle(themeColor(theme.muted))
                }
                .accessibilityElement(children: .combine)

                ViewThatFits(in: .horizontal) {
                    HStack(spacing: 8) { facts(digest) }
                    VStack(alignment: .leading, spacing: 8) { facts(digest) }
                }

                if let best = digest.bestDay {
                    Text("Best day: \(best.date) (\(euro(best.savedEur)))")
                        .font(.caption)
                        .foregroundStyle(themeColor(theme.muted))
                }

                if let tweak = digest.tweak, !tweak.isEmpty {
                    Label(tweak, systemImage: "lightbulb")
                        .font(.footnote)
                        .foregroundStyle(themeColor(theme.winter))
                        .fixedSize(horizontal: false, vertical: true)
                        .padding(.horizontal, 10)
                        .padding(.vertical, 8)
                        .background(themeColor(theme.winter).opacity(0.12))
                        .clipShape(RoundedRectangle(cornerRadius: 8, style: .continuous))
                }

                if digest.partial {
                    Text("\(digest.daysMeasured) of \(digest.daysTotal) days measured")
                        .font(.caption2)
                        .foregroundStyle(themeColor(theme.muted))
                }
            }
            .padding(16)
            .frame(maxWidth: .infinity, alignment: .leading)
            .notifCardBackground(theme)
            .accessibilityElement(children: .contain)
            .accessibilityLabel("Your week")
        }
    }

    // The same ‹ › stepper as the web panel; disabled in demo/disconnected mode or when the
    // week label carries no parseable Monday.
    private func weekStepper(_ digest: WeekDigest) -> some View {
        HStack(spacing: 6) {
            Button {
                Task { await store.stepDigestWeek(direction: -1) }
            } label: {
                Image(systemName: "chevron.left")
                    .font(.caption.weight(.semibold))
                    .frame(width: 26, height: 26)
            }
            .buttonStyle(.bordered)
            .disabled(!store.canStepDigestWeek)
            .accessibilityLabel("Previous week")

            Text(digest.weekLabel)
                .font(.caption.weight(.semibold))
                .foregroundStyle(themeColor(theme.muted))
                .lineLimit(1)
                .minimumScaleFactor(0.8)

            Button {
                Task { await store.stepDigestWeek(direction: 1) }
            } label: {
                Image(systemName: "chevron.right")
                    .font(.caption.weight(.semibold))
                    .frame(width: 26, height: 26)
            }
            .buttonStyle(.bordered)
            .disabled(!store.canStepDigestWeek)
            .accessibilityLabel("Next week")
        }
    }

    @ViewBuilder
    private func facts(_ digest: WeekDigest) -> some View {
        DigestFact(
            value: percent(digest.selfSufficiencyPct),
            name: "self-sufficient",
            theme: theme
        )
        DigestFact(
            value: "\(digest.solarKwh.formatted(.number.precision(.fractionLength(0 ... 1)))) kWh",
            name: "from the sun",
            theme: theme
        )
        DigestFact(
            value: "\(digest.adjustmentsTotal)",
            name: "battery adjustments",
            theme: theme,
            // The web puts this breakdown in the fact's tooltip; here it rides accessibility.
            detail: "\(digest.actions.modeSwitches) battery mode changes, "
                + "\(digest.actions.negativeSoaks) paid-to-charge, "
                + "\(digest.actions.overrides) manual"
        )
    }

    private func heroColor(_ digest: WeekDigest) -> HexColor {
        guard let saved = digest.savedEur else { return theme.muted }
        return saved >= 0 ? theme.accent : theme.text
    }
}

private struct DigestFact: View {
    let value: String
    let name: String
    let theme: EMSTheme
    var detail: String?

    var body: some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(value)
                .font(.footnote.weight(.semibold))
                .foregroundStyle(themeColor(theme.text))
                .lineLimit(1)
                .minimumScaleFactor(0.75)
            Text(name)
                .font(.caption2)
                .foregroundStyle(themeColor(theme.muted))
        }
        .padding(.horizontal, 10)
        .padding(.vertical, 8)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(themeColor(theme.secondaryPanel))
        .clipShape(RoundedRectangle(cornerRadius: 8, style: .continuous))
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("\(value) \(name)")
        .accessibilityValue(detail ?? "")
    }
}

// MARK: - Notifications entry row (Dashboard)

// The bell, translated to the dashboard's card language: count + unread state, pushing the list.
struct NotificationsLinkRow: View {
    let store: NotificationsStore
    let theme: EMSTheme

    var body: some View {
        NavigationLink {
            NotificationsListView(store: store)
        } label: {
            HStack(spacing: 12) {
                Image(systemName: store.unread > 0 ? "bell.badge.fill" : "bell")
                    .font(.body.weight(.semibold))
                    .foregroundStyle(themeColor(store.unread > 0 ? theme.accent : theme.muted))
                    .frame(width: 24)
                VStack(alignment: .leading, spacing: 2) {
                    Text("Notifications")
                        .font(.subheadline.weight(.semibold))
                        .foregroundStyle(themeColor(theme.text))
                    Text(subtitle)
                        .font(.caption)
                        .foregroundStyle(themeColor(theme.muted))
                }
                Spacer(minLength: 8)
                Image(systemName: "chevron.right")
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(themeColor(theme.muted))
            }
            .padding(16)
            .frame(maxWidth: .infinity, alignment: .leading)
            .notifCardBackground(theme)
        }
        .buttonStyle(.plain)
        .accessibilityLabel(store.unread > 0
            ? "Notifications — \(store.unread) unread"
            : "Notifications")
    }

    private var subtitle: String {
        if store.unread > 0 {
            return "\(store.unread) unread"
        }
        return store.items.isEmpty ? "No notifications yet." : "All caught up."
    }
}

// MARK: - Full notifications list (pushed)

struct NotificationsListView: View {
    let store: NotificationsStore
    @Environment(\.colorScheme) private var colorScheme

    private var theme: EMSTheme {
        colorScheme == .dark ? .dark : .light
    }

    var body: some View {
        ZStack {
            themeColor(theme.background).ignoresSafeArea()

            ScrollView {
                VStack(spacing: 10) {
                    if let error = store.errorMessage, store.items.isEmpty {
                        emptyNote("Could not load notifications. \(error)",
                                  systemImage: "wifi.exclamationmark")
                    } else if store.items.isEmpty {
                        emptyNote("No notifications yet.", systemImage: "bell")
                    } else {
                        ForEach(store.items) { item in
                            NotificationRow(item: item, theme: theme)
                        }
                    }
                }
                .padding()
            }
        }
        .navigationTitle("Notifications")
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .topBarTrailing) {
                Button("Mark all read") {
                    Task { await store.markAllRead() }
                }
                .disabled(store.unread == 0)
            }
        }
        .refreshable { await store.refresh() }
        .task {
            // Fresh feed on arrival (the web bell polls; a fetch-on-open is the modest mirror).
            if store.client != nil {
                await store.refresh()
            }
        }
    }

    private func emptyNote(_ text: String, systemImage: String) -> some View {
        Label(text, systemImage: systemImage)
            .font(.subheadline)
            .foregroundStyle(themeColor(theme.muted))
            .fixedSize(horizontal: false, vertical: true)
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(16)
            .notifCardBackground(theme)
    }
}

private struct NotificationRow: View {
    let item: NotificationItem
    let theme: EMSTheme

    var body: some View {
        HStack(alignment: .top, spacing: 10) {
            Circle()
                .fill(themeColor(item.read ? theme.line : theme.accent))
                .frame(width: 8, height: 8)
                .padding(.top, 5)

            VStack(alignment: .leading, spacing: 3) {
                Text(item.title)
                    .font(.subheadline.weight(item.read ? .regular : .semibold))
                    .foregroundStyle(themeColor(theme.text))
                    .fixedSize(horizontal: false, vertical: true)
                if !item.body.isEmpty {
                    Text(item.body)
                        .font(.footnote)
                        .foregroundStyle(themeColor(theme.muted))
                        .fixedSize(horizontal: false, vertical: true)
                }
                Text(relativeTime(item.ts))
                    .font(.caption2)
                    .foregroundStyle(themeColor(theme.muted))
            }

            Spacer(minLength: 0)
        }
        .padding(12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(themeColor(theme.secondaryPanel))
        .clipShape(RoundedRectangle(cornerRadius: 10, style: .continuous))
        .accessibilityElement(children: .combine)
        .accessibilityLabel("\(item.read ? "" : "Unread. ")\(item.title). \(item.body)")
    }
}

// MARK: - Helpers

// Mirrors the web bell's relativeTime (Notifications.tsx) exactly.
private func relativeTime(_ iso: String, now: Date = Date()) -> String {
    guard let date = ISOTimestamp.parse(iso) else { return "" }
    let diffSec = max(0, Int(now.timeIntervalSince(date).rounded()))
    if diffSec < 60 { return "just now" }
    let diffMin = Int((Double(diffSec) / 60).rounded())
    if diffMin < 60 { return "\(diffMin)m ago" }
    let diffHr = Int((Double(diffMin) / 60).rounded())
    if diffHr < 24 { return "\(diffHr)h ago" }
    return "\(Int((Double(diffHr) / 24).rounded()))d ago"
}

private func euro(_ value: Double?) -> String {
    guard let value else { return "--" }
    return value.formatted(.currency(code: "EUR"))
}

private func percent(_ value: Double?) -> String {
    guard let value else { return "--" }
    return "\(value.formatted(.number.precision(.fractionLength(0))))%"
}

// Shared card chrome, matching the dashboard panels.
private extension View {
    func notifCardBackground(_ theme: EMSTheme) -> some View {
        self
            .background(themeColor(theme.panel))
            .clipShape(RoundedRectangle(cornerRadius: 12, style: .continuous))
            .overlay {
                RoundedRectangle(cornerRadius: 12, style: .continuous)
                    .stroke(themeColor(theme.line), lineWidth: 1)
            }
    }
}
