// swift-tools-version: 6.0
import PackageDescription

let package = Package(
    name: "EMSControl",
    platforms: [.iOS(.v17), .macOS(.v14)],
    products: [
        .library(name: "EMSControlCore", targets: ["EMSControlCore"])
    ],
    targets: [
        .target(
            name: "EMSControlCore",
            // Demo JSON fixtures + privacy manifest for App Group UserDefaults (#251).
            resources: [
                .process("../../Resources"),
                .copy("PrivacyInfo.xcprivacy"),
            ]
        ),
        .testTarget(
            name: "EMSControlCoreTests",
            dependencies: ["EMSControlCore"]
        )
    ]
)
