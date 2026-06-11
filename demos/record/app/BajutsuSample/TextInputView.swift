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

                TextField("Basic", text: $basic)
                    .textFieldStyle(.roundedBorder)
                    .keyboardType(.asciiCapable)
                    .autocorrectionDisabled()
                    .textInputAutocapitalization(.never)
                Text("Value: \(basic)")
                Text("Count: \(basic.count)")
                Button("Clear") { basic = "" }
                    .buttonStyle(.bordered)

                TextField("Email", text: $email)
                    .textFieldStyle(.roundedBorder)
                    .keyboardType(.emailAddress)
                    .autocorrectionDisabled()
                    .textInputAutocapitalization(.never)

                TextEditor(text: $multiline)
                    .frame(height: 80)
                    .border(.gray.opacity(0.3))
                Text("Editor: \(multiline)")

                // Needs >= 3 chars: submit stays disabled until valid, and an error
                // label shows once the field has content but is still too short.
                TextField("Required (min 3)", text: $required)
                    .textFieldStyle(.roundedBorder)
                    .keyboardType(.asciiCapable)
                    .autocorrectionDisabled()
                    .textInputAutocapitalization(.never)
                if !required.isEmpty && !requiredValid {
                    Text("Too short")
                        .foregroundStyle(.red)
                }
                Button("Submit") { submitted = required }
                    .buttonStyle(.borderedProminent)
                    .disabled(!requiredValid)
                if !submitted.isEmpty {
                    Text("Submitted: \(submitted)")
                }
            }
            .padding()
        }
    }
}
