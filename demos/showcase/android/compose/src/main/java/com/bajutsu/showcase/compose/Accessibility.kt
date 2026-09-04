package com.bajutsu.showcase.compose

import androidx.compose.ui.Modifier
import androidx.compose.ui.semantics.selected
import androidx.compose.ui.semantics.semantics
import dev.bajutsu.identifier.compose.accessibilityId
import dev.bajutsu.identifier.compose.accessibilityStateValue
import dev.bajutsu.identifier.compose.enableAccessibilityIds

// SPEC §8: the single place identifiers (and state-mirroring values) enter the tree. Every helper is
// gated on BuildConfig.ACCESSIBLE, so the `noax` flavor compiles to a tree with no ids and no mirrored
// values — the honest "we skipped accessibility" app that `record` must cope with and `doctor` flags
// Blocked. Named to echo the iOS accessibilityID / accessibilityStateValue helpers. The tagging itself
// delegates to IdentifierTool (BE-0405), which ships no gate of its own.

/**
 * Attach a stable testTag in the a11y build; no-op otherwise. See IdentifierTool's
 * `accessibilityId` for how this reaches UI Automator's `resource-id`; the SPEC §5 dotted ids
 * (e.g. `stable.refresh`) reproduce verbatim, so the shared `scenarios/` set drives this app
 * unchanged.
 */
fun Modifier.aid(id: String): Modifier =
    if (BuildConfig.ACCESSIBLE) this.accessibilityId(id) else this

/**
 * Mirror state into `contentDescription` in the a11y build so assertions can read it; no-op
 * otherwise. See IdentifierTool's `accessibilityStateValue` for why `contentDescription` — the
 * Views twin mirrors to `content-desc` for the same reason (SPEC §2.1).
 */
fun Modifier.stateValue(value: String): Modifier =
    if (BuildConfig.ACCESSIBLE) this.accessibilityStateValue(value) else this

/**
 * Reflect a selected state (the iOS `.isSelected` trait). Unconditional, like the iOS apps: traits
 * are ordinary accessibility semantics, not the assertion-only ids/values SPEC §8 gates.
 */
fun Modifier.selectedState(isSelected: Boolean): Modifier =
    if (isSelected) this.semantics { selected = true } else this

/**
 * Enable `testTagsAsResourceId` so every `aid(...)` testTag surfaces as a UI Automator `resource-id`,
 * a11y flavor only. See IdentifierTool's `enableAccessibilityIds` for why this must also be applied
 * inside each modal window (ModalBottomSheet, Dialog).
 */
fun Modifier.enableTestTagsAsResourceId(): Modifier =
    if (BuildConfig.ACCESSIBLE) this.enableAccessibilityIds() else this
