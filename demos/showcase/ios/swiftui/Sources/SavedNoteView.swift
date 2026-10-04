import SwiftUI

// BE-0447: the update fixture, reached only under the SHOWCASE_SAVED_NOTE launch env. Everything
// else in the app is in-memory by design (scenarios/relaunch.yaml relies on that), so this screen is
// the one place a note outlives the process: the update scenario saves it on the previous build,
// installs the current build over it with the data container kept, and reads it back. `saved.build`
// names which build is in front, so the scenario can tell the install actually replaced the app.
struct SavedNoteView: View {
    @State private var draft = ""
    @State private var saved = SavedNoteStore.load()

    private static let build: String = {
        #if SHOWCASE_PREVIOUS
        return "previous"
        #else
        return "current"
        #endif
    }()

    var body: some View {
        VStack(spacing: 24) {
            Text("Build: \(Self.build)")
                .accessibilityID("saved.build")
                .accessibilityStateValue(Self.build)
            TextField("Note", text: $draft)
                .textFieldStyle(.roundedBorder)
                .accessibilityID("saved.field")
            Button("Save") {
                // Shown only once it is on disk, so a failed write fails the save step's own check
                // rather than surfacing later as a note the update seemed to lose.
                if SavedNoteStore.save(draft) { saved = draft }
            }
            .accessibilityID("saved.save")
            Text("Saved: \(saved ?? "none")")
                .accessibilityID("saved.value")
                .accessibilityStateValue(saved ?? "none")
        }
        .padding()
    }
}

/// A file in Documents, written atomically and synchronously: `installApp` terminates the app right
/// after the save, and a UserDefaults write still buffered in-process could be lost to that kill.
enum SavedNoteStore {
    private static var url: URL {
        FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("saved-note.txt")
    }

    static func load() -> String? {
        try? String(contentsOf: url, encoding: .utf8)
    }

    static func save(_ note: String) -> Bool {
        do {
            try note.write(to: url, atomically: true, encoding: .utf8)
            return true
        } catch {
            return false
        }
    }
}
