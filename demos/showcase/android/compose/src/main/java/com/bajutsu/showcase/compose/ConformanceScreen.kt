package com.bajutsu.showcase.compose

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.text.BasicTextField
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp

// The marker present on every conformance screen, including the empty (zero-match) one — mirrors the
// iOS ConformanceView.readyID and the on-device harness's _READY_ID (they must stay in step).
const val CONFORMANCE_READY_ID = "conformance.ready"

// The always-present editable field the text-editing and tap_point contract invariants act on
// (BE-0280) — mirrors the iOS ConformanceView.fieldID and the web _render field. Present on every
// conformance screen like the marker, with a fixed size so the coordinate tap has a known center.
const val CONFORMANCE_FIELD_ID = "conformance.field"

// BE-0114 / BE-0270: the on-device realization of a driver-conformance screen for the adb backend,
// the Compose twin of the iOS ConformanceView. The conformance suite seeds an arbitrary set of
// identifiers — duplicated (an ambiguous selector), empty (a zero-match), or unique — and each
// becomes one clickable, gestureable element carrying that identifier as its resource-id (via
// testTag) and as its visible label. That drives the adb driver through the *same* backend-agnostic
// contract (tests/driver_conformance.py) as FakeDriver, Playwright, and XCUITest, against the
// driver's real query / act code rather than the shared base alone. Reached only when the
// SHOWCASE_CONFORMANCE launch env is set (see AppModel / RootScreen), so the normal observe-only app
// (BE-0079) is untouched; the suite reseeds the screen by re-launching with a new SHOWCASE_CONFORMANCE
// extra, delivered to the running singleTask activity through onNewIntent.
@Composable
fun ConformanceScreen(identifiers: List<String>) {
    Column(
        modifier = Modifier
            .fillMaxSize()
            .enableTestTagsAsResourceId(),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.spacedBy(8.dp, Alignment.CenterVertically),
    ) {
        // A positive readiness check: the harness waits on this marker to confirm the app is actually
        // in conformance mode, rather than inferring it from the absence of ids — which a transient,
        // near-empty tree during a relaunch could satisfy too early.
        Text("ready", Modifier.aid(CONFORMANCE_READY_ID))
        // The editable field, always present so the text-editing / tap_point invariants have a real
        // field on every screen. A BasicTextField surfaces its content as the node's text (the value
        // the adb driver reads back); the fixed size gives the coordinate tap a known center.
        var fieldText by remember { mutableStateOf("") }
        BasicTextField(
            value = fieldText,
            onValueChange = { fieldText = it },
            // Mirror the text into content-desc: the adb driver maps a node's `text` to `label` and
            // content-desc to `value`, and the contract reads `value` (as the iOS AXValue and the web
            // input value do), so without this the typed text would land in `label` and the round-trip
            // length change the contract observes would be invisible.
            modifier = Modifier
                .size(width = 280.dp, height = 44.dp)
                .background(MaterialTheme.colorScheme.surfaceVariant)
                .aid(CONFORMANCE_FIELD_ID)
                .stateValue(fieldText),
        )
        // Duplicates are the point (the ambiguous-selector case), so the children are keyed by
        // position (Column's default), never by identifier — keying by id would collapse repeats.
        identifiers.forEach { identifier ->
            // A generous, opaque, *clickable* hit area. Clickable so the adb driver tags the node with
            // the BUTTON trait (a tappable node), which — paired with the visible label — resolves the
            // shared `{ label, traits: [button] }` selector (BE-0107). Sized so a two-finger
            // pinch/rotate (the MULTI_TOUCH case) has real room between its touch points; an
            // intrinsically-sized control would collapse them. The text equals the identifier, so the
            // driver reads the element's label as the identifier too.
            Box(
                modifier = Modifier
                    .size(width = 280.dp, height = 90.dp)
                    .background(MaterialTheme.colorScheme.surfaceVariant)
                    .aid(identifier)
                    .clickable {},
                contentAlignment = Alignment.Center,
            ) {
                Text(identifier)
            }
        }
    }
}
