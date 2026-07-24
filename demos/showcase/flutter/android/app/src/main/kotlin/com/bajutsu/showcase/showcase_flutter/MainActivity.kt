package com.bajutsu.showcase.showcase_flutter

import android.content.pm.PackageManager
import io.flutter.embedding.android.FlutterActivity
import io.flutter.embedding.engine.FlutterEngine
import io.flutter.plugin.common.MethodChannel

/**
 * The showcase's native seam (BE-0008). Two Flutter method channels hide the platform difference the
 * Dart app must not carry: `showcase/launch` hands back the `SHOWCASE_*` launch env, which on
 * Android arrives as intent extras (BE-0007, not the process environment), and `showcase/native`
 * raises the two deliberate runtime-permission dialogs (SPEC §5.4 / §7) — the out-of-process
 * alert-guard fixture — returning the resolved status the Permissions screen mirrors to
 * `perm.*.value`.
 */
class MainActivity : FlutterActivity() {
    private var pendingPermission: MethodChannel.Result? = null
    private var pendingGrantedStatus: String = "denied"

    override fun configureFlutterEngine(flutterEngine: FlutterEngine) {
        super.configureFlutterEngine(flutterEngine)
        val messenger = flutterEngine.dartExecutor.binaryMessenger

        MethodChannel(messenger, "showcase/launch").setMethodCallHandler { call, result ->
            if (call.method == "launchEnv") result.success(launchEnv()) else result.notImplemented()
        }

        MethodChannel(messenger, "showcase/native").setMethodCallHandler { call, result ->
            when (call.method) {
                "requestNotif" -> requestPermission("android.permission.POST_NOTIFICATIONS", "authorized", result)
                "requestLocation" -> requestPermission("android.permission.ACCESS_FINE_LOCATION", "authorizedWhenInUse", result)
                else -> result.notImplemented()
            }
        }
    }

    /** launchEnv (SPEC §3) arrives as string intent extras; read once, defaults live in the model. */
    private fun launchEnv(): Map<String, String> {
        val extras = intent?.extras ?: return emptyMap()
        val keys = listOf(
            "SHOWCASE_UITEST", "SHOWCASE_TAB", "SHOWCASE_API_URL", "SHOWCASE_HTTP_BASE",
            "SHOWCASE_GESTURES", "SHOWCASE_CONFORMANCE", "BAJUTSU_COLLECTOR", "BAJUTSU_COLLECTOR_TOKEN",
        )
        return keys.mapNotNull { key -> extras.getString(key)?.let { key to it } }.toMap()
    }

    private fun requestPermission(permission: String, grantedStatus: String, result: MethodChannel.Result) {
        if (checkSelfPermission(permission) == PackageManager.PERMISSION_GRANTED) {
            result.success(grantedStatus)
            return
        }
        // One request at a time: a second overlapping request would overwrite the pending result and
        // strand the first Dart `await` forever. Fail the new call loudly instead of dropping a reply
        // (the showcase taps the two prompts in sequence, so this never fires in the normal flow).
        if (pendingPermission != null) {
            result.error("busy", "a permission request is already in progress", null)
            return
        }
        pendingPermission = result
        pendingGrantedStatus = grantedStatus
        requestPermissions(arrayOf(permission), REQUEST_CODE)
    }

    override fun onRequestPermissionsResult(requestCode: Int, permissions: Array<out String>, grantResults: IntArray) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        if (requestCode != REQUEST_CODE) return
        val granted = grantResults.isNotEmpty() && grantResults[0] == PackageManager.PERMISSION_GRANTED
        pendingPermission?.success(if (granted) pendingGrantedStatus else "denied")
        pendingPermission = null
    }

    private companion object {
        const val REQUEST_CODE = 4200
    }
}
