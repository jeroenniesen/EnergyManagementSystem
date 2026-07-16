import SwiftUI
import EMSControlCore

// The Car tab (iOS parity iteration 1): a first-class home for everything about the car, mirroring
// the web's Car view (ems/web/frontend/src/Car.tsx). In order:
//   (a) the full advisory charge-timing card (the shared CarPanel the dashboard shows compact),
//       read from the dashboard snapshot's carPlan — a single fetched-every-cycle source;
//   (b) "While the car charges" — the home BATTERY's behaviour during a charging session (master
//       toggle + three modes + the fixed-watts stepper), saved immediately via POST /api/settings;
//   (c) the weekly-minimum schedule (ev.schedule), rendered read-first with native rows, edited +
//       saved through the same immediate-save path;
//   (d) a compact detected-sessions history (GET /api/car/sessions) with an honest empty state.
// Sections (b)–(d) are backed by CarStore; (a) reads DashboardStore.
struct CarView: View {
    @Environment(DashboardStore.self) private var dashboardStore
    @Environment(\.colorScheme) private var colorScheme
    let store: CarStore

    private var theme: EMSTheme {
        colorScheme == .dark ? .dark : .light
    }

    private var carPlan: CarPlanSnapshot {
        dashboardStore.snapshot?.carPlan ?? .empty
    }

    // The smallest honest "what's the house using right now" — the same coalesced reading the
    // dashboard already exposes (non_ev_load_w); feeds match_home_load's "~N W" copy + the
    // static-discharge warning threshold. nil hides both rather than guessing.
    private var houseLoadW: Double? {
        dashboardStore.snapshot?.status.nonEvLoadW
    }

    var body: some View {
        NavigationStack {
            ZStack {
                background
                ScrollView {
                    VStack(alignment: .leading, spacing: 16) {
                        if carPlan.enabled {
                            CarPanel(carPlan: carPlan, theme: theme, variant: .full)
                        } else {
                            CarChargingOffCard(theme: theme)
                        }

                        CarModeCard(store: store, houseLoadW: houseLoadW, theme: theme)
                        CarScheduleCard(store: store, theme: theme)
                        CarSessionsCard(
                            sessions: store.sessions,
                            isLoading: store.isLoading && !store.loaded,
                            theme: theme
                        )
                    }
                    .padding()
                }
            }
            .navigationTitle("Car")
            .refreshable {
                if store.client != nil { await store.refresh() }
            }
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

// Shown as the hero when the EV feature is off — honest, and points at where to enable it.
private struct CarChargingOffCard: View {
    let theme: EMSTheme

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Label("Car charging", systemImage: "bolt.car.fill")
                .font(.headline)
                .foregroundStyle(themeColor(theme.text))
            Text("Car charging is off. Turn it on in the web app's Settings → Car to get a "
                + "cheapest-time-to-charge plan here.")
                .font(.footnote)
                .foregroundStyle(themeColor(theme.muted))
                .fixedSize(horizontal: false, vertical: true)
        }
        .padding(16)
        .frame(maxWidth: .infinity, alignment: .leading)
        .carCardBackground(theme)
    }
}

// MARK: - (b) "While the car charges" battery modes

private struct CarModeCard: View {
    let store: CarStore
    let houseLoadW: Double?
    let theme: EMSTheme

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(alignment: .firstTextBaseline) {
                Text("While the car charges")
                    .font(.headline)
                    .foregroundStyle(themeColor(theme.text))
                Spacer(minLength: 8)
                Toggle("", isOn: Binding(
                    get: { store.mode.holdEnabled },
                    set: { next in Task { await store.setHoldEnabled(next) } }
                ))
                .labelsHidden()
                .tint(themeColor(theme.accent))
                .accessibilityLabel("Special battery behaviour while the car charges")
            }

            Text(store.mode.holdEnabled
                ? "The battery follows the mode you pick below whenever the car is charging."
                : "Off — the planner runs exactly as with no car; the battery is untouched. Turn "
                    + "this on to apply the mode you pick below.")
                .font(.footnote)
                .foregroundStyle(themeColor(theme.muted))
                .fixedSize(horizontal: false, vertical: true)

            VStack(spacing: 8) {
                ForEach(CarChargingMode.allCases, id: \.self) { mode in
                    CarModeRow(
                        mode: mode,
                        selected: store.mode.mode == mode,
                        detail: detail(for: mode),
                        theme: theme
                    ) {
                        Task { await store.setMode(mode) }
                    }

                    if store.mode.mode == mode, mode == .staticDischarge {
                        wattsStepper
                    }
                }
            }
            .disabled(!store.mode.holdEnabled)
            .opacity(store.mode.holdEnabled ? 1 : 0.5)

            saveStatus
        }
        .padding(16)
        .frame(maxWidth: .infinity, alignment: .leading)
        .carCardBackground(theme)
    }

    private var wattsStepper: some View {
        VStack(alignment: .leading, spacing: 4) {
            Stepper(
                value: Binding(
                    get: { store.mode.dischargeW },
                    set: { next in Task { await store.setDischargeW(next) } }
                ),
                in: CarModeSettings.minW ... CarModeSettings.maxW,
                step: CarModeSettings.stepW
            ) {
                HStack {
                    Text("Discharge power")
                        .font(.footnote.weight(.semibold))
                        .foregroundStyle(themeColor(theme.text))
                    Spacer(minLength: 8)
                    Text("\(store.mode.dischargeW) W")
                        .font(.footnote.weight(.semibold))
                        .foregroundStyle(themeColor(theme.accent))
                }
            }
            if let houseLoadW, Double(store.mode.dischargeW) > houseLoadW {
                Text("Above your home's usual draw — the extra feeds the car from the battery, "
                    + "which is your choice.")
                    .font(.caption)
                    .foregroundStyle(themeColor(theme.amber))
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        .padding(10)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(themeColor(theme.secondaryPanel))
        .clipShape(RoundedRectangle(cornerRadius: 8, style: .continuous))
    }

    @ViewBuilder
    private var saveStatus: some View {
        switch store.saveState {
        case .saved:
            Label("Saved", systemImage: "checkmark.circle.fill")
                .font(.caption.weight(.semibold))
                .foregroundStyle(themeColor(theme.accent))
        case .error:
            Label(store.saveError ?? "Couldn't save — try again.", systemImage: "exclamationmark.triangle")
                .font(.caption.weight(.semibold))
                .foregroundStyle(themeColor(theme.error))
                .fixedSize(horizontal: false, vertical: true)
        case .saving:
            Label("Saving…", systemImage: "arrow.triangle.2.circlepath")
                .font(.caption.weight(.semibold))
                .foregroundStyle(themeColor(theme.muted))
        case .idle:
            EmptyView()
        }
    }

    private func detail(for mode: CarChargingMode) -> String {
        // static_discharge's live wattage is already in the stepper; keep its row copy generic.
        mode == .matchHomeLoad ? mode.detail(houseLoadW: houseLoadW) : mode.detail(houseLoadW: nil)
    }
}

private struct CarModeRow: View {
    let mode: CarChargingMode
    let selected: Bool
    let detail: String
    let theme: EMSTheme
    let onSelect: () -> Void

    var body: some View {
        Button(action: onSelect) {
            HStack(alignment: .top, spacing: 10) {
                Image(systemName: selected ? "largecircle.fill.circle" : "circle")
                    .font(.body)
                    .foregroundStyle(themeColor(selected ? theme.accent : theme.muted))
                VStack(alignment: .leading, spacing: 3) {
                    Text(mode.title)
                        .font(.subheadline.weight(.semibold))
                        .foregroundStyle(themeColor(theme.text))
                    Text(detail)
                        .font(.caption)
                        .foregroundStyle(themeColor(theme.muted))
                        .fixedSize(horizontal: false, vertical: true)
                }
                Spacer(minLength: 0)
            }
            .padding(10)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(themeColor(selected ? theme.accent : theme.secondaryPanel).opacity(selected ? 0.16 : 1))
            .clipShape(RoundedRectangle(cornerRadius: 8, style: .continuous))
            .overlay {
                RoundedRectangle(cornerRadius: 8, style: .continuous)
                    .stroke(themeColor(selected ? theme.accent : theme.line), lineWidth: selected ? 1.5 : 1)
            }
        }
        .buttonStyle(.plain)
        .accessibilityAddTraits(selected ? [.isButton, .isSelected] : .isButton)
        .accessibilityLabel(mode.title)
    }
}

// MARK: - (c) Weekly schedule

private struct CarScheduleCard: View {
    let store: CarStore
    let theme: EMSTheme

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            VStack(alignment: .leading, spacing: 3) {
                Text("Weekly minimum charge")
                    .font(.headline)
                    .foregroundStyle(themeColor(theme.text))
                Text(subtitle)
                    .font(.footnote)
                    .foregroundStyle(themeColor(theme.muted))
                    .fixedSize(horizontal: false, vertical: true)
            }

            VStack(spacing: 8) {
                ForEach(CarSchedule.dayOrder, id: \.self) { day in
                    CarScheduleRow(store: store, day: day, theme: theme)
                }
            }
        }
        .padding(16)
        .frame(maxWidth: .infinity, alignment: .leading)
        .carCardBackground(theme)
    }

    private var subtitle: String {
        let count = store.schedule.enabledDayCount
        if count == 0 {
            return "No days set. Turn on a day to have EMS reach that charge by your ready-by time."
        }
        return "EMS reaches each enabled day's minimum charge by its ready-by time. "
            + "\(count) day\(count == 1 ? "" : "s") on."
    }
}

private struct CarScheduleRow: View {
    let store: CarStore
    let day: String
    let theme: EMSTheme

    private var value: CarScheduleDay { store.schedule[day] }

    var body: some View {
        VStack(spacing: 8) {
            HStack {
                Text(CarSchedule.dayLabel[day] ?? day.capitalized)
                    .font(.subheadline.weight(.semibold))
                    .foregroundStyle(themeColor(value.enabled ? theme.text : theme.muted))
                Spacer(minLength: 8)
                Toggle("", isOn: Binding(
                    get: { value.enabled },
                    set: { next in Task { await store.updateScheduleDay(day) { $0.enabled = next } } }
                ))
                .labelsHidden()
                .tint(themeColor(theme.accent))
                .accessibilityLabel("Enable \(CarSchedule.dayLabel[day] ?? day)")
            }

            if value.enabled {
                Stepper(
                    value: Binding(
                        get: { value.minPct },
                        set: { next in Task { await store.updateScheduleDay(day) { $0.minPct = next } } }
                    ),
                    in: 0 ... 100,
                    step: 5
                ) {
                    HStack {
                        Text("Minimum charge")
                            .font(.caption.weight(.semibold))
                            .foregroundStyle(themeColor(theme.muted))
                        Spacer(minLength: 8)
                        Text("\(value.minPct)%")
                            .font(.caption.weight(.semibold))
                            .foregroundStyle(themeColor(theme.accent))
                    }
                }

                DatePicker(
                    selection: Binding(
                        get: { CarScheduleTime.toDate(value.readyBy) },
                        set: { next in
                            let hhmm = CarScheduleTime.toString(next)
                            Task { await store.updateScheduleDay(day) { $0.readyBy = hhmm } }
                        }
                    ),
                    displayedComponents: .hourAndMinute
                ) {
                    Text("Ready by")
                        .font(.caption.weight(.semibold))
                        .foregroundStyle(themeColor(theme.muted))
                }
            }
        }
        .padding(10)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(themeColor(theme.secondaryPanel))
        .clipShape(RoundedRectangle(cornerRadius: 8, style: .continuous))
    }
}

// Convert between the "HH:MM" schedule string and the Date a compact time picker needs.
private enum CarScheduleTime {
    static func toDate(_ hhmm: String) -> Date {
        let parts = hhmm.split(separator: ":")
        var comps = DateComponents()
        comps.hour = parts.first.flatMap { Int($0) } ?? 7
        comps.minute = parts.count > 1 ? (Int(parts[1]) ?? 30) : 30
        return Calendar.current.date(from: comps) ?? Date()
    }

    static func toString(_ date: Date) -> String {
        let c = Calendar.current.dateComponents([.hour, .minute], from: date)
        return String(format: "%02d:%02d", c.hour ?? 7, c.minute ?? 30)
    }
}

// MARK: - (d) Detected sessions

private struct CarSessionsCard: View {
    let sessions: [CarSession]
    let isLoading: Bool
    let theme: EMSTheme

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            VStack(alignment: .leading, spacing: 3) {
                Text("Recent charging")
                    .font(.headline)
                    .foregroundStyle(themeColor(theme.text))
                Text("Charging sessions detected from your meter history.")
                    .font(.footnote)
                    .foregroundStyle(themeColor(theme.muted))
                    .fixedSize(horizontal: false, vertical: true)
            }

            if isLoading {
                HStack(spacing: 8) {
                    ProgressView().controlSize(.small)
                    Text("Reading charging history…")
                        .font(.footnote)
                        .foregroundStyle(themeColor(theme.muted))
                }
            } else if sessions.isEmpty {
                Text("No charging sessions detected recently.")
                    .font(.footnote)
                    .foregroundStyle(themeColor(theme.muted))
                    .fixedSize(horizontal: false, vertical: true)
            } else {
                VStack(spacing: 8) {
                    ForEach(sessions) { session in
                        CarSessionRow(session: session, theme: theme)
                    }
                }
            }
        }
        .padding(16)
        .frame(maxWidth: .infinity, alignment: .leading)
        .carCardBackground(theme)
    }
}

private struct CarSessionRow: View {
    let session: CarSession
    let theme: EMSTheme

    var body: some View {
        HStack(alignment: .firstTextBaseline) {
            Text(range)
                .font(.footnote.weight(.semibold))
                .foregroundStyle(themeColor(theme.text))
            Spacer(minLength: 8)
            Text(stats)
                .font(.caption.weight(.semibold))
                .foregroundStyle(themeColor(theme.muted))
                .multilineTextAlignment(.trailing)
        }
        .padding(10)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(themeColor(theme.secondaryPanel))
        .clipShape(RoundedRectangle(cornerRadius: 8, style: .continuous))
        .accessibilityElement(children: .combine)
        .accessibilityLabel("\(range). \(stats).")
    }

    private var range: String {
        guard let start = ISOTimestamp.parse(session.start) else { return "Charging session" }
        let startLabel = start.formatted(.dateTime.weekday(.abbreviated).hour().minute())
        if let end = ISOTimestamp.parse(session.end) {
            return "\(startLabel)–\(end.formatted(.dateTime.hour().minute()))"
        }
        return startLabel
    }

    private var stats: String {
        let kwh = session.kwh.formatted(.number.precision(.fractionLength(0 ... 1)))
        let avg = session.avgKw.formatted(.number.precision(.fractionLength(0 ... 1)))
        return "\(kwh) kWh · avg \(avg) kW"
    }
}

// Shared card chrome, matching the dashboard panels.
private extension View {
    func carCardBackground(_ theme: EMSTheme) -> some View {
        self
            .background(themeColor(theme.panel))
            .clipShape(RoundedRectangle(cornerRadius: 12, style: .continuous))
            .overlay {
                RoundedRectangle(cornerRadius: 12, style: .continuous)
                    .stroke(themeColor(theme.line), lineWidth: 1)
            }
    }
}
