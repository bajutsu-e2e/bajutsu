package com.bajutsu.showcase.compose

import android.Manifest
import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.location.Location
import android.location.LocationListener
import android.location.LocationManager
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.PickVisualMediaRequest
import androidx.activity.result.contract.ActivityResultContracts
import androidx.activity.result.contract.ActivityResultContracts.PickVisualMedia
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import java.util.Locale

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

    // `selectPhotos` (SPEC §5.4): the system photo picker in its two modes, mirroring the picked count
    // back to perm.photos.value. Multi-selection confirms with the picker's add button; single-selection
    // returns on its one tap.
    val photosLauncher = rememberLauncherForActivityResult(ActivityResultContracts.PickMultipleVisualMedia()) { uris ->
        model.selectedPhotoCount = uris.size
    }
    val photoLauncher = rememberLauncherForActivityResult(PickVisualMedia()) { uri ->
        model.selectedPhotoCount = if (uri != null) 1 else 0
    }
    val imagesOnly = PickVisualMediaRequest(PickVisualMedia.ImageOnly)

    Column(Modifier.fillMaxSize()) {
        TopAppBar(title = { Text("Permissions") })
        // Scrollable like the Views twin's scroll page, so a shorter screen can still reach the
        // Photos section the select_photos scenarios scroll to.
        Column(
            Modifier.verticalScroll(rememberScrollState()).padding(16.dp),
            verticalArrangement = Arrangement.spacedBy(8.dp),
        ) {
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
            // Reads one fresh GPS fix (SPEC §5.4) — the position a `setLocation` step moved,
            // whichever route bajutsu took to move it (BE-0481).
            TextButton(onClick = { model.locationReading = "reading"; readLocation(context) { model.locationReading = it } }, modifier = Modifier.aid("perm.location.read")) {
                Text("Read Location")
            }
            Text("Position: ${model.locationReading}", Modifier.aid("perm.location.reading").stateValue(model.locationReading))

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

            Text("Photos", style = MaterialTheme.typography.titleMedium)
            TextButton(onClick = { photosLauncher.launch(imagesOnly) }, modifier = Modifier.aid("perm.openPhotoPicker")) {
                Text("Open Photo Picker")
            }
            TextButton(onClick = { photoLauncher.launch(imagesOnly) }, modifier = Modifier.aid("perm.openPhotoPickerSingle")) {
                Text("Open Photo Picker (Single)")
            }
            Text("Selected: ${model.selectedPhotoCount}", Modifier.aid("perm.photos.value").stateValue(model.selectedPhotoCount.toString()))
        }
    }
}

/** How long a read waits for a fix delivered to it before it reports the last known one. */
private const val FRESH_FIX_TIMEOUT_MS = 5_000L

/**
 * Report the next `gps` fix delivered after the tap as `lat,lon` (or `(none)` / `denied`).
 *
 * Only a fix delivered to this request counts: the platform's "current location" can be a cached
 * fix up to tens of seconds old, which after a second console `geo fix` still shows the first one.
 * The emulator's GPS re-emits its `geo fix` about once a second while someone listens, so the console
 * route answers within a second. A test provider emits only when mocked, so the in-app route delivers
 * nothing new; after [FRESH_FIX_TIMEOUT_MS] the last known fix, which is the mock, is reported.
 */
private fun readLocation(context: Context, onReading: (String) -> Unit) {
    val manager = context.getSystemService(Context.LOCATION_SERVICE) as LocationManager
    val main = Handler(Looper.getMainLooper())
    var reported = false
    lateinit var listener: LocationListener
    fun report(fix: Location?) {
        if (reported) return
        reported = true
        manager.removeUpdates(listener)
        main.removeCallbacksAndMessages(null)
        onReading(fix?.let { String.format(Locale.US, "%.3f,%.3f", it.latitude, it.longitude) } ?: "(none)")
    }
    // An object, not a lambda: below API 30 the other callbacks are abstract and the platform calls them.
    listener = object : LocationListener {
        override fun onLocationChanged(location: Location) = report(location)
        override fun onProviderEnabled(provider: String) = Unit
        override fun onProviderDisabled(provider: String) = Unit
        @Deprecated("Deprecated in Java")
        override fun onStatusChanged(provider: String?, status: Int, extras: Bundle?) = Unit
    }
    try {
        manager.requestLocationUpdates(LocationManager.GPS_PROVIDER, 0L, 0f, listener, Looper.getMainLooper())
    } catch (_: SecurityException) {
        onReading("denied")
        return
    }
    main.postDelayed({
        report(runCatching { manager.getLastKnownLocation(LocationManager.GPS_PROVIDER) }.getOrNull())
    }, FRESH_FIX_TIMEOUT_MS)
}

private fun clipboard(context: Context): ClipboardManager =
    context.getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
