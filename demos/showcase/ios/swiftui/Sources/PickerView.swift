import SwiftUI

// BE-0356: the on-device realization of a wheel-style picker screen. A `UIPickerView` and a
// wheel-mode `UIDatePicker` are the two controls `setPickerValue` exists for, and neither can be set
// by any other step: a wheel's rows are not separately addressable elements, so `tap` cannot reach
// one and a coordinate drag cannot guarantee stopping on one. XCUITest's own
// `adjust(toPickerWheelValue:)` acts on the resolved element instead, which is what the runner's
// `/setPickerValue` route drives. Reached only when the SHOWCASE_PICKERS launch env is set (see
// AppModel), mirroring the SHOWCASE_GESTURES affordance, so the normal observe-only app (BE-0079) is
// untouched. The screen is a flat VStack — no scroll — so both wheels are always in the accessibility
// tree and the run resolves them without depending on XCUITest's unreliable swipe-to-scroll.
struct PickerView: View {
    // The single-component case: a plain `UIPickerView`, addressed by its own identifier.
    @State private var school = "高校"
    // The multi-component case: a wheel-mode `UIDatePicker` lays its components out as sibling
    // `pickerWheel` children, none carrying an identifier of its own — exactly the shape
    // `within` + `traits` + `index` addressing exists for. Both how many components there are and
    // how they are ordered follow the locale the run pins (month | day | year under the `en_US`
    // default), so the scenario names rows by index rather than assuming a fixed layout.
    @State private var birthdate = PickerView.fixedBirthdate

    private static let schools = ["中学", "高校", "大学", "大学院"]

    /// A fixed starting date, so the run asserts against a value it can name rather than today's.
    /// `DateComponents` over `Date()` for the same reason the rest of the showcase avoids clocks:
    /// a scenario observes the app's own state, and that state must not vary by the day it runs.
    private static var fixedBirthdate: Date {
        var components = DateComponents()
        components.year = 2015
        components.month = 4
        components.day = 1
        return Calendar(identifier: .gregorian).date(from: components) ?? Date()
    }

    var body: some View {
        VStack(spacing: 24) {
            Picker("School", selection: $school) {
                ForEach(Self.schools, id: \.self) { Text($0).tag($0) }
            }
            .pickerStyle(.wheel)
            .frame(width: 280, height: 140)
            .accessibilityID("form.school")
            // The sibling mirror the assertions read: a `UIPickerView`'s own accessibility value is
            // the row it shows, which `setPickerValue` itself reads back, so a separate element
            // proves the *app* saw the selection rather than only the wheel having moved.
            Text(school)
                .foregroundStyle(.secondary)
                .accessibilityID("form.school.value")
                .accessibilityStateValue(school)

            DatePicker(
                "Birthdate", selection: $birthdate, displayedComponents: [.date]
            )
            .datePickerStyle(.wheel)
            .labelsHidden()
            .frame(width: 280, height: 180)
            .accessibilityID("form.birthdate")
            Text(Self.yearMonth(birthdate))
                .foregroundStyle(.secondary)
                .accessibilityID("form.birthdate.value")
                .accessibilityStateValue(Self.yearMonth(birthdate))
        }
    }

    /// The selected year and month as `YYYY-MM`, the stable projection the run asserts on. The
    /// wheels' own row labels are whatever the pinned locale renders (`May` / `2016` under `en_US`),
    /// so mirroring them verbatim would tie the assertion to that locale as well as to the
    /// selection. The scenario still has to name those labels to *set* a wheel — it has nothing else
    /// to name a row by — but it reads the result back from here instead.
    private static func yearMonth(_ date: Date) -> String {
        let parts = Calendar(identifier: .gregorian).dateComponents([.year, .month], from: date)
        return String(format: "%04d-%02d", parts.year ?? 0, parts.month ?? 0)
    }
}
