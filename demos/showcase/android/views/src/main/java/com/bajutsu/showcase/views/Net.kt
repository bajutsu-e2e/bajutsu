package com.bajutsu.showcase.views

import dev.bajutsu.android.BajutsuNet
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONObject
import java.util.concurrent.TimeUnit

/**
 * The showcase's network calls (SPEC §6), shared verbatim with the compose module — over OkHttp so
 * BajutsuAndroid's interceptor can observe them (BE-0283 — the Android peer of BajutsuKit's
 * `URLProtocol` on iOS). Each call mirrors its outcome to a `*.status` value (`loading` →
 * `done`/`error`) so a scenario can wait on the response before asserting. Any HTTP response counts as
 * `done`; only a transport failure is `error`.
 */
object Net {
    private val JSON = "application/json".toMediaType()

    private val client = OkHttpClient.Builder()
        // Finite timeouts: a hung endpoint must resolve to `error`, not park the status on `loading`
        // forever (the iOS twin's URLSession times out the same way).
        .connectTimeout(15, TimeUnit.SECONDS)
        .readTimeout(15, TimeUnit.SECONDS)
        // The one line that gives bajutsu network capture on Android; inert unless bajutsu injected a
        // collector (BE-0283).
        .addInterceptor(BajutsuNet.interceptor())
        .build()

    /** GET a URL, returning `done` on any response, `error` on a transport failure or timeout. */
    suspend fun get(urlString: String): String = withContext(Dispatchers.IO) {
        try {
            val req = Request.Builder()
                .url(urlString)
                .header("Authorization", "Bearer demo-secret-abc123")
                .build()
            client.newCall(req).execute().use { "done" }
        } catch (e: Exception) {
            "error"
        }
    }

    /**
     * POST the training-log entry to `<base>/post` as JSON. Carries a secret header
     * (`Authorization: Bearer …`) and a `password` body field so redaction has something to mask
     * (SPEC §6 / DESIGN §9).
     */
    suspend fun postLog(base: String, note: String, count: Int, intense: Boolean): String =
        withContext(Dispatchers.IO) {
            try {
                val payload = JSONObject()
                    .put("note", note)
                    .put("count", count)
                    .put("intense", intense)
                    .put("password", "hunter2")
                val req = Request.Builder()
                    .url("$base/post")
                    .header("Authorization", "Bearer demo-secret-abc123")
                    .post(payload.toString().toRequestBody(JSON))
                    .build()
                client.newCall(req).execute().use { "done" }
            } catch (e: Exception) {
                "error"
            }
        }
}
