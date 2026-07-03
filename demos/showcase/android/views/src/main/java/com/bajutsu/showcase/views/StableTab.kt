package com.bajutsu.showcase.views

import android.content.Intent
import android.view.View
import android.widget.LinearLayout
import androidx.lifecycle.lifecycleScope
import kotlinx.coroutines.launch

// Tab: Stable (SPEC §5.1). Catalog list with async load; tapping a row pushes Horse Detail (a child
// Activity — the system Back pops it). Status mirrors to stable.status so a scenario can wait on the
// response before asserting.
class StableTab(private val activity: MainActivity, private val model: AppModel) {
    val root: View

    init {
        val status = activity.secondaryLabel("Status: idle").aid("stable_status").stateValue("idle")

        val refresh = activity.textButton("Refresh") {
            status.text = "Status: loading"
            status.stateValue("loading")
            activity.lifecycleScope.launch {
                val result = Net.get(model.apiURL + "/horses")
                status.text = "Status: $result"
                status.stateValue(result)
            }
        }.aid("stable_refresh")

        val rows = LinearLayout(activity).apply { orientation = LinearLayout.VERTICAL }
        if (model.horses.isEmpty()) {
            // Defensive markup, not a reachable state: the catalog is fixed non-empty (BE-0079).
            rows.addView(activity.secondaryLabel("No horses").aid("stable_empty"))
        } else {
            model.horses.forEach { horse ->
                rows.addView(
                    activity.label(horse.name).apply {
                        setOnClickListener {
                            activity.startActivity(
                                Intent(activity, HorseDetailActivity::class.java)
                                    .putExtra("id", horse.id)
                                    .putExtra("apiURL", model.apiURL),
                            )
                        }
                    }.aid("stable_row_${horse.id}"),
                )
            }
        }

        root = activity.vstack(
            activity.header("Stable"),
            refresh,
            activity.scrollPage(rows),
            status,
        ).apply {
            // Let the list take the middle; header/refresh/status keep natural height.
            (getChildAt(2).layoutParams as LinearLayout.LayoutParams).apply { height = 0; weight = 1f }
        }
    }
}
