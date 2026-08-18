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
    // The single-component case: a plain `UIPickerView`. The identifier lands on the picker, whose
    // wheel is a separate child element, so the scenario reaches the wheel with `within` + the
    // `pickerWheel` trait rather than by the identifier alone — `adjust(toPickerWheelValue:)` raises
    // on anything that is not itself a wheel. Here the child reports the picker's own frame, so the
    // containment `within` scopes by holds; the date picker below is where that stops being true.
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

            // Caption, wheel and mirror form one accessibility container, and the grouping is what
            // makes the wheels addressable at all. iOS lays a wheel `UIDatePicker`'s components out
            // at their own intrinsic height and clips them to the picker's frame, so each component
            // reports a frame that overflows the picker by (291 - 216) / 2 pt top and bottom. Since
            // `within` scopes by frame containment, an id on the picker itself resolves a container
            // that geometrically excludes its own wheels. A SwiftUI container's accessibility frame
            // is the union of its children's, so the caption above and the mirror below extend it
            // past the overflow. Keeping `spacing` alone above that 37.5pt makes containment hold
            // whatever the caption's font metrics turn out to be.
            VStack(spacing: 44) {
                Text("Birthdate")
                    .font(.headline)
                DatePicker(
                    "Birthdate", selection: $birthdate, displayedComponents: [.date]
                )
                .datePickerStyle(.wheel)
                .labelsHidden()
                Text(Self.yearMonth(birthdate))
                    .foregroundStyle(.secondary)
                    .accessibilityID("form.birthdate.value")
                    .accessibilityStateValue(Self.yearMonth(birthdate))
            }
            .accessibilityElement(children: .contain)
            .accessibilityID("form.birthdate")
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
