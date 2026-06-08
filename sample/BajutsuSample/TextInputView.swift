import SwiftUI

// Text-entry variants plus inline validation. The entered text and a live
// character count are mirrored to result labels so a backend can assert exactly
// what was typed without reading the field's own (sometimes redacted) value.
struct TextInputView: View {
    @State private var basic = ""
    @State private var email = ""
    @State private var multiline = ""
    @State private var required = ""
    @State private var submitted = ""

    private var requiredValid: Bool { required.count >= 3 }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 20) {
                Text("Text")
                    .font(.title)
                    .accessibilityIdentifier("text.title")

                TextField("Basic", text: $basic)
                    .textFieldStyle(.roundedBorder)
                    .keyboardType(.asciiCapable)
                    .autocorrectionDisabled()
                    .textInputAutocapitalization(.never)
                    .accessibilityIdentifier("text.basic")
                Text("Value: \(basic)")
                    .accessibilityIdentifier("text.basic.value")
                    .accessibilityValue(basic)
                Text("Count: \(basic.count)")
                    .accessibilityIdentifier("text.count")
                    .accessibilityValue("\(basic.count)")
                Button("Clear") { basic = "" }
                    .buttonStyle(.bordered)
                    .accessibilityIdentifier("text.clear")

                TextField("Email", text: $email)
                    .textFieldStyle(.roundedBorder)
                    .keyboardType(.emailAddress)
                    .autocorrectionDisabled()
                    .textInputAutocapitalization(.never)
                    .accessibilityIdentifier("text.email")

                TextEditor(text: $multiline)
                    .frame(height: 80)
                    .border(.gray.opacity(0.3))
                    .accessibilityIdentifier("text.editor")
                Text("Editor: \(multiline)")
                    .accessibilityIdentifier("text.editor.value")
                    .accessibilityValue(multiline)

                // Needs >= 3 chars: submit stays disabled until valid, and an error
                // label shows once the field has content but is still too short.
                TextField("Required (min 3)", text: $required)
                    .textFieldStyle(.roundedBorder)
                    .keyboardType(.asciiCapable)
                    .autocorrectionDisabled()
                    .textInputAutocapitalization(.never)
                    .accessibilityIdentifier("text.required")
                if !required.isEmpty && !requiredValid {
                    Text("Too short")
                        .foregroundStyle(.red)
                        .accessibilityIdentifier("text.error")
                }
                Button("Submit") { submitted = required }
                    .buttonStyle(.borderedProminent)
                    .disabled(!requiredValid)
                    .accessibilityIdentifier("text.submit")
                if !submitted.isEmpty {
                    Text("Submitted: \(submitted)")
                        .accessibilityIdentifier("text.submitted")
                        .accessibilityValue(submitted)
                }
            }
            .padding()
        }
    }
}
