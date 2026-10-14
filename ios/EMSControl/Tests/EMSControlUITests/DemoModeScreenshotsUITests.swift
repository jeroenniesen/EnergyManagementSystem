import XCTest

/// App Store screenshot capture in demo mode (#259). Run via `scripts/ios-screenshots.sh`.
final class DemoModeScreenshotsUITests: XCTestCase {
    override func setUpWithError() throws {
        continueAfterFailure = false
    }

    func testDemoModeScreenshotsMainTabs() throws {
        let outputDir = try XCTUnwrap(
            ProcessInfo.processInfo.environment["EMS_SCREENSHOT_DIR"],
            "EMS_SCREENSHOT_DIR must be set by scripts/ios-screenshots.sh or the EMSControl scheme"
        )
        XCTAssertFalse(outputDir.isEmpty)

        let app = XCUIApplication()
        app.launchArguments = [
            "-UITestingReset",
            "-UITestingScreenshots",
            "-AppleLanguages", "(en)",
            "-AppleLocale", "en_US",
        ]
        app.launch()

        enterDemoMode(app: app)

        let tabBar = app.tabBars.firstMatch
        XCTAssertTrue(tabBar.waitForExistence(timeout: 3))

        captureTab(
            outputDir: outputDir,
            app: app,
            tabBar: tabBar,
            tab: "Dashboard",
            fileName: "01-dashboard",
            readyIdentifier: "dashboard.metric.battery"
        )
        captureTab(
            outputDir: outputDir,
            app: app,
            tabBar: tabBar,
            tab: "Insights",
            fileName: "02-insights",
            readyIdentifier: "insights.report.headline"
        )
        captureTab(
            outputDir: outputDir,
            app: app,
            tabBar: tabBar,
            tab: "Car",
            fileName: "03-car",
            readyIdentifier: "car.tab.content"
        )
        captureTab(
            outputDir: outputDir,
            app: app,
            tabBar: tabBar,
            tab: "Activity",
            fileName: "04-activity",
            readyIdentifier: "activity.entry.first"
        )
        captureTab(
            outputDir: outputDir,
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

        let dashboardReady = app.descendants(matching: .any).matching(identifier: "dashboard.metric.battery")
            .firstMatch
        XCTAssertTrue(dashboardReady.waitForExistence(timeout: 5))
    }

    private func captureTab(
        outputDir: String,
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
        saveScreenshot(named: fileName, outputDir: outputDir)
    }

    private func saveScreenshot(named name: String, outputDir: String) {
        let url = URL(fileURLWithPath: outputDir, isDirectory: true).appendingPathComponent("\(name).png")
        let screenshot = XCUIScreen.main.screenshot()
        do {
            try FileManager.default.createDirectory(atPath: outputDir, withIntermediateDirectories: true)
            try screenshot.pngRepresentation.write(to: url)
        } catch {
            XCTFail("Failed to write \(url.path): \(error)")
        }
        let attachment = XCTAttachment(screenshot: screenshot)
        attachment.name = name
        attachment.lifetime = .keepAlways
        add(attachment)
    }
}
