// swift-tools-version:5.9
import PackageDescription

let package = Package(
    name: "BajutsuKit",
    platforms: [.iOS(.v15), .macOS(.v11)],
    products: [
        .library(name: "BajutsuKit", targets: ["BajutsuKit"]),
        .library(name: "BajutsuRunner", targets: ["BajutsuRunner"]),
    ],
    targets: [
        .target(name: "BajutsuKit"),
        .target(name: "BajutsuRunner"),
        .testTarget(name: "BajutsuKitTests", dependencies: ["BajutsuKit"]),
        .testTarget(name: "BajutsuRunnerTests", dependencies: ["BajutsuRunner"]),
    ]
)
