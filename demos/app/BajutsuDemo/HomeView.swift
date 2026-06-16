import SwiftUI

struct HomeView: View {
    @EnvironmentObject var model: AppModel

    var body: some View {
        NavigationStack {
            VStack(spacing: 16) {
                Text("Home")
                    .font(.title)
                    .accessibilityIdentifier("home.title")

                HStack(spacing: 12) {
                    // The counter value is mirrored into the accessibility value so a
                    // headless backend (idb) can read it with a `value.equals` assertion.
                    Text("Count: \(model.counter)")
                        .accessibilityIdentifier("counter.value")
                        .accessibilityValue("\(model.counter)")
                    Button("Increment") { model.increment() }
                        .buttonStyle(.bordered)
                        .accessibilityIdentifier("counter.increment")
                }

                Button("Log out") { model.logout() }
                    .accessibilityIdentifier("home.logout")

                Spacer()
            }
            .padding()
        }
    }
}
