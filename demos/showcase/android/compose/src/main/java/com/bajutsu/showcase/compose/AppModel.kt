package com.bajutsu.showcase.compose

import android.net.Uri
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateListOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue

/** A catalog horse. `id` is data-derived and drives the `*.row.<id>` identifiers. */
data class Horse(val id: Int, val name: String)

/** A stable notice. Twenty are seeded; `id` drives the `notice.row.<id>` identifiers. */
data class Notice(val id: Int, val title: String, val body: String)

/**
 * The seeded notices (shared verbatim with the Views app — SPEC §5.5). Intentionally longer than one
 * screen so the bottom rows start off-screen: reaching `notice.row.20` requires scrolling, the
 * canonical scroll-to-element target.
 */
val showcaseNotices: List<Notice> =
    (1..20).map { Notice(it, "Notice $it", "Details for stable notice number $it.") }

/** The five tabs, left to right (SPEC §5). */
enum class Tab { STABLE, SEARCH, LOG, NOTICES, PERMISSIONS }

/**
 * App state plus the launch-env hooks Bajutsu drives (SPEC §3). On Android `launchEnv` arrives as
 * intent extras (BE-0007), read once at launch. The catalog is fixed at five horses — there is no
 * launch-env seed knob (BE-0079): a scenario observes the app's own data, it cannot inject a state.
 */
class AppModel(env: Map<String, String>) {
    // Tab selection + per-tab navigation stacks (a deeplink pops these to root).
    var selectedTab by mutableStateOf(tab(env["SHOWCASE_TAB"]))
    val stablePath = mutableStateListOf<Int>() // pushed horse ids
    val noticesPath = mutableStateListOf<Int>() // pushed notice ids

    val notices = showcaseNotices

    // Shared catalog (Stable + Search both filter this). Fixed at launch.
    val horses: List<Horse> = (1..5).map { Horse(it, "Horse $it") }

    val animationsDisabled = env["SHOWCASE_UITEST"] != null

    // Networking config (SPEC §3, §6).
    val apiURL = env["SHOWCASE_API_URL"] ?: "https://example.com"
    val httpBase = env["SHOWCASE_HTTP_BASE"] ?: "https://httpbin.org"

    fun horses(matching: String): List<Horse> =
        if (matching.isEmpty()) horses else horses.filter { it.name.contains(matching, ignoreCase = true) }

    fun horse(id: Int): Horse? = horses.firstOrNull { it.id == id }

    fun notice(id: Int): Notice? = notices.firstOrNull { it.id == id }

    /**
     * Deeplinks (SPEC §4): select a tab (and pop it to root). A deeplink does not push a detail screen
     * (BE-0079): a detail is reached only by tapping its catalog row. `am start -a VIEW -d <scheme>://<host>`.
     */
    fun handleDeepLink(uri: Uri) {
        stablePath.clear()
        noticesPath.clear()
        selectedTab = tab(uri.host)
    }

    companion object {
        /** Map a `SHOWCASE_TAB` value (and deeplink host) to a tab. */
        fun tab(name: String?): Tab = when (name) {
            "search" -> Tab.SEARCH
            "log" -> Tab.LOG
            "notices" -> Tab.NOTICES
            "permissions" -> Tab.PERMISSIONS
            else -> Tab.STABLE
        }
    }
}
