package com.bajutsu.showcase.views

/**
 * The title of the last push bajutsu delivered (BE-0498), shown at `perm_push_received`.
 *
 * Process-wide, because the push handler registers in `Application.onCreate`, before any Activity
 * exists. The Permissions tab sets [onChange] when it builds its readout, so a push that arrives while
 * the tab is up updates it; the handler runs on the main thread, as the label update needs.
 */
object PushInbox {
    var lastTitle: String = ""
        private set
    var onChange: ((String) -> Unit)? = null

    fun receive(title: String) {
        lastTitle = title
        onChange?.invoke(title)
    }
}
