package com.bajutsu.showcase.compose

import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.semantics.selected
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.semantics.stateDescription

// SPEC §8: the single place identifiers (and state-mirroring values) enter the tree. Every helper is
// gated on BuildConfig.ACCESSIBLE, so the `noax` flavor compiles to a tree with no ids and no mirrored
// values — the honest "we skipped accessibility" app that `record` must cope with and `doctor` flags
// Blocked. Named to echo the iOS accessibilityID / accessibilityStateValue helpers.

/**
 * Attach a stable testTag in the a11y build; no-op otherwise. The testTag surfaces as UI Automator's
 * `resource-id` because the content root sets `testTagsAsResourceId = true` (BE-0007's Compose id
 * convention). testTag accepts any string, so the SPEC §5 dotted ids (e.g. `stable.refresh`) reproduce
 * verbatim — the shared `scenarios/` set drives this app unchanged.
 */
fun Modifier.aid(id: String): Modifier =
    if (BuildConfig.ACCESSIBLE) this.testTag(id) else this

/** Mirror state into `stateDescription` in the a11y build so assertions can read it; no-op otherwise. */
fun Modifier.stateValue(value: String): Modifier =
    if (BuildConfig.ACCESSIBLE) this.semantics { stateDescription = value } else this

/**
 * Reflect a selected state (the iOS `.isSelected` trait). Unconditional, like the iOS apps: traits
 * are ordinary accessibility semantics, not the assertion-only ids/values SPEC §8 gates.
 */
fun Modifier.selectedState(isSelected: Boolean): Modifier =
    if (isSelected) this.semantics { selected = true } else this
