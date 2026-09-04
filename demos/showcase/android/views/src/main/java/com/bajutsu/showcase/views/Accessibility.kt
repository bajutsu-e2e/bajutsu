package com.bajutsu.showcase.views

import android.view.View
import dev.bajutsu.android.BajutsuZOrder
import dev.bajutsu.identifier.accessibilityId
import dev.bajutsu.identifier.accessibilityStateValue

// SPEC §8: the single place identifiers (and state-mirroring values) enter the tree, the Views twin
// of the UIKit `accessibilityID(_:)` extension. Gated on BuildConfig.ACCESSIBLE, so the `noax` flavor
// compiles to a tree with no resource-ids and no mirrored values — the honest "we skipped
// accessibility" app that `record` must cope with and `doctor` flags Blocked. The tagging itself
// delegates to IdentifierTool (BE-0405), which ships no gate of its own; z-order reporting stays
// composed here rather than in IdentifierTool, since it depends on BajutsuAndroid.

/**
 * Assign the view its stable id from res/values/ids.xml in the a11y build; no-op otherwise. `name`
 * is the SPEC §5 id with '.'/'-' mapped to '_' (an android:id name allows neither); an undeclared
 * name resolves to 0 and is skipped, which is how data-derived rows beyond the pre-declared fixture
 * range stay id-less.
 */
fun <T : View> T.aid(name: String): T {
    if (BuildConfig.ACCESSIBLE) {
        accessibilityId(name)
        // Opt the same views into reporting their own front-to-back position (BE-0355). Gated with
        // the id for one reason: a view bajutsu cannot name is a view no evidence reader can look up
        // a position for. Debug-flavored showcase only — the delegate is readable by any
        // accessibility client on the device.
        BajutsuZOrder.report(this)
    }
    return this
}

/**
 * Mirror state into `contentDescription` in the a11y build so assertions can read it; no-op
 * otherwise. The Views analog of iOS's accessibilityValue: UI Automator exposes it as `content-desc`.
 */
fun <T : View> T.stateValue(value: String): T {
    if (BuildConfig.ACCESSIBLE) accessibilityStateValue(value)
    return this
}
