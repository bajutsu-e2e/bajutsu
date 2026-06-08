// swift-tools-version:5.9
import PackageDescription

let package = Package(
    name: "BajutsuKit",
    platforms: [.iOS(.v15), .macOS(.v11)],
    products: [
        .library(name: "BajutsuKit", targets: ["BajutsuKit"]),
    ],
    targets: [
        .target(name: "BajutsuKit"),
    ]
)
