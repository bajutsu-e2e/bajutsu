import CoreLocation
import Flutter
import UIKit
import UserNotifications

/// The showcase's native seam (BE-0008). Two Flutter method channels hide the platform difference
/// the Dart app must not carry: `showcase/launch` hands back the `SHOWCASE_*` launch env XCUITest
/// sets as the process environment (SPEC §3), and `showcase/native` raises the two deliberate
/// SpringBoard alerts (SPEC §5.4 / §7) — which Dart cannot raise itself — returning the resolved
/// authorization status the Permissions screen mirrors to `perm.*.value`.
@main
@objc class AppDelegate: FlutterAppDelegate, FlutterImplicitEngineDelegate {
  private var locationRequest: LocationRequest?

  override func application(
    _ application: UIApplication,
    didFinishLaunchingWithOptions launchOptions: [UIApplication.LaunchOptionsKey: Any]?
  ) -> Bool {
    return super.application(application, didFinishLaunchingWithOptions: launchOptions)
  }

  func didInitializeImplicitFlutterEngine(_ engineBridge: FlutterImplicitEngineBridge) {
    GeneratedPluginRegistrant.register(with: engineBridge.pluginRegistry)
    let messenger = engineBridge.pluginRegistry.registrar(forPlugin: "Showcase")!.messenger()

    FlutterMethodChannel(name: "showcase/launch", binaryMessenger: messenger)
      .setMethodCallHandler { call, result in
        if call.method == "launchEnv" {
          result(Self.launchEnv())
        } else {
          result(FlutterMethodNotImplemented)
        }
      }

    FlutterMethodChannel(name: "showcase/native", binaryMessenger: messenger)
      .setMethodCallHandler { [weak self] call, result in
        // Fail loudly rather than never reply: a dropped result strands the Dart `await` forever.
        guard let self else {
          result(FlutterError(code: "unavailable", message: "app delegate gone", details: nil))
          return
        }
        switch call.method {
        case "requestNotif":
          // A real authorization error is folded into "denied" — the status model has no error state,
          // and for this fixture a systemic failure and a user deny are treated the same.
          UNUserNotificationCenter.current().requestAuthorization(options: [.alert, .sound, .badge]) { granted, _ in
            DispatchQueue.main.async { result(granted ? "authorized" : "denied") }
          }
        case "requestLocation":
          // One location request at a time: a second would replace the stored request and strand the
          // first result. Clear the slot when it resolves so a later request works.
          if self.locationRequest != nil {
            result(FlutterError(code: "busy", message: "a location request is already in progress", details: nil))
            return
          }
          self.locationRequest = LocationRequest { [weak self] status in
            self?.locationRequest = nil
            result(status)
          }
        default:
          result(FlutterMethodNotImplemented)
        }
      }
  }

  /// The launch env XCUITest injects, arriving as the process environment (SPEC §3). Filtered to the
  /// `SHOWCASE_*` hooks and the `BAJUTSU_*` collector keys so unrelated process vars never leak in.
  private static func launchEnv() -> [String: String] {
    ProcessInfo.processInfo.environment.filter { $0.key.hasPrefix("SHOWCASE_") || $0.key.hasPrefix("BAJUTSU_") }
  }
}

/// A one-shot location-authorization request: it holds the `CLLocationManager` (whose delegate
/// callback is the only way to learn the answer) and resolves the Flutter result once the user
/// answers the prompt.
private final class LocationRequest: NSObject, CLLocationManagerDelegate {
  private let manager = CLLocationManager()
  private var reply: ((String) -> Void)?

  init(reply: @escaping (String) -> Void) {
    self.reply = reply
    super.init()
    manager.delegate = self
    manager.requestWhenInUseAuthorization()
  }

  func locationManagerDidChangeAuthorization(_ manager: CLLocationManager) {
    resolve(manager.authorizationStatus)
  }

  private func resolve(_ status: CLAuthorizationStatus) {
    // `.notDetermined` fires once before the user answers; wait for the real decision.
    guard status != .notDetermined else { return }
    let resolved = (status == .authorizedWhenInUse || status == .authorizedAlways) ? "authorizedWhenInUse" : "denied"
    reply?(resolved)
    reply = nil
  }
}
