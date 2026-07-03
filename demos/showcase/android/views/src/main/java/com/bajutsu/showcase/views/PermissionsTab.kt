package com.bajutsu.showcase.views

import android.Manifest
import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.view.View
import android.widget.LinearLayout
import androidx.activity.result.contract.ActivityResultContracts

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

        val form = activity.vstack(
            activity.label("Notifications"),
            requestNotif,
            notifValue,
            notifGranted,
            activity.label("Location"),
            requestLocation,
            locationValue,
            activity.label("System"),
            copy,
            paste,
            pastedValue,
        )
        root = activity.vstack(activity.header("Permissions"), activity.scrollPage(form)).apply {
            (getChildAt(1).layoutParams as LinearLayout.LayoutParams).apply { height = 0; weight = 1f }
        }
    }

    private fun clipboard(): ClipboardManager =
        activity.getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
}
