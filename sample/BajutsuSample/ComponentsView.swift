import SwiftUI

// A grab-bag of interaction patterns: long press, an in-app confirmation alert,
// and a swipe-direction gesture.
struct ComponentsView: View {
    @State private var revealed = false
    @State private var showDeleteAlert = false
    @State private var deleted = false
    @State private var swipeDir = "none"

    var body: some View {
        VStack(spacing: 24) {
            Text("Components")
                .font(.title)
                .accessibilityIdentifier("comp.title")

            // Long press: a plain view (not a Button, whose tap gesture would consume it).
            Text("Hold me")
                .padding()
                .frame(maxWidth: .infinity)
                .background(.blue.opacity(0.15))
                .accessibilityIdentifier("comp.longpress")
                .onLongPressGesture(minimumDuration: 0.4) { revealed = true }
            if revealed {
                Text("Revealed").accessibilityIdentifier("comp.secret")
            }

            // In-app confirmation alert (a SwiftUI .alert is visible to idb).
            Button("Remove") { showDeleteAlert = true }
                .buttonStyle(.bordered)
                .accessibilityIdentifier("comp.remove")
            if deleted {
                Text("Deleted").accessibilityIdentifier("comp.deleted")
            }

            // Swipe: a DragGesture records the direction (onEnded needs no flick velocity).
            Text("Swipe me")
                .padding()
                .frame(maxWidth: .infinity, minHeight: 80)
                .background(.green.opacity(0.15))
                .accessibilityIdentifier("comp.swipearea")
                .gesture(DragGesture(minimumDistance: 20).onEnded { v in
                    let dx = v.translation.width, dy = v.translation.height
                    if abs(dx) > abs(dy) {
                        swipeDir = dx < 0 ? "left" : "right"
                    } else {
                        swipeDir = dy < 0 ? "up" : "down"
                    }
                })
            Text("Swiped: \(swipeDir)")
                .accessibilityIdentifier("comp.swipeResult")
                .accessibilityValue(swipeDir)
        }
        .padding()
        .alert("Delete item?", isPresented: $showDeleteAlert) {
            Button("Delete", role: .destructive) { deleted = true }
            Button("Cancel", role: .cancel) {}
        }
    }
}
