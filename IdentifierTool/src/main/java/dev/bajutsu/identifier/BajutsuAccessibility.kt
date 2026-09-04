package dev.bajutsu.identifier

import android.view.View

// The View-based half of IdentifierTool (BE-0405), ported from the showcase's own `aid` / `stateValue`
// with the BuildConfig.ACCESSIBLE gate dropped: a compiled library cannot read a field generated on an
// arbitrary consumer's own classpath, so gating is left to the caller. See IdentifierTool/README.md.

/**
 * Assign the view its id, resolved by name from the consumer's own `android:id` resources.
 * UI Automator surfaces it as `resource-id`. `name` must already exist as a declared `android:id`
 * resource; an undeclared name resolves to `0` and is skipped, which reads as "this view carries no
 * id," not as a typo.
 */
fun <T : View> T.accessibilityId(name: String): T {
    val resolved = resources.getIdentifier(name, "id", context.packageName)
    if (resolved != 0) id = resolved
    return this
}

/** Mirror state into `contentDescription`, surfaced by UI Automator as `content-desc`. */
fun <T : View> T.accessibilityStateValue(value: String): T {
    contentDescription = value
    return this
}
