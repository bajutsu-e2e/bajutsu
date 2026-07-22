package com.bajutsu.showcase.compose

import androidx.compose.foundation.background
import androidx.compose.foundation.gestures.detectTransformGestures
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.size
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.unit.dp

// BE-0232: the Compose realization of the two-finger gesture screen, mirroring the iOS GestureView
// (BE-0019). Pinch and rotate are the one class of actuation a single-touch backend (like the retired
// idb backend, BE-0290) cannot perform, and on adb they need a rooted `sendevent` sweep; each target flips its mirrored a11y value
// once its gesture is recognized, so the shared `gestures_multitouch` run can assert the actuation
// landed rather than merely not erroring. Reached only when the SHOWCASE_GESTURES launch env is set
// (see AppModel / RootScreen), so the normal five-tab app (BE-0079) never renders it. A flat, scroll-
// free Column so both targets are always in the tree — a two-finger target must be fully on-screen for
// its touch points anyway, and the shared scenario reads their ids (log.pinch / log.rotate) unchanged.
@Composable
fun GestureScreen() {
    Column(
        modifier = Modifier
            .fillMaxSize()
            .enableTestTagsAsResourceId(),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.spacedBy(24.dp, Alignment.CenterVertically),
    ) {
        GestureTarget("Pinch me", "log.pinch", pressedLabel = "pinched") { zoom, _ -> zoom != 1f }
        GestureTarget("Rotate me", "log.rotate", pressedLabel = "rotated") { _, rot -> rot != 0f }
    }
}

// A generous, opaque hit area (as on iOS): a pinch/rotate is two touch points that need real room,
// and the gesture degenerates on an intrinsically-sized view. `detectTransformGestures` reports pan,
// zoom, and rotation together; `recognized` picks the channel this target cares about (zoom for
// pinch, rotation for rotate), so a pure pinch does not trip the rotate target and vice versa. The id
// (log.pinch) tags the hit area and its `.value` twin mirrors the result into content-desc — the same
// aid + stateValue pairing the Log tab uses (SPEC §8), so the shared scenario reads it unchanged.
@Composable
private fun GestureTarget(
    label: String,
    id: String,
    pressedLabel: String,
    recognized: (zoom: Float, rotation: Float) -> Boolean,
) {
    var fired by remember { mutableStateOf(false) }
    Text(
        text = label,
        modifier = Modifier
            .size(width = 280.dp, height = 120.dp)
            .background(MaterialTheme.colorScheme.surfaceVariant)
            .aid(id)
            .pointerInput(Unit) {
                detectTransformGestures { _, _, zoom, rotation ->
                    if (recognized(zoom, rotation)) fired = true
                }
            },
    )
    val value = if (fired) pressedLabel else "idle"
    Text(value, Modifier.aid("$id.value").stateValue(value))
}
