// swift-tools-version:6.0
import PackageDescription

// The tools version is 6.0 because iOS 18 is not expressible below it (`.v18` is unavailable at
// 5.9), and the deployment target is iOS 18 because swift-openapi-hummingbird requires iOS 17+.
// Every target pins the Swift 5 language mode: tools 6.0 would otherwise default them to Swift 6's
// strict concurrency checking, which the runner's main-thread hop (`Router.onMain`) does not pass.
// Migrating that is deliberate future work, not a side effect of raising the platform floor.
let package = Package(
    name: "BajutsuKit",
    platforms: [.iOS(.v18), .macOS(.v14)],
    products: [
        .library(name: "BajutsuKit", targets: ["BajutsuKit"]),
        .library(name: "BajutsuRunner", targets: ["BajutsuRunner"]),
    ],
    // Pinned with `exact:` rather than `from:`: the generator runs as a build-time plugin behind
    // `-skipPackagePluginValidation`, and the runner's .xcodeproj is regenerated and git-ignored, so
    // it carries no resolution of its own — the manifest is the only thing that pins that path.
    dependencies: [
        .package(url: "https://github.com/apple/swift-openapi-generator", exact: "1.13.0"),
        .package(url: "https://github.com/apple/swift-openapi-runtime", exact: "1.12.0"),
        .package(url: "https://github.com/hummingbird-project/hummingbird", exact: "2.26.0"),
        .package(url: "https://github.com/hummingbird-project/swift-openapi-hummingbird", exact: "2.0.1"),
    ],
    targets: [
        .target(name: "BajutsuKit", swiftSettings: [.swiftLanguageMode(.v5)]),
        // An Objective-C shim that catches a raised NSException so the resident runner can survive a
        // failed XCUITest interaction instead of aborting; see the header for why Swift needs it.
        .target(name: "ObjCExceptionCatcher"),
        .target(
            name: "BajutsuRunner",
            dependencies: [
                "ObjCExceptionCatcher",
                .product(name: "OpenAPIRuntime", package: "swift-openapi-runtime"),
                .product(name: "Hummingbird", package: "hummingbird"),
                .product(name: "OpenAPIHummingbird", package: "swift-openapi-hummingbird"),
            ],
            swiftSettings: [.swiftLanguageMode(.v5)],
            plugins: [.plugin(name: "OpenAPIGenerator", package: "swift-openapi-generator")]
        ),
        .testTarget(
            name: "BajutsuKitTests", dependencies: ["BajutsuKit"],
            swiftSettings: [.swiftLanguageMode(.v5)]
        ),
        .testTarget(
            name: "BajutsuRunnerTests", dependencies: ["BajutsuRunner"],
            swiftSettings: [.swiftLanguageMode(.v5)]
        ),
    ]
)
