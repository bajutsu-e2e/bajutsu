package dev.bajutsu.identifier.sample

import android.app.Activity
import android.os.Bundle
import android.widget.Button
import android.widget.LinearLayout
import android.widget.TextView
import dev.bajutsu.identifier.accessibilityId
import dev.bajutsu.identifier.accessibilityStateValue

/**
 * The minimal shape any app adopting IdentifierTool writes: no other bajutsu library, and no gate
 * of its own around the calls below (see IdentifierTool/README.md). `refresh_button` and
 * `status_label` are declared ahead of time in res/values/ids.xml, as a Views-based consumer must.
 */
class MainActivity : Activity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        val status = TextView(this).apply {
            text = "idle"
            accessibilityId("status_label")
            accessibilityStateValue("idle")
        }
        val refresh = Button(this).apply {
            text = "Refresh"
            accessibilityId("refresh_button")
            setOnClickListener {
                status.text = "refreshed"
                status.accessibilityStateValue("refreshed")
            }
        }

        setContentView(
            LinearLayout(this).apply {
                orientation = LinearLayout.VERTICAL
                addView(status)
                addView(refresh)
            }
        )
    }
}
