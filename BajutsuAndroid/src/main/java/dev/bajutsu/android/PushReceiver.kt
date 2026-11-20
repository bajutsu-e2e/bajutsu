package dev.bajutsu.android

import android.annotation.SuppressLint
import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.util.Base64
import androidx.core.app.NotificationChannelCompat
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import org.json.JSONObject
import java.util.concurrent.atomic.AtomicInteger

/**
 * A push notification bajutsu delivered to the app (BE-0498), mapped on the host from the
 * scenario's APNs-shaped payload.
 *
 * [title] and [body] come from `aps.alert`; both are null for a payload with no alert, such as a
 * silent push. [data] holds every top-level payload key other than `aps`, as a string-to-string map
 * like an FCM message's data map: a string value as-is, any other value as its JSON text.
 */
data class BajutsuPush(val title: String?, val body: String?, val data: Map<String, String>)

/**
 * The app's own handling of a pushed notification, registered with [Bajutsu.startPush].
 *
 * Called on the main thread from the receiver's `onReceive`, so it must return promptly, like any
 * broadcast receiver. An app that uses FCM can build a `RemoteMessage` from the [BajutsuPush] here and
 * call its own `onMessageReceived`, which keeps Firebase out of this library. A thrown exception
 * fails the `push` step with the exception's message, which bajutsu writes into the run's report
 * verbatim, so the message must not include the payload: it may carry substituted secrets.
 */
fun interface BajutsuPushHandler {
    fun onPush(push: BajutsuPush)
}

/**
 * Handles bajutsu's ordered push broadcast from the app process (BE-0498).
 *
 * The `title`, `body`, and `data` extras travel base64-encoded (a shell-safe alphabet, so the sending
 * `adb shell` argv needs no quoting); `data` decodes to a JSON object. With a [handler] the receiver
 * calls it and does nothing else; without one it posts a local notification, whose tap opens the
 * app's launch intent with each `data` entry as a string extra. The result code tells bajutsu what
 * happened; an untouched code (`0`) reads as "no receiver".
 */
class PushReceiver(private val handler: BajutsuPushHandler?) : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        // The receiver is exported, so any local app can send garbage; a decode failure must not
        // crash the app under test. bajutsu always sends valid extras, so leaving the result unset
        // here only affects malformed third-party input.
        val push = runCatching { decode(intent) }.getOrNull() ?: return
        if (handler != null) {
            try {
                handler.onPush(push)
            } catch (e: Throwable) {
                // Throwable, not Exception: a test handler's `TODO()` or `assert` throws an Error,
                // which must come back as the handler's failure, not crash the app as "no receiver".
                resultCode = Bajutsu.RESULT_PUSH_FAILED
                resultData = encode(e.message ?: e.javaClass.name)
                return
            }
            resultCode = Bajutsu.RESULT_OK
            return
        }
        resultCode = notify(context, push)
    }

    private fun notify(context: Context, push: BajutsuPush): Int {
        // Nothing to show: a silent push succeeds without a notification, so it needs no permission.
        if (push.title == null && push.body == null) return Bajutsu.RESULT_OK
        val manager = NotificationManagerCompat.from(context)
        // Covers the POST_NOTIFICATIONS runtime permission on API 33+ and a user who turned the app's
        // notifications off on an older level.
        if (!manager.areNotificationsEnabled()) return Bajutsu.RESULT_NO_NOTIFICATION_PERMISSION
        manager.createNotificationChannel(
            NotificationChannelCompat.Builder(CHANNEL_ID, NotificationManagerCompat.IMPORTANCE_DEFAULT)
                .setName(CHANNEL_ID)
                .build(),
        )
        val id = nextId.incrementAndGet()
        val builder = NotificationCompat.Builder(context, CHANNEL_ID)
            .setSmallIcon(smallIcon(context))
            .setAutoCancel(true)
        push.title?.let(builder::setContentTitle)
        push.body?.let(builder::setContentText)
        contentIntent(context, push, id)?.let(builder::setContentIntent)
        return try {
            post(manager, id, builder)
            Bajutsu.RESULT_OK
        } catch (_: SecurityException) {
            // The permission was revoked between the check and the post.
            Bajutsu.RESULT_NO_NOTIFICATION_PERMISSION
        }
    }

    @SuppressLint("MissingPermission") // checked by areNotificationsEnabled(); a race is caught above
    private fun post(manager: NotificationManagerCompat, id: Int, builder: NotificationCompat.Builder) {
        manager.notify(id, builder.build())
    }

    private fun contentIntent(context: Context, push: BajutsuPush, id: Int): PendingIntent? {
        val launch = context.packageManager.getLaunchIntentForPackage(context.packageName) ?: return null
        for ((key, value) in push.data) launch.putExtra(key, value)
        // One request code per notification, so two posted pushes keep their own extras.
        return PendingIntent.getActivity(
            context,
            id,
            launch,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )
    }

    private fun smallIcon(context: Context): Int =
        context.applicationInfo.icon.takeIf { it != 0 } ?: android.R.drawable.ic_dialog_info

    private fun decode(intent: Intent): BajutsuPush {
        val title = intent.getStringExtra("title")?.let(::decodeText)
        val body = intent.getStringExtra("body")?.let(::decodeText)
        val data = mutableMapOf<String, String>()
        intent.getStringExtra("data")?.let { b64 ->
            val json = JSONObject(decodeText(b64))
            for (key in json.keys()) data[key] = json.getString(key)
        }
        return BajutsuPush(title, body, data)
    }

    private fun encode(text: String): String =
        Base64.encodeToString(text.toByteArray(Charsets.UTF_8), Base64.NO_WRAP)

    private fun decodeText(b64: String): String =
        String(Base64.decode(b64, Base64.NO_WRAP), Charsets.UTF_8)

    private companion object {
        const val CHANNEL_ID = "bajutsu"
        val nextId = AtomicInteger()
    }
}
