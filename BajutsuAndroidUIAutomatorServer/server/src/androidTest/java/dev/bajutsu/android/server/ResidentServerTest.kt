package dev.bajutsu.android.server

import android.app.UiAutomation
import android.os.SystemClock
import android.util.Log
import android.util.Xml
import android.view.InputDevice
import android.view.MotionEvent
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import androidx.test.uiautomator.UiDevice
import java.io.BufferedReader
import java.io.ByteArrayOutputStream
import java.io.OutputStream
import java.net.InetAddress
import java.net.ServerSocket
import java.net.Socket
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

    private fun handle(client: Socket, device: UiDevice, readMark: ReadMark) {
        // A stalled client (slow or incomplete request) must not block the single-threaded accept
        // loop: without a read timeout, readLine() would wait forever and wedge the whole server.
        client.soTimeout = SO_TIMEOUT_MS
        val reader = client.getInputStream().bufferedReader(StandardCharsets.UTF_8)
        val target = readRequestTarget(reader) ?: return
        val out = client.getOutputStream()
        when (target.substringBefore('?')) {
            "/source" -> respondSource(out, device, readMark, sinceOf(target))
            "/act" -> respondAct(out, device, readMark, target)
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
     * the same way the host's `narrow_to_active_window` does, so a node whose bare identity happens to
     * collide with an equally bare SystemUI container is not counted here when the host never counted
     * it either.
     *
     * The win is the gap. Resolving here puts the read and the injection microseconds apart in one
     * process, where the host path spends a round trip plus `adb shell input`'s JVM startup between
     * them — the window in which a still-settling screen moves out from under a computed coordinate.
     * It does not make the accessibility tree itself current: a `since` mark is honored first, exactly
     * as [respondSource] does, so the bounds read here postdate the gesture the host is following up on.
     */
    private fun respondAct(out: OutputStream, device: UiDevice, readMark: ReadMark, target: String) {
        // Validated, never defaulted. A missing or malformed field here would otherwise pick an
        // element by assumption — `index` 0 of `count` 1 — which is exactly the guess this endpoint
        // exists to refuse. An identity field may legitimately be empty (a node with no text), so
        // presence is required and emptiness is not.
        val kind = paramOf(target, "kind") ?: return respond(out, BAD_REQUEST, TEXT, "no kind\n".bytes())
        val index = paramOf(target, "index")?.toIntOrNull()
            ?: return respond(out, BAD_REQUEST, TEXT, "no usable index\n".bytes())
        val count = paramOf(target, "count")?.toIntOrNull()
            ?: return respond(out, BAD_REQUEST, TEXT, "no usable count\n".bytes())
        val want = IDENTITY_FIELDS.map {
            paramOf(target, it.first)
                ?: return respond(out, BAD_REQUEST, TEXT, "no ${it.first}\n".bytes())
        }
        device.waitForIdle()
        sinceOf(target)?.let {
            readMark.awaitPostdate(it, POSTDATE_BUDGET_MS)
            device.waitForIdle()
        }
        val matches = matchingBounds(settledDump(device), want)
        if (matches.size != count || index !in matches.indices) {
            // Loudly stale, never a guess: the screen the host resolved on is not the screen here, so
            // acting on `matches[index]` would be acting on a different element. The host re-resolves.
            return respond(out, STALE_STATUS, TEXT, "stale: ${matches.size} of $count\n".bytes())
        }
        val bounds = matches[index]
        val x = (bounds[0] + bounds[2]) / 2
        val y = (bounds[1] + bounds[3]) / 2
        // Each injector's boolean return is the only signal that the touch actually reached the
        // screen — `UiDevice.click` / `.swipe` and `UiAutomation.injectInputEvent` (via
        // [injectDoubleTap]) all report `false` when the platform rejects the event. Answering
        // `200 OK` regardless would let a rejected injection reach the host as a landed gesture: no
        // step re-resolves or degrades, and only the scenario's own later assertion could ever catch
        // it. A non-200 here surfaces the rejection immediately (see [INJECT_FAILED_STATUS]).
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
        respond(out, "200 OK", TEXT, "ok\n".bytes())
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
     * (`narrow_to_active_window`) — without it, an unlabeled Compose node whose identity happens to
     * collide with an equally bare SystemUI container (empty `resource-id`/`content-desc`/`text`, a
     * generic `class`) would count nodes here that the host's narrowed copy never saw, so `count`
     * never agrees and every such gesture answers stale on every attempt.
     */
    private fun matchingBounds(xml: ByteArray, want: List<String>): List<IntArray> {
        val found = mutableListOf<IntArray>()
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

    /** `[l,t][r,b]` as four ints, or null when the attribute is missing or malformed. */
    private fun parseBounds(raw: String?): IntArray? {
        val nums = Regex("-?\\d+").findAll(raw ?: "").map { it.value.toInt() }.toList()
        return if (nums.size == 4) nums.toIntArray() else null
    }

    private fun String.bytes(): ByteArray = toByteArray(StandardCharsets.UTF_8)

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
        val body = settledDump(device)
        respond(
            out,
            "200 OK",
            "application/xml; charset=utf-8",
            body,
            mapOf(READ_MARK_HEADER to mark.toString()),
        )
    }

    /**
     * Dump the hierarchy once it has stopped *tearing*: re-dump across [UiDevice.waitForIdle] until two
     * consecutive dumps are byte-identical, or [SETTLE_DUMPS] is reached.
     *
     * This closes tearing only — the window where Android has republished some node bounds but not the
     * rest, so a lone dump captures a half-updated tree. It does **not** decide *staleness* (whether the
     * read postdates the gesture); that is the mark gate's job (BE-0332 Units 3 and 4), which has
     * already run in [respondSource] when `since` was set. Layering the two restores both guarantees the
     * retired `stableHierarchy` gave, but now the mark — not a settled-but-late tree — is what certifies
     * freshness, so the read-lag bug BE-0332 fixes stays fixed: a merely-late tree that has stopped
     * changing settles here yet never postdates the mark, so the host still re-polls for it.
     *
     * `waitForIdle` alone is the mechanism this timing class distrusts (it can look idle between two
     * node republishes), which is why the barrier is two matching dumps, not one idle. Bounded and
     * condition-driven (settle, not a fixed sleep — Bajutsu determinism); a node that never settles (an
     * animation) costs at most [SETTLE_DUMPS] dumps and returns the last read. A tear outlasting the
     * bound still gets through, exactly as the host's `_CATCHUP_DWELL_S` accepts one that outlasts its
     * dwell.
     */
    private fun settledDump(device: UiDevice): ByteArray {
        var previous = dumpHierarchy(device)
        repeat(SETTLE_DUMPS - 1) {
            device.waitForIdle()
            val current = dumpHierarchy(device)
            if (current.contentEquals(previous)) return current
            previous = current
        }
        return previous.also { Log.d(TAG, "hierarchy did not settle after $SETTLE_DUMPS dumps") }
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

        // Max dumps per read while `settledDump` waits for two consecutive hierarchies to match — the
        // tearing barrier layered under the mark gate (BE-0332 Unit 4). A settled screen matches on the
        // 2nd dump; the headroom absorbs a republish that lands mid-read without letting an animated
        // node spin forever. Same value the retired `stableHierarchy` used, now scoped to tearing while
        // the mark decides staleness.
        const val SETTLE_DUMPS = 4

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
        // it counts matches (`bajutsu/adb_resident.py` narrow_to_active_window, keyed off this same
        // package name) — matchingBounds below must drop them too, or a count taken over the full dump
        // disagrees with one taken over the host's narrowed copy.
        val SYSTEM_DECOR_PACKAGES = setOf("com.android.systemui")

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
