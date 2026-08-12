// swift-tools-version:5.9
import PackageDescription

let package = Package(
    name: "BajutsuKit",
    platforms: [.iOS(.v15), .macOS(.v11)],
    products: [
        .library(name: "BajutsuKit", targets: ["BajutsuKit"]),
        .library(name: "BajutsuRunner", targets: ["BajutsuRunner"]),
    ],
    dependencies: [
        .package(url: "https://github.com/apple/swift-openapi-generator", exact: "1.13.0"),
        .package(url: "https://github.com/apple/swift-openapi-runtime", exact: "1.12.0"),
    ],
    targets: [
        .target(name: "BajutsuKit"),
        // An Objective-C shim that catches a raised NSException so the resident runner can survive a
        // failed XCUITest interaction instead of aborting; see the header for why Swift needs it.
        .target(name: "ObjCExceptionCatcher"),
        .target(name: "BajutsuRunner", dependencies: ["ObjCExceptionCatcher"]),
        .testTarget(name: "BajutsuKitTests", dependencies: ["BajutsuKit"]),
        .testTarget(
            name: "BajutsuRunnerTests",
            dependencies: [
                "BajutsuRunner",
                .product(name: "OpenAPIRuntime", package: "swift-openapi-runtime"),
            ],
            plugins: [.plugin(name: "OpenAPIGenerator", package: "swift-openapi-generator")]
        ),
    ]
)
