import XCTest
import EMSControlCore

final class DemoDataStoreTests: XCTestCase {
    func testDemoDashboardLoadsAndIsMarkedDemo() throws {
        let store = DemoDataStore()
        let snapshot = try store.dashboardSnapshot()
        XCTAssertEqual(snapshot.serverName, "Demo Home EMS")
        XCTAssertTrue(snapshot.isDemo)
    }

    func testDemoDashboardIncludesBatteryPlanConfidenceScenarios() throws {
        let store = DemoDataStore()
        let snapshot = try store.dashboardSnapshot()

        XCTAssertEqual(snapshot.batteryPlan.status, "on_track")
        XCTAssertFalse(snapshot.batteryPlan.graph.forecastSoc.isEmpty)

        let statuses = Set(BatteryPlanSnapshot.demoScenarios.map(\.status))
        XCTAssertTrue(statuses.contains("on_track"))
        XCTAssertTrue(statuses.contains("behind_target"))
        XCTAssertTrue(statuses.contains("paused_safely"))
    }
}
