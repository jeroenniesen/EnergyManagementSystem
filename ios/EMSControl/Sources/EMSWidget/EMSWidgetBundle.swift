import SwiftUI
import WidgetKit
import EMSControlCore

// EMS home-screen widget (BACKLOG B-59).
//
// SCOPE: one static widget in systemSmall + systemMedium. A Live Activity for the car-charge
// window is intentionally OUT OF SCOPE for v1 — starting one from a background widget refresh needs
// push-to-start via APNs (a push key + server-side ActivityKit push), which this LAN-only app does
// not have. When that lands, add an `ActivityConfiguration` + a `WidgetLiveActivity` here and start
// it from the EMS server. See README for the tracking note.

@main
struct EMSWidgetBundle: WidgetBundle {
    var body: some Widget {
        EMSStatusWidget()
    }
}

struct EMSStatusWidget: Widget {
    let kind = "EMSStatusWidget"

    var body: some WidgetConfiguration {
        StaticConfiguration(kind: kind, provider: EMSTimelineProvider()) { entry in
            EMSWidgetEntryView(entry: entry)
        }
        .configurationDisplayName("EMS Status")
        .description("Battery level and today's verdict at a glance.")
        .supportedFamilies([.systemSmall, .systemMedium])
    }
}

// MARK: - Previews (canned entries so Xcode previews + the gallery render without a server)

#Preview("Small · live", as: .systemSmall) {
    EMSStatusWidget()
} timeline: {
    EMSEntry(date: .now, content: .ready(.sample, stale: false))
    EMSEntry(date: .now, content: .needsSetup)
    EMSEntry(date: .now, content: .ready(.sample, stale: true))
}

#Preview("Medium · live", as: .systemMedium) {
    EMSStatusWidget()
} timeline: {
    EMSEntry(date: .now, content: .ready(.sample, stale: false))
    EMSEntry(
        date: .now,
        content: .ready(
            WidgetRenderData(
                socPct: 41,
                verdict: WidgetVerdict(word: "Charging", live: false),
                headline: "Topping up from the grid in the cheapest window before the evening peak.",
                carLine: "Car: Tue 02:00 · 34.5 kWh",
                asOf: .now
            ),
            stale: false
        )
    )
    EMSEntry(date: .now, content: .unreachable)
}
