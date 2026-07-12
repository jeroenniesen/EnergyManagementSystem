import Foundation
import WidgetKit
import EMSControlCore

// Timeline entry + provider for the EMS home-screen widget (BACKLOG B-59).
//
// The widget reuses EMSControlCore directly — APIClient, the API models, and the pure render
// helpers in WidgetSupport.swift — because the framework links cleanly into the extension (it is
// Foundation/URLSession/Security only, all extension-safe). The only widget-local piece is a
// 5-second-timeout transport, so a home-screen refresh never hangs on an unreachable LAN server.

/// What the widget should draw for a given entry.
enum WidgetContent: Equatable {
    /// No server mirrored into the app group yet — prompt the user to open the app.
    case needsSetup
    /// Config exists but the fetch failed and there is no cached data to fall back to.
    case unreachable
    /// Live (or last-good, when `stale`) data.
    case ready(WidgetRenderData, stale: Bool)
}

struct EMSEntry: TimelineEntry {
    let date: Date
    let content: WidgetContent
}

/// A URLSession-backed transport with a short timeout, so the widget times out fast instead of
/// blocking WidgetKit's refresh budget. `@unchecked Sendable`: URLSession is thread-safe and the
/// stored session is only ever read.
struct WidgetTransport: HTTPTransport, @unchecked Sendable {
    private let session: URLSession

    init(timeout: TimeInterval) {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.timeoutIntervalForRequest = timeout
        configuration.timeoutIntervalForResource = timeout
        configuration.waitsForConnectivity = false
        session = URLSession(configuration: configuration)
    }

    func data(for request: URLRequest) async throws -> (Data, HTTPURLResponse) {
        let (data, response) = try await session.data(for: request)
        guard let http = response as? HTTPURLResponse else {
            throw APIClientError.invalidResponse
        }
        return (data, http)
    }
}

struct EMSTimelineProvider: TimelineProvider {
    /// Refresh cadence: one entry every 20 minutes. WidgetKit treats this as a hint and coalesces
    /// refreshes for the whole system, so we intentionally do not poll faster.
    private static let refreshInterval: TimeInterval = 20 * 60
    private static let requestTimeout: TimeInterval = 5

    func placeholder(in context: Context) -> EMSEntry {
        // Redacted placeholder for the widget gallery / first render.
        EMSEntry(date: Date(), content: .ready(.sample, stale: false))
    }

    func getSnapshot(in context: Context, completion: @escaping @Sendable (EMSEntry) -> Void) {
        if context.isPreview {
            completion(EMSEntry(date: Date(), content: .ready(.sample, stale: false)))
            return
        }
        // Capture only Sendable locals: `context` (TimelineProviderContext) is not Sendable, so it
        // must not cross into the Task under Swift 6 strict concurrency.
        let family = context.family
        Task {
            completion(await load(family: family))
        }
    }

    func getTimeline(in context: Context, completion: @escaping @Sendable (Timeline<EMSEntry>) -> Void) {
        let family = context.family
        Task {
            let entry = await load(family: family)
            let next = Date().addingTimeInterval(Self.refreshInterval)
            completion(Timeline(entries: [entry], policy: .after(next)))
        }
    }

    // MARK: - Loading

    private func load(family: WidgetFamily) async -> EMSEntry {
        guard let config = AppGroupConfigStore().load() else {
            return EMSEntry(date: Date(), content: .needsSetup)
        }

        let cache = WidgetSnapshotCache()
        do {
            let data = try await fetch(config: config, family: family)
            cache.save(data)
            return EMSEntry(date: Date(), content: .ready(data, stale: false))
        } catch {
            // Graceful stale: keep showing the last good data, marked "as of HH:mm".
            if let last = cache.load() {
                return EMSEntry(date: Date(), content: .ready(last, stale: true))
            }
            return EMSEntry(date: Date(), content: .unreachable)
        }
    }

    private func fetch(config: WidgetServerConfig, family: WidgetFamily) async throws -> WidgetRenderData {
        let client = APIClient(
            baseURL: config.baseURL,
            token: config.token,
            transport: WidgetTransport(timeout: Self.requestTimeout)
        )

        // Status + battery are the load-bearing calls for every size; if either fails we fall back
        // to stale/unreachable rather than draw a half-empty widget.
        async let statusTask = client.fetchStatus()
        async let batteryTask = client.fetchBattery()
        let status = try await statusTask
        let battery = try await batteryTask

        let verdict = WidgetVerdictBuilder.make(dryRun: status.dryRun, mode: battery.currentMode)
        let soc = battery.aggregate?.socPct ?? status.socPct

        var headline: String?
        var carLine: String?
        if family != .systemSmall {
            // Medium adds the status headline sentence (/api/decision) and the next car-charge
            // window (/api/car/plan). Both are best-effort: a degraded backend that omits them
            // simply drops that row instead of blanking the widget.
            async let decisionTask = client.fetchDecision()
            async let carPlanTask = client.fetchCarPlan()
            headline = (try? await decisionTask).flatMap(Self.headline(from:))
            if let carPlan = try? await carPlanTask {
                carLine = WidgetCarLine.text(from: carPlan)
            }
        }

        return WidgetRenderData(
            socPct: soc,
            verdict: verdict,
            headline: headline,
            carLine: carLine,
            asOf: Date()
        )
    }

    private static func headline(from decision: DecisionSnapshot) -> String? {
        let candidate = decision.homeState?.headline
            ?? decision.planReasonExplained
            ?? decision.reason
        guard let candidate, !candidate.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
            return nil
        }
        return candidate
    }
}
