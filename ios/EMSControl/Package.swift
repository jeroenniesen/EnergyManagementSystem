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
            resources: [.process("../../Resources")]
        ),
        .testTarget(
            name: "EMSControlCoreTests",
            dependencies: ["EMSControlCore"]
        )
    ]
)
