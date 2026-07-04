import Foundation

public struct HexColor: Equatable, Sendable {
    public let hex: String
    public init(_ hex: String) { self.hex = hex.lowercased() }
}

public struct EMSTheme: Equatable, Sendable {
    public let background: HexColor
    public let panel: HexColor
    public let secondaryPanel: HexColor
    public let line: HexColor
    public let text: HexColor
    public let muted: HexColor
    public let accent: HexColor
    public let amber: HexColor
    public let error: HexColor
    public let winter: HexColor

    public static let dark = EMSTheme(
        background: HexColor("#0b0e13"),
        panel: HexColor("#161b23"),
        secondaryPanel: HexColor("#1e242e"),
        line: HexColor("#2a313c"),
        text: HexColor("#e6e9ef"),
        muted: HexColor("#8b95a5"),
        accent: HexColor("#46c8a8"),
        amber: HexColor("#e0a23a"),
        error: HexColor("#f4b0b0"),
        winter: HexColor("#5aa2e0")
    )

    public static let light = EMSTheme(
        background: HexColor("#eef1f6"),
        panel: HexColor("#ffffff"),
        secondaryPanel: HexColor("#f1f4f9"),
        line: HexColor("#e2e7ef"),
        text: HexColor("#1b2330"),
        muted: HexColor("#5c6675"),
        accent: HexColor("#1f9e84"),
        amber: HexColor("#b07410"),
        error: HexColor("#c0392b"),
        winter: HexColor("#2f7fc4")
    )
}
