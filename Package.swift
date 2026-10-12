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
    // Hummingbird is *not* here yet, though Unit 2 chose it. Unit 4 serves the generated handlers
    // over the runner's existing socket layer, so nothing imports Hummingbird until Unit 5 swaps the
    // listener; declaring it before then would link SwiftNIO into the wheel-bundled runner for code
    // nothing calls, and force the package's iOS floor to 17 for the same nothing.
    dependencies: [
        .package(url: "https://github.com/apple/swift-openapi-generator", exact: "1.13.0"),
        .package(url: "https://github.com/apple/swift-openapi-runtime", exact: "1.12.0"),
        // Declared even though the OpenAPI runtime already brings it in: `LegacyBackedTransport`
        // spells the `ServerTransport` signature itself, so the runner's own code imports HTTPTypes
        // rather than only the generated code doing so. The version matches what the two pins above
        // already resolve to.
        .package(url: "https://github.com/apple/swift-http-types", exact: "1.6.0"),
    ],
    // Every target's `path:` is explicit and points back into BajutsuKit/: the manifest lives at
    // the repo root so this package resolves over `.package(url:)` from another repo (SPM's
    // git-based resolution requires Package.swift at the clone's root), but the Swift sources stay
    // put alongside the rest of BajutsuKit's non-Swift tooling (Runner/, README.md) rather than
    // moving to the repo root themselves.
    targets: [
        .target(name: "BajutsuKit", path: "BajutsuKit/Sources/BajutsuKit"),
        // An Objective-C shim that catches a raised NSException so the resident runner can survive a
        // failed XCUITest interaction instead of aborting; see the header for why Swift needs it.
        .target(name: "ObjCExceptionCatcher", path: "BajutsuKit/Sources/ObjCExceptionCatcher"),
        .target(
            name: "BajutsuRunner",
            dependencies: [
                "ObjCExceptionCatcher",
                .product(name: "OpenAPIRuntime", package: "swift-openapi-runtime"),
                .product(name: "HTTPTypes", package: "swift-http-types"),
            ],
            path: "BajutsuKit/Sources/BajutsuRunner",
            plugins: [.plugin(name: "OpenAPIGenerator", package: "swift-openapi-generator")]
        ),
        .testTarget(name: "BajutsuKitTests", dependencies: ["BajutsuKit"], path: "BajutsuKit/Tests/BajutsuKitTests"),
        .testTarget(name: "BajutsuRunnerTests", dependencies: ["BajutsuRunner", "ObjCExceptionCatcher"], path: "BajutsuKit/Tests/BajutsuRunnerTests"),
    ]
)
