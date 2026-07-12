package dev.bajutsu.android

import android.content.BroadcastReceiver
import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.content.Intent
import android.util.Base64

/**
 * Handles bajutsu's ordered clipboard broadcast from the app process, where clipboard access is
 * allowed (Android 10+ restricts it to the foreground app / default IME).
 *
 * The `op` extra selects set / get / clear. Text travels base64-encoded both ways (a shell-safe
 * alphabet, so the sending `adb shell` argv needs no quoting), decoded here and re-encoded into the
 * broadcast result for `get`. On success it sets [Bajutsu.RESULT_OK]; leaving the result untouched
 * for an unknown op lets bajutsu detect "no receiver / not handled" and fail loudly rather than read
 * an empty clipboard as success.
 */
class ClipboardReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        val clipboard = context.getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
        when (intent.getStringExtra("op")) {
            "set" -> {
                val text = intent.getStringExtra("b64")?.let(::decode).orEmpty()
                clipboard.setPrimaryClip(ClipData.newPlainText("bajutsu", text))
                succeed(null)
            }
            "get" -> {
                val text = clipboard.primaryClip
                    ?.takeIf { it.itemCount > 0 }
                    ?.getItemAt(0)
                    ?.coerceToText(context)
                    ?.toString()
                    .orEmpty()
                succeed(encode(text))
            }
            "clear" -> {
                // clearPrimaryClip() is API 28+; an empty clip is the minSdk-26-safe equivalent.
                clipboard.setPrimaryClip(ClipData.newPlainText("", ""))
                succeed(null)
            }
            else -> return // unknown/absent op: leave the result unset so bajutsu sees "not handled"
        }
    }

    private fun succeed(data: String?) {
        resultCode = Bajutsu.RESULT_OK
        if (data != null) resultData = data
    }

    private fun encode(text: String): String =
        Base64.encodeToString(text.toByteArray(Charsets.UTF_8), Base64.NO_WRAP)

    private fun decode(b64: String): String =
        String(Base64.decode(b64, Base64.NO_WRAP), Charsets.UTF_8)
}
