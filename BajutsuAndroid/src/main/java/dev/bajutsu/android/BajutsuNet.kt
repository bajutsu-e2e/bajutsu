package dev.bajutsu.android

import android.util.Log
import okhttp3.Call
import okhttp3.Callback
import okhttp3.Headers
import okhttp3.Interceptor
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import okhttp3.Response
import okio.Buffer
import org.json.JSONObject
import java.io.IOException

/**
 * In-app network observation for bajutsu on Android — the OkHttp peer of BajutsuKit's `URLProtocol`.
 *
 * When bajutsu runs a scenario it starts a collector on the host's `127.0.0.1:<port>`, bridges it to
 * the emulator with `adb reverse` (BE-0283), and injects its URL into the app as the
 * `BAJUTSU_COLLECTOR` intent extra. Unlike iOS's `URLProtocol` — which swizzles into every
 * `URLSession` transparently — Android has no single OS-level HTTP hook that reaches every client, so
 * the app under test adds one line, `.addInterceptor(BajutsuNet.interceptor())`, to its
 * `OkHttpClient.Builder`. The interceptor reports each completed exchange to the collector, where a
 * step's `request` assertion checks it. The JSON it POSTs mirrors bajutsu's `NetworkExchange`
 * (`bajutsu/network.py`) field-for-field, so the collector and the assertion pipeline need no change.
 *
 * **Test/debug only.** It captures headers and bodies. Reporting is inert until [configure] finds
 * `BAJUTSU_COLLECTOR`, so the interceptor is harmless to leave installed, but gate the [configure]
 * call on a test/debug flag the same way the clipboard receiver is, so a release build never reports.
 */
object BajutsuNet {
    @Volatile private var collectorUrl: String? = null

    /** Per-run shared token (`BAJUTSU_COLLECTOR_TOKEN`) the collector requires on each report POST. */
    @Volatile private var collectorToken: String? = null

    private const val TAG = "BajutsuNet"

    // Bound the response body copy so a large download is never buffered whole just to report it.
    private const val BODY_PEEK_LIMIT = 64L * 1024

    // A dedicated client for the report POST, so the report is never itself intercepted (mirrors
    // iOS's separate reportSession). Built lazily the first time an exchange is reported.
    private val reportClient by lazy { OkHttpClient() }

    /**
     * Enable reporting from the launch env, mirroring iOS's `startIfEnabled`.
     *
     * A no-op unless `BAJUTSU_COLLECTOR` is present, so calling it unconditionally is safe; on Android
     * the launch env arrives as intent extras, read once at launch (the same map `MainActivity`
     * already builds). Call it before the first network request so the interceptor observes from the
     * start.
     */
    fun configure(env: Map<String, String>) {
        val url = env["BAJUTSU_COLLECTOR"] ?: return
        collectorUrl = url
        collectorToken = env["BAJUTSU_COLLECTOR_TOKEN"]
    }

    /** The interceptor the app adds to its `OkHttpClient.Builder`; inert until [configure] runs. */
    fun interceptor(): Interceptor = Interceptor { chain ->
        val request = chain.request()
        val url = collectorUrl ?: return@Interceptor chain.proceed(request) // not observing
        val startedAt = System.currentTimeMillis()
        val response =
            try {
                chain.proceed(request)
            } catch (e: IOException) {
                // The exchange failed at the transport layer (timeout, connection refused, DNS). iOS
                // reports these too (via didCompleteWithError, status absent), so a `request` assertion
                // and network.json see the attempt rather than nothing. Report, then rethrow so the
                // app's own call fails exactly as it would have without the interceptor.
                runCatching { reportFailure(url, request, System.currentTimeMillis() - startedAt) }
                    .onFailure { Log.w(TAG, "failed to report failed exchange to the collector", it) }
                throw e
            }
        // report() reads the response body (peekBody) and builds JSON — real I/O that can throw (a
        // truncated body, a JSONException). An interceptor that throws fails the app's own call even
        // though the network response was fine, so a report failure must never escape this lambda.
        runCatching { report(url, request, response, System.currentTimeMillis() - startedAt) }
            .onFailure { Log.w(TAG, "failed to report exchange to the collector", it) }
        response
    }

    private fun report(url: String, request: Request, response: Response, durationMs: Long) {
        val payload =
            basePayload(request, durationMs)
                .put("status", response.code)
                .put("responseHeaders", headersJson(response.headers))
        // peekBody clones up to the limit without consuming the stream the caller still reads.
        response.peekBody(BODY_PEEK_LIMIT).string().takeIf { it.isNotEmpty() }
            ?.let { payload.put("responseBody", it) }
        post(url, payload)
    }

    private fun reportFailure(url: String, request: Request, durationMs: Long) {
        // No response reached the interceptor, so status/responseHeaders/responseBody are absent —
        // NetworkExchange accepts a null status, matching iOS's failed-exchange report.
        post(url, basePayload(request, durationMs))
    }

    // The request-side payload common to a completed and a failed exchange. Field names / shape mirror
    // NetworkExchange (bajutsu/evidence/network.py); startedAt is omitted, as on iOS — the collector
    // timestamps arrival itself.
    private fun basePayload(request: Request, durationMs: Long): JSONObject {
        val payload =
            JSONObject()
                .put("method", request.method)
                .put("url", request.url.toString())
                .put("path", request.url.encodedPath) // path only (no query), for matching
                .put("durationMs", durationMs.toDouble())
                .put("requestHeaders", headersJson(request.headers))
        requestBody(request)?.let { payload.put("requestBody", it) }
        return payload
    }

    private fun post(url: String, payload: JSONObject) {
        val post = Request.Builder()
            .url(url)
            .post(payload.toString().toRequestBody(JSON))
            .apply { collectorToken?.let { header("Authorization", "Bearer $it") } }
            .build()
        // Fire-and-forget: a report must never block or fail the app's own request, but a transport
        // failure here (collector unreachable, tunnel torn down mid-request) must still leave a trace —
        // otherwise a `request` assertion just fails with "exchange not observed" and nothing points at
        // the real cause.
        reportClient.newCall(post).enqueue(object : Callback {
            override fun onFailure(call: Call, e: IOException) {
                Log.w(TAG, "failed to POST exchange to the collector", e)
            }
            override fun onResponse(call: Call, response: Response) = response.close()
        })
    }

    private fun headersJson(headers: Headers): JSONObject {
        val obj = JSONObject()
        for (i in 0 until headers.size) obj.put(headers.name(i), headers.value(i))
        return obj
    }

    private fun requestBody(request: Request): String? {
        val body = request.body ?: return null
        // A one-shot or duplex body can be written only once (by the network call itself), so copying
        // it here would corrupt the request; skip those rather than break the app under test.
        if (body.isOneShot() || body.isDuplex()) return null
        // Bound like the response body (BODY_PEEK_LIMIT): an unknown or large body is skipped rather
        // than fully materialized into memory just to report it.
        val length = body.contentLength()
        if (length < 0 || length > BODY_PEEK_LIMIT) return null
        return runCatching {
            Buffer().also { body.writeTo(it) }.readUtf8().takeIf { it.isNotEmpty() }
        }.getOrNull()
    }

    private val JSON = "application/json".toMediaType()
}
