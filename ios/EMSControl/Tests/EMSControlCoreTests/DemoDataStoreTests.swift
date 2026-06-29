import XCTest
@testable import EMSControlCore

final class DemoDataStoreTests: XCTestCase {
    func testDemoDashboardLoadsAndIsMarkedDemo() throws {
        let store = DemoDataStore(bundle: .module)
        let snapshot = try store.dashboardSnapshot()
        XCTAssertEqual(snapshot.serverName, "Demo Home EMS")
        XCTAssertTrue(snapshot.isDemo)
    }
}
