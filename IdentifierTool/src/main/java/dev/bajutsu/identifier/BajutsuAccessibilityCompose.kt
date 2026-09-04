package dev.bajutsu.identifier

import androidx.compose.ui.ExperimentalComposeUiApi
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.semantics.testTagsAsResourceId

// The Compose half of IdentifierTool (BE-0405), ported from the showcase's own `aid` / `stateValue` /
// `enableTestTagsAsResourceId` with the BuildConfig.ACCESSIBLE gate dropped — see
// BajutsuAccessibility.kt and IdentifierTool/README.md for why. Plain extension functions, not
// @Composable ones, so this file needs no Compose compiler plugin.

/**
 * Attach a stable testTag, surfaced by UI Automator as `resource-id` wherever the enclosing tree has
 * `testTagsAsResourceId = true` set (see [enableAccessibilityIds]). testTag accepts any string, so a
 * dotted id (e.g. `stable.refresh`) reproduces verbatim.
 */
fun Modifier.accessibilityId(id: String): Modifier = this.testTag(id)

/**
 * Mirror state into `contentDescription` so assertions can read it. `contentDescription` is chosen
 * over `stateDescription` because a `uiautomator dump` exposes the former as `content-desc` but has
 * no attribute for the latter.
 */
fun Modifier.accessibilityStateValue(value: String): Modifier =
    this.semantics { contentDescription = value }

/**
 * Enable `testTagsAsResourceId` so every [accessibilityId] testTag in this subtree surfaces as a UI
 * Automator `resource-id`. Apply at the content root AND inside each modal window (`ModalBottomSheet`,
 * `Dialog`): those host their own semantics tree, so the root's flag never reaches them and their
 * testTags would otherwise dump with an empty `resource-id`.
 */
@OptIn(ExperimentalComposeUiApi::class)
fun Modifier.enableAccessibilityIds(): Modifier = this.semantics { testTagsAsResourceId = true }
