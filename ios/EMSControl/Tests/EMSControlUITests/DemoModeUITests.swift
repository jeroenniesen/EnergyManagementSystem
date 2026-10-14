import XCTest

final class DemoModeUITests: XCTestCase {
    override func setUpWithError() throws {
        continueAfterFailure = false
    }

    func testDemoModeWalksAllTabs() throws {
        let app = XCUIApplication()
        app.launch()

        let viewDemo = app.buttons["connection.viewDemo"]
        XCTAssertTrue(viewDemo.waitForExistence(timeout: 5))
        viewDemo.tap()

        let banner = app.otherElements["demo.mode.banner"]
        XCTAssertTrue(banner.waitForExistence(timeout: 5))

        let tabBar = app.tabBars.firstMatch
        XCTAssertTrue(tabBar.waitForExistence(timeout: 3))

        for tab in ["Dashboard", "Insights", "Car", "Activity", "Chat"] {
            tabBar.buttons[tab].tap()
            XCTAssertTrue(banner.waitForExistence(timeout: 3), "Demo banner should stay visible on \(tab)")
        }
    }
}
