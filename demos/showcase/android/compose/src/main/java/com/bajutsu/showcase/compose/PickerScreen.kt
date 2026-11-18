package com.bajutsu.showcase.compose

import android.view.ViewGroup
import android.widget.DatePicker
import android.widget.NumberPicker
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import androidx.compose.ui.viewinterop.AndroidView

// BE-0480: the Compose realization of the wheel picker screen, mirroring the iOS PickerView (BE-0356).
// Compose ships no wheel of its own, so the screen hosts the platform `NumberPicker` and a spinner-mode
// `DatePicker` through `AndroidView` — the Compose path `setPickerValue` supports on Android, and the
// one a Compose-drawn wheel (out of scope) is not. Reached only when the SHOWCASE_PICKERS launch env is
// set (see AppModel / RootScreen), so the normal five-tab app (BE-0079) never renders it. A flat,
// scroll-free Column so both wheels are always in the tree.
//
// A wheel's own node carries no resource-id (`NumberPicker`'s accessibility provider sets none), so
// each id goes on a container the shared scenario scopes with `within`, then picks the wheel by its
// `pickerWheel` trait and `index` — the same addressing the iOS screen needs. The ids are the dotted
// SPEC forms, which a Compose testTag carries verbatim.
@Composable
fun PickerScreen() {
    var school by remember { mutableStateOf(SCHOOLS[1]) }
    var birth by remember { mutableStateOf(yearMonth(START_YEAR, START_MONTH)) }
    Column(
        modifier = Modifier
            .fillMaxSize()
            .enableTestTagsAsResourceId(),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.spacedBy(16.dp, Alignment.CenterVertically),
    ) {
        Box(Modifier.aid("form.school")) {
            AndroidView(factory = { context ->
                NumberPicker(context).apply {
                    minValue = 0
                    maxValue = SCHOOLS.size - 1
                    displayedValues = SCHOOLS
                    value = 1
                    wrapSelectorWheel = false
                    // The wheel is set by scrolling, never by typing, so its input row never takes focus.
                    descendantFocusability = ViewGroup.FOCUS_BLOCK_DESCENDANTS
                    setOnValueChangedListener { _, _, picked -> school = SCHOOLS[picked] }
                }
            })
        }
        Text(school, Modifier.aid("form.school.value").stateValue(school))
        Column(Modifier.aid("form.birthdate"), horizontalAlignment = Alignment.CenterHorizontally) {
            Text("Birthdate")
            AndroidView(factory = { context ->
                DatePicker(context, null, 0, R.style.Widget_Showcase_SpinnerDatePicker).apply {
                    init(START_YEAR, START_MONTH, START_DAY) { _, year, month, _ ->
                        birth = yearMonth(year, month)
                    }
                }
            })
            Text(birth, Modifier.aid("form.birthdate.value").stateValue(birth))
        }
    }
}

private val SCHOOLS = arrayOf("中学", "高校", "大学", "大学院")

// A fixed starting date, as on iOS, so the run asserts against a value it can name. `DatePicker`
// months are zero-based: 3 is April.
private const val START_YEAR = 2015
private const val START_MONTH = 3
private const val START_DAY = 1

/** `YYYY-MM`, the projection the run asserts on rather than a locale-rendered row label. */
private fun yearMonth(year: Int, zeroBasedMonth: Int): String = "%04d-%02d".format(year, zeroBasedMonth + 1)
