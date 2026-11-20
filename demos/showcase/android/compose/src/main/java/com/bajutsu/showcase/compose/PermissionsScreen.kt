package com.bajutsu.showcase.compose

import android.Manifest
import android.app.NotificationChannel
import android.app.NotificationManager
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
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalConfiguration
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import androidx.core.app.NotificationCompat
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
    // The banner guard's fixture (BE-0496): the counter shows whether a tap reached the app bar's
    // banner target rather than the heads-up notification over it.
    var bannerTaps by remember { mutableIntStateOf(0) }

    Column(Modifier.fillMaxSize()) {
        // The banner target sits in the app bar, the band a heads-up notification lands over at every
        // API level measured (33-35); the content column below starts under the banner's lower edge.
        TopAppBar(
            title = { Text("Permissions") },
            actions = {
                TextButton(onClick = { bannerTaps++ }, modifier = Modifier.aid("perm.bannerTarget")) {
                    Text("Banner Target")
                }
            },
        )
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
            // The language the app's own configuration carries (SPEC §5.4), so a scenario can see the
            // per-app locale `runsOn.android.locale` (or its
            // `preconditions.device.android.locale` override) applied (BE-0478), not only the read-back.
            val locale = LocalConfiguration.current.locales[0].toLanguageTag()
            Text("Locale: $locale", Modifier.aid("sys.locale.value").stateValue(locale))

            Text("Photos", style = MaterialTheme.typography.titleMedium)
            TextButton(onClick = { photosLauncher.launch(imagesOnly) }, modifier = Modifier.aid("perm.openPhotoPicker")) {
                Text("Open Photo Picker")
            }
            TextButton(onClick = { photoLauncher.launch(imagesOnly) }, modifier = Modifier.aid("perm.openPhotoPickerSingle")) {
                Text("Open Photo Picker (Single)")
            }
            Text("Selected: ${model.selectedPhotoCount}", Modifier.aid("perm.photos.value").stateValue(model.selectedPhotoCount.toString()))

            // The banner fixture's controls come last (BE-0496), so they add nothing above the Photos
            // section: the picker's return can scroll this column back to its top, where the photo
            // count has to stay in view for the select_photos scenarios that wait on it.
            Text("Banner", style = MaterialTheme.typography.titleMedium)
            // The counter above the button: the scroll to the button stops once it shows, so a row
            // below it could still sit under the fold when the scenario reads the count.
            Text("Banner target taps: $bannerTaps", Modifier.aid("perm.bannerTaps").stateValue(bannerTaps.toString()))
            // Posts on a high-importance channel, which SystemUI presents as a heads-up notification
            // over the top of this screen (BE-0496).
            TextButton(onClick = { postHeadsUp(context) }, modifier = Modifier.aid("perm.postNotif")) {
                Text("Post Notification")
            }
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

private const val HEADS_UP_CHANNEL = "showcase_heads_up"
private const val HEADS_UP_ID_BASE = 4960
private var postedCount = 0

private fun postHeadsUp(context: Context) {
    val manager = context.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
    manager.createNotificationChannel(
        NotificationChannel(HEADS_UP_CHANNEL, "Heads-up", NotificationManager.IMPORTANCE_HIGH),
    )
    val notification = NotificationCompat.Builder(context, HEADS_UP_CHANNEL)
        .setSmallIcon(android.R.drawable.ic_dialog_info)
        .setContentTitle("Showcase notification")
        .setContentText("A heads-up banner over the app")
        .setPriority(NotificationCompat.PRIORITY_HIGH)
        .build()
    // An empty shade and a fresh id per tap: SystemUI does not alert again for an update to a
    // notification already in the shade, so reposting one id shows the banner only once.
    manager.cancelAll()
    manager.notify(HEADS_UP_ID_BASE + postedCount++, notification)
}

private fun clipboard(context: Context): ClipboardManager =
    context.getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
