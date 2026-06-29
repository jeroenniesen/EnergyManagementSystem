import XCTest
@testable import EMSControlCore

final class ThemeTests: XCTestCase {
    func testThemeTokensMatchWebPalette() {
        XCTAssertEqual(EMSTheme.dark.background.hex, "#0b0e13")
        XCTAssertEqual(EMSTheme.dark.panel.hex, "#161b23")
        XCTAssertEqual(EMSTheme.dark.accent.hex, "#46c8a8")
        XCTAssertEqual(EMSTheme.light.background.hex, "#eef1f6")
        XCTAssertEqual(EMSTheme.light.panel.hex, "#ffffff")
        XCTAssertEqual(EMSTheme.light.accent.hex, "#1f9e84")
    }
}
