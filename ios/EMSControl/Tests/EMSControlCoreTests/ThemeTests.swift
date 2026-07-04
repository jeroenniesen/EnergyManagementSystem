import XCTest
import EMSControlCore

final class ThemeTests: XCTestCase {
    func testDarkThemeTokensMatchWebPalette() {
        XCTAssertEqual(EMSTheme.dark.background.hex, "#0b0e13")
        XCTAssertEqual(EMSTheme.dark.panel.hex, "#161b23")
        XCTAssertEqual(EMSTheme.dark.secondaryPanel.hex, "#1e242e")
        XCTAssertEqual(EMSTheme.dark.line.hex, "#2a313c")
        XCTAssertEqual(EMSTheme.dark.text.hex, "#e6e9ef")
        XCTAssertEqual(EMSTheme.dark.muted.hex, "#8b95a5")
        XCTAssertEqual(EMSTheme.dark.accent.hex, "#46c8a8")
        XCTAssertEqual(EMSTheme.dark.amber.hex, "#e0a23a")
        XCTAssertEqual(EMSTheme.dark.error.hex, "#f4b0b0")
        XCTAssertEqual(EMSTheme.dark.winter.hex, "#5aa2e0")
    }

    func testLightThemeTokensMatchWebPalette() {
        XCTAssertEqual(EMSTheme.light.background.hex, "#eef1f6")
        XCTAssertEqual(EMSTheme.light.panel.hex, "#ffffff")
        XCTAssertEqual(EMSTheme.light.secondaryPanel.hex, "#f1f4f9")
        XCTAssertEqual(EMSTheme.light.line.hex, "#e2e7ef")
        XCTAssertEqual(EMSTheme.light.text.hex, "#1b2330")
        XCTAssertEqual(EMSTheme.light.muted.hex, "#5c6675")
        XCTAssertEqual(EMSTheme.light.accent.hex, "#1f9e84")
        XCTAssertEqual(EMSTheme.light.amber.hex, "#b07410")
        XCTAssertEqual(EMSTheme.light.error.hex, "#c0392b")
        XCTAssertEqual(EMSTheme.light.winter.hex, "#2f7fc4")
    }
}
