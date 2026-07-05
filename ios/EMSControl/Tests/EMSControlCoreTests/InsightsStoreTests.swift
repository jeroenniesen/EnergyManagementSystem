import Foundation
import XCTest
@testable import EMSControlCore

@MainActor
final class InsightsStoreTests: XCTestCase {
    func testRefreshLoadsReportAndFinanceForCurrentPeriodAndAnchor() async {
        let transport = InsightsRoutingTransport()
        let client = APIClient(baseURL: URL(string: "http://ems.local:8080")!, transport: transport)
        let store = InsightsStore(client: client, period: .week, anchor: "2026-07-01")

        await store.refresh()

        XCTAssertEqual(store.report?.label, "2026-07-01")
        XCTAssertEqual(store.finance?.totals.savedEur, 0.46)
        XCTAssertFalse(store.isLoading)
        XCTAssertNil(store.errorMessage)
        XCTAssertEqual(transport.requestedQueries, [
            "/api/report?period=week&date=2026-07-01",
            "/api/finance?period=week&date=2026-07-01",
        ])
    }

    func testChangingPeriodResetsAnchorToTodayAndRefreshes() async {
        let transport = InsightsRoutingTransport()
        let client = APIClient(baseURL: URL(string: "http://ems.local:8080")!, transport: transport)
        let store = InsightsStore(
            client: client,
            period: .month,
            anchor: "2026-06-15",
            today: { "2026-07-04" }
        )

        await store.setPeriod(.day)

        XCTAssertEqual(store.period, .day)
        XCTAssertEqual(store.anchor, "2026-07-04")
        XCTAssertTrue(transport.requestedQueries.contains("/api/report?period=day&date=2026-07-04"))
        XCTAssertTrue(transport.requestedQueries.contains("/api/finance?period=day&date=2026-07-04"))
    }

    func testMovingPeriodUsesPeriodStepAndRefreshes() async {
        let transport = InsightsRoutingTransport()
        let client = APIClient(baseURL: URL(string: "http://ems.local:8080")!, transport: transport)
        let store = InsightsStore(client: client, period: .week, anchor: "2026-07-04")

        await store.movePeriod(direction: -1)

        XCTAssertEqual(store.anchor, "2026-06-27")
        XCTAssertEqual(transport.requestedQueries.first, "/api/report?period=week&date=2026-06-27")
    }

    func testRefreshFailureKeepsPreviousReportAndSetsError() async {
        let transport = InsightsRoutingTransport()
        let client = APIClient(baseURL: URL(string: "http://ems.local:8080")!, transport: transport)
        let store = InsightsStore(client: client, period: .day, anchor: "2026-07-04")
        await store.refresh()
        let previous = store.report

        transport.shouldFail = true
        await store.refresh()

        XCTAssertEqual(store.report, previous)
        XCTAssertNotNil(store.errorMessage)
        XCTAssertFalse(store.isLoading)
    }
}

private final class InsightsRoutingTransport: HTTPTransport, @unchecked Sendable {
    private let lock = NSLock()
    private var _requestedQueries: [String] = []
    var shouldFail = false

    var requestedQueries: [String] {
        lock.withLock { _requestedQueries }
    }

    func data(for request: URLRequest) async throws -> (Data, HTTPURLResponse) {
        let path = request.url?.path ?? ""
        let query = request.url?.query.map { "?\($0)" } ?? ""
        lock.withLock {
            _requestedQueries.append("\(path)\(query)")
        }

        if shouldFail {
            return (
                #"{"detail":"temporary failure"}"#.data(using: .utf8)!,
                HTTPURLResponse(url: request.url!, statusCode: 503, httpVersion: nil, headerFields: nil)!
            )
        }

        let data: Data
        switch path {
        case "/api/report":
            data = reportJSON()
        case "/api/finance":
            data = financeJSON()
        default:
            data = #"{"detail":"unexpected path"}"#.data(using: .utf8)!
            return (data, HTTPURLResponse(url: request.url!, statusCode: 404, httpVersion: nil, headerFields: nil)!)
        }
        return (data, HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!)
    }
}

private func reportJSON() -> Data {
    """
    {
      "period": "week",
      "label": "2026-07-01",
      "partial": false,
      "flows": {
        "has_data": true,
        "partial": false,
        "solar_kwh": 12.4,
        "grid_import_kwh": 2.1,
        "grid_export_kwh": 0.4,
        "battery_charge_kwh": 4.2,
        "battery_discharge_kwh": 3.8,
        "home_kwh": 9.6,
        "car_kwh": 1.2,
        "car_guard_leak_kwh": 0.0,
        "self_sufficiency_pct": 80.0,
        "solar_self_consumption_pct": 91.0
      },
      "scores": [
        {"key": "self_consumption", "label": "Self-consumption", "value": 80.0, "raw": 80.0, "unit": "%", "explanation": "Most load was local."},
        {"key": "co2", "label": "CO2", "value": 74.0, "raw": 2.4, "unit": "kg", "explanation": "Solar and battery avoided grid CO2."}
      ],
      "series": [
        {"start": "2026-07-03T10:00:00+02:00", "grid_import_kwh": 0.2, "grid_export_kwh": 0.0, "house_kwh": 0.5, "car_kwh": 0.1, "solar_kwh": 0.8, "samples": 4}
      ]
    }
    """.data(using: .utf8)!
}

private func financeJSON() -> Data {
    """
    {
      "period": "week",
      "label": "2026-07-01",
      "partial": false,
      "days": [
        {"day": "2026-07-01", "has_data": true, "price_coverage": 1.0, "grid_cost_eur": 1.20, "battery_cost_eur": 0.14, "baseline_cost_eur": 1.80, "saved_eur": 0.46, "grid_import_kwh": 5.2, "grid_export_kwh": 0.2}
      ],
      "totals": {
        "grid_cost_eur": 1.20,
        "battery_cost_eur": 0.14,
        "saved_eur": 0.46,
        "days_with_prices": 1,
        "days_with_data": 1
      }
    }
    """.data(using: .utf8)!
}
