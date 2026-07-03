package com.bajutsu.showcase.views

import android.view.View

// SPEC §8: the single place identifiers (and state-mirroring values) enter the tree, the Views twin
// of the UIKit `accessibilityID(_:)` extension. Gated on BuildConfig.ACCESSIBLE, so the `noax` flavor
// compiles to a tree with no resource-ids and no mirrored values — the honest "we skipped
// accessibility" app that `record` must cope with and `doctor` flags Blocked.

/**
 * Assign the view its stable id from res/values/ids.xml in the a11y build; no-op otherwise. UI
 * Automator surfaces the id as `resource-id`. `name` is the SPEC §5 id with '.'/'-' mapped to '_'
 * (an android:id name allows neither); an undeclared name resolves to 0 and is skipped, which is how
 * data-derived rows beyond the pre-declared fixture range stay id-less.
 */
fun <T : View> T.aid(name: String): T {
    if (BuildConfig.ACCESSIBLE) {
        val resolved = resources.getIdentifier(name, "id", context.packageName)
        if (resolved != 0) id = resolved
    }
    return this
}

/**
 * Mirror state into `contentDescription` in the a11y build so assertions can read it; no-op
 * otherwise. The Views analog of iOS's accessibilityValue: UI Automator exposes it as `content-desc`.
 */
fun <T : View> T.stateValue(value: String): T {
    if (BuildConfig.ACCESSIBLE) contentDescription = value
    return this
}
