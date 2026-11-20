package com.bajutsu.showcase.compose

import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue

/**
 * The title of the last push bajutsu delivered (BE-0498), shown at `perm.push.received`.
 *
 * Process-wide rather than in [AppModel], because the push handler registers in
 * `Application.onCreate`, before any Activity (and its model) exists. Compose state, so the
 * Permissions screen recomposes when the handler writes it on the main thread.
 */
object PushInbox {
    var lastTitle by mutableStateOf("")
}
