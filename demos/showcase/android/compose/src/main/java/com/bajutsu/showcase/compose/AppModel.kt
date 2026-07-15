package com.bajutsu.showcase.compose

import android.net.Uri
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
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
 *
 * The per-tab UI state below lives on the model rather than in each screen's `remember` so it survives
 * a tab switch, matching the iOS TabView and the Views twin (which keeps every tab view alive): the
 * `when(selectedTab)` in RootScreen disposes the hidden tab's composition, so screen-local `remember`
 * would reset submitted rows, typed queries, and permission results on every switch.
 */
class AppModel(env: Map<String, String>) {
    // Two-finger gesture mode (BE-0232): a test-only affordance gated on the SHOWCASE_GESTURES launch
    // env, mirroring the iOS AppModel. When set, RootScreen swaps the whole five-tab UI for the flat,
    // scroll-free pinch/rotate screen, so the shared `gestures_multitouch` scenario can drive its
    // targets without depending on scroll — two-finger gestures being the class adb drives only over a
    // rooted `sendevent` sweep.
    val gesturesMode: Boolean = env["SHOWCASE_GESTURES"] != null

    // Driver-conformance mode (BE-0114 / BE-0270): a test-only affordance gated on the
    // SHOWCASE_CONFORMANCE launch env, mirroring the iOS AppModel. null (env absent) = the normal
    // observe-only app (BE-0079, untouched); non-null = render exactly these identifiers (duplicates
    // / the empty set included) via ConformanceScreen. Observable so a reseed — a re-launch carrying a
    // new SHOWCASE_CONFORMANCE extra, delivered to the singleTask activity through onNewIntent — re-
    // renders the screen without relaunching the process (see MainActivity.applyConformance).
    var conformanceIds by mutableStateOf(parseConformance(env["SHOWCASE_CONFORMANCE"]))
        private set

    // Tab selection + per-tab navigation stacks (a deeplink pops these to root).
    var selectedTab by mutableStateOf(tab(env["SHOWCASE_TAB"]))
    val stablePath = mutableStateListOf<Int>() // pushed horse ids
    val noticesPath = mutableStateListOf<Int>() // pushed notice ids

    val notices = showcaseNotices

    // Shared catalog (Stable + Search both filter this). Fixed at launch.
    val horses: List<Horse> = (1..5).map { Horse(it, "Horse $it") }

    // Networking config (SPEC §3, §6).
    val apiURL = env["SHOWCASE_API_URL"] ?: "https://example.com"
    val httpBase = env["SHOWCASE_HTTP_BASE"] ?: "https://httpbin.org"

    // --- Hoisted per-tab UI state (see class doc) -------------------------------------------------
    // Stable tab
    var stableStatus by mutableStateOf("idle")
    // Horse Detail (one open at a time; reset when a new row is pushed via pushHorse)
    var horseFavorite by mutableStateOf(false)
    var horseStatus by mutableStateOf("idle")
    // Search tab
    var searchQuery by mutableStateOf("")
    // Log tab
    var logNote by mutableStateOf("")
    var logCount by mutableIntStateOf(1)
    var logIntense by mutableStateOf(false)
    var logSegment by mutableStateOf("one")
    var logStatus by mutableStateOf("idle")
    val logRows = mutableStateListOf<Int>()
    var logLongPressed by mutableStateOf(false)
    var logDoubleTaps by mutableIntStateOf(0)
    var logShowSheet by mutableStateOf(false)
    var logShowCover by mutableStateOf(false)
    var logShowDialog by mutableStateOf(false)
    var logDialogResult by mutableStateOf("none")
    var logShowToast by mutableStateOf(false)
    // Permissions tab
    var notifStatus by mutableStateOf("notDetermined")
    var locationStatus by mutableStateOf("notDetermined")
    var pasted by mutableStateOf("")

    /**
     * Reseed the conformance screen from a new SHOWCASE_CONFORMANCE extra (BE-0270), a no-op when the
     * extra is absent so a non-conformance intent (e.g. a deeplink) never clears the current screen.
     */
    fun applyConformance(spec: String?) {
        if (spec != null) conformanceIds = parseConformance(spec)
    }

    fun horses(matching: String): List<Horse> =
        if (matching.isEmpty()) horses else horses.filter { it.name.contains(matching, ignoreCase = true) }

    fun horse(id: Int): Horse? = horses.firstOrNull { it.id == id }

    fun notice(id: Int): Notice? = notices.firstOrNull { it.id == id }

    /** Push a Horse Detail, resetting its per-detail state so a new horse starts clean. */
    fun pushHorse(id: Int) {
        horseFavorite = false
        horseStatus = "idle"
        stablePath.add(id)
    }

    /**
     * Deeplinks (SPEC §4): select a tab, pop it to root, and dismiss any open Log modal. A deeplink does
     * not push a detail screen (BE-0079): a detail is reached only by tapping its catalog row.
     * `am start -a VIEW -d <scheme>://<host>`.
     */
    fun handleDeepLink(uri: Uri) {
        stablePath.clear()
        noticesPath.clear()
        logShowSheet = false
        logShowCover = false
        logShowDialog = false
        selectedTab = tab(uri.host)
    }

    companion object {
        /**
         * Parse the SHOWCASE_CONFORMANCE id spec (BE-0270). Wire format: the identifiers comma-joined
         * with a leading `,` sentinel, so the value is never the empty string — adb's
         * `am start --es KEY ""` drops an empty trailing arg (the quirk the clipboard channel also
         * documents), which would make the zero-match (empty) screen indistinguishable from "not in
         * conformance mode". null (key absent) leaves conformance mode off; `,a,b` renders [a, b]; a
         * bare `,` is the empty set. A leading `,` is stripped if present, so a hand-passed `a,b`
         * (no sentinel) still parses.
         */
        fun parseConformance(spec: String?): List<String>? {
            if (spec == null) return null
            val body = spec.removePrefix(",")
            return if (body.isEmpty()) emptyList() else body.split(",")
        }

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
