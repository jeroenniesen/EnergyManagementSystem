import XCTest
import EMSControlCore

final class DemoDataStoreTests: XCTestCase {
    func testDemoDashboardLoadsAndIsMarkedDemo() throws {
        let store = DemoDataStore()
        let snapshot = try store.dashboardSnapshot()
        XCTAssertEqual(snapshot.serverName, "Demo Home EMS")
        XCTAssertTrue(snapshot.isDemo)
    }
}
