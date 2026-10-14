import XCTest

/// App Store screenshot capture in demo mode (#259). Run via `scripts/ios-screenshots.sh`.
final class DemoModeScreenshotsUITests: XCTestCase {
    override func setUpWithError() throws {
        continueAfterFailure = false
    }

    func testDemoModeScreenshotsMainTabs() throws {
        let outputDir = ProcessInfo.processInfo.environment["EMS_SCREENSHOT_DIR"]
        XCTAssertNotNil(outputDir, "EMS_SCREENSHOT_DIR must be set by scripts/ios-screenshots.sh")
        XCTAssertFalse(outputDir?.isEmpty == true)

        let app = XCUIApplication()
        app.launchArguments = ["-UITestingReset"]
        app.launch()

        enterDemoMode(app: app)

        let tabBar = app.tabBars.firstMatch
        XCTAssertTrue(tabBar.waitForExistence(timeout: 3))

        captureTab(
            app: app,
            tabBar: tabBar,
            tab: "Dashboard",
            fileName: "01-dashboard",
            readyIdentifier: "dashboard.metric.battery"
        )
        captureTab(
            app: app,
            tabBar: tabBar,
            tab: "Insights",
            fileName: "02-insights",
            readyIdentifier: "insights.report.headline"
        )
        captureTab(
            app: app,
            tabBar: tabBar,
            tab: "Car",
            fileName: "03-car",
            readyIdentifier: "car.tab.content"
        )
        captureTab(
            app: app,
            tabBar: tabBar,
            tab: "Activity",
            fileName: "04-activity",
            readyIdentifier: "activity.entry.first"
        )
        captureTab(
            app: app,
            tabBar: tabBar,
            tab: "Chat",
            fileName: "05-chat",
            readyIdentifier: "chat.question.field"
        )
    }

    private func enterDemoMode(app: XCUIApplication) {
        let viewDemo = app.buttons["connection.viewDemo"]
        XCTAssertTrue(viewDemo.waitForExistence(timeout: 5))
        if !viewDemo.isHittable {
            app.swipeUp()
        }
        viewDemo.tap()

        let banner = app.descendants(matching: .any).matching(identifier: "demo.mode.banner").firstMatch
        XCTAssertTrue(banner.waitForExistence(timeout: 5))
    }

    private func captureTab(
        app: XCUIApplication,
        tabBar: XCUIElement,
        tab: String,
        fileName: String,
        readyIdentifier: String
    ) {
        tabBar.buttons[tab].tap()
        XCTAssertTrue(
            app.descendants(matching: .any).matching(identifier: readyIdentifier).firstMatch
                .waitForExistence(timeout: 5)
        )
        saveScreenshot(named: fileName)
    }

    private func saveScreenshot(named name: String) {
        guard let dir = ProcessInfo.processInfo.environment["EMS_SCREENSHOT_DIR"], !dir.isEmpty else {
            XCTFail("EMS_SCREENSHOT_DIR missing")
            return
        }
        let url = URL(fileURLWithPath: dir, isDirectory: true).appendingPathComponent("\(name).png")
        let screenshot = XCUIScreen.main.screenshot()
        do {
            try FileManager.default.createDirectory(atPath: dir, withIntermediateDirectories: true)
            try screenshot.pngRepresentation.write(to: url)
        } catch {
            XCTFail("Failed to write \(url.path): \(error)")
        }
    }
}
