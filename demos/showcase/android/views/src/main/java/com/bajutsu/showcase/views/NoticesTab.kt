package com.bajutsu.showcase.views

import android.content.Intent
import android.view.View
import android.widget.LinearLayout

// Tab: Notices (SPEC §5.5). A plain vertical list of 20 static notices — intentionally longer than
// one screen, so the bottom rows start off-screen and reaching notice.row.20 means scrolling, the
// canonical scroll-to-element target. Tapping a row pushes Notice Detail (a child Activity).
class NoticesTab(private val activity: MainActivity, private val model: AppModel) {
    val root: View

    init {
        val rows = LinearLayout(activity).apply { orientation = LinearLayout.VERTICAL }
        model.notices.forEach { notice ->
            rows.addView(
                activity.label(notice.title).apply {
                    setOnClickListener {
                        activity.startActivity(
                            Intent(activity, NoticeDetailActivity::class.java).putExtra("id", notice.id),
                        )
                    }
                }.aid("notice_row_${notice.id}"),
            )
        }
        root = activity.vstack(activity.header("Notices"), activity.scrollPage(rows)).apply {
            (getChildAt(1).layoutParams as LinearLayout.LayoutParams).apply { height = 0; weight = 1f }
        }
    }
}
