package com.bajutsu.showcase.views

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
import android.view.View
import android.widget.LinearLayout
import androidx.activity.result.PickVisualMediaRequest
import androidx.activity.result.contract.ActivityResultContracts
import androidx.activity.result.contract.ActivityResultContracts.PickVisualMedia
import java.util.Locale

// Tab: Permissions (SPEC §5.4 / §7) — the OS-integration screen. It owns the two deliberate
// runtime-permission prompts (notifications + location), the Android analog of iOS's SpringBoard
// alerts and the canonical alert-guard fixture, plus a System section: an in-app clipboard
// round-trip the driver can drive and assert. Nothing here runs at launch; prompts fire only on taps.
class PermissionsTab(private val activity: MainActivity) {
    val root: View

    init {
        val notifValue = activity.secondaryLabel("Notifications: notDetermined")
            .aid("perm_notif_value").stateValue("notDetermined")
        // A positive condition the run can wait for once granted; hidden until then.
        val notifGranted = activity.label("Granted").aid("perm_notif_authorized").apply { visibility = View.GONE }
        val notifLauncher = activity.registerForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
            val state = if (granted) "authorized" else "denied"
            notifValue.text = "Notifications: $state"
            notifValue.stateValue(state)
            notifGranted.visibility = if (granted) View.VISIBLE else View.GONE
        }
        val requestNotif = activity.textButton("Request Notifications") {
            notifLauncher.launch(Manifest.permission.POST_NOTIFICATIONS)
        }.aid("perm_requestNotif")

        val locationValue = activity.secondaryLabel("Location: notDetermined")
            .aid("perm_location_value").stateValue("notDetermined")
        val locationLauncher = activity.registerForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
            val state = if (granted) "authorizedWhenInUse" else "denied"
            locationValue.text = "Location: $state"
            locationValue.stateValue(state)
        }
        val requestLocation = activity.textButton("Request Location") {
            locationLauncher.launch(Manifest.permission.ACCESS_FINE_LOCATION)
        }.aid("perm_requestLocation")
        // Reads one fresh GPS fix (SPEC §5.4) — the position a `setLocation` step moved,
        // whichever route bajutsu took to move it (BE-0481).
        val locationReading = activity.secondaryLabel("Position: ").aid("perm_location_reading").stateValue("")
        val readLocation = activity.textButton("Read Location") {
            val show: (String) -> Unit = { reading ->
                locationReading.text = "Position: $reading"
                locationReading.stateValue(reading)
            }
            show("reading")
            readLocation(show)
        }.aid("perm_location_read")

        // Clipboard round-trip (SPEC §5.4): Copy writes a known string, Paste reads it back into
        // sys.paste.value — clipboard state the driver's tree query cannot otherwise observe.
        val pastedValue = activity.secondaryLabel("Pasted: ").aid("sys_paste_value").stateValue("")
        val copy = activity.textButton("Copy") {
            clipboard().setPrimaryClip(ClipData.newPlainText("bajutsu", "bajutsu-clip"))
        }.aid("sys_copy")
        val paste = activity.textButton("Paste") {
            val text = clipboard().primaryClip?.getItemAt(0)?.text?.toString() ?: ""
            pastedValue.text = "Pasted: $text"
            pastedValue.stateValue(text)
        }.aid("sys_paste")

        // `selectPhotos` (SPEC §5.4): the system photo picker in its two modes, mirroring the picked
        // count back to perm.photos.value. Multi-selection confirms with the picker's add button;
        // single-selection returns on its one tap.
        val photosValue = activity.secondaryLabel("Selected: 0").aid("perm_photos_value").stateValue("0")
        fun showPhotoCount(count: Int) {
            photosValue.text = "Selected: $count"
            photosValue.stateValue(count.toString())
        }
        val photosLauncher = activity.registerForActivityResult(ActivityResultContracts.PickMultipleVisualMedia()) { uris ->
            showPhotoCount(uris.size)
        }
        val photoLauncher = activity.registerForActivityResult(PickVisualMedia()) { uri ->
            showPhotoCount(if (uri != null) 1 else 0)
        }
        val imagesOnly = PickVisualMediaRequest(PickVisualMedia.ImageOnly)
        val openPhotos = activity.textButton("Open Photo Picker") { photosLauncher.launch(imagesOnly) }
            .aid("perm_openPhotoPicker")
        val openPhoto = activity.textButton("Open Photo Picker (Single)") { photoLauncher.launch(imagesOnly) }
            .aid("perm_openPhotoPickerSingle")

        val form = activity.vstack(
            activity.label("Notifications"),
            requestNotif,
            notifValue,
            notifGranted,
            activity.label("Location"),
            requestLocation,
            locationValue,
            readLocation,
            locationReading,
            activity.label("System"),
            copy,
            paste,
            pastedValue,
            activity.label("Photos"),
            openPhotos,
            openPhoto,
            photosValue,
        )
        root = activity.vstack(activity.header("Permissions"), activity.scrollPage(form)).apply {
            (getChildAt(1).layoutParams as LinearLayout.LayoutParams).apply { height = 0; weight = 1f }
        }
    }

    /**
     * Report the next `gps` fix delivered after the tap as `lat,lon` (or `(none)` / `denied`).
     *
     * Only a fix delivered to this request counts: the platform's "current location" can be a cached
     * fix up to tens of seconds old, which after a second console `geo fix` still shows the first one.
     * The emulator's GPS re-emits its `geo fix` about once a second while someone listens, so the console
     * route answers within a second. A test provider emits only when mocked, so the in-app route delivers
     * nothing new; after [FRESH_FIX_TIMEOUT_MS] the last known fix, which is the mock, is reported.
     */
    private fun readLocation(onReading: (String) -> Unit) {
        val manager = activity.getSystemService(Context.LOCATION_SERVICE) as LocationManager
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

    private fun clipboard(): ClipboardManager =
        activity.getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
}

/** How long a read waits for a fix delivered to it before it reports the last known one. */
private const val FRESH_FIX_TIMEOUT_MS = 5_000L
