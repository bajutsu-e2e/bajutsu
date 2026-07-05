package com.bajutsu.showcase.views

import android.os.Bundle
import android.util.TypedValue
import android.widget.LinearLayout
import androidx.appcompat.app.AppCompatActivity
import androidx.lifecycle.lifecycleScope
import kotlinx.coroutines.launch

// Horse Detail (SPEC §5.1), pushed by tapping a Stable row. The system Back pops it — BE-0007 drives
// it with the OS back keyevent; there is no app-defined back id. horse.title / horse.id.value are
// real content (the entity), so they keep ids even though screen titles do not.
class HorseDetailActivity : AppCompatActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val id = intent.getIntExtra("id", 0)
        val apiURL = intent.getStringExtra("apiURL") ?: "https://example.com"
        val name = "Horse $id"

        val status = secondaryLabel("Status: idle").aid("horse_status").stateValue("idle")
        val fetch = textButton("Fetch detail") {
            status.text = "Status: loading"
            status.stateValue("loading")
            lifecycleScope.launch {
                val result = Net.get("$apiURL/horses/$id")
                status.text = "Status: $result"
                status.stateValue(result)
            }
        }.aid("horse_fetch")

        // A button-backed toggle; isSelected reflects the state, value mirrors on/off.
        var favorite = false
        val favoriteValue = secondaryLabel("Not favorited").aid("horse_favorite_value").stateValue("off")
        val favoriteButton = textButton("☆ Favorite") {}.aid("horse_favorite")
        favoriteButton.setOnClickListener {
            favorite = !favorite
            favoriteButton.text = if (favorite) "★ Favorite" else "☆ Favorite"
            favoriteButton.isSelected = favorite
            favoriteValue.text = if (favorite) "Favorited" else "Not favorited"
            favoriteValue.stateValue(if (favorite) "on" else "off")
        }

        setContentView(
            vstack(
                header(name),
                label(name).apply { setTextSize(TypedValue.COMPLEX_UNIT_SP, 20f) }.aid("horse_title"),
                label("ID: $id").aid("horse_id_value").stateValue(id.toString()),
                fetch,
                status,
                favoriteButton,
                favoriteValue,
            ),
        )
    }
}
