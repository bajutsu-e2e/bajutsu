package com.bajutsu.showcase.compose

import android.content.Context
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Button
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp

// BE-0447: the update fixture, mirroring the iOS SavedNoteView, reached only under the
// SHOWCASE_SAVED_NOTE launch env. Everything else in the app is in-memory by design
// (scenarios/relaunch.yaml relies on that), so this screen is the one place a note outlives the
// process: the update scenario saves it on the `previous` flavor, installs the a11y build over it with
// its data kept, and reads it back. `saved.build` names which build is in front.
@Composable
fun SavedNoteScreen() {
    val prefs = LocalContext.current.getSharedPreferences("saved-note", Context.MODE_PRIVATE)
    var draft by remember { mutableStateOf("") }
    var saved by remember { mutableStateOf(prefs.getString("note", null)) }

    Column(
        modifier = Modifier
            .fillMaxSize()
            .padding(24.dp)
            .enableTestTagsAsResourceId(),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.spacedBy(24.dp, Alignment.CenterVertically),
    ) {
        Text(
            text = "Build: ${BuildConfig.SHOWCASE_BUILD}",
            modifier = Modifier.aid("saved.build").stateValue(BuildConfig.SHOWCASE_BUILD),
        )
        OutlinedTextField(
            value = draft,
            onValueChange = { draft = it },
            label = { Text("Note") },
            modifier = Modifier.aid("saved.field"),
        )
        Button(
            onClick = {
                // `commit`, not `apply`: `installApp` force-stops the app right after the save, and
                // an asynchronous write still queued could be lost to that kill. Shown only once it
                // is on disk, so a failed write fails the save step's own check.
                if (prefs.edit().putString("note", draft).commit()) saved = draft
            },
            modifier = Modifier.aid("saved.save"),
        ) {
            Text("Save")
        }
        Text(
            text = "Saved: ${saved ?: "none"}",
            modifier = Modifier.aid("saved.value").stateValue(saved ?: "none"),
        )
    }
}
