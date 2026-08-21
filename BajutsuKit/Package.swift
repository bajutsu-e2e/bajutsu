// swift-tools-version:5.9
import PackageDescription

let package = Package(
    name: "BajutsuKit",
    platforms: [.iOS(.v15), .macOS(.v11)],
    products: [
        .library(name: "BajutsuKit", targets: ["BajutsuKit"]),
        .library(name: "BajutsuRunner", targets: ["BajutsuRunner"]),
    ],
    // Pinned with `exact:` rather than `from:`: the generator runs as a build-time plugin behind
    // `-skipPackagePluginValidation`, and the runner's .xcodeproj is regenerated and git-ignored, so
    // it carries no resolution of its own — the manifest is the only thing that pins that path.
    //
    // Hummingbird is *not* here yet, though Unit 2 chose it: no source imports it until Unit 4 wires
    // the server, and declaring it early would link SwiftNIO into the wheel-bundled runner for code
    // nothing calls, and force the package's iOS floor to 17 for the same nothing.
    dependencies: [
        .package(url: "https://github.com/apple/swift-openapi-generator", exact: "1.13.0"),
        .package(url: "https://github.com/apple/swift-openapi-runtime", exact: "1.12.0"),
    ],
    targets: [
        .target(name: "BajutsuKit"),
        // An Objective-C shim that catches a raised NSException so the resident runner can survive a
        // failed XCUITest interaction instead of aborting; see the header for why Swift needs it.
        .target(name: "ObjCExceptionCatcher"),
        .target(
            name: "BajutsuRunner",
            dependencies: [
                "ObjCExceptionCatcher",
                .product(name: "OpenAPIRuntime", package: "swift-openapi-runtime"),
            ],
            plugins: [.plugin(name: "OpenAPIGenerator", package: "swift-openapi-generator")]
        ),
        .testTarget(name: "BajutsuKitTests", dependencies: ["BajutsuKit"]),
        .testTarget(name: "BajutsuRunnerTests", dependencies: ["BajutsuRunner"]),
    ]
)
