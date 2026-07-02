package com.bajutsu.showcase.compose

import android.Manifest
import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp

// Tab: Permissions (SPEC §5.4 / §7) — the OS-integration screen. It owns the two deliberate
// runtime-permission prompts (notifications + location), the Android analog of iOS's SpringBoard
// alerts and the canonical alert-guard fixture, plus a System section: an in-app clipboard round-trip
// that the driver can drive and assert. Nothing here runs at launch; the prompts fire only on taps.
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun PermissionsScreen(model: AppModel) {
    val context = LocalContext.current

    val notifLauncher = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
        model.notifStatus = if (granted) "authorized" else "denied"
    }
    val locationLauncher = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
        model.locationStatus = if (granted) "authorizedWhenInUse" else "denied"
    }

    Column(Modifier.fillMaxSize()) {
        TopAppBar(title = { Text("Permissions") })
        Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Text("Notifications", style = MaterialTheme.typography.titleMedium)
            // Raises the POST_NOTIFICATIONS runtime dialog (out-of-process — the alert-guard fixture).
            TextButton(onClick = { notifLauncher.launch(Manifest.permission.POST_NOTIFICATIONS) }, modifier = Modifier.aid("perm.requestNotif")) {
                Text("Request Notifications")
            }
            Text("Notifications: ${model.notifStatus}", Modifier.aid("perm.notif.value").stateValue(model.notifStatus))
            if (model.notifStatus == "authorized") {
                // A positive condition the run can wait for once granted.
                Text("Granted", Modifier.aid("perm.notif.authorized"))
            }

            Text("Location", style = MaterialTheme.typography.titleMedium)
            TextButton(onClick = { locationLauncher.launch(Manifest.permission.ACCESS_FINE_LOCATION) }, modifier = Modifier.aid("perm.requestLocation")) {
                Text("Request Location")
            }
            Text("Location: ${model.locationStatus}", Modifier.aid("perm.location.value").stateValue(model.locationStatus))

            // Clipboard round-trip (SPEC §5.4): Copy writes a known string, Paste reads it back into
            // sys.paste.value — pasteboard state the driver's app-scoped query cannot otherwise see.
            Text("System", style = MaterialTheme.typography.titleMedium)
            TextButton(onClick = { clipboard(context).setPrimaryClip(ClipData.newPlainText("bajutsu", "bajutsu-clip")) }, modifier = Modifier.aid("sys.copy")) {
                Text("Copy")
            }
            TextButton(onClick = { model.pasted = clipboard(context).primaryClip?.getItemAt(0)?.text?.toString() ?: "" }, modifier = Modifier.aid("sys.paste")) {
                Text("Paste")
            }
            Text("Pasted: ${model.pasted}", Modifier.aid("sys.paste.value").stateValue(model.pasted))
        }
    }
}

private fun clipboard(context: Context): ClipboardManager =
    context.getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
