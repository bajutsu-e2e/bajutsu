package com.bajutsu.showcase.views

import android.text.Editable
import android.text.InputType
import android.text.TextWatcher
import android.view.View
import android.widget.EditText
import android.widget.LinearLayout

// Tab: Search (SPEC §5.2). Filters the shared catalog by name, case-insensitive; the match count
// mirrors to search.count so a scenario can assert it without counting rows.
class SearchTab(private val activity: MainActivity, private val model: AppModel) {
    val root: View

    init {
        val count = activity.secondaryLabel("Matches: ${model.horses.size}")
            .aid("search_count").stateValue(model.horses.size.toString())
        val rows = LinearLayout(activity).apply { orientation = LinearLayout.VERTICAL }

        val field = EditText(activity).apply {
            hint = "Search horses"
            // Plain text without suggestions so typed Latin text is not mangled by an IME (iOS parity).
            inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_FLAG_NO_SUGGESTIONS
            maxLines = 1
        }.aid("search_field")

        fun render(query: String) {
            val matches = model.horses(matching = query)
            count.text = "Matches: ${matches.size}"
            count.stateValue(matches.size.toString())
            rows.removeAllViews()
            if (matches.isEmpty()) {
                rows.addView(activity.secondaryLabel("No matches").aid("search_results_empty"))
            } else {
                matches.forEach { horse ->
                    rows.addView(activity.label(horse.name).aid("search_row_${horse.id}"))
                }
            }
        }

        field.addTextChangedListener(object : TextWatcher {
            override fun beforeTextChanged(s: CharSequence?, start: Int, before: Int, count: Int) {}
            override fun onTextChanged(s: CharSequence?, start: Int, before: Int, count: Int) {}
            override fun afterTextChanged(s: Editable?) = render(s?.toString() ?: "")
        })
        val clear = activity.textButton("Clear") { field.setText("") }.aid("search_clear")

        render("")
        root = activity.vstack(
            activity.header("Search"),
            activity.hstack(field, clear).apply {
                (getChildAt(0).layoutParams as LinearLayout.LayoutParams).apply { width = 0; weight = 1f }
            },
            count,
            activity.scrollPage(rows),
        ).apply {
            (getChildAt(3).layoutParams as LinearLayout.LayoutParams).apply { height = 0; weight = 1f }
        }
    }
}
