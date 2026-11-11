package com.bajutsu.showcase.views

import android.content.Context
import android.view.View
import android.view.ViewGroup
import android.widget.DatePicker
import android.widget.FrameLayout
import android.widget.LinearLayout
import android.widget.NumberPicker

/**
 * BE-0480: the Views realization of the wheel picker screen, mirroring the iOS PickerView (BE-0356).
 * An `android.widget.NumberPicker` and a spinner-mode `DatePicker` (three `NumberPicker` wheels) are
 * the controls `setPickerValue` drives on Android, and no other step can set one: a coordinate drag
 * cannot promise to stop on a row. Reached only when the SHOWCASE_PICKERS launch env is set (see
 * MainActivity), so the normal five-tab app (BE-0079) never renders it. A flat, scroll-free column,
 * so both wheels are always in the tree.
 *
 * A wheel's own node carries no resource-id (`NumberPicker`'s accessibility provider sets none), so
 * each id goes on a container the shared scenario scopes with `within`, then picks the wheel by its
 * `pickerWheel` trait and `index` — the same addressing the iOS screen needs.
 */
class PickerScreen(context: Context) {
    val root: View

    init {
        val schoolValue = context.secondaryLabel(SCHOOLS[1]).aid("form_school_value").stateValue(SCHOOLS[1])
        val school = NumberPicker(context).apply {
            minValue = 0
            maxValue = SCHOOLS.size - 1
            displayedValues = SCHOOLS
            value = 1
            wrapSelectorWheel = false
            // The wheel is set by scrolling, never by typing, so its input row never takes focus.
            descendantFocusability = ViewGroup.FOCUS_BLOCK_DESCENDANTS
            setOnValueChangedListener { _, _, picked ->
                schoolValue.text = SCHOOLS[picked]
                schoolValue.stateValue(SCHOOLS[picked])
            }
        }
        val schoolBox = FrameLayout(context).apply {
            importantForAccessibility = View.IMPORTANT_FOR_ACCESSIBILITY_YES
            addView(school)
        }.aid("form_school")

        val birthValue = context.secondaryLabel(yearMonth(START_YEAR, START_MONTH))
            .aid("form_birthdate_value")
            .stateValue(yearMonth(START_YEAR, START_MONTH))
        val birthdate = DatePicker(context, null, 0, R.style.Widget_Showcase_SpinnerDatePicker).apply {
            init(START_YEAR, START_MONTH, START_DAY) { _, year, month, _ ->
                birthValue.text = yearMonth(year, month)
                birthValue.stateValue(yearMonth(year, month))
            }
        }
        val birthBox = context.vstack(context.label("Birthdate"), birthdate, birthValue).apply {
            importantForAccessibility = View.IMPORTANT_FOR_ACCESSIBILITY_YES
        }.aid("form_birthdate")

        root = LinearLayout(context).apply {
            orientation = LinearLayout.VERTICAL
            addView(schoolBox)
            addView(schoolValue)
            addView(birthBox)
        }
    }

    private companion object {
        val SCHOOLS = arrayOf("中学", "高校", "大学", "大学院")

        // A fixed starting date, as on iOS, so the run asserts against a value it can name.
        // `DatePicker` months are zero-based: 3 is April.
        const val START_YEAR = 2015
        const val START_MONTH = 3
        const val START_DAY = 1

        /** `YYYY-MM`, the projection the run asserts on rather than a locale-rendered row label. */
        fun yearMonth(year: Int, zeroBasedMonth: Int): String = "%04d-%02d".format(year, zeroBasedMonth + 1)
    }
}
