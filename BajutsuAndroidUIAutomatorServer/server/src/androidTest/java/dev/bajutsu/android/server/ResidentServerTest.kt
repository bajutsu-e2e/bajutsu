package dev.bajutsu.android.server

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
 * Transport is a hand-rolled HTTP/1.1 over a raw [ServerSocket] — the server answers exactly one
 * verb on one path, so a full HTTP library would be dead weight. bajutsu reaches the socket over
 * `adb forward` (wired in a later slice); binding to loopback keeps it off the device network.
 */
@RunWith(AndroidJUnit4::class)
class ResidentServerTest {

    @Test
    fun serve() {
        val device = UiDevice.getInstance(InstrumentationRegistry.getInstrumentation())
        ServerSocket(PORT, BACKLOG, InetAddress.getLoopbackAddress()).use { server ->
            Log.i(TAG, "resident UI Automator server listening on 127.0.0.1:$PORT")
            while (true) {
                // Keep the loop — and so the warm session — alive across a bad connection: a
                // broken pipe or abrupt disconnect on one request must not kill the resident
                // @Test, or every read would pay `uiautomator dump`'s startup cost again.
                try {
                    server.accept().use { client -> handle(client, device) }
                } catch (e: Exception) {
                    Log.w(TAG, "dropped one connection", e)
                }
            }
        }
    }

    private fun handle(client: Socket, device: UiDevice) {
        // A stalled client (slow or incomplete request) must not block the single-threaded accept
        // loop: without a read timeout, readLine() would wait forever and wedge the whole server.
        client.soTimeout = SO_TIMEOUT_MS
        val reader = client.getInputStream().bufferedReader(StandardCharsets.UTF_8)
        val path = readRequestPath(reader) ?: return
        val out = client.getOutputStream()
        when (path) {
            "/source" -> respondSource(out, device)
            else -> respond(out, "404 Not Found", "text/plain", "unknown path\n".toByteArray())
        }
        out.flush()
    }

    /** First token after the method on the request line; null if the request is empty/malformed. */
    private fun readRequestPath(reader: BufferedReader): String? {
        val requestLine = reader.readLine() ?: return null
        val path = requestLine.split(' ').getOrNull(1) ?: return null
        // Drain the remaining request headers so the client sees a clean, complete exchange.
        while (true) {
            val line = reader.readLine() ?: break
            if (line.isEmpty()) break
        }
        return path
    }

    private fun respondSource(out: OutputStream, device: UiDevice) {
        // dumpWindowHierarchy traverses every window, so this XML also carries the SystemUI status
        // bar (clock, wifi, battery, notification icons — 29 nodes) that the platform `uiautomator
        // dump` omits by scoping to the active window. `parse_hierarchy` parses the format unchanged.
        respond(out, "200 OK", "application/xml; charset=utf-8", stableHierarchy(device))
    }

    /**
     * Dump the window hierarchy once it has settled: re-dump across [UiDevice.waitForIdle] until two
     * consecutive dumps are byte-identical, or [STABLE_DUMPS] is reached.
     *
     * `waitForIdle` alone (BE-0245's original fix) drains the accessibility event queue, matching what
     * the platform `uiautomator dump` shell command does — but a warm resident session reads the tree
     * faster than the dump command's per-invocation startup, so it can still snapshot a stale value
     * when a gesture's result (e.g. an a11y `value` flipping idle→pressed) is posted just *after* the
     * queue looked idle. `uiautomator dump`'s startup latency masked that window; the resident channel
     * exposes it, producing flaky post-gesture reads (BE-0245 follow-up). Requiring two matching dumps
     * makes "the tree stopped changing" the read barrier, closing that window so the resident and dump
     * paths yield the same Elements. Bounded and condition-driven (settle, not a fixed sleep — Bajutsu
     * determinism); an element that never settles (an animation) costs at most [STABLE_DUMPS] dumps and
     * returns the last read, still far cheaper than `uiautomator dump`'s ≈ 2.4 s startup.
     */
    private fun stableHierarchy(device: UiDevice): ByteArray {
        device.waitForIdle()
        var previous = dumpHierarchy(device)
        repeat(STABLE_DUMPS - 1) {
            device.waitForIdle()
            val current = dumpHierarchy(device)
            if (current.contentEquals(previous)) return current
            previous = current
        }
        return previous.also { Log.d(TAG, "hierarchy did not settle after $STABLE_DUMPS dumps") }
    }

    private fun dumpHierarchy(device: UiDevice): ByteArray =
        ByteArrayOutputStream().also { device.dumpWindowHierarchy(it) }.toByteArray()

    private fun respond(out: OutputStream, status: String, contentType: String, body: ByteArray) {
        val header = buildString {
            append("HTTP/1.1 ").append(status).append("\r\n")
            append("Content-Type: ").append(contentType).append("\r\n")
            append("Content-Length: ").append(body.size).append("\r\n")
            append("Connection: close\r\n")
            append("\r\n")
        }
        out.write(header.toByteArray(StandardCharsets.UTF_8))
        out.write(body)
    }

    private companion object {
        const val TAG = "BajutsuResidentServer"
        const val PORT = 6790
        const val BACKLOG = 16
        const val SO_TIMEOUT_MS = 5_000

        // Max dumps per read while waiting for two consecutive hierarchies to match (see
        // stableHierarchy). A settled screen matches on the 2nd dump; the extra headroom absorbs a
        // gesture result that lands mid-read without letting an animated node spin forever.
        const val STABLE_DUMPS = 4
    }
}
