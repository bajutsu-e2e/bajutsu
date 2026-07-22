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
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.rememberModalBottomSheetState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.rememberCoroutineScope
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
// All mutable state lives on AppModel (not screen-local `remember`) so it survives a tab switch — see
// the AppModel class doc.
@OptIn(ExperimentalMaterial3Api::class, ExperimentalFoundationApi::class)
@Composable
fun LogScreen(model: AppModel) {
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
                    value = model.logNote,
                    onValueChange = { model.logNote = it },
                    label = { Text("Note") },
                    minLines = 3,
                    modifier = Modifier.fillMaxWidth().aid("log.note"),
                )

                // Stepper: the increment button carries log.count (tap to increment, capped 0..99);
                // the value mirrors to log.count.value.
                Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    TextButton(onClick = { if (model.logCount > 0) model.logCount-- }) { Text("−") }
                    TextButton(onClick = { if (model.logCount < 99) model.logCount++ }, modifier = Modifier.aid("log.count")) { Text("Count +") }
                }
                Text("Count: ${model.logCount}", Modifier.aid("log.count.value").stateValue(model.logCount.toString()))

                // A button-backed toggle (parity with iOS: the retired idb backend could not flip a native
                // switch on iOS 26, BE-0290). `selected` reflects state; value mirrors on/off.
                TextButton(
                    onClick = { model.logIntense = !model.logIntense },
                    modifier = Modifier.aid("log.intense").selectedState(model.logIntense),
                ) { Text(if (model.logIntense) "☑ Intense" else "☐ Intense") }
                Text(if (model.logIntense) "Intense" else "Easy", Modifier.aid("log.intense.value").stateValue(if (model.logIntense) "on" else "off"))

                TextButton(
                    onClick = {
                        model.logStatus = "loading"
                        scope.launch {
                            model.logStatus = Net.postLog(model.httpBase, model.logNote, model.logCount, model.logIntense)
                            if (model.logStatus == "done") {
                                model.logRows.add((model.logRows.lastOrNull() ?: 0) + 1)
                                model.logShowToast = true
                            }
                        }
                    },
                    modifier = Modifier.aid("log.submit"),
                ) { Text("Submit") }
                Text("Status: ${model.logStatus}", Modifier.aid("log.status").stateValue(model.logStatus))

                // Modals — the four presentation styles.
                TextButton(onClick = { model.logShowSheet = true }, modifier = Modifier.aid("log.openFilter")) { Text("Open Filter") }
                TextButton(onClick = { model.logShowCover = true }, modifier = Modifier.aid("log.openGallery")) { Text("Open Gallery") }
                TextButton(onClick = { model.logShowDialog = true }, modifier = Modifier.aid("log.openDelete")) { Text("Open Delete") }
                Text("Dialog: ${model.logDialogResult}", Modifier.aid("log.dialog.value").stateValue(model.logDialogResult))

                // Gesture targets: a long-press and a double-tap, each mirroring its result.
                Text(
                    "Long-press me",
                    Modifier
                        .fillMaxWidth()
                        .combinedClickable(onClick = {}, onLongClick = { model.logLongPressed = true })
                        .padding(vertical = 8.dp)
                        .aid("log.longpress"),
                )
                Text(
                    if (model.logLongPressed) "pressed" else "idle",
                    Modifier.aid("log.longpress.value").stateValue(if (model.logLongPressed) "pressed" else "idle"),
                )
                Text(
                    "Double-tap me",
                    Modifier
                        .fillMaxWidth()
                        .pointerInput(Unit) { detectTapGestures(onDoubleTap = { model.logDoubleTaps++ }) }
                        .padding(vertical = 8.dp)
                        .aid("log.doubletap"),
                )
                Text("Double-taps: ${model.logDoubleTaps}", Modifier.aid("log.doubletap.value").stateValue(model.logDoubleTaps.toString()))

                // A button-backed segmented control; the selected button carries `selected`, choice
                // mirrors to log.segment.value.
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    segments.forEach { choice ->
                        TextButton(
                            onClick = { model.logSegment = choice },
                            modifier = Modifier.aid("log.segment.$choice").selectedState(model.logSegment == choice),
                        ) { Text(if (model.logSegment == choice) "● ${choice.replaceFirstChar { it.uppercase() }}" else choice.replaceFirstChar { it.uppercase() }) }
                    }
                }
                Text("Segment: ${model.logSegment}", Modifier.aid("log.segment.value").stateValue(model.logSegment))

                // Submitted entries.
                model.logRows.forEach { n -> Text("Entry $n", Modifier.aid("log.row.$n")) }
            }
        }

        // The transient toast (~1.2 s auto-dismiss → exercises `wait until gone`).
        if (model.logShowToast) {
            LaunchedEffect(model.logRows.size) {
                delay(1200)
                model.logShowToast = false
            }
            Surface(
                Modifier.align(Alignment.TopCenter).padding(16.dp).aid("log.toast"),
                shape = CircleShape,
                tonalElevation = 3.dp,
            ) { Text("Saved", Modifier.padding(horizontal = 16.dp, vertical = 10.dp)) }
        }
    }

    // Sheet with detents (SPEC §5.3). A ModalBottomSheet is its own window, so it must re-enable
    // testTagsAsResourceId for its testTags to surface as resource-ids (the root flag doesn't reach it).
    if (model.logShowSheet) {
        ModalBottomSheet(onDismissRequest = { model.logShowSheet = false }, sheetState = rememberModalBottomSheetState()) {
            Column(
                Modifier.enableTestTagsAsResourceId().padding(24.dp),
                verticalArrangement = Arrangement.spacedBy(16.dp),
            ) {
                Text("Filter", Modifier.aid("log.sheet.title"))
                TextButton(onClick = { model.logShowSheet = false }, modifier = Modifier.aid("log.sheet.apply")) { Text("Apply") }
                TextButton(onClick = { model.logShowSheet = false }, modifier = Modifier.aid("log.sheet.close")) { Text("Close") }
            }
        }
    }

    // Full-screen cover. Also its own window — re-enable testTagsAsResourceId on its root.
    if (model.logShowCover) {
        Dialog(onDismissRequest = { model.logShowCover = false }, properties = DialogProperties(usePlatformDefaultWidth = false)) {
            Surface(Modifier.fillMaxSize().enableTestTagsAsResourceId()) {
                Column(Modifier.padding(24.dp), verticalArrangement = Arrangement.spacedBy(16.dp)) {
                    Text("Gallery", Modifier.aid("log.cover.title"))
                    TextButton(onClick = { model.logShowCover = false }, modifier = Modifier.aid("log.cover.close")) { Text("Close") }
                }
            }
        }
    }

    // Action sheet: a custom overlay of plain buttons (parity with iOS — a confirmationDialog's actions
    // render as duplicate elements on iOS 26, which the retired idb backend could not drive, BE-0290). It renders inside the main composition, so the
    // root testTagsAsResourceId already reaches it. The scrim consumes taps (empty detectTapGestures) so
    // controls beneath the "modal" are not actuable while it is shown. Result mirrors to log.dialog.value.
    if (model.logShowDialog) {
        Box(
            Modifier
                .fillMaxSize()
                .background(Color(0x33000000))
                .pointerInput(Unit) { detectTapGestures {} },
            contentAlignment = Alignment.Center,
        ) {
            Surface(shape = RoundedCornerShape(16.dp), tonalElevation = 6.dp) {
                Column(Modifier.padding(24.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    Text("Delete entry", Modifier.aid("log.dialog.title"))
                    TextButton(onClick = { model.logDialogResult = "archive"; model.logShowDialog = false }, modifier = Modifier.aid("log.dialog.archive")) { Text("Archive") }
                    TextButton(onClick = { model.logDialogResult = "delete"; model.logShowDialog = false }, modifier = Modifier.aid("log.dialog.delete")) { Text("Delete") }
                    TextButton(onClick = { model.logShowDialog = false }, modifier = Modifier.aid("log.dialog.cancel")) { Text("Cancel") }
                }
            }
        }
    }
}
