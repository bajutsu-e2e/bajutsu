package dev.bajutsu.android

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.location.Criteria
import android.location.Location
import android.location.LocationManager
import android.location.provider.ProviderProperties
import android.os.Build
import android.os.SystemClock

/**
 * Handles bajutsu's ordered location broadcast from the app process, so `setLocation` works on a
 * real device, which has no emulator console for `adb emu geo fix` (BE-0481).
 *
 * The `op` extra selects set / clear. `set` reads `lat` / `lon` as strings (no quoting on the sending
 * `adb shell` argv) and mocks the `gps` and `network` providers through
 * [LocationManager.addTestProvider]. That call needs the mock-location app-op, which bajutsu grants
 * over adb (`appops set <package> android:mock_location allow`) before it broadcasts; without the
 * grant the platform raises `SecurityException`, reported as [Bajutsu.RESULT_NO_MOCK_PERMISSION] so
 * bajutsu can name the missing grant instead of "no receiver".
 */
class LocationReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        val manager = context.getSystemService(Context.LOCATION_SERVICE) as LocationManager
        try {
            when (intent.getStringExtra("op")) {
                "set" -> {
                    // bajutsu always sends numbers; a malformed value from another local app is left
                    // unhandled rather than mocked to a guessed position.
                    val lat = intent.getStringExtra("lat")?.toDoubleOrNull() ?: return
                    val lon = intent.getStringExtra("lon")?.toDoubleOrNull() ?: return
                    for (provider in PROVIDERS) mock(manager, provider, lat, lon)
                }
                "clear" -> for (provider in PROVIDERS) remove(manager, provider)
                else -> return // unknown/absent op: leave the result unset so bajutsu sees "not handled"
            }
        } catch (_: SecurityException) {
            resultCode = Bajutsu.RESULT_NO_MOCK_PERMISSION
            return
        } catch (e: IllegalArgumentException) {
            // A provider call the platform rejects must not crash the app under test; bajutsu reports
            // the message instead. Undo a partial mock, so no provider is left half-set.
            for (provider in PROVIDERS) runCatching { remove(manager, provider) }
            resultCode = Bajutsu.RESULT_MOCK_FAILED
            resultData = e.message.orEmpty()
            return
        }
        resultCode = Bajutsu.RESULT_OK
    }

    private fun mock(manager: LocationManager, provider: String, lat: Double, lon: Double) {
        // A provider left by an earlier `set` is replaced, so each `set` starts from known properties.
        remove(manager, provider)
        addTestProvider(manager, provider)
        manager.setTestProviderEnabled(provider, true)
        // The platform rejects a Location without accuracy, time, and elapsed-realtime with
        // IllegalArgumentException, so all three are set.
        val location = Location(provider).apply {
            latitude = lat
            longitude = lon
            accuracy = 1f
            time = System.currentTimeMillis()
            elapsedRealtimeNanos = SystemClock.elapsedRealtimeNanos()
        }
        manager.setTestProviderLocation(provider, location)
    }

    @Suppress("DEPRECATION") // the pre-API-31 overload is the only one the library's minSdk 26 has
    private fun addTestProvider(manager: LocationManager, provider: String) {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
            val properties = ProviderProperties.Builder()
                .setHasAltitudeSupport(true)
                .setHasSpeedSupport(true)
                .setHasBearingSupport(true)
                .setPowerUsage(ProviderProperties.POWER_USAGE_LOW)
                .setAccuracy(ProviderProperties.ACCURACY_FINE)
                .build()
            manager.addTestProvider(provider, properties)
        } else {
            manager.addTestProvider(
                provider,
                false, // requiresNetwork
                false, // requiresSatellite
                false, // requiresCell
                false, // hasMonetaryCost
                true, // supportsAltitude
                true, // supportsSpeed
                true, // supportsBearing
                Criteria.POWER_LOW,
                Criteria.ACCURACY_FINE,
            )
        }
    }

    private fun remove(manager: LocationManager, provider: String) {
        // An absent test provider raises IllegalArgumentException; absent already counts as removed.
        try {
            manager.removeTestProvider(provider)
        } catch (_: IllegalArgumentException) {
        }
    }

    private companion object {
        // `fused` stays unmocked: mocking it needs Play services, which the library does not depend on.
        val PROVIDERS = listOf(LocationManager.GPS_PROVIDER, LocationManager.NETWORK_PROVIDER)
    }
}
