package com.bajutsu.showcase.views

import android.os.Bundle
import androidx.appcompat.app.AppCompatActivity

// Notice Detail (SPEC §5.5), pushed by tapping a Notices row. The system Back pops it; the detail
// title is the screen's identifying element (screen titles themselves carry no id).
class NoticeDetailActivity : AppCompatActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val id = intent.getIntExtra("id", 0)
        val notice = showcaseNotices.firstOrNull { it.id == id }
        val title = notice?.title ?: "Notice $id"

        setContentView(
            vstack(
                header(title),
                label(title).aid("notice_detail_title"),
                secondaryLabel(notice?.body ?: "").aid("notice_detail_body"),
            ),
        )
    }
}
