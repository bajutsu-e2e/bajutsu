package com.bajutsu.showcase.compose

import androidx.activity.compose.BackHandler
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp

// Tab: Notices (SPEC §5.5). A plain vertical list of 20 static notices — intentionally longer than one
// screen, so the bottom rows start off-screen and reaching notice.row.20 is the canonical
// scroll-to-element target. Tapping a row pushes its detail.
@Composable
fun NoticesScreen(model: AppModel) {
    val pushedId = model.noticesPath.lastOrNull()
    if (pushedId == null) {
        NoticesList(model)
    } else {
        BackHandler { model.noticesPath.removeAt(model.noticesPath.lastIndex) }
        NoticeDetailScreen(model, pushedId)
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun NoticesList(model: AppModel) {
    Column(Modifier.fillMaxSize()) {
        TopAppBar(title = { Text("Notices") })
        LazyColumn(Modifier.weight(1f)) {
            items(model.notices, key = { it.id }) { notice ->
                Text(
                    notice.title,
                    Modifier
                        .fillMaxWidth()
                        .clickable { model.noticesPath.add(notice.id) }
                        .padding(16.dp)
                        .aid("notice.row.${notice.id}"),
                )
                HorizontalDivider()
            }
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun NoticeDetailScreen(model: AppModel, id: Int) {
    val notice = model.notice(id)
    Column(Modifier.fillMaxSize()) {
        TopAppBar(title = { Text(notice?.title ?: "Notice $id") })
        Column(Modifier.padding(16.dp)) {
            Text(notice?.title ?: "Notice $id", Modifier.aid("notice.detail.title"), style = MaterialTheme.typography.titleLarge)
            Text(notice?.body ?: "", Modifier.aid("notice.detail.body"), color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
    }
}
