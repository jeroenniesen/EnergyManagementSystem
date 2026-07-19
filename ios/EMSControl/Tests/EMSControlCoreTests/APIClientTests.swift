import Foundation
import XCTest
@testable import EMSControlCore

final class APIClientTests: XCTestCase {
    func testAuthorizationHeaderUsesBearerToken() async throws {
        let transport = RecordingTransport(data: statusJSON())
        let client = APIClient(baseURL: URL(string: "http://ems.local:8080")!, token: "abc123", transport: transport)

        _ = try await client.fetchStatus()

        XCTAssertEqual(transport.lastRequest?.value(forHTTPHeaderField: "Authorization"), "Bearer abc123")
    }

    func testFetchStatusUsesProductionPath() async throws {
        let transport = RecordingTransport(data: statusJSON())
        let client = APIClient(baseURL: URL(string: "http://ems.local:8080")!, transport: transport)

        let response = try await client.fetchStatus()

        XCTAssertEqual(response.socPct, 55.0)
        XCTAssertEqual(response.devMode, "mock")
        XCTAssertEqual(transport.lastRequest?.url?.path, "/api/status")
        XCTAssertEqual(transport.lastRequest?.httpMethod, "GET")
    }

    func testFetchDecisionDecodesHomeState() async throws {
        let transport = RecordingTransport(data: decisionJSON())
        let client = APIClient(baseURL: URL(string: "http://ems.local:8080")!, transport: transport)

        let response = try await client.fetchDecision()

        XCTAssertEqual(response.intent, "allow_self_consumption")
        XCTAssertEqual(response.homeState?.headline, "Watching - the battery is running the house")
        XCTAssertEqual(response.homeState?.tone, "watching")
        XCTAssertEqual(transport.lastRequest?.url?.path, "/api/decision")
    }

    func testFetchEnergyStoryUsesNextWindowQuery() async throws {
        let transport = RecordingTransport(data: energyStoryJSON())
        let client = APIClient(baseURL: URL(string: "http://ems.local:8080")!, transport: transport)

        let response = try await client.fetchEnergyStory()

        XCTAssertEqual(response.headline, "Next 24h - your solar fills the battery.")
        XCTAssertEqual(response.slots.count, 3)
        XCTAssertEqual(response.recent.count, 2)
        XCTAssertEqual(response.slots[1].action, "grid_charge")
        XCTAssertEqual(response.slots[2].socPct, 64.0)
        XCTAssertEqual(response.recent[0].solarW, 6200.0)
        XCTAssertEqual(transport.lastRequest?.url?.path, "/api/energy-story")
        XCTAssertEqual(transport.lastRequest?.url?.query, "window=next")
    }

    func testFetchBatteryPlanUsesSharedContract() async throws {
        let transport = RecordingTransport(data: batteryPlanJSON())
        let client = APIClient(baseURL: URL(string: "http://ems.local:8080")!, transport: transport)

        let response = try await client.fetchBatteryPlan()

        XCTAssertEqual(response.status, "on_track")
        XCTAssertEqual(response.currentAction, "self_consumption")
        XCTAssertEqual(response.plannedGridTopupKwh, 0.0)
        XCTAssertEqual(response.graph.forecastSoc.count, 2)
        XCTAssertEqual(response.graph.plannedActions[0].action, "solar_charge")
        XCTAssertEqual(response.deviation.status, "ok")
        XCTAssertEqual(transport.lastRequest?.url?.path, "/api/battery-plan")
    }

    func testFetchReportAndFinanceUseDayPeriod() async throws {
        let reportTransport = RecordingTransport(data: reportJSON())
        let financeTransport = RecordingTransport(data: financeJSON())
        let baseURL = URL(string: "http://ems.local:8080")!

        let report = try await APIClient(baseURL: baseURL, transport: reportTransport).fetchReport()
        let finance = try await APIClient(baseURL: baseURL, transport: financeTransport).fetchFinance()

        XCTAssertEqual(report.scores.map(\.key), ["self_consumption", "co2", "best_price"])
        XCTAssertEqual(finance.totals.savedEur, 0.01)
        XCTAssertEqual(reportTransport.lastRequest?.url?.path, "/api/report")
        XCTAssertEqual(reportTransport.lastRequest?.url?.query, "period=day")
        XCTAssertEqual(financeTransport.lastRequest?.url?.path, "/api/finance")
        XCTAssertEqual(financeTransport.lastRequest?.url?.query, "period=day")
    }

    func testFetchReportAndFinanceUseSelectedPeriodAndDate() async throws {
        let reportTransport = RecordingTransport(data: reportJSON())
        let financeTransport = RecordingTransport(data: financeJSON())
        let baseURL = URL(string: "http://ems.local:8080")!

        _ = try await APIClient(baseURL: baseURL, transport: reportTransport)
            .fetchReport(period: .month, anchor: "2026-06-15")
        _ = try await APIClient(baseURL: baseURL, transport: financeTransport)
            .fetchFinance(period: .month, anchor: "2026-06-15")

        XCTAssertEqual(reportTransport.lastRequest?.url?.path, "/api/report")
        XCTAssertEqual(reportTransport.lastRequest?.url?.query, "period=month&date=2026-06-15")
        XCTAssertEqual(financeTransport.lastRequest?.url?.path, "/api/finance")
        XCTAssertEqual(financeTransport.lastRequest?.url?.query, "period=month&date=2026-06-15")
    }

    func testDashboardCompositionKeepsCoreStatusWhenOptionalEndpointFails() async throws {
        let transport = PartiallyFailingDashboardTransport(failingPath: "/api/finance")
        let client = APIClient(baseURL: URL(string: "http://ems.local:8080")!, transport: transport)

        let snapshot = try await client.fetchDashboard()

        XCTAssertEqual(snapshot.status.socPct, 55.0)
        XCTAssertEqual(snapshot.decision.homeState?.tone, "watching")
        XCTAssertEqual(snapshot.batteryPlan.status, "on_track")
        XCTAssertEqual(snapshot.batteryPlan.graph.priceWindows.count, 1)
        XCTAssertNil(snapshot.finance.totals.savedEur)
        XCTAssertTrue(snapshot.carPlan.enabled)
        XCTAssertEqual(snapshot.carPlan.plan?.deadlines.first?.minPct, 80)
    }

    func testFetchFAQUsesExpectedPathAndAuthorizationHeader() async throws {
        let transport = RecordingTransport(data: faqJSON())
        let client = APIClient(baseURL: URL(string: "http://ems.local:8080")!, token: "abc123", transport: transport)

        let response = try await client.fetchFAQ()

        XCTAssertEqual(response.items.count, 1)
        XCTAssertEqual(transport.lastRequest?.url?.path, "/api/faq")
        XCTAssertEqual(transport.lastRequest?.httpMethod, "GET")
        XCTAssertEqual(transport.lastRequest?.value(forHTTPHeaderField: "Authorization"), "Bearer abc123")
    }

    func testFetchAuthStatusUsesExpectedPathAndAuthorizationHeader() async throws {
        let transport = RecordingTransport(data: authJSON(required: true, authenticated: true))
        let client = APIClient(baseURL: URL(string: "http://ems.local:8080")!, token: "abc123", transport: transport)

        let response = try await client.fetchAuthStatus()

        XCTAssertEqual(response, AuthStatus(required: true, authenticated: true))
        XCTAssertEqual(transport.lastRequest?.url?.path, "/api/auth")
        XCTAssertEqual(transport.lastRequest?.httpMethod, "GET")
        XCTAssertEqual(transport.lastRequest?.value(forHTTPHeaderField: "Authorization"), "Bearer abc123")
    }

    func testFetchHealthUsesExpectedPaths() async throws {
        let liveTransport = RecordingTransport(data: healthJSON(status: "alive"))
        let readyTransport = RecordingTransport(data: healthJSON(status: "ready"))

        let live = try await APIClient(
            baseURL: URL(string: "http://ems.local:8080")!,
            transport: liveTransport
        ).fetchLiveHealth()
        let ready = try await APIClient(
            baseURL: URL(string: "http://ems.local:8080")!,
            transport: readyTransport
        ).fetchReadyHealth()

        XCTAssertEqual(live, HealthStatus(status: "alive"))
        XCTAssertEqual(ready, HealthStatus(status: "ready"))
        XCTAssertEqual(liveTransport.lastRequest?.url?.path, "/health/live")
        XCTAssertEqual(readyTransport.lastRequest?.url?.path, "/health/ready")
    }

    func testLoginPostsCredentialsWithoutAuthHeaderAndDecodesResponse() async throws {
        let transport = RecordingTransport(data: loginJSON())
        let client = APIClient(baseURL: URL(string: "http://ems.local:8080")!, transport: transport)

        let response = try await client.login(username: "jeroen", password: "hunter2000")

        XCTAssertEqual(response.token, "session-xyz")
        XCTAssertEqual(response.user, AuthUser(username: "jeroen", role: "admin"))
        XCTAssertEqual(transport.lastRequest?.url?.path, "/api/auth/login")
        XCTAssertEqual(transport.lastRequest?.httpMethod, "POST")
        XCTAssertEqual(transport.lastRequest?.value(forHTTPHeaderField: "Content-Type"), "application/json")
        // Login is auth-exempt: it must NOT carry a leftover/stale bearer.
        XCTAssertNil(transport.lastRequest?.value(forHTTPHeaderField: "Authorization"))

        let body = try XCTUnwrap(transport.lastRequest?.httpBody)
        let json = try JSONSerialization.jsonObject(with: body) as? [String: Any]
        XCTAssertEqual(json?["username"] as? String, "jeroen")
        XCTAssertEqual(json?["password"] as? String, "hunter2000")
    }

    func testLoginSurfacesUnauthorizedAsHTTPStatus() async throws {
        let transport = StatusCodeTransport(data: Data(#"{"detail":"invalid credentials"}"#.utf8), statusCode: 401)
        let client = APIClient(baseURL: URL(string: "http://ems.local:8080")!, transport: transport)

        do {
            _ = try await client.login(username: "jeroen", password: "wrong")
            XCTFail("Expected login to throw on 401")
        } catch let error as APIClientError {
            XCTAssertEqual(error, .httpStatus(401))
        }
    }

    func testProvisionWidgetTokenSendsReplaceAndNameWithBearer() async throws {
        let transport = RecordingTransport(data: tokenJSON(name: "iOS widget · Jeroen's iPhone"))
        let client = APIClient(baseURL: URL(string: "http://ems.local:8080")!, token: "session-xyz", transport: transport)

        let response = try await client.provisionWidgetToken(name: "iOS widget · Jeroen's iPhone")

        XCTAssertEqual(response.token, "access-abc")
        XCTAssertEqual(response.name, "iOS widget · Jeroen's iPhone")
        XCTAssertEqual(transport.lastRequest?.url?.path, "/api/auth/tokens")
        XCTAssertEqual(transport.lastRequest?.httpMethod, "POST")
        // Provisioning REQUIRES the session bearer.
        XCTAssertEqual(transport.lastRequest?.value(forHTTPHeaderField: "Authorization"), "Bearer session-xyz")

        let body = try XCTUnwrap(transport.lastRequest?.httpBody)
        let json = try JSONSerialization.jsonObject(with: body) as? [String: Any]
        XCTAssertEqual(json?["name"] as? String, "iOS widget · Jeroen's iPhone")
        XCTAssertEqual(json?["replace"] as? Bool, true)
    }

    func testFetchExplainerUsesExpectedPathAndAuthorizationHeader() async throws {
        let transport = RecordingTransport(data: explainerJSON())
        let client = APIClient(baseURL: URL(string: "http://ems.local:8080")!, token: "abc123", transport: transport)

        let response = try await client.fetchExplainer()

        XCTAssertEqual(response, ExplainerStatus(mode: "external_llm", active: true, language: "nl"))
        XCTAssertEqual(transport.lastRequest?.url?.path, "/api/explainer")
        XCTAssertEqual(transport.lastRequest?.httpMethod, "GET")
        XCTAssertEqual(transport.lastRequest?.value(forHTTPHeaderField: "Authorization"), "Bearer abc123")
    }

    func testSendChatUsesJSONBodyAndAuthorizationHeader() async throws {
        let transport = RecordingTransport(data: chatJSON())
        let client = APIClient(baseURL: URL(string: "http://ems.local:8080")!, token: "abc123", transport: transport)

        let response = try await client.sendChat(question: "Why is the battery charging?")

        XCTAssertEqual(response.answer, "Because prices are low.")
        XCTAssertEqual(transport.lastRequest?.url?.path, "/api/chat")
        XCTAssertEqual(transport.lastRequest?.httpMethod, "POST")
        XCTAssertEqual(transport.lastRequest?.value(forHTTPHeaderField: "Authorization"), "Bearer abc123")
        XCTAssertEqual(transport.lastRequest?.value(forHTTPHeaderField: "Content-Type"), "application/json")
        XCTAssertEqual(
            transport.lastRequest?.httpBody,
            try JSONEncoder.ems.encode(ChatRequest(question: "Why is the battery charging?"))
        )
    }

    func testFetchCarPlanUsesExpectedPathAndAuthorizationHeader() async throws {
        let transport = RecordingTransport(data: carPlanFullJSON())
        let client = APIClient(baseURL: URL(string: "http://ems.local:8080")!, token: "abc123", transport: transport)

        let response = try await client.fetchCarPlan()

        XCTAssertTrue(response.enabled)
        XCTAssertEqual(response.effectiveKw, 11.0)
        XCTAssertEqual(response.plan?.deadlines.first?.minPct, 80)
        XCTAssertEqual(response.car?.id, "tesla-model-y-long-range")
        XCTAssertEqual(transport.lastRequest?.url?.path, "/api/car/plan")
        XCTAssertEqual(transport.lastRequest?.httpMethod, "GET")
        XCTAssertEqual(transport.lastRequest?.value(forHTTPHeaderField: "Authorization"), "Bearer abc123")
    }

    func testSetCarSocPostsPctBodyAndParsesReturnedSoc() async throws {
        let transport = RecordingTransport(data: carSocResponseJSON())
        let client = APIClient(baseURL: URL(string: "http://ems.local:8080")!, token: "abc123", transport: transport)

        let soc = try await client.setCarSoc(pct: 55)

        XCTAssertEqual(soc?.socPct, 55.0)
        XCTAssertEqual(soc?.anchorPct, 55.0)
        XCTAssertEqual(transport.lastRequest?.url?.path, "/api/car/soc")
        XCTAssertEqual(transport.lastRequest?.httpMethod, "POST")
        XCTAssertEqual(transport.lastRequest?.value(forHTTPHeaderField: "Content-Type"), "application/json")
        XCTAssertEqual(transport.lastRequest?.value(forHTTPHeaderField: "Authorization"), "Bearer abc123")

        let body = try XCTUnwrap(transport.lastRequest?.httpBody)
        let json = try JSONSerialization.jsonObject(with: body) as? [String: Any]
        XCTAssertEqual(json?["pct"] as? Int, 55)
    }

    func testSetCarSocSurfacesUnauthorized() async throws {
        let transport = StatusCodeTransport(data: Data("{\"detail\":\"unauthorized\"}".utf8), statusCode: 401)
        let client = APIClient(baseURL: URL(string: "http://ems.local:8080")!, transport: transport)

        do {
            _ = try await client.setCarSoc(pct: 50)
            XCTFail("Expected setCarSoc to throw on 401")
        } catch let error as APIClientError {
            XCTAssertEqual(error, .httpStatus(401))
        }
    }

    func testDashboardCarPlanFallsBackToEmptyWhenCarPlanEndpointFails() async throws {
        let transport = PartiallyFailingDashboardTransport(failingPath: "/api/car/plan")
        let client = APIClient(baseURL: URL(string: "http://ems.local:8080")!, transport: transport)

        let snapshot = try await client.fetchDashboard()

        XCTAssertFalse(snapshot.carPlan.enabled)
        XCTAssertNil(snapshot.carPlan.plan)
        XCTAssertEqual(snapshot.status.socPct, 55.0)
    }
}

private final class RecordingTransport: HTTPTransport, @unchecked Sendable {
    var lastRequest: URLRequest?
    let data: Data

    init(data: Data) { self.data = data }

    func data(for request: URLRequest) async throws -> (Data, HTTPURLResponse) {
        lastRequest = request
        return (data, HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!)
    }
}

private final class PartiallyFailingDashboardTransport: HTTPTransport, @unchecked Sendable {
    let failingPath: String

    init(failingPath: String) {
        self.failingPath = failingPath
    }

    func data(for request: URLRequest) async throws -> (Data, HTTPURLResponse) {
        let path = request.url?.path ?? ""
        if path == failingPath {
            return (
                #"{"detail":"temporary failure"}"#.data(using: .utf8)!,
                HTTPURLResponse(url: request.url!, statusCode: 503, httpVersion: nil, headerFields: nil)!
            )
        }

        let data: Data
        switch path {
        case "/api/status":
            data = statusJSON()
        case "/api/freshness":
            data = #"{"battery":"fresh","ev":"fresh","grid":"fresh","soc":"fresh","solar":"fresh"}"#.data(using: .utf8)!
        case "/api/decision":
            data = decisionJSON()
        case "/api/alerts":
            data = #"{"data_quality":"complete","alerts":[]}"#.data(using: .utf8)!
        case "/api/battery":
            data = #"{"current_mode":"auto","capabilities":null,"towers":[],"aggregate":null}"#.data(using: .utf8)!
        case "/api/charge-need":
            data = #"{"usable_kwh":10.8,"current_soc_pct":55.0,"current_kwh":5.94,"reserve_kwh":1.08,"target_kwh":9.51,"target_soc_pct":88.1,"deficit_kwh":3.57,"on_track":false,"reason":"Need more charge."}"#.data(using: .utf8)!
        case "/api/savings":
            data = #"{"today_eur":0.0}"#.data(using: .utf8)!
        case "/api/energy-story":
            data = energyStoryJSON()
        case "/api/battery-plan":
            data = batteryPlanJSON()
        case "/api/report":
            data = reportJSON()
        case "/api/finance":
            data = financeJSON()
        case "/api/car/plan":
            data = carPlanFullJSON()
        default:
            data = #"{"detail":"unexpected path"}"#.data(using: .utf8)!
            return (data, HTTPURLResponse(url: request.url!, statusCode: 404, httpVersion: nil, headerFields: nil)!)
        }
        return (data, HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!)
    }
}

private final class StatusCodeTransport: HTTPTransport, @unchecked Sendable {
    var lastRequest: URLRequest?
    let data: Data
    let statusCode: Int

    init(data: Data, statusCode: Int) {
        self.data = data
        self.statusCode = statusCode
    }

    func data(for request: URLRequest) async throws -> (Data, HTTPURLResponse) {
        lastRequest = request
        return (data, HTTPURLResponse(url: request.url!, statusCode: statusCode, httpVersion: nil, headerFields: nil)!)
    }
}

private func carPlanFullJSON() -> Data {
    """
    {
      "enabled": true,
      "effective_kw": 11.0,
      "soc": {
        "soc_pct": 20.0,
        "anchor_pct": 20.0,
        "anchor_ts": "2026-07-12T06:00:00+00:00",
        "added_kwh": 0.0,
        "sessions_since_anchor": 0,
        "age_hours": 5.0,
        "stale": false
      },
      "plan": {
        "soc": 20.0,
        "deadlines": [
          {"ready_by": "2026-07-13T07:30:00+02:00", "min_pct": 80, "required_kwh": 34.5, "planned_kwh": 34.5, "pending_kwh": 34.5, "shortfall_kwh": 0.0, "already_met": false, "feasible": true}
        ],
        "slots": [],
        "windows": [
          {"start": "2026-07-13T02:00:00+02:00", "end": "2026-07-13T05:30:00+02:00", "ac_kwh": 38.3, "battery_kwh": 34.5, "est_cost_eur": 4.05, "solar_share_pct": 0, "reason": "Cheapest slots."}
        ],
        "advice": "Plug in overnight to reach 80%.",
        "total_est_cost_eur": 4.05,
        "total_planned_kwh": 34.5
      },
      "car": {"id": "tesla-model-y-long-range", "brand": "Tesla", "model": "Model Y Long Range", "battery_net_kwh": 75.0, "max_ac_kw": 11.0, "years": "2020–present"}
    }
    """.data(using: .utf8)!
}

private func carSocResponseJSON() -> Data {
    """
    {
      "soc": {
        "soc_pct": 55.0,
        "anchor_pct": 55.0,
        "anchor_ts": "2026-07-12T12:00:00+00:00",
        "added_kwh": 0.0,
        "sessions_since_anchor": 0,
        "age_hours": 0.0,
        "stale": false
      }
    }
    """.data(using: .utf8)!
}

private func batteryPlanJSON() -> Data {
    """
    {
      "status": "on_track",
      "summary": "Tonight is covered.",
      "current_action": "self_consumption",
      "current_reason": "Battery is following the current plan.",
      "window_start": "2026-07-03T21:15:00+02:00",
      "window_end": "2026-07-03T21:45:00+02:00",
      "current_soc_pct": 55.0,
      "reserve_soc_pct": 10.0,
      "target_soc_pct": 88.1,
      "target_deadline": "2026-07-04T20:45:00+02:00",
      "planned_grid_topup_kwh": 0.0,
      "deviation": {"status": "ok", "message": "Actual battery level is close to plan."},
      "warnings": [],
      "graph": {
        "forecast_soc": [
          {"ts": "2026-07-03T21:15:00+02:00", "soc_pct": 58.0},
          {"ts": "2026-07-03T21:30:00+02:00", "soc_pct": 61.0}
        ],
        "actual_soc": [
          {"ts": "2026-07-03T21:00:00+02:00", "soc_pct": 55.0}
        ],
        "reserve_line": [
          {"ts": "2026-07-03T21:15:00+02:00", "soc_pct": 10.0},
          {"ts": "2026-07-03T21:45:00+02:00", "soc_pct": 10.0}
        ],
        "target_line": [
          {"ts": "2026-07-03T21:15:00+02:00", "soc_pct": 88.1},
          {"ts": "2026-07-03T21:45:00+02:00", "soc_pct": 88.1}
        ],
        "planned_actions": [
          {"start": "2026-07-03T21:15:00+02:00", "end": "2026-07-03T21:30:00+02:00", "action": "solar_charge"}
        ],
        "price_windows": [
          {"start": "2026-07-03T21:30:00+02:00", "end": "2026-07-03T21:45:00+02:00", "min_eur_per_kwh": 0.45, "max_eur_per_kwh": 0.45}
        ],
        "solar": [
          {"ts": "2026-07-03T21:15:00+02:00", "forecast_w": 1500.0, "actual_w": null}
        ]
      }
    }
    """.data(using: .utf8)!
}

private func statusJSON() -> Data {
    """
    {
      "dry_run": true,
      "dev_mode": "mock",
      "soc_pct": 55.0,
      "grid_power_w": 200.0,
      "solar_power_w": 0.0,
      "battery_power_w": 800.0,
      "house_load_w": 1000.0,
      "non_ev_load_w": 1000.0
    }
    """.data(using: .utf8)!
}

private func decisionJSON() -> Data {
    """
    {
      "intent": "allow_self_consumption",
      "desired_mode": "auto",
      "applied": false,
      "outcome": "dry_run",
      "reason": "dry-run: would set auto",
      "plan_reason": "running the house on the battery",
      "plan_reason_explained": "running the house on the battery",
      "override_active": false,
      "car_charging": false,
      "target_soc": null,
      "home_state": {
        "headline": "Watching - the battery is running the house",
        "tone": "watching",
        "simulated": true
      }
    }
    """.data(using: .utf8)!
}

private func energyStoryJSON() -> Data {
    """
    {
      "window": "next",
      "now": "2026-07-03T19:18:31.519577+00:00",
      "current_soc_pct": 55.0,
      "reserve_soc_pct": 10.0,
      "target_soc_pct": 88.1,
      "target_kwh": 9.5,
      "target_deadline": "2026-07-04T20:45:00+02:00",
      "current_price_eur_per_kwh": 0.12,
      "slots": [
        { "start": "2026-07-03T21:15:00+02:00", "soc_pct": 58.0, "grid_w": 0.0, "solar_w": 1500.0, "battery_w": -900.0, "load_w": 600.0, "eur_per_kwh": 0.12, "action": "solar_charge" },
        { "start": "2026-07-03T21:30:00+02:00", "soc_pct": 61.0, "grid_w": 1000.0, "solar_w": 0.0, "battery_w": -1000.0, "load_w": 600.0, "eur_per_kwh": 0.08, "action": "grid_charge" },
        { "start": "2026-07-03T21:45:00+02:00", "soc_pct": 64.0, "grid_w": 0.0, "solar_w": 0.0, "battery_w": 800.0, "load_w": 800.0, "eur_per_kwh": 0.45, "action": "discharge" }
      ],
      "totals": {
        "import_kwh": 0.0,
        "export_kwh": 3.26,
        "solar_kwh": 17.97,
        "charge_kwh": 8.72,
        "grid_charge_kwh": 0.0,
        "solar_charge_kwh": 8.71,
        "discharge_kwh": 5.16,
        "load_kwh": 11.15,
        "grid_cost_eur": -0.62,
        "self_sufficiency_pct": 100.0,
        "soc_start_pct": 52.8,
        "soc_end_pct": 81.2,
        "soc_min_pct": 23.4,
        "soc_max_pct": 100.0
      },
      "headline": "Next 24h - your solar fills the battery.",
      "trust_markers": ["Reserve respected"],
      "recent": [
        { "start": "2026-07-03T20:45:00+02:00", "soc_pct": 52.0, "grid_w": 0.0, "solar_w": 6200.0, "battery_w": -2500.0, "load_w": 900.0, "eur_per_kwh": 0.12, "action": "solar_charge" },
        { "start": "2026-07-03T21:00:00+02:00", "soc_pct": 55.0, "grid_w": 0.0, "solar_w": 5800.0, "battery_w": -2400.0, "load_w": 900.0, "eur_per_kwh": 0.12, "action": "solar_charge" }
      ],
      "recent_hours": 3,
      "on_track": {
        "status": "ahead",
        "actual_soc_pct": 55.0,
        "target_soc_pct": 88.1,
        "deficit_kwh": 3.6,
        "message": "On track."
      },
      "recent_review": {
        "hours": 3,
        "solar_actual_kwh": 0.0,
        "solar_forecast_kwh": 0.0,
        "solar_pct_of_forecast": null,
        "battery_charged_kwh": 0.0,
        "battery_discharged_kwh": 0.2,
        "message": "Last 3h: battery -0.2 kWh."
      }
    }
    """.data(using: .utf8)!
}

private func reportJSON() -> Data {
    """
    {
      "period": "day",
      "label": "2026-07-03",
      "partial": true,
      "flows": {"has_data": true, "self_sufficiency_pct": 80.0},
      "scores": [
        {"key": "self_consumption", "label": "Self-consumption", "value": 80.0, "raw": 80.0, "unit": "%", "explanation": "No solar this period."},
        {"key": "co2", "label": "CO2", "value": 80.0, "raw": 0.0, "unit": "kg", "explanation": "Avoided CO2."},
        {"key": "best_price", "label": "Best price", "value": 100.0, "raw": 0.12, "unit": "EUR/kWh", "explanation": "Prices were flat."}
      ],
      "series": []
    }
    """.data(using: .utf8)!
}

private func financeJSON() -> Data {
    """
    {
      "period": "day",
      "label": "2026-07-03",
      "partial": true,
      "days": [],
      "totals": {
        "grid_cost_eur": 0.01,
        "battery_cost_eur": 0.01,
        "saved_eur": 0.01,
        "grid_import_kwh": 0.05,
        "grid_export_kwh": 0.0,
        "days_with_prices": 1,
        "days_with_data": 1
      }
    }
    """.data(using: .utf8)!
}

private func faqJSON() -> Data {
    """
    {
      "ai_on": true,
      "items": [
        { "key": "plan", "question": "What is the plan?", "answer": "Charge before sunset." }
      ]
    }
    """.data(using: .utf8)!
}

private func explainerJSON() -> Data {
    """
    {
      "mode": "external_llm",
      "active": true,
      "language": "nl"
    }
    """.data(using: .utf8)!
}

private func chatJSON() -> Data {
    """
    {
      "answer": "Because prices are low.",
      "source": "faq"
    }
    """.data(using: .utf8)!
}

private func loginJSON() -> Data {
    """
    {
      "token": "session-xyz",
      "user": { "username": "jeroen", "role": "admin" }
    }
    """.data(using: .utf8)!
}

private func tokenJSON(name: String) -> Data {
    try! JSONSerialization.data(withJSONObject: ["token": "access-abc", "name": name])
}

private func authJSON(required: Bool, authenticated: Bool) -> Data {
    """
    {
      "required": \(required),
      "authenticated": \(authenticated)
    }
    """.data(using: .utf8)!
}

private func healthJSON(status: String) -> Data {
    """
    {
      "status": "\(status)"
    }
    """.data(using: .utf8)!
}
