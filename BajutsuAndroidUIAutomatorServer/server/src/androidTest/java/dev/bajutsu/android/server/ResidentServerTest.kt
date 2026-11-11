package dev.bajutsu.android.server

import android.app.UiAutomation
import android.graphics.Rect
import android.os.Bundle
import android.os.SystemClock
import android.util.Base64
import android.util.Log
import android.util.Xml
import android.view.InputDevice
import android.view.MotionEvent
import android.view.accessibility.AccessibilityNodeInfo
import android.view.accessibility.AccessibilityWindowInfo
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import androidx.test.uiautomator.Configurator
import androidx.test.uiautomator.UiDevice
import java.io.BufferedReader
import java.io.ByteArrayOutputStream
import java.io.OutputStream
import java.net.InetAddress
import java.net.ServerSocket
import java.net.Socket
import java.net.SocketTimeoutException
import java.net.URLDecoder
import java.nio.charset.StandardCharsets
import java.util.concurrent.TimeUnit
import java.util.concurrent.locks.ReentrantLock
import kotlin.concurrent.withLock
import org.junit.Test
import org.junit.runner.RunWith
import org.xmlpull.v1.XmlPullParser

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
 * mark before dumping (BE-0332 Unit 4): the reader returns a tree the host's gesture has already
 * reached, rather than inferring *staleness* from two byte-identical dumps (the old barrier, which
 * read a late-but-settled tree as current). A bounded settle still runs after the mark gate to close
 * *tearing* — the window where Android has republished only some node bounds, so a lone dump captures
 * a half-updated tree — so the mark decides freshness while the settle keeps the wholeness the retired
 * barrier also gave. The XML body is unchanged, so bajutsu's `parse_hierarchy` still consumes it
 * as-is; the mark rides in the header.
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

    /**
     * Serve requests on one connection until the peer stops sending, then let it close.
     *
     * A connection per request cost a fresh TCP handshake on every read, every clock probe and every
     * gesture — tens of milliseconds each, compounding across a scenario's many reads (BE-0407 unit
     * 21, the Android peer of unit 11's XCUITest keep-alive). The loop ends the way the iOS runner's
     * does: on a peer that has gone idle, on a request line that does not parse, and on a reply that
     * could not be written — ending the connection rather than desynchronizing it.
     *
     * Safe to serve serially because the host holds exactly one resident connection at a time: reads,
     * the clock probe and gestures all issue from the driver's own thread, and the one call that does
     * run on a worker — the overlapped screenshot (BE-0407 unit 2) — goes through `adb exec-out
     * screencap`, not through here.
     */
    private fun handle(client: Socket, device: UiDevice, readMark: ReadMark) {
        // A stalled client (slow or incomplete request) must not block the single-threaded accept
        // loop: without a read timeout, readLine() would wait forever and wedge the whole server.
        // With the connection now reused, the same timeout doubles as the idle ceiling on it.
        client.soTimeout = SO_TIMEOUT_MS
        // A reply is small and the host blocks on it, so Nagle's delay buys nothing and can cost a
        // round trip's worth of latency on exactly the exchanges this unit is shortening.
        client.tcpNoDelay = true
        val reader = client.getInputStream().bufferedReader(StandardCharsets.UTF_8)
        val out = client.getOutputStream()
        while (true) {
            // Two different silences, told apart because only one is ordinary. `soTimeout` governs
            // every read on the socket, so a timeout *after* the request line arrived is a fault the
            // host is blocking on — for `/act` it reaches the driver as "may or may not have
            // landed", when in fact nothing was injected — while one before it is a peer that simply
            // went quiet, the ordinary end of a reused connection.
            val started = Started()
            val request = try {
                readRequest(reader, started) ?: return
            } catch (e: SocketTimeoutException) {
                if (started.value) {
                    Log.w(TAG, "connection went quiet mid-request; nothing was served", e)
                } else {
                    Log.d(TAG, "connection idle past ${SO_TIMEOUT_MS}ms; closing it", e)
                }
                return
            }
            val target = request.target
            if (target.isEmpty()) {
                // A request line that does not parse. With one connection per request this could
                // only be a bad client; with the connection reused it is the signature of the two
                // ends disagreeing about where the last reply ended, which is the one symptom that
                // would reveal such a desynchronization in the wild.
                Log.w(TAG, "malformed request line; ending the connection rather than guessing")
                return
            }
            when (target.substringBefore('?')) {
                "/source" -> respondSource(out, device, readMark, target)
                "/act" -> respondAct(out, device, readMark, target)
                "/type" -> respondType(out, device, readMark, target, request.body)
                "/setPickerValue" -> respondSetPickerValue(out, device, readMark, target)
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
    }

    /** Whether a request line has been read yet, so the caller can tell an idle peer from a fault. */
    private class Started {
        var value = false
    }

    /** One parsed request: the target (path plus any query) and the body, empty when there is none. */
    private class Request(val target: String, val body: String)

    /**
     * The next request; null at end of input, an empty target if the request line does not parse.
     *
     * Always consumes the whole body, which became load-bearing once the connection is reused: bytes
     * left unread would be parsed as the *next* request on it, so a desynchronized connection would
     * answer wrong rather than fail. Only `/type` reads what it holds. Nothing here logs the body or
     * its length, because a typed value may be a secret.
     */
    private fun readRequest(reader: BufferedReader, started: Started): Request? {
        val requestLine = reader.readLine() ?: return null
        started.value = true
        val target = requestLine.split(' ').getOrNull(1) ?: return Request("", "")
        // Drain the remaining request headers so the client sees a clean, complete exchange.
        var contentLength = 0
        while (true) {
            val line = reader.readLine() ?: break
            if (line.isEmpty()) break
            if (line.startsWith(CONTENT_LENGTH_HEADER, ignoreCase = true)) {
                contentLength = line.substringAfter(':').trim().toIntOrNull() ?: 0
            }
        }
        val body = StringBuilder()
        var left = contentLength
        val buffer = CharArray(DRAIN_CHUNK)
        while (left > 0) {
            val read = reader.read(buffer, 0, minOf(left, buffer.size))
            // A peer gone mid-body: nothing was served, and a partial text must not reach `/type`.
            if (read < 0) {
                Log.w(TAG, "connection ended mid-body; nothing was served")
                return null
            }
            val chunk = String(buffer, 0, read)
            body.append(chunk)
            left -= chunk.toByteArray(StandardCharsets.UTF_8).size
        }
        return Request(target, body.toString())
    }

    /** One query parameter's decoded value, or null when the target does not carry it. */
    private fun paramOf(target: String, name: String): String? {
        val query = target.substringAfter('?', "")
        for (param in query.split('&')) {
            if (param.startsWith("$name=")) {
                return URLDecoder.decode(param.removePrefix("$name="), "UTF-8")
            }
        }
        return null
    }

    /**
     * The `since` device-clock mark from a `?since=<mark>` target, or null if absent.
     *
     * Parsed as a Double because the host carries the mark as one (BE-0332 Unit 4); a malformed value
     * yields null, so the read simply does not wait rather than failing.
     */
    private fun sinceOf(target: String): Double? = paramOf(target, "since")?.toDoubleOrNull()

    /**
     * How long this request may wait, as the host asked. The host names it because the host's own
     * timeout is what it has to fit inside (`adb_resident/budgets.py`); a target without the parameter
     * gets the default, and a malformed or negative value cannot unbound the wait.
     */
    private fun budgetOf(target: String): Long =
        paramOf(target, "budgetMs")?.toLongOrNull()?.coerceIn(0L, DEFAULT_WAIT_BUDGET_MS)
            ?: DEFAULT_WAIT_BUDGET_MS

    /**
     * One request's allowance for waiting on the device, and the record of where it went.
     *
     * `UiDevice.waitForIdle()` waits up to 10 s by default, and on a screen that keeps publishing
     * accessibility events it waits all of it. `dumpWindowHierarchy` runs the same wait twice inside
     * itself, where no argument can reach it, so the bound goes through [Configurator]: the one
     * setting both read. Measured on a 10 Hz stopwatch screen on an API 34 emulator before this bound
     * existed: 120.9 s for one `GET /source`, twelve 10 s waits, against the host's 5 s timeout. Only
     * the wait is cut short, never replaced by a sleep: a screen that goes idle still releases it at
     * once.
     *
     * Safe as a process-wide setting because the accept loop serves one request at a time.
     */
    private class WaitBudget(
        private val label: String,
        private val device: UiDevice,
        private val budgetMs: Long,
    ) {
        private val startedAt = SystemClock.uptimeMillis()
        private val deadline = startedAt + budgetMs
        private var lapStart = startedAt
        private val laps = StringBuilder()

        private fun remaining(): Long = (deadline - SystemClock.uptimeMillis()).coerceAtLeast(0L)

        fun spent(): Boolean = remaining() == 0L

        /** One bounded idle wait. A cap per wait keeps the first from spending the whole budget. */
        fun awaitIdle() {
            Configurator.getInstance().waitForIdleTimeout = minOf(IDLE_WAIT_MS, remaining())
            device.waitForIdle()
            lap("idle")
        }

        /** Bound the waits inside the next dump, sharing what is left between them. */
        fun capDumpWaits() {
            Configurator.getInstance().waitForIdleTimeout =
                minOf(IDLE_WAIT_MS, remaining() / WAITS_PER_DUMP)
        }

        fun lap(stage: String) {
            val now = SystemClock.uptimeMillis()
            laps.append(stage).append('=').append(now - lapStart).append("ms ")
            lapStart = now
        }

        /**
         * Say where this request's time went. Logged before the reply goes out, so a request the host
         * has already given up on still leaves its record, loudest when it used the whole budget.
         */
        fun report() {
            val total = SystemClock.uptimeMillis() - startedAt
            val line = "$label waits: ${laps}total=${total}ms budget=${budgetMs}ms"
            if (total >= budgetMs) Log.w(TAG, line) else Log.d(TAG, line)
        }
    }

    /**
     * Resolve a target by its accessibility fields against a *fresh* dump and inject the gesture here,
     * in the warm session, rather than answering a coordinate the host will inject a round trip later.
     *
     * The host still decides *which* element a selector means — `resolve_unique` runs there, so an
     * ambiguous selector fails before anything is sent (Bajutsu determinism). What crosses is the
     * already-chosen element's identity: its `resource-id`, `content-desc`, `text`, and `class`, plus
     * its ordinal among the nodes that share all four and how many of those the host saw. The device's
     * only judgement is whether that identity still names the same number of nodes. It never falls back
     * to a coordinate the host computed: a mismatch answers [STALE_STATUS], and the host re-resolves.
     *
     * Fields rather than a digest, so the host and the device never have to agree on a hash. Fields
     * rather than an index into the whole dump, so the two do not have to agree on the *position* a
     * node sits at in their respective trees — but they do still have to agree on which *windows* the
     * tree includes, because `count` is a count: `matchingBounds` below drops SystemUI's own windows
     * the same way the host's `narrowed_root` does, so a node whose bare identity happens to
     * collide with an equally bare SystemUI container is not counted here when the host never counted
     * it either.
     *
     * The win is the gap. Resolving here puts the read and the injection microseconds apart in one
     * process, where the host path spends a round trip plus `adb shell input`'s JVM startup between
     * them — the window in which a still-settling screen moves out from under a computed coordinate.
     * The tree this resolves against is made current the same way [respondSource]'s is: a `since` mark
     * is honored first, so the bounds read here postdate the gesture the host is following up on. And
     * the gesture this injects is followed to its own publish before the answer goes out — see
     * [publishHeader], which is what lets the host skip the read-lag barrier for it (BE-0339 Unit 5).
     */
    private fun respondAct(out: OutputStream, device: UiDevice, readMark: ReadMark, target: String) {
        // Validated first, and never defaulted. A missing or malformed field here would otherwise
        // pick an element by assumption — `index` 0 of `count` 1 — which is exactly the guess this
        // endpoint exists to refuse. An identity field may legitimately be empty (a node with no
        // text), so presence is required and emptiness is not. Before the waits below, so a request
        // that cannot be served does not first spend the postdate budget earning its `400`.
        val kind = paramOf(target, "kind") ?: return respond(out, BAD_REQUEST, TEXT, "no kind\n".bytes())
        val index = paramOf(target, "index")?.toIntOrNull()
            ?: return respond(out, BAD_REQUEST, TEXT, "no usable index\n".bytes())
        val count = paramOf(target, "count")?.toIntOrNull()
            ?: return respond(out, BAD_REQUEST, TEXT, "no usable count\n".bytes())
        val want = IDENTITY_FIELDS.map {
            paramOf(target, it.first)
                ?: return respond(out, BAD_REQUEST, TEXT, "no ${it.first}\n".bytes())
        }
        val budget = WaitBudget("act", device, budgetOf(target))
        budget.awaitIdle()
        sinceOf(target)?.let {
            readMark.awaitPostdate(it, POSTDATE_BUDGET_MS)
            budget.lap("postdate")
            budget.awaitIdle()
        }
        val matches = settledActBounds(device, want, budget)
        budget.report()
        if (matches.size != count || index !in matches.indices) {
            // Loudly stale, never a guess: the screen the host resolved on is not the screen here, so
            // acting on `matches[index]` would be acting on a different element. The host re-resolves.
            return respond(out, STALE_STATUS, TEXT, "stale: ${matches.size} of $count\n".bytes())
        }
        val bounds = matches[index]
        val x = bounds.centerX()
        val y = bounds.centerY()
        // Each injector's boolean return is the only signal that the touch actually reached the
        // screen — `UiDevice.click` / `.swipe` and `UiAutomation.injectInputEvent` (via
        // [injectDoubleTap]) all report `false` when the platform rejects the event. Answering
        // `200 OK` regardless would let a rejected injection reach the host as a landed gesture: no
        // step re-resolves or degrades, and only the scenario's own later assertion could ever catch
        // it. A non-200 here surfaces the rejection immediately (see [INJECT_FAILED_STATUS]).
        val injectedAt = SystemClock.uptimeMillis()
        val landed = when (kind) {
            "tap" -> device.click(x, y)
            "longPress" -> {
                // `UiDevice` has no press-and-hold, so a zero-length swipe over the requested duration
                // is the press: the same shape the host's `input swipe x y x y <ms>` takes, minus the
                // process startup. Steps pace the drag, so one step per ~10ms holds the contact down.
                val ms = paramOf(target, "durationMs")?.toIntOrNull() ?: DEFAULT_LONG_PRESS_MS
                device.swipe(x, y, x, y, (ms / SWIPE_STEP_MS).coerceAtLeast(1))
            }
            "doubleTap" -> injectDoubleTap(x, y)
            else -> return respond(out, BAD_REQUEST, TEXT, "unknown kind $kind\n".bytes())
        }
        if (!landed) {
            return respond(out, INJECT_FAILED_STATUS, TEXT, "$kind rejected by the platform\n".bytes())
        }
        respondLanded(out, device, readMark, injectedAt, paramOf(target, "tree") != "0")
    }

    /**
     * Move the `NumberPicker` wheel the host resolved to the row showing `value` (BE-0480).
     *
     * The opening is [respondAct]'s, so the wheel is addressed exactly as a tap target is: identity,
     * ordinal and count, validated without defaults, then re-found in a settled dump, answering
     * [STALE_STATUS] when the screen moved. From there the handler works on the accessibility node
     * rather than a coordinate. Each `ACTION_SCROLL_FORWARD` / `_BACKWARD` is the wheel's own one-row
     * step, and the value is read back after each one, so the walk stops on the named row or says
     * why it could not. No coordinate drag could promise that landing.
     *
     * Typing into the wheel's input field would take one request instead of many, but whether it
     * commits depends on the field's input filter and on focus leaving at the right time, which is
     * each app's configuration rather than the platform's (BE-0480 *Alternatives considered*).
     *
     * Every refusal after the stale check answers [UNPROCESSABLE_STATUS] with a reason token ahead of
     * the first colon in the body, which the host maps to its own error (`adb_resident.set_picker_value`).
     */
    private fun respondSetPickerValue(
        out: OutputStream,
        device: UiDevice,
        readMark: ReadMark,
        target: String,
    ) {
        val index = paramOf(target, "index")?.toIntOrNull()
            ?: return respond(out, BAD_REQUEST, TEXT, "no usable index\n".bytes())
        val count = paramOf(target, "count")?.toIntOrNull()
            ?: return respond(out, BAD_REQUEST, TEXT, "no usable count\n".bytes())
        val want = IDENTITY_FIELDS.map {
            paramOf(target, it.first)
                ?: return respond(out, BAD_REQUEST, TEXT, "no ${it.first}\n".bytes())
        }
        // Present but possibly empty, like an identity field: an empty row label is still a label.
        val value = paramOf(target, "value")
            ?: return respond(out, BAD_REQUEST, TEXT, "no value\n".bytes())
        val budget = WaitBudget("setPickerValue", device, budgetOf(target))
        budget.awaitIdle()
        sinceOf(target)?.let {
            readMark.awaitPostdate(it, POSTDATE_BUDGET_MS)
            budget.lap("postdate")
            budget.awaitIdle()
        }
        val matches = settledActBounds(device, want, budget)
        budget.report()
        if (matches.size != count || index !in matches.indices) {
            return respond(out, STALE_STATUS, TEXT, "stale: ${matches.size} of $count\n".bytes())
        }
        val bounds = matches[index]
        // A throw mid-walk would otherwise drop the connection, which the host can only read as a
        // lost reply ("may have moved"); answering it names the cause instead.
        try {
            walkAndRespond(out, device, readMark, target, want, bounds, value)
        } catch (e: RuntimeException) {
            Log.w(TAG, "setPickerValue walk threw", e)
            respond(out, INJECT_FAILED_STATUS, TEXT, "the walk threw ${e.javaClass.simpleName}\n".bytes())
        }
    }

    private fun walkAndRespond(
        out: OutputStream,
        device: UiDevice,
        readMark: ReadMark,
        target: String,
        want: List<String>,
        bounds: Rect,
        value: String,
    ) {
        val wheel = when (val found = pickerWheelAt(bounds, want)) {
            is WheelSearch.Found -> found.node
            WheelSearch.Absent ->
                return refusePicker(out, NOT_A_PICKER_WHEEL, "no android.widget.NumberPicker at $bounds")
            is WheelSearch.Ambiguous ->
                return refusePicker(out, NOT_A_PICKER_WHEEL, "${found.count} NumberPickers at $bounds")
            WheelSearch.TooLarge ->
                return respond(
                    out,
                    INJECT_FAILED_STATUS,
                    TEXT,
                    "the node search stopped at $FOCUS_WALK_LIMIT nodes before $bounds\n".bytes(),
                )
        }
        val start = wheelValue(wheel)
            ?: return refusePicker(out, NOT_A_PICKER_WHEEL, "the wheel at $bounds shows no input row")
        // Taken before the first step, for the reason [publishHeader] gives for `injectedAt`.
        val injectedAt = SystemClock.uptimeMillis()
        val walk = walkWheel(wheel, start, value)
        val rows = walk.rows
        when (walk.end) {
            WalkEnd.LANDED ->
                respondLanded(out, device, readMark, injectedAt, paramOf(target, "tree") != "0")
            WalkEnd.ABSENT ->
                refusePicker(out, PICKER_VALUE_ABSENT, "the wheel holds no row \"$value\" ($rows rows walked)")
            WalkEnd.IMMOVABLE ->
                refusePicker(out, PICKER_IMMOVABLE, "the wheel at $bounds refused both scroll directions")
            WalkEnd.STALLED ->
                refusePicker(
                    out,
                    PICKER_WALK_EXHAUSTED,
                    "row $rows did not change within ${2 * PICKER_ROW_BUDGET_MS}ms",
                )
            WalkEnd.ROW_CAP ->
                refusePicker(out, PICKER_WALK_EXHAUSTED, "hit the $MAX_PICKER_ROWS-row cap before the row")
            WalkEnd.DEADLINE ->
                refusePicker(
                    out,
                    PICKER_WALK_EXHAUSTED,
                    "hit the ${walkDeadlineMs()}ms deadline after $rows rows",
                )
            WalkEnd.UNSETTLED ->
                respond(out, INJECT_FAILED_STATUS, TEXT, "the wheel left the row after reaching it\n".bytes())
            WalkEnd.LOST ->
                respond(out, INJECT_FAILED_STATUS, TEXT, "the wheel left the screen mid-walk\n".bytes())
        }
    }

    private enum class WalkEnd { LANDED, ABSENT, IMMOVABLE, STALLED, ROW_CAP, DEADLINE, UNSETTLED, LOST }

    private class Walk(val end: WalkEnd, val rows: Int)

    private sealed interface WheelSearch {
        class Found(val node: AccessibilityNodeInfo) : WheelSearch
        class Ambiguous(val count: Int) : WheelSearch
        object Absent : WheelSearch
        object TooLarge : WheelSearch
    }

    /**
     * Step `wheel` one row at a time until it shows `value`, and say how the walk ended.
     *
     * The first pass goes toward the value when both read as integers, so a non-wrapping year wheel
     * takes the short way; otherwise it goes forward. A pass ends at an edge, which `NumberPicker`
     * signals by refusing the scroll action, and the second pass walks back the other way. That
     * also covers rows whose numeric order is not monotonic. A wrapping wheel never refuses, and
     * coming back to where it started is what ends its walk. A wheel that refuses its very first step
     * both ways (a disabled one) ends as [WalkEnd.IMMOVABLE], so the host does not report a missing
     * row.
     *
     * A row that does not change across two row budgets ends the walk as [WalkEnd.STALLED] rather
     * than as an edge. Turning back there would be unsafe: the next action first completes the step
     * still in flight, so a read could show the target row an instant before the wheel moves on.
     * For the same reason a landing must hold for [PICKER_LANDING_HOLD_MS] before it counts.
     */
    private fun walkWheel(wheel: AccessibilityNodeInfo, start: String, value: String): Walk {
        if (start == value) return Walk(WalkEnd.LANDED, 0)
        val forward = AccessibilityNodeInfo.AccessibilityAction.ACTION_SCROLL_FORWARD.id
        val backward = AccessibilityNodeInfo.AccessibilityAction.ACTION_SCROLL_BACKWARD.id
        val from = start.trim().toLongOrNull()
        val to = value.trim().toLongOrNull()
        val first = if (from != null && to != null && to < from) backward else forward
        val passes = listOf(first, if (first == forward) backward else forward)
        val deadline = SystemClock.uptimeMillis() + walkDeadlineMs()
        var rows = 0
        var moved = false
        var current = start
        for ((pass, action) in passes.withIndex()) {
            while (true) {
                if (rows >= MAX_PICKER_ROWS) return Walk(WalkEnd.ROW_CAP, rows)
                if (SystemClock.uptimeMillis() >= deadline) return Walk(WalkEnd.DEADLINE, rows)
                if (!wheel.refresh()) return Walk(WalkEnd.LOST, rows)
                val stepStarted = SystemClock.uptimeMillis()
                if (!wheel.performAction(action)) break
                rows++
                moved = true
                val before = current
                current = rowChanged(wheel, before) ?: return Walk(WalkEnd.LOST, rows)
                if (current == before) current = rowChanged(wheel, before) ?: return Walk(WalkEnd.LOST, rows)
                // The row labels stay out of the log, as the typed text does in `/type`.
                Log.d(TAG, "picker row $rows took ${SystemClock.uptimeMillis() - stepStarted}ms")
                if (current == before) return Walk(WalkEnd.STALLED, rows)
                if (current == value) {
                    val held = rowHolds(wheel, value) ?: return Walk(WalkEnd.LOST, rows)
                    return Walk(if (held) WalkEnd.LANDED else WalkEnd.UNSETTLED, rows)
                }
                if (pass == 0 && current == start) return Walk(WalkEnd.ABSENT, rows)
            }
        }
        return Walk(if (moved) WalkEnd.ABSENT else WalkEnd.IMMOVABLE, rows)
    }

    /**
     * The wheel's value once it differs from `before`, or `before` itself when one row's budget runs
     * out first; null when the wheel is gone. A condition wait polled at [PICKER_POLL_MS]: the row
     * changes when `NumberPicker`'s own one-row animation ends, which no idle wait tracks (an idle
     * wait needs a quiet spell longer than the whole animation).
     */
    private fun rowChanged(wheel: AccessibilityNodeInfo, before: String): String? {
        val deadline = SystemClock.uptimeMillis() + PICKER_ROW_BUDGET_MS
        while (true) {
            if (!wheel.refresh()) return null
            val now = wheelValue(wheel) ?: return null
            if (now != before || SystemClock.uptimeMillis() >= deadline) return now
            SystemClock.sleep(PICKER_POLL_MS)
        }
    }

    /** Whether the wheel still shows `value` throughout [PICKER_LANDING_HOLD_MS]; null when it is gone. */
    private fun rowHolds(wheel: AccessibilityNodeInfo, value: String): Boolean? {
        val until = SystemClock.uptimeMillis() + PICKER_LANDING_HOLD_MS
        while (true) {
            if (!wheel.refresh()) return null
            val now = wheelValue(wheel) ?: return null
            if (now != value) return false
            if (SystemClock.uptimeMillis() >= until) return true
            SystemClock.sleep(PICKER_POLL_MS)
        }
    }

    /**
     * The one `NumberPicker` node whose on-screen bounds are `bounds` and whose identity is `want`,
     * searching every window but SystemUI's, as [matchingBounds] does. The dump reports bounds
     * clipped to the window, so a node matches on its window-clipped bounds as well as its raw ones.
     * Two such nodes (a dialog's wheel over the screen's own) answer [WheelSearch.Ambiguous] rather
     * than a guess, the same refusal the host gives an ambiguous selector.
     */
    private fun pickerWheelAt(bounds: Rect, want: List<String>): WheelSearch {
        val automation = InstrumentationRegistry.getInstrumentation().uiAutomation
        val found = mutableListOf<AccessibilityNodeInfo>()
        var visited = 0
        for (window in automation.windows) {
            val root = window.root ?: continue
            val pkg = root.packageName?.toString()
            if (pkg != null && pkg in SYSTEM_DECOR_PACKAGES) continue
            val windowBounds = Rect().also(window::getBoundsInScreen)
            val pending = ArrayDeque(listOf(root))
            while (pending.isNotEmpty()) {
                if (visited++ >= FOCUS_WALK_LIMIT) return WheelSearch.TooLarge
                val node = pending.removeFirst()
                val raw = Rect().also(node::getBoundsInScreen)
                val clipped = Rect(raw).also { it.intersect(windowBounds) }
                if ((raw == bounds || clipped == bounds) && identityOf(node) == want) found.add(node)
                for (i in 0 until node.childCount) node.getChild(i)?.let(pending::addLast)
            }
        }
        return when {
            found.size > 1 -> WheelSearch.Ambiguous(found.size)
            found.isEmpty() || found[0].className?.toString() != NUMBER_PICKER_CLASS -> WheelSearch.Absent
            else -> WheelSearch.Found(found[0])
        }
    }

    /** A node's four identity fields, in [IDENTITY_FIELDS] order, as the dump would report them. */
    private fun identityOf(node: AccessibilityNodeInfo): List<String> = listOf(
        node.viewIdResourceName.orEmpty(),
        node.contentDescription?.toString().orEmpty(),
        node.text?.toString().orEmpty(),
        node.className?.toString().orEmpty(),
    )

    /**
     * The row a wheel shows as selected: the text of its `numberpicker_input` child. Null when the
     * child is missing, gone, or carries no text, so an unreadable row never reads as an empty label.
     */
    private fun wheelValue(wheel: AccessibilityNodeInfo): String? {
        for (i in 0 until wheel.childCount) {
            val child = wheel.getChild(i) ?: continue
            if (child.viewIdResourceName == NUMBER_PICKER_INPUT_ID) {
                // The child can come from the connection's node cache, whose text may predate the step.
                if (!child.refresh()) return null
                return child.text?.toString()
            }
        }
        return null
    }

    private fun walkDeadlineMs(): Long = MAX_PICKER_ROWS * PICKER_ROW_BUDGET_MS

    private fun refusePicker(out: OutputStream, reason: String, detail: String) =
        respond(out, UNPROCESSABLE_STATUS, TEXT, "$reason: $detail\n".bytes())

    /**
     * Enter text into the field holding input focus, for what `adb shell input text` cannot carry.
     *
     * `input` maps each character to key events through the virtual keyboard's character map, which
     * has no key for a non-ASCII character. `ACTION_SET_TEXT` hands the field its whole new value, so
     * no key map is involved, and it needs no IME installed or switched — a device-wide setting a test
     * tool should not touch. The action replaces the content where typing inserts, so the new value is
     * built here from the field's current text and selection, then the caret is put back after the
     * inserted text. It reaches the field directly and exercises no IME; that is the accepted limit.
     *
     * A password field is refused rather than attempted: its accessibility text is masked or absent, so
     * nothing here can tell what the action would overwrite. So is a field without the action. Each
     * refusal is a non-200 the host fails the step on, and no reply ever quotes the text.
     */
    private fun respondType(
        out: OutputStream,
        device: UiDevice,
        readMark: ReadMark,
        target: String,
        encoded: String,
    ) {
        val text = try {
            String(Base64.decode(encoded, Base64.NO_WRAP), StandardCharsets.UTF_8)
        } catch (e: IllegalArgumentException) {
            Log.w(TAG, "a /type body was not base64", e)
            return respond(out, BAD_REQUEST, TEXT, "body is not base64\n".bytes())
        }
        // The new value is built from this read and replaces the whole field, so a read older than the
        // previous step's text would erase that text. The host's `since` mark predates that step, so
        // an event newer than it proves the app has started publishing the text; idle then lets the
        // publishing finish, and the `refresh` in `awaitInputFocus` keeps a cached node from answering.
        val budget = WaitBudget("type", device, budgetOf(target))
        budget.awaitIdle()
        sinceOf(target)?.let {
            readMark.awaitPostdate(it, POSTDATE_BUDGET_MS)
            budget.lap("postdate")
            budget.awaitIdle()
        }
        budget.report()
        val field = awaitInputFocus()
            ?: return respond(out, STALE_STATUS, TEXT, "no field holds input focus\n".bytes())
        val refusal = when {
            field.isPassword -> "the focused field is a password field\n"
            !field.isEditable ->
                "the focused node (${field.className} in ${field.packageName}) is not editable\n"
            AccessibilityNodeInfo.AccessibilityAction.ACTION_SET_TEXT !in field.actionList ->
                "the focused field does not accept ACTION_SET_TEXT\n"
            else -> null
        }
        if (refusal != null) return respond(out, UNPROCESSABLE_STATUS, TEXT, refusal.bytes())
        // An empty field can report its hint as its text; that hint must not end up in the value.
        val current = if (field.isShowingHintText) "" else field.text?.toString().orEmpty()
        // -1 is the platform's "no selection known", so such a field appends.
        val anchor = field.textSelectionStart.takeIf { it in 0..current.length } ?: current.length
        val focus = field.textSelectionEnd.takeIf { it in 0..current.length } ?: anchor
        val from = minOf(anchor, focus)
        val updated = current.substring(0, from) + text + current.substring(maxOf(anchor, focus))
        val set = field.performAction(
            AccessibilityNodeInfo.ACTION_SET_TEXT,
            Bundle().apply {
                putCharSequence(AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE, updated)
            },
        )
        if (!set) return respond(out, INJECT_FAILED_STATUS, TEXT, "ACTION_SET_TEXT was rejected\n".bytes())
        val caret = from + text.length
        // The action leaves the caret at the end, so only an insertion before the end needs moving it.
        // The text is in by now, so a refused move is logged rather than reported as a failed entry.
        if (caret != updated.length && !setCaret(field, caret)) {
            Log.w(TAG, "the field refused ACTION_SET_SELECTION; its caret stays at the end")
        }
        respond(out, "200 OK", TEXT, "typed\n".bytes())
    }

    /**
     * The freshly read node holding input focus, once it is editable; at the deadline, whatever holds
     * focus by then, for the caller to refuse by name.
     *
     * Waiting for an editable node rather than any node: a tap's focus arrives after the tap, so until
     * then the node holding focus can be the one from before it, which is not the field being typed in.
     */
    private fun awaitInputFocus(): AccessibilityNodeInfo? {
        val automation = InstrumentationRegistry.getInstrumentation().uiAutomation
        val deadline = SystemClock.uptimeMillis() + FOCUS_BUDGET_MS
        while (true) {
            val node = automation.findFocus(AccessibilityNodeInfo.FOCUS_INPUT)
            node?.refresh()
            if (node?.isEditable == true) return node
            focusInTopApplicationWindow(automation)?.let { return it }
            if (SystemClock.uptimeMillis() >= deadline) {
                val windows = automation.windows.joinToString {
                    "${it.root?.packageName}(type=${it.type} active=${it.isActive} focused=${it.isFocused})"
                }
                Log.w(TAG, "no editable node held input focus; windows, front to back: $windows")
                return node
            }
            SystemClock.sleep(FOCUS_POLL_MS)
        }
    }

    /**
     * The editable node holding input focus in the frontmost application window.
     *
     * `UiAutomation.findFocus` searches the *active* window, and while the soft keyboard comes up that
     * can be the keyboard's own: CI saw a `FrameLayout` in `com.google.android.inputmethod.latin`
     * answer for seconds after the keyboard had opened on the field. Asking the frontmost application
     * window skips the keyboard, and a dialog is an application window too, so a field left focused
     * beneath one is never typed into.
     */
    private fun focusInTopApplicationWindow(automation: UiAutomation): AccessibilityNodeInfo? {
        val window = automation.windows.firstOrNull {
            it.type == AccessibilityWindowInfo.TYPE_APPLICATION
        } ?: return null
        val root = window.root ?: return null
        root.findFocus(AccessibilityNodeInfo.FOCUS_INPUT)?.let { node ->
            node.refresh()
            if (node.isEditable) return node
        }
        return focusedEditableIn(root)
    }

    /**
     * The editable node under [root] that reports itself focused, read fresh from the app.
     *
     * `findFocus` answers from the accessibility cache, which keeps one input-focus position for all
     * windows and moves it only on a focus event. CI saw that position stay on a node that was not the
     * field for the whole focus budget, while the field held focus and the keyboard was open on it
     * (#2385), so every retry got the same stale answer. `refresh` fetches each node's state from the
     * app's own process, so this walk sees the field's focus whatever the cache holds.
     */
    private fun focusedEditableIn(root: AccessibilityNodeInfo): AccessibilityNodeInfo? {
        val pending = ArrayDeque<AccessibilityNodeInfo>().apply { add(root) }
        var visited = 0
        while (pending.isNotEmpty() && visited < FOCUS_WALK_LIMIT) {
            val node = pending.removeFirst()
            visited++
            if (!node.refresh()) continue
            if (node.isEditable && node.isFocused) return node
            for (i in 0 until node.childCount) node.getChild(i)?.let(pending::addLast)
        }
        return null
    }

    private fun setCaret(field: AccessibilityNodeInfo, caret: Int): Boolean =
        field.performAction(
            AccessibilityNodeInfo.ACTION_SET_SELECTION,
            Bundle().apply {
                putInt(AccessibilityNodeInfo.ACTION_ARGUMENT_SELECTION_START_INT, caret)
                putInt(AccessibilityNodeInfo.ACTION_ARGUMENT_SELECTION_END_INT, caret)
            },
        )

    /**
     * Answer a gesture that landed: its publish confirmation, and the caught-up tree when there is one.
     *
     * The tree rides along only where the publish was confirmed (BE-0407 unit 19). That is the whole
     * safety argument: a confirmed publish means an accessibility event postdates the injection, so a
     * dump taken now cannot describe the pre-gesture screen — the same claim [publishHeader] already
     * licenses the host to skip its read-lag barrier on. Unconfirmed, the dump could be the old
     * screen, and handing one over would turn a barrier the host still arms into a stale tree it
     * trusts. So an unconfirmed gesture answers exactly as it did before this unit.
     *
     * The saving is a whole round trip: the host's own `_settle` opens with a read it can now skip,
     * measured by the investigation at 400-600ms on this backend.
     *
     * A host that says `tree=0` gets the bare `ok` instead, and the dump is never taken. That is the
     * `nativeZ` target's case: the reply cannot carry that reading, so a tree it seeded would report
     * every position as absent, and building one anyway would spend a settled dump per gesture on a
     * body the host discards.
     */
    private fun respondLanded(
        out: OutputStream,
        device: UiDevice,
        readMark: ReadMark,
        injectedAt: Long,
        wantTree: Boolean,
    ) {
        val published = publishHeader(readMark, injectedAt)
        if (published.isEmpty() || !wantTree) return respond(out, "200 OK", TEXT, "ok\n".bytes())
        // Snapshot before the settle, for the reason [respondSource] states at length: the mark must
        // never outrun the body it is stamped on, or a stale tree would certify as caught up.
        val mark = readMark.current()
        val budget = WaitBudget("act tree", device, ACT_TREE_BUDGET_MS)
        val body = settledDump(device, budget)
        budget.report()
        respond(
            out,
            "200 OK",
            "application/xml; charset=utf-8",
            body,
            published + (READ_MARK_HEADER to mark.toString()),
        )
    }

    /**
     * [ACT_PUBLISH_HEADER] naming the event that postdates `injectedAt`, or no header when none came.
     *
     * The question the host cannot answer for itself: *has the gesture just injected already reached
     * the accessibility tree?* Answering it here is the point — this session is already observing the
     * event stream the answer lives in ([ReadMark]), where the host can only infer it by re-reading
     * trees a round trip away. A host told "yes" needs no read-lag barrier for this gesture at all;
     * one told nothing arms it exactly as before (BE-0339 Unit 5).
     *
     * Absence is the honest answer for both causes it cannot separate — a gesture that published
     * nothing because it moved no frame, and one whose publish outran [ACT_PUBLISH_BUDGET_MS] — and
     * it is also what an older server without this header returns, so the host needs no version
     * negotiation to read it: a header is a confirmation and nothing else is.
     *
     * `injectedAt` is taken *before* the injection, not after, which is the same mark the host's own
     * barrier anchors on (`AdbDriver._capture_mark`, BE-0332 Unit 3) and confirms the same thing it
     * does: an accessibility event postdates the gesture, not that the particular property a step
     * reads has been republished. Taking it after would drop an event a still-running injection
     * already published — a press-and-hold occupies its whole duration inside the call — and report
     * unconfirmed for a gesture that had in fact landed.
     */
    private fun publishHeader(readMark: ReadMark, injectedAt: Long): Map<String, String> {
        readMark.awaitPostdate(injectedAt.toDouble(), ACT_PUBLISH_BUDGET_MS)
        val published = readMark.current()
        return if (published > injectedAt) mapOf(ACT_PUBLISH_HEADER to published.toString()) else emptyMap()
    }

    /**
     * Two taps whose separation is *stated*, not hoped for — the whole reason this gesture is here.
     *
     * Every earlier recipe left the inter-tap gap to something incidental and bet it would land inside
     * the platform's double-tap window: `input tap ; input tap` paid a JVM startup between the taps
     * (BE-0210), the rooted `sendevent` sequence pays five process spawns (BE-0208), and two
     * [UiDevice.click] calls pay `click`'s internal settle. All three pass on a fast host and fail on a
     * loaded one, which is the shape of a flake rather than a bug — the CI emulator failed with the
     * touches visibly landing and the app treating them as two single taps.
     *
     * Building the events here fixes that: [MotionEvent.getEventTime] is what the platform's detector
     * measures, and these timestamps are chosen rather than observed. The sleep is not a wait for a
     * condition — nothing is being polled — it is the gesture's own duration, the same way a long
     * press holds for the duration it was asked for; it keeps the real interval and the stamped one
     * honest for a detector that consults either.
     *
     * Returns true only when both contacts were accepted by the platform; see [respondAct]'s `landed`
     * check.
     */
    private fun injectDoubleTap(x: Int, y: Int): Boolean {
        val automation = InstrumentationRegistry.getInstrumentation().uiAutomation
        val first = SystemClock.uptimeMillis()
        val firstOk = injectTap(automation, first, x, y)
        SystemClock.sleep(TAP_HOLD_MS + INTER_TAP_MS)
        val secondOk = injectTap(automation, first + TAP_HOLD_MS + INTER_TAP_MS, x, y)
        return firstOk && secondOk
    }

    /** One down/up contact at `x`,`y`, stamped from `downTime` and held for [TAP_HOLD_MS]. */
    private fun injectTap(automation: UiAutomation, downTime: Long, x: Int, y: Int): Boolean {
        val down = inject(automation, MotionEvent.ACTION_DOWN, downTime, downTime, x, y)
        val up = inject(automation, MotionEvent.ACTION_UP, downTime, downTime + TAP_HOLD_MS, x, y)
        return down && up
    }

    private fun inject(
        automation: UiAutomation,
        action: Int,
        downTime: Long,
        eventTime: Long,
        x: Int,
        y: Int,
    ): Boolean {
        val event = MotionEvent.obtain(downTime, eventTime, action, x.toFloat(), y.toFloat(), 0)
        event.source = InputDevice.SOURCE_TOUCHSCREEN
        return try {
            // Synchronous: the call returns once the event has been dispatched, so the second contact
            // cannot overtake the first and invert the pair the detector is trying to read.
            automation.injectInputEvent(event, true)
        } finally {
            event.recycle()
        }
    }

    /**
     * Every app-window node in `xml` whose four identity fields equal `want`, in document order, as
     * `[left, top, right, bottom]`.
     *
     * Bounds come from this dump, not from the host's, so the gesture lands where the element is now.
     * `dumpWindowHierarchy` emits one top-level `<node>` per window; a SystemUI window (and everything
     * under it) is skipped, the same filter the host applies before it counts matches
     * (`narrowed_root`) — without it, an unlabeled Compose node whose identity happens to
     * collide with an equally bare SystemUI container (empty `resource-id`/`content-desc`/`text`, a
     * generic `class`) would count nodes here that the host's narrowed copy never saw, so `count`
     * never agrees and every such gesture answers stale on every attempt.
     */
    private fun matchingBounds(xml: ByteArray, want: List<String>): List<Rect> {
        val found = mutableListOf<Rect>()
        val parser = Xml.newPullParser()
        parser.setInput(xml.inputStream(), "UTF-8")
        var nodeDepth = 0
        var decorNodeDepth: Int? = null
        while (parser.next() != XmlPullParser.END_DOCUMENT) {
            when (parser.eventType) {
                XmlPullParser.START_TAG -> {
                    if (parser.name != "node") continue
                    nodeDepth++
                    if (nodeDepth == 1) {
                        val pkg = parser.getAttributeValue(null, "package")
                        if (pkg != null && pkg in SYSTEM_DECOR_PACKAGES) decorNodeDepth = nodeDepth
                    }
                    if (decorNodeDepth != null) continue
                    val have = IDENTITY_FIELDS.map { parser.getAttributeValue(null, it.second) ?: "" }
                    if (have == want) parseBounds(parser.getAttributeValue(null, "bounds"))?.let(found::add)
                }
                XmlPullParser.END_TAG -> {
                    if (parser.name != "node") continue
                    if (decorNodeDepth == nodeDepth) decorNodeDepth = null
                    nodeDepth--
                }
            }
        }
        return found
    }

    /** `[l,t][r,b]` as a [Rect], or null when the attribute is missing or malformed. */
    private fun parseBounds(raw: String?): Rect? {
        val nums = Regex("-?\\d+").findAll(raw ?: "").map { it.value.toInt() }.toList()
        return if (nums.size == 4) Rect(nums[0], nums[1], nums[2], nums[3]) else null
    }

    private fun String.bytes(): ByteArray = toByteArray(StandardCharsets.UTF_8)

    private fun respondSource(out: OutputStream, device: UiDevice, readMark: ReadMark, target: String) {
        val since = sinceOf(target)
        // dumpWindowHierarchy traverses every window, so this XML also carries the SystemUI status
        // bar (clock, wifi, battery, notification icons — 29 nodes) that the platform `uiautomator
        // dump` omits by scoping to the active window. `parse_hierarchy` parses the format unchanged.
        val budget = WaitBudget("source", device, budgetOf(target))
        budget.awaitIdle()
        if (since != null) {
            // The mark-anchored barrier (BE-0332 Unit 4): block until an accessibility event postdates
            // the mark the host took before its gesture, then drain any it triggered. A genuine
            // condition wait — it releases the instant such an event arrives, and the budget only caps
            // a lag that never comes (never a fixed sleep — Bajutsu determinism). On expiry the latest
            // tree is dumped anyway, carrying its own (still-stale) mark for the host to judge.
            readMark.awaitPostdate(since, POSTDATE_BUDGET_MS)
            budget.lap("postdate")
            budget.awaitIdle()
        }
        // Snapshot the read mark *before* the settle: the invariant the host relies on is
        // `mark > actuation_mark` ⟹ body is post-gesture, and that holds only when the body is at
        // least as fresh as the mark. `settledDump` returns its *last* dump, so any event landing
        // during the settle only makes the body fresher than this mark — the mark still *undercounts*
        // events relative to the body, and the host waits for a fresher read rather than trusting a
        // stale one. Reading the mark after the settle would instead let such an event push the mark
        // above the body's freshness, certifying a stale tree as caught up. In the mark path
        // `awaitPostdate` has already advanced `current()` past `since`, so this undercount never
        // drops the mark back below the actuation it must clear.
        val mark = readMark.current()
        val body = settledDump(device, budget)
        budget.report()
        val headers = mutableMapOf(READ_MARK_HEADER to mark.toString())
        // Only when the host asked (BE-0407 unit 18). The walk covers every node on every read —
        // 20-100ms — and answers nothing at all for an app that opted no view into `nativeZ`, which
        // is every app but the ones BE-0355 was built for. The host asks on behalf of a target that
        // says so in its own config, so the cost lands on the runs that read the value and on no
        // others; an older host that never sends the parameter simply stops paying for a reading it
        // was already discarding.
        if (paramOf(target, "nativeZ") == "1") {
            nativeZHeader(device)?.let { headers[NATIVE_Z_HEADER] = it }
        }
        respond(
            out,
            "200 OK",
            "application/xml; charset=utf-8",
            body,
            headers,
        )
    }

    /**
     * The `nativeZ` each opted-in view reports, as a header value the XML body stays untouched by.
     *
     * `dumpWindowHierarchy` serializes in one platform call and hands out no per-node
     * [AccessibilityNodeInfo] to refresh, so the values come from a second walk. That walk covers the
     * active window while the body spans every window, so the two disagree on both length and
     * offset — a value keyed by document-order position would land on the wrong node (BE-0355 Unit 0
     * measured the gap at 28 nodes on a plain showcase screen). Each value is therefore keyed by what
     * the host can recompute from the `<node>` it is reading: bounds, class, package, and the
     * occurrence index among nodes agreeing on all three. The bounds half of that key must be the
     * screen-clipped rect [AccessibilityNodeInfoDumper] itself writes to the `bounds` attribute
     * (`AccessibilityNodeInfoHelper.getVisibleBoundsInScreen`'s node-rect-intersected-with-display
     * behavior), not the raw [AccessibilityNodeInfo.getBoundsInScreen] — a node hanging past the
     * display edge would otherwise key on a rect the host never recomputes, silently reporting no
     * position for exactly the clipped/occluded layouts an investigator opens the evidence for. This
     * walk only reproduces the display-edge half of that clipping, not clipping by an ancestor's own
     * bounds (e.g. a node inside a smaller scrollable container that never reaches the screen edge) —
     * a narrower gap than the one this closes, left beside the multi-window limitation already
     * recorded in `_native_z_key`'s docstring on the host.
     *
     * Only nodes whose own [AccessibilityNodeInfo.getAvailableExtraData] already lists the key are
     * asked for a value; the walk itself still runs over every node on every read, so it is not
     * bounded to opted-in apps the way the per-node check alone would suggest.
     */
    private fun nativeZHeader(device: UiDevice): String? =
        try {
            nativeZHeaderOrThrow(device)
        } catch (e: Exception) {
            // A node can go stale mid-walk (the live tree this runs against right after
            // `settledDump` is not guaranteed frozen), and this diagnostic reading rides on
            // `/source`'s own response — a fault here must degrade to the same honest absence a
            // non-cooperating app reports, not cost the caller the hierarchy read it actually
            // depends on.
            Log.w(TAG, "nativeZ walk failed; reporting no positions for this read", e)
            null
        }

    private fun nativeZHeaderOrThrow(device: UiDevice): String? {
        val root = InstrumentationRegistry.getInstrumentation().uiAutomation.rootInActiveWindow
            ?: return null
        val display = Rect(0, 0, device.displayWidth, device.displayHeight)
        val seen = mutableMapOf<String, Int>()
        val parts = mutableListOf<String>()
        fun visit(node: AccessibilityNodeInfo?) {
            if (node == null) return
            val bounds = Rect().also { node.getBoundsInScreen(it) }
            // `Rect.intersect` leaves a fully off-screen rect unchanged rather than collapsing it
            // to empty, matching `getVisibleBoundsInScreen`'s own behavior — the dumped body keys
            // such a node on its raw (off-screen) rect too, so this stays consistent with it.
            bounds.intersect(display)
            val key = "${bounds.left},${bounds.top},${bounds.right},${bounds.bottom}" +
                "|${node.className ?: ""}|${node.packageName ?: ""}"
            val occurrence = seen.getOrDefault(key, 0)
            seen[key] = occurrence + 1
            if (node.availableExtraData.orEmpty().contains(NATIVE_Z_KEY)) {
                // An opted-in view can answer either way: the app-side library sets the value while
                // the node is being built, and a view that instead overrides the platform's
                // on-demand callback answers only when asked. Read the value already here first, so
                // the common case costs no round trip, and ask only when it is absent.
                var z = node.extras.getFloat(NATIVE_Z_KEY, Float.NaN)
                if (z.isNaN() && node.refreshWithExtraData(NATIVE_Z_KEY, Bundle())) {
                    z = node.extras.getFloat(NATIVE_Z_KEY, Float.NaN)
                }
                if (!z.isNaN()) parts.add("$key|$occurrence=$z")
            }
            for (i in 0 until node.childCount) visit(node.getChild(i))
        }
        visit(root)
        if (parts.isEmpty()) return null
        val header = parts.joinToString(";")
        // A header field is not a body: past a few hundred views the value outgrows what an HTTP
        // header should carry, and `nativeZ` is diagnostic — dropping it costs a reading, while
        // truncating it would hand some node another's.
        return if (header.length > NATIVE_Z_HEADER_MAX) null else header
    }

    /**
     * Re-dump across [UiDevice.waitForIdle] until two consecutive `read()`s agree per `sameAs`, or
     * [SETTLE_DUMPS] is reached. [settledDump] and [settledActBounds] both close *tearing* this way —
     * the window where Android has republished some node bounds but not the rest, so a lone dump
     * captures a half-updated tree — and differ only in what they read and how they compare it.
     *
     * This does **not** decide *staleness* (whether the read postdates a gesture); that is the mark
     * gate's job (BE-0332 Units 3 and 4), which runs in [respondSource]/[respondAct] before either calls
     * in here. `read`/`sameAs` must stay scoped to wholeness only — folding a freshness/mark check into
     * either would blur that split; the mark check belongs in the caller, before `settled` is invoked,
     * as both callers already do. `waitForIdle` alone is the mechanism this timing class distrusts (it
     * can look idle between two node republishes), which is why the barrier is two agreeing reads, not
     * one idle. Bounded and condition-driven (settle, not a fixed sleep — Bajutsu determinism); a read
     * that never settles (an animation) costs at most [SETTLE_DUMPS] reads and returns the last one,
     * exactly as the host's `AdbDriver._CATCHUP_DWELL_S` accepts a tear that outlasts its dwell.
     */
    private fun <T> settled(budget: WaitBudget, label: String, read: () -> T, sameAs: (T, T) -> Boolean): T {
        budget.capDumpWaits()
        var previous = read()
        budget.lap("dump")
        repeat(SETTLE_DUMPS - 1) {
            // The tree in hand is the honest answer once the budget is gone: the host's own condition
            // wait reads again, and its timeout is what a late answer would otherwise lose to.
            if (budget.spent()) {
                Log.d(TAG, "$label stopped settling when its wait budget ran out")
                return previous
            }
            budget.awaitIdle()
            budget.capDumpWaits()
            val current = read()
            budget.lap("dump")
            if (sameAs(current, previous)) return current
            previous = current
        }
        return previous.also { Log.d(TAG, "$label did not settle after $SETTLE_DUMPS dumps") }
    }

    /**
     * Dump the hierarchy once it has stopped tearing, comparing the whole dumped XML. Used by
     * [respondSource], which answers an arbitrary host-side selector and so cannot narrow the
     * comparison the way [settledActBounds] does for `/act`.
     */
    private fun settledDump(device: UiDevice, budget: WaitBudget): ByteArray =
        settled(budget, "hierarchy", { dumpHierarchy(device) }, ByteArray::contentEquals)

    /**
     * Like [settledDump], but scoped to the identity `/act` has already resolved: settles once two
     * consecutive reads agree on `want`'s matched bounds, rather than on the whole dumped XML.
     *
     * `respondAct` already knows: `want` names the one element it is about to inject a touch into.
     * Comparing only its bounds — instead of the whole tree — means an unrelated node still changing
     * elsewhere on screen (the status bar clock every dump carries, a spinner, a blinking cursor) no
     * longer forces every gesture to pay the full settle budget once the target's own bounds have
     * stopped moving.
     *
     * `read` re-parses [matchingBounds] only when the raw dump actually changed since the last read —
     * a byte-identical dump can only re-derive the same bounds, so the cheap [ByteArray.contentEquals]
     * check skips the [XmlPullParser] walk on a screen that has genuinely gone idle, rather than paying
     * it on every settle attempt.
     */
    private fun settledActBounds(device: UiDevice, want: List<String>, budget: WaitBudget): List<Rect> {
        var seenXml: ByteArray? = null
        var seenBounds = emptyList<Rect>()
        return settled(budget, "act bounds", {
            val xml = dumpHierarchy(device)
            if (seenXml?.contentEquals(xml) != true) {
                seenBounds = matchingBounds(xml, want)
                seenXml = xml
            }
            seenBounds
        }) { a, b -> a == b }
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
            // The connection stays open for the next request on it (BE-0407 unit 21); `Content-Length`
            // above is what lets the client find where this reply ends, so the framing needs nothing
            // more than the header it already sent.
            append("Connection: keep-alive\r\n")
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
        // host side (`bajutsu/common/backend_cli/adb_resident.py` `_READ_MARK_HEADER`).
        const val READ_MARK_HEADER = "X-Bajutsu-Read-Mark"

        // The response header GET /source stamps with each opted-in view's own `View.getZ()`
        // (BE-0355 Unit 3). Carried in a header for the same reason the read mark is: the XML body
        // stays byte-identical to `uiautomator dump`'s, so `parse_hierarchy` is unchanged. Kept in
        // sync with the host side (`bajutsu/common/backend_cli/adb_resident.py` `_NATIVE_Z_HEADER`).
        const val NATIVE_Z_HEADER = "X-Bajutsu-Native-Z"

        // The response header POST /act stamps when an accessibility event postdated the injection it
        // just made (BE-0339 Unit 5): the device-clock time of that event. Its *absence* is the answer
        // when none came, which is also what an older server returns — see `publishHeader`. Kept in
        // sync with the host side (`bajutsu/common/backend_cli/adb_resident.py` `_ACT_PUBLISH_HEADER`).
        const val ACT_PUBLISH_HEADER = "X-Bajutsu-Act-Publish"

        // How long POST /act waits for its own gesture to publish before answering unconfirmed.
        //
        // Not a correctness bound — but not free either, and the difference is what sizes it. The
        // whole window is spent before the reply goes out, so a gesture that publishes nothing pays it
        // *and then* still arms the read-lag barrier the host armed for every gesture before this
        // change. The window therefore trades a fixed cost on the gestures that confirm nothing
        // against a barrier saved on the ones that do; what it never decides is whether skipping that
        // barrier is safe.
        //
        // An eighth of the host's own budget (`AdbDriver._READ_LAG_S`, 4 s), because the two waits are
        // not interchangeable: this one is paid inline on every gesture, while the host's is spent
        // lazily and is often absorbed by reads the scenario was taking anyway. Widening it to catch
        // more publishes is therefore not free — it lengthens every gesture that confirms none, and a
        // gesture that moves no frame is routine (`AdbDriver._await_catchup` says so at its timeout).
        const val ACT_PUBLISH_BUDGET_MS = 500L

        // The extra-data key an opted-in view advertises. This server is app-independent by design
        // (see settings.gradle.kts), so it names the key rather than depending on the app-side
        // library that answers it — kept in sync with `BajutsuAndroid`'s `BajutsuZOrder` and with
        // `bajutsu/common/drivers/adb.py`.
        const val NATIVE_Z_KEY = "dev.bajutsu.EXTRA_DATA_NATIVE_Z"

        // Past this the reading is dropped rather than truncated (see `nativeZHeader`).
        const val NATIVE_Z_HEADER_MAX = 8_000

        // How long GET /source?since= waits for an event to postdate the requested mark before dumping
        // the latest tree anyway (BE-0332 Unit 4). A ceiling on the read lag, not a fixed delay: the
        // wait releases the instant an event arrives past the mark. Kept well inside the host's
        // `fetch_source` 5 s HTTP timeout, and shorter than the host's own read-lag budget
        // (`AdbDriver._READ_LAG_S`, 4 s) so the host re-poll stays the outer bound. The read-lag
        // investigation saw the post-gesture update land within ~2 s (BE-0332 Motivation).
        const val POSTDATE_BUDGET_MS = 2_000L

        // What one request may wait when the host names no budget. `adb_resident/budgets.py` is where
        // these numbers are chosen and why they fit the host's timeouts, and a host test holds this
        // file to it (`tests/test_adb_resident_budgets.py`).
        const val DEFAULT_WAIT_BUDGET_MS = 3_500L

        // The most any one idle wait may take. A screen that is not idle after a second will not be
        // idle after ten for this request's purposes; the budget is better left for the mark.
        const val IDLE_WAIT_MS = 1_000L

        // `dumpWindowHierarchy` waits for idle this many times inside itself (measured: see
        // [WaitBudget]), so a dump's share of what is left is divided by it.
        const val WAITS_PER_DUMP = 2L

        // What the tree a confirmed gesture carries back may wait, spent after the gesture landed.
        const val ACT_TREE_BUDGET_MS = 1_500L

        // Max reads per `settled` call — shared by `settledDump` (whole hierarchy) and `settledActBounds`
        // (one element's bounds) — the tearing barrier layered under the mark gate (BE-0332 Unit 4). A
        // settled read matches on the 2nd attempt; the headroom absorbs a republish that lands mid-read
        // without letting an animated value spin forever. Same value the retired `stableHierarchy` used,
        // now scoped to tearing while the mark decides staleness.
        const val SETTLE_DUMPS = 4

        // Read so a body can be taken off a reused connection; see `readRequest`.
        const val CONTENT_LENGTH_HEADER = "content-length:"
        const val DRAIN_CHUNK = 4096

        const val TEXT = "text/plain; charset=utf-8"
        const val BAD_REQUEST = "400 Bad Request"

        // A resolved-but-changed target: the identity the host sent no longer names the same number of
        // nodes here, so the screen moved between its resolve and this call. 409 rather than 404 —
        // nothing is missing, the state simply conflicts — and the host answers by re-resolving, as the
        // XCUITest channel does for its own stale handles (BE-0289).
        const val STALE_STATUS = "409 Conflict"

        // The platform rejected the injection outright (`UiDevice.click` / `.swipe` /
        // `UiAutomation.injectInputEvent` returned false) — the gesture never reached the screen, so it
        // is safe to answer loudly rather than claim `200 OK` on a touch that did not land. The host's
        // `adb_resident.act` treats any non-{200,404,409} status as `AdbResidentError`, which degrades
        // this one gesture to the coordinate path without latching the channel — exactly right here,
        // since nothing was injected and a coordinate retry is not a second touch.
        const val INJECT_FAILED_STATUS = "500 Internal Server Error"

        // The device understood a `/type` request and will not perform it on this field. Any non-200
        // fails the step on the host, so the distinct status only keeps the wire honest.
        const val UNPROCESSABLE_STATUS = "422 Unprocessable Entity"

        // How long `/type` waits for input focus to arrive after the tap that asked for it.
        const val FOCUS_BUDGET_MS = 2_000L
        const val FOCUS_POLL_MS = 20L

        // How many nodes the cache-free focus walk reads before giving up; an app screen holds far
        // fewer, so the bound only stops a pathological tree from eating the focus budget.
        const val FOCUS_WALK_LIMIT = 2_000

        // The element identity the host addresses a gesture by, as (query parameter, XML attribute).
        // Four fields, none of them a coordinate: the host picks the element, and this names it again
        // against a dump taken here.
        val IDENTITY_FIELDS = listOf(
            "rid" to "resource-id",
            "desc" to "content-desc",
            "text" to "text",
            "cls" to "class",
        )

        // SystemUI owns the status/navigation-bar windows that dumpWindowHierarchy's full tree carries
        // and the platform `uiautomator dump` (active window only) omits. The host drops these before
        // it counts matches (`bajutsu/common/backend_cli/adb_resident.py` narrowed_root, keyed off this same
        // package name) — matchingBounds below must drop them too, or a count taken over the full dump
        // disagrees with one taken over the host's narrowed copy.
        val SYSTEM_DECOR_PACKAGES = setOf("com.android.systemui")

        // The wheel `/setPickerValue` drives, and the child that shows its selected row (BE-0480).
        // Kept in step with the host's `_NUMBER_PICKER_CLASS` (`bajutsu/common/drivers/adb/_functions.py`).
        const val NUMBER_PICKER_CLASS = "android.widget.NumberPicker"
        const val NUMBER_PICKER_INPUT_ID = "android:id/numberpicker_input"

        // The reason tokens a `/setPickerValue` refusal leads its body with; the host maps each to its
        // own error (`adb_resident.set_picker_value`).
        const val NOT_A_PICKER_WHEEL = "not-a-picker-wheel"
        const val PICKER_VALUE_ABSENT = "picker-value-absent"
        const val PICKER_WALK_EXHAUSTED = "picker-walk-exhausted"
        const val PICKER_IMMOVABLE = "picker-immovable"

        // The most rows one `/setPickerValue` steps across both passes. A spinner `DatePicker`'s year
        // wheel spans about 200 rows, so a walk to its far edge and back still fits.
        const val MAX_PICKER_ROWS = 400

        // How long one row's wait gives the wheel to show its next row. `NumberPicker` animates each
        // step for 300ms, and an API 34 arm64 emulator took 142ms to 1469ms per row (median 625ms,
        // 38 rows, read from the `picker row` log line), so the budget sits just above that maximum.
        // A row that outlasts it twice in a row ends the walk as `picker-walk-exhausted`; it never
        // reads as an edge. With the row cap it sets the walk deadline, which `adb_resident/budgets.py`
        // sizes the host timeout by (`tests/test_adb_resident_budgets.py` holds the two ends together).
        const val PICKER_ROW_BUDGET_MS = 1_500L

        // How often a row's wait re-reads the wheel: a poll cadence, not a delay.
        const val PICKER_POLL_MS = 20L

        // How long a landed row must hold before the walk reports it. Longer than `NumberPicker`'s
        // 300ms one-row animation, so a step still in flight shows before the reply goes out.
        const val PICKER_LANDING_HOLD_MS = 400L

        // `UiDevice.swipe` paces a drag in steps of about this long, so a press-and-hold's duration is
        // requested as a step count.
        const val SWIPE_STEP_MS = 10
        const val DEFAULT_LONG_PRESS_MS = 700

        // The double tap's two intervals, both comfortably inside the platform's 300ms window and
        // above Compose's 40ms floor (`doubleTapMinTimeMillis`, which rejects a pair that arrives too
        // fast to be two deliberate taps). Chosen numbers, not measured ones — that is the point.
        const val TAP_HOLD_MS = 40L
        const val INTER_TAP_MS = 60L
    }
}
