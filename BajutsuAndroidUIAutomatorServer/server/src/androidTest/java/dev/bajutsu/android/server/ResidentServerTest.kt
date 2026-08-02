package dev.bajutsu.android.server

import android.os.SystemClock
import android.util.Log
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import androidx.test.uiautomator.UiDevice
import java.io.BufferedReader
import java.io.ByteArrayOutputStream
import java.io.OutputStream
import java.net.InetAddress
import java.net.ServerSocket
import java.net.Socket
import java.nio.charset.StandardCharsets
import java.util.concurrent.TimeUnit
import java.util.concurrent.locks.ReentrantLock
import kotlin.concurrent.withLock
import org.junit.Test
import org.junit.runner.RunWith

/**
 * The resident UI Automator server (BE-0245).
 *
 * `am instrument -w` runs this @Test, which never returns: it opens a socket and serves hierarchy
 * reads until the instrumentation is killed. That is the whole point — the `UiAutomation` session
 * behind [UiDevice] is created once and stays live, so each `GET /source` answers from an
 * already-warm session instead of paying `uiautomator dump`'s per-invocation startup (≈ 2.4 s).
 *
 * The body it returns is [UiDevice.dumpWindowHierarchy]'s XML, which shares its
 * `AccessibilityNodeInfoDumper` origin with `uiautomator dump`, so bajutsu's `parse_hierarchy`
 * consumes it unchanged.
 *
 * Transport is a hand-rolled HTTP/1.1 over a raw [ServerSocket] — the server answers a small, fixed
 * set of paths, so a full HTTP library would be dead weight. bajutsu reaches the socket over
 * `adb forward` (wired in a later slice); binding to loopback keeps it off the device network.
 *
 * Every `GET /source` also carries a read mark (BE-0332 Unit 3): the [android.app.UiAutomation]
 * session is already observing the accessibility event stream (that is how [UiDevice.waitForIdle]
 * works), so a listener records the device-clock time of the most recent event, and the header
 * [READ_MARK_HEADER] reports the value as of the served dump. `GET /clock` returns the device's
 * current [SystemClock.uptimeMillis] — the same clock [android.view.accessibility.AccessibilityEvent.getEventTime]
 * uses — so the host can take a "before the gesture" mark that a later read must postdate, with no
 * host-to-device clock skew. A `GET /source?since=<mark>` then blocks until an event postdates that
 * mark before dumping once (BE-0332 Unit 4): the reader returns a tree the host's gesture has already
 * reached, rather than inferring stability from two byte-identical dumps (the old barrier, which read
 * a late-but-settled tree as current and cost a second dump on every settled screen). The XML body is
 * unchanged, so bajutsu's `parse_hierarchy` still consumes it as-is; the mark rides in the header.
 */
@RunWith(AndroidJUnit4::class)
class ResidentServerTest {

    @Test
    fun serve() {
        val instrumentation = InstrumentationRegistry.getInstrumentation()
        val device = UiDevice.getInstance(instrumentation)
        // The most recent accessibility event's device-clock time, plus a monitor a read blocks on
        // until an event postdates its requested mark (BE-0332 Units 3 and 4). The listener is
        // additive: UiAutomation keeps its own internal event bookkeeping (what `waitForIdle` rests
        // on), so observing here does not disturb it.
        val readMark = ReadMark()
        instrumentation.uiAutomation.setOnAccessibilityEventListener { event ->
            readMark.record(event.eventTime)
        }
        ServerSocket(PORT, BACKLOG, InetAddress.getLoopbackAddress()).use { server ->
            Log.i(TAG, "resident UI Automator server listening on 127.0.0.1:$PORT")
            while (true) {
                // Keep the loop — and so the warm session — alive across a bad connection: a
                // broken pipe or abrupt disconnect on one request must not kill the resident
                // @Test, or every read would pay `uiautomator dump`'s startup cost again.
                try {
                    server.accept().use { client -> handle(client, device, readMark) }
                } catch (e: Exception) {
                    Log.w(TAG, "dropped one connection", e)
                }
            }
        }
    }

    private fun handle(client: Socket, device: UiDevice, readMark: ReadMark) {
        // A stalled client (slow or incomplete request) must not block the single-threaded accept
        // loop: without a read timeout, readLine() would wait forever and wedge the whole server.
        client.soTimeout = SO_TIMEOUT_MS
        val reader = client.getInputStream().bufferedReader(StandardCharsets.UTF_8)
        val target = readRequestTarget(reader) ?: return
        val out = client.getOutputStream()
        when (target.substringBefore('?')) {
            "/source" -> respondSource(out, device, readMark, sinceOf(target))
            "/clock" ->
                respond(
                    out,
                    "200 OK",
                    "text/plain; charset=utf-8",
                    SystemClock.uptimeMillis().toString().toByteArray(StandardCharsets.UTF_8),
                )
            else -> respond(out, "404 Not Found", "text/plain", "unknown path\n".toByteArray())
        }
        out.flush()
    }

    /** The request target (path plus any query) after the method; null if empty/malformed. */
    private fun readRequestTarget(reader: BufferedReader): String? {
        val requestLine = reader.readLine() ?: return null
        val target = requestLine.split(' ').getOrNull(1) ?: return null
        // Drain the remaining request headers so the client sees a clean, complete exchange.
        while (true) {
            val line = reader.readLine() ?: break
            if (line.isEmpty()) break
        }
        return target
    }

    /**
     * The `since` device-clock mark from a `GET /source?since=<mark>` target, or null if absent.
     *
     * Parsed as a Double because the host carries the mark as one (BE-0332 Unit 4); a malformed value
     * yields null, so the read simply does not wait rather than failing.
     */
    private fun sinceOf(target: String): Double? {
        val query = target.substringAfter('?', "")
        for (param in query.split('&')) {
            if (param.startsWith("since=")) return param.removePrefix("since=").toDoubleOrNull()
        }
        return null
    }

    private fun respondSource(out: OutputStream, device: UiDevice, readMark: ReadMark, since: Double?) {
        // dumpWindowHierarchy traverses every window, so this XML also carries the SystemUI status
        // bar (clock, wifi, battery, notification icons — 29 nodes) that the platform `uiautomator
        // dump` omits by scoping to the active window. `parse_hierarchy` parses the format unchanged.
        device.waitForIdle()
        if (since != null) {
            // The mark-anchored barrier (BE-0332 Unit 4): block until an accessibility event postdates
            // the mark the host took before its gesture, then drain any it triggered. A genuine
            // condition wait — it releases the instant such an event arrives, and the budget only caps
            // a lag that never comes (never a fixed sleep — Bajutsu determinism). On expiry the latest
            // tree is dumped anyway, carrying its own (still-stale) mark for the host to judge.
            readMark.awaitPostdate(since, POSTDATE_BUDGET_MS)
            device.waitForIdle()
        }
        // Snapshot the read mark *before* the dump: the invariant the host relies on is
        // `mark > actuation_mark` ⟹ body is post-gesture, and that holds only when the body is at
        // least as fresh as the mark. Reading the mark after the dump would let an event arriving in
        // between push the mark above the body's freshness, certifying a stale tree as caught up.
        // Snapshotting first makes the mark only ever *undercount* events relative to the body, so the
        // host waits for a fresher read rather than trusting a stale one.
        val mark = readMark.current()
        val body = dumpHierarchy(device)
        respond(
            out,
            "200 OK",
            "application/xml; charset=utf-8",
            body,
            mapOf(READ_MARK_HEADER to mark.toString()),
        )
    }

    private fun dumpHierarchy(device: UiDevice): ByteArray =
        ByteArrayOutputStream().also { device.dumpWindowHierarchy(it) }.toByteArray()

    private fun respond(
        out: OutputStream,
        status: String,
        contentType: String,
        body: ByteArray,
        extraHeaders: Map<String, String> = emptyMap(),
    ) {
        val header = buildString {
            append("HTTP/1.1 ").append(status).append("\r\n")
            append("Content-Type: ").append(contentType).append("\r\n")
            append("Content-Length: ").append(body.size).append("\r\n")
            for ((name, value) in extraHeaders) append(name).append(": ").append(value).append("\r\n")
            append("Connection: close\r\n")
            append("\r\n")
        }
        out.write(header.toByteArray(StandardCharsets.UTF_8))
        out.write(body)
    }

    /**
     * The device-clock time of the most recent accessibility event, with a monitor so a reader can
     * block until an event postdates a requested mark (BE-0332 Unit 4).
     *
     * Written from the `UiAutomation` callback thread and read from the accept-loop thread; the lock
     * guards both the value and the wait/notify so a waiting reader never misses a wake.
     */
    private class ReadMark {
        private val lock = ReentrantLock()
        private val advanced = lock.newCondition()
        // Seeded with the current clock so a read taken before any event still reports a sensible,
        // pre-actuation mark.
        private var eventTime = SystemClock.uptimeMillis()

        /** Record an event's device-clock time and wake any reader waiting for a postdating event. */
        fun record(time: Long) = lock.withLock {
            eventTime = time
            advanced.signalAll()
        }

        /** The most recent event's device-clock time, as of now. */
        fun current(): Long = lock.withLock { eventTime }

        /**
         * Block until an event postdates [since], or [budgetMs] elapses. A genuine condition wait: it
         * returns the instant an event arrives past the mark, and the budget only caps a lag that
         * never comes — never a fixed delay. [since] is a Double because the host carries the mark as
         * one; device event times are whole milliseconds, so the comparison stays exact.
         */
        fun awaitPostdate(since: Double, budgetMs: Long) = lock.withLock {
            var remaining = TimeUnit.MILLISECONDS.toNanos(budgetMs)
            while (eventTime.toDouble() <= since && remaining > 0L) {
                remaining = advanced.awaitNanos(remaining)
            }
        }
    }

    private companion object {
        const val TAG = "BajutsuResidentServer"
        const val PORT = 6790
        const val BACKLOG = 16
        const val SO_TIMEOUT_MS = 5_000

        // The response header GET /source stamps with the read mark (BE-0332 Unit 3): the device-clock
        // time of the newest accessibility event observed as of the served dump. Kept in sync with the
        // host side (`bajutsu/adb_resident.py` `_READ_MARK_HEADER`).
        const val READ_MARK_HEADER = "X-Bajutsu-Read-Mark"

        // How long GET /source?since= waits for an event to postdate the requested mark before dumping
        // the latest tree anyway (BE-0332 Unit 4). A ceiling on the read lag, not a fixed delay: the
        // wait releases the instant an event arrives past the mark. Kept well inside the host's
        // `fetch_source` 5 s HTTP timeout, and shorter than the host's own read-lag budget
        // (`AdbDriver._READ_LAG_S`, 4 s) so the host re-poll stays the outer bound. The read-lag
        // investigation saw the post-gesture update land within ~2 s (BE-0332 Motivation).
        const val POSTDATE_BUDGET_MS = 2_000L
    }
}
