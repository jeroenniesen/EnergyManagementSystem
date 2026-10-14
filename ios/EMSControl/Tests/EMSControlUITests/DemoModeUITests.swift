import XCTest

final class DemoModeUITests: XCTestCase {
    override func setUpWithError() throws {
        continueAfterFailure = false
    }

    func testDemoModeWalksAllTabs() throws {
        let app = XCUIApplication()
        app.launchArguments = ["-UITestingReset"]
        app.launch()

        let viewDemo = app.buttons["connection.viewDemo"]
        XCTAssertTrue(viewDemo.waitForExistence(timeout: 5))
        if !viewDemo.isHittable {
            app.swipeUp()
        }
        viewDemo.tap()

        let banner = app.descendants(matching: .any).matching(identifier: "demo.mode.banner").firstMatch
        XCTAssertTrue(banner.waitForExistence(timeout: 5))

        let tabBar = app.tabBars.firstMatch
        XCTAssertTrue(tabBar.waitForExistence(timeout: 3))

        tabBar.buttons["Dashboard"].tap()
        XCTAssertTrue(
            app.descendants(matching: .any).matching(identifier: "dashboard.metric.battery").firstMatch
                .waitForExistence(timeout: 3)
        )

        tabBar.buttons["Insights"].tap()
        XCTAssertTrue(banner.waitForExistence(timeout: 3))
        XCTAssertTrue(
            app.descendants(matching: .any).matching(identifier: "insights.report.headline").firstMatch
                .waitForExistence(timeout: 5)
        )

        tabBar.buttons["Car"].tap()
        XCTAssertTrue(banner.waitForExistence(timeout: 3))
        XCTAssertTrue(app.otherElements["car.tab.content"].waitForExistence(timeout: 5))

        tabBar.buttons["Activity"].tap()
        XCTAssertTrue(banner.waitForExistence(timeout: 3))
        XCTAssertTrue(app.otherElements["activity.entry.first"].waitForExistence(timeout: 5))

        tabBar.buttons["Chat"].tap()
        XCTAssertTrue(banner.waitForExistence(timeout: 3))
        XCTAssertTrue(app.textFields["chat.question.field"].waitForExistence(timeout: 5))

        app.buttons["dashboard.settings"].tap()
        app.buttons["settings.leaveDemo"].tap()
        app.buttons["Leave Demo"].tap()

        XCTAssertTrue(viewDemo.waitForExistence(timeout: 5))
    }
}
