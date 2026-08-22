package dev.bajutsu.android

import android.os.Bundle
import android.view.View
import android.view.accessibility.AccessibilityNodeInfo

/**
 * The app-side half of BE-0355's `nativeZ` on Android: report a view's own front-to-back position.
 *
 * Android already has a first-class mechanism for data an accessibility client did not ask for by
 * default, so this needs no channel of its own. A view declares the key it can answer through
 * [AccessibilityNodeInfo.setAvailableExtraData] and fills it on request; bajutsu's resident server,
 * which already talks to the device as an accessibility client, asks only the views that advertise
 * it. An app that never opts a view in pays one list check per node and reports nothing, which is
 * the honest absence `nativeZ` reads as `null`.
 *
 * The value is [View.getZ] — elevation plus any translation on the z axis — in device pixels. That
 * is the value Android's own draw order sorts siblings by, so unlike iOS's `CALayer.zPosition` it is
 * not degenerate on an ordinary layout. It orders siblings within one parent and nothing wider: a
 * child at `0` under a parent at `8` still composites in front of that parent's sibling at `4`.
 */
object BajutsuZOrder {
    /** The extra-data key bajutsu asks for. Must match `bajutsu/drivers/adb.py`. */
    const val EXTRA_DATA_KEY: String = "dev.bajutsu.EXTRA_DATA_NATIVE_Z"

    /**
     * Report *view*'s front-to-back position to bajutsu.
     *
     * Call it in a **test/debug build only**, wherever the app already tags a view for testing. It
     * installs an accessibility delegate, so it needs no subclass and composes with an app's own
     * identifiers. Any accessibility client on the device can read the reported position, which is
     * why it must not ship in a release build. Idempotent per view, but it replaces any delegate
     * already installed on that view — pass one to keep through *delegate*.
     */
    @JvmStatic
    @JvmOverloads
    fun report(view: View, delegate: View.AccessibilityDelegate? = null) {
        view.accessibilityDelegate = ZDelegate(delegate)
    }

    private class ZDelegate(private val wrapped: View.AccessibilityDelegate?) :
        View.AccessibilityDelegate() {

        override fun onInitializeAccessibilityNodeInfo(host: View, info: AccessibilityNodeInfo) {
            wrapped?.onInitializeAccessibilityNodeInfo(host, info)
                ?: super.onInitializeAccessibilityNodeInfo(host, info)
            // Additive: keep whatever the platform or the wrapped delegate already advertised, so
            // opting a text view in never costs it the platform's own character-location key.
            val existing = info.availableExtraData.orEmpty()
            if (EXTRA_DATA_KEY !in existing) {
                info.availableExtraData = existing + EXTRA_DATA_KEY
            }
            info.extras.putFloat(EXTRA_DATA_KEY, host.z)
        }

        // BE-0355 Unit 0's spike found the platform never routes this on-demand callback through
        // an accessibility delegate — only `onInitializeAccessibilityNodeInfo` above is called, so
        // the eager `putFloat` there is the only path that actually delivers `EXTRA_DATA_KEY`.
        // Kept, and still forwarding to `wrapped`, for a `View` subclass that overrides this
        // callback directly (not through a delegate) — the platform's own on-demand mechanism the
        // roadmap item names, which this override composes with rather than replaces.
        override fun addExtraDataToAccessibilityNodeInfo(
            host: View,
            info: AccessibilityNodeInfo,
            extraDataKey: String,
            arguments: Bundle?,
        ) {
            if (extraDataKey == EXTRA_DATA_KEY) {
                info.extras.putFloat(EXTRA_DATA_KEY, host.z)
                return
            }
            wrapped?.addExtraDataToAccessibilityNodeInfo(host, info, extraDataKey, arguments)
                ?: super.addExtraDataToAccessibilityNodeInfo(host, info, extraDataKey, arguments)
        }
    }
}
