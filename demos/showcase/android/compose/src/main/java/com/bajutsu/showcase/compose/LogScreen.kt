package com.bajutsu.showcase.compose

import androidx.compose.foundation.ExperimentalFoundationApi
import androidx.compose.foundation.background
import androidx.compose.foundation.combinedClickable
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.rememberModalBottomSheetState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateListOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.unit.dp
import androidx.compose.ui.window.Dialog
import androidx.compose.ui.window.DialogProperties
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch

// Tab: Log (SPEC §5.3) — a training-log composer exercising every input control, dedicated gesture
// targets, and all four modal styles (bottom sheet, full-screen cover, custom action-sheet overlay,
// auto-dismissing toast). Each control mirrors its result to an a11y value so a scenario can assert it.
@OptIn(ExperimentalMaterial3Api::class, ExperimentalFoundationApi::class)
@Composable
fun LogScreen(model: AppModel) {
    var note by remember { mutableStateOf("") }
    var count by remember { mutableIntStateOf(1) }
    var intense by remember { mutableStateOf(false) }
    var segment by remember { mutableStateOf("one") }
    var status by remember { mutableStateOf("idle") }
    val rows = remember { mutableStateListOf<Int>() }

    var longPressed by remember { mutableStateOf(false) }
    var doubleTaps by remember { mutableIntStateOf(0) }

    var showSheet by remember { mutableStateOf(false) }
    var showCover by remember { mutableStateOf(false) }
    var showDialog by remember { mutableStateOf(false) }
    var dialogResult by remember { mutableStateOf("none") }
    var showToast by remember { mutableStateOf(false) }

    val scope = rememberCoroutineScope()
    val segments = listOf("one", "two", "three")

    Box(Modifier.fillMaxSize()) {
        Column(Modifier.fillMaxSize()) {
            TopAppBar(title = { Text("Log") })
            Column(
                Modifier.weight(1f).verticalScroll(rememberScrollState()).padding(16.dp),
                verticalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                OutlinedTextField(
                    value = note,
                    onValueChange = { note = it },
                    label = { Text("Note") },
                    minLines = 3,
                    modifier = Modifier.fillMaxWidth().aid("log.note"),
                )

                // Stepper: the increment button carries log.count (tap to increment, capped 0..99);
                // the value mirrors to log.count.value.
                Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    TextButton(onClick = { if (count > 0) count-- }) { Text("−") }
                    TextButton(onClick = { if (count < 99) count++ }, modifier = Modifier.aid("log.count")) { Text("Count +") }
                }
                Text("Count: $count", Modifier.aid("log.count.value").stateValue(count.toString()))

                // A button-backed toggle (parity with iOS: a native switch does not flip under idb on
                // iOS 26). `selected` reflects state; value mirrors on/off.
                TextButton(
                    onClick = { intense = !intense },
                    modifier = Modifier.aid("log.intense").selectedState(intense),
                ) { Text(if (intense) "☑ Intense" else "☐ Intense") }
                Text(if (intense) "Intense" else "Easy", Modifier.aid("log.intense.value").stateValue(if (intense) "on" else "off"))

                TextButton(
                    onClick = {
                        status = "loading"
                        scope.launch {
                            status = Net.postLog(model.httpBase, note, count, intense)
                            if (status == "done") {
                                rows.add((rows.lastOrNull() ?: 0) + 1)
                                showToast = true
                            }
                        }
                    },
                    modifier = Modifier.aid("log.submit"),
                ) { Text("Submit") }
                Text("Status: $status", Modifier.aid("log.status").stateValue(status))

                // Modals — the four presentation styles.
                TextButton(onClick = { showSheet = true }, modifier = Modifier.aid("log.openFilter")) { Text("Open Filter") }
                TextButton(onClick = { showCover = true }, modifier = Modifier.aid("log.openGallery")) { Text("Open Gallery") }
                TextButton(onClick = { showDialog = true }, modifier = Modifier.aid("log.openDelete")) { Text("Open Delete") }
                Text("Dialog: $dialogResult", Modifier.aid("log.dialog.value").stateValue(dialogResult))

                // Gesture targets: a long-press and a double-tap, each mirroring its result.
                Text(
                    "Long-press me",
                    Modifier
                        .fillMaxWidth()
                        .combinedClickable(onClick = {}, onLongClick = { longPressed = true })
                        .padding(vertical = 8.dp)
                        .aid("log.longpress"),
                )
                Text(
                    if (longPressed) "pressed" else "idle",
                    Modifier.aid("log.longpress.value").stateValue(if (longPressed) "pressed" else "idle"),
                )
                Text(
                    "Double-tap me",
                    Modifier
                        .fillMaxWidth()
                        .pointerInput(Unit) { detectTapGestures(onDoubleTap = { doubleTaps++ }) }
                        .padding(vertical = 8.dp)
                        .aid("log.doubletap"),
                )
                Text("Double-taps: $doubleTaps", Modifier.aid("log.doubletap.value").stateValue(doubleTaps.toString()))

                // A button-backed segmented control; the selected button carries `selected`, choice
                // mirrors to log.segment.value.
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    segments.forEach { choice ->
                        TextButton(
                            onClick = { segment = choice },
                            modifier = Modifier.aid("log.segment.$choice").selectedState(segment == choice),
                        ) { Text(if (segment == choice) "● ${choice.replaceFirstChar { it.uppercase() }}" else choice.replaceFirstChar { it.uppercase() }) }
                    }
                }
                Text("Segment: $segment", Modifier.aid("log.segment.value").stateValue(segment))

                // Submitted entries.
                rows.forEach { n -> Text("Entry $n", Modifier.aid("log.row.$n")) }
            }
        }

        // The transient toast (~1.2 s auto-dismiss → exercises `wait until gone`).
        if (showToast) {
            LaunchedEffect(rows.size) {
                delay(1200)
                showToast = false
            }
            Surface(
                Modifier.align(Alignment.TopCenter).padding(16.dp).aid("log.toast"),
                shape = androidx.compose.foundation.shape.CircleShape,
                tonalElevation = 3.dp,
            ) { Text("Saved", Modifier.padding(horizontal = 16.dp, vertical = 10.dp)) }
        }
    }

    // Sheet with detents (SPEC §5.3).
    if (showSheet) {
        ModalBottomSheet(onDismissRequest = { showSheet = false }, sheetState = rememberModalBottomSheetState()) {
            Column(Modifier.padding(24.dp), verticalArrangement = Arrangement.spacedBy(16.dp)) {
                Text("Filter", Modifier.aid("log.sheet.title"))
                TextButton(onClick = { showSheet = false }, modifier = Modifier.aid("log.sheet.apply")) { Text("Apply") }
                TextButton(onClick = { showSheet = false }, modifier = Modifier.aid("log.sheet.close")) { Text("Close") }
            }
        }
    }

    // Full-screen cover.
    if (showCover) {
        Dialog(onDismissRequest = { showCover = false }, properties = DialogProperties(usePlatformDefaultWidth = false)) {
            Surface(Modifier.fillMaxSize()) {
                Column(Modifier.padding(24.dp), verticalArrangement = Arrangement.spacedBy(16.dp)) {
                    Text("Gallery", Modifier.aid("log.cover.title"))
                    TextButton(onClick = { showCover = false }, modifier = Modifier.aid("log.cover.close")) { Text("Close") }
                }
            }
        }
    }

    // Action sheet: a custom overlay of plain buttons (parity with iOS — a confirmationDialog's actions
    // render as duplicate elements under idb on iOS 26). Result mirrors to log.dialog.value.
    if (showDialog) {
        Box(
            Modifier.fillMaxSize().background(Color(0x33000000)),
            contentAlignment = Alignment.Center,
        ) {
            Surface(shape = androidx.compose.foundation.shape.RoundedCornerShape(16.dp), tonalElevation = 6.dp) {
                Column(Modifier.padding(24.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    Text("Delete entry", Modifier.aid("log.dialog.title"))
                    TextButton(onClick = { dialogResult = "archive"; showDialog = false }, modifier = Modifier.aid("log.dialog.archive")) { Text("Archive") }
                    TextButton(onClick = { dialogResult = "delete"; showDialog = false }, modifier = Modifier.aid("log.dialog.delete")) { Text("Delete") }
                    TextButton(onClick = { showDialog = false }, modifier = Modifier.aid("log.dialog.cancel")) { Text("Cancel") }
                }
            }
        }
    }
}
