package com.bajutsu.showcase.views

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL

/**
 * The showcase's network calls (SPEC §6), shared verbatim with the compose module. Plain
 * HttpURLConnection — Android has no BajutsuKit yet, and BE-0007's backend has no native network
 * monitor (it reuses iOS's mock story). Each call mirrors its outcome to a `*.status` value
 * (`loading` → `done`/`error`) so a scenario can wait on the response before asserting. Any HTTP
 * response counts as `done`; only a transport failure is `error`.
 */
object Net {
    /** GET a URL, returning `done` on any response, `error` on a transport failure. */
    suspend fun get(urlString: String): String = withContext(Dispatchers.IO) {
        try {
            val conn = (URL(urlString).openConnection() as HttpURLConnection).apply {
                requestMethod = "GET"
                setRequestProperty("Authorization", "Bearer demo-secret-abc123")
            }
            conn.responseCode
            conn.disconnect()
            "done"
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
                val conn = (URL("$base/post").openConnection() as HttpURLConnection).apply {
                    requestMethod = "POST"
                    doOutput = true
                    setRequestProperty("Content-Type", "application/json")
                    setRequestProperty("Authorization", "Bearer demo-secret-abc123")
                }
                val payload = JSONObject()
                    .put("note", note)
                    .put("count", count)
                    .put("intense", intense)
                    .put("password", "hunter2")
                conn.outputStream.use { it.write(payload.toString().toByteArray()) }
                conn.responseCode
                conn.disconnect()
                "done"
            } catch (e: Exception) {
                "error"
            }
        }
}
