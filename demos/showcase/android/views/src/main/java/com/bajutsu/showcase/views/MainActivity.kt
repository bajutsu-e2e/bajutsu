package com.bajutsu.showcase.views

import android.content.Intent
import android.os.Bundle
import android.view.View
import android.widget.FrameLayout
import android.widget.LinearLayout
import androidx.appcompat.app.AppCompatActivity

/**
 * The five-tab main UI (SPEC §5): Stable, Search, Log, Notices, Permissions. Reads the `SHOWCASE_*`
 * launch-env hooks from intent extras (BE-0007: `launchEnv` → intent extras) and routes the VIEW
 * deeplink to a tab selection. Each tab button also carries its own idNamespace as its `aid` — the
 * Views twin of iOS's UITabBarItem.accessibilityIdentifier — so an a11y build can address tabs by
 * `id` instead of falling back to label/deeplink.
 */
class MainActivity : AppCompatActivity() {
    private lateinit var model: AppModel
    private lateinit var overlay: FrameLayout
    private lateinit var content: FrameLayout
    private lateinit var tabRoots: Map<Tab, View>
    private lateinit var tabButtons: Map<Tab, View>
    private lateinit var logTab: LogTab

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        model = AppModel(readLaunchEnv(intent))
        intent?.data?.let { model.selectedTab = model.deepLinkTab(it) }

        content = FrameLayout(this)
        tabButtons = Tab.entries.associateWith { tab -> textButton(title(tab)) { select(tab) }.aid(tabId(tab)) }
        val tabBar = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            tabButtons.values.forEach { button ->
                addView(button, LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f))
            }
        }
        val column = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            addView(content, LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, 0, 1f))
            addView(tabBar, LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.WRAP_CONTENT))
        }
        // The overlay layer hosts the Log tab's custom action sheet and its transient toast.
        // (Built outside an .apply block: inside one, `overlay` would resolve to the
        // FrameLayout's own View.overlay property, not this field.)
        overlay = FrameLayout(this)
        val root = FrameLayout(this)
        root.addView(column)
        root.addView(overlay)
        setContentView(root)

        // All five tabs are built once and toggled, so tab state survives switching (iOS parity).
        logTab = LogTab(this, model, overlay)
        tabRoots = mapOf(
            Tab.STABLE to StableTab(this, model).root,
            Tab.SEARCH to SearchTab(this, model).root,
            Tab.LOG to logTab.root,
            Tab.NOTICES to NoticesTab(this, model).root,
            Tab.PERMISSIONS to PermissionsTab(this).root,
        )
        tabRoots.values.forEach { content.addView(it) }
        select(model.selectedTab)
    }

    // A deeplink to the running app lands here (singleTask); any pushed detail Activity above has
    // already been finished by the task re-parenting — the "pop to root" of SPEC §4. Dismiss any open
    // Log modal too (SPEC §4: a deeplink dismisses modals), which a plain tab switch would not close.
    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        setIntent(intent)
        intent.data?.let {
            logTab.dismissModals()
            select(model.deepLinkTab(it))
        }
    }

    private fun select(tab: Tab) {
        model.selectedTab = tab
        tabRoots.forEach { (t, v) -> v.visibility = if (t == tab) View.VISIBLE else View.GONE }
        // Mirrors the OS-native selected state a UITabBarItem carries for free — a plain Button has
        // none, so the active tab's `selected` trait (SPEC-shared scenarios/tabs.yaml) needs it set
        // explicitly here, and cleared on whichever tab was previously active.
        tabButtons.forEach { (t, v) -> v.isSelected = (t == tab) }
    }

    private fun title(tab: Tab): String = when (tab) {
        Tab.STABLE -> "Stable"
        Tab.SEARCH -> "Search"
        Tab.LOG -> "Log"
        Tab.NOTICES -> "Notices"
        Tab.PERMISSIONS -> "Permissions"
    }

    // The tab's own idNamespace (SPEC §9), mirroring MainTabBarController's accessibilityID(_:) — not
    // "tab_stable" etc., so the same bare id also matches the iOS side of the shared scenarios/ set.
    private fun tabId(tab: Tab): String = when (tab) {
        Tab.STABLE -> "stable"
        Tab.SEARCH -> "search"
        Tab.LOG -> "log"
        Tab.NOTICES -> "notice"
        Tab.PERMISSIONS -> "perm"
    }

    // launchEnv (SPEC §3) arrives as string intent extras; read once, defaults live in AppModel.
    private fun readLaunchEnv(intent: Intent?): Map<String, String> {
        val extras = intent?.extras ?: return emptyMap()
        val keys = listOf("SHOWCASE_UITEST", "SHOWCASE_TAB", "SHOWCASE_API_URL", "SHOWCASE_HTTP_BASE")
        return keys.mapNotNull { key -> extras.getString(key)?.let { key to it } }.toMap()
    }
}
