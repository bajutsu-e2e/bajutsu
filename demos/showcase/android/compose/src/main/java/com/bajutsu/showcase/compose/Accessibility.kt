package com.bajutsu.showcase.compose

import androidx.compose.ui.ExperimentalComposeUiApi
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.selected
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.semantics.testTagsAsResourceId

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

/**
 * Mirror state into `contentDescription` in the a11y build so assertions can read it; no-op otherwise.
 * `contentDescription` is chosen over `stateDescription` because a `uiautomator dump` exposes the former
 * as `content-desc` but has no attribute for the latter — the Views twin mirrors to `content-desc` for
 * the same reason (SPEC §2.1). The exact BE-0007 selector field this maps to (`label` vs `value`) is a
 * driver decision; what matters here is that the mirrored value is present in the dump at all.
 */
fun Modifier.stateValue(value: String): Modifier =
    if (BuildConfig.ACCESSIBLE) this.semantics { contentDescription = value } else this

/**
 * Reflect a selected state (the iOS `.isSelected` trait). Unconditional, like the iOS apps: traits
 * are ordinary accessibility semantics, not the assertion-only ids/values SPEC §8 gates.
 */
fun Modifier.selectedState(isSelected: Boolean): Modifier =
    if (isSelected) this.semantics { selected = true } else this

/**
 * Enable `testTagsAsResourceId` so every `aid(...)` testTag surfaces as a UI Automator `resource-id`
 * (BE-0007's Compose id convention), a11y flavor only. Applied at the content root AND inside each
 * modal window (ModalBottomSheet, Dialog): those host their own semantics tree, so the root flag does
 * not reach them and their testTags would otherwise dump with an empty resource-id.
 */
@OptIn(ExperimentalComposeUiApi::class)
fun Modifier.enableTestTagsAsResourceId(): Modifier =
    if (BuildConfig.ACCESSIBLE) this.semantics { testTagsAsResourceId = true } else this
