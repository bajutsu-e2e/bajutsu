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
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.launch

// Tab: Stable (SPEC §5.1). Catalog list with async load; tapping a row pushes Horse Detail. A deeplink
// to this tab pops it to root (model.stablePath cleared in handleDeepLink).
@Composable
fun StableScreen(model: AppModel) {
    val pushedId = model.stablePath.lastOrNull()
    if (pushedId == null) {
        StableList(model)
    } else {
        // The system Back button pops (BE-0007 drives it by the OS back keyevent); no app back id.
        BackHandler { model.stablePath.removeAt(model.stablePath.lastIndex) }
        HorseDetailScreen(model, pushedId)
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun StableList(model: AppModel) {
    var status by remember { mutableStateOf("idle") }
    val scope = rememberCoroutineScope()

    Column(Modifier.fillMaxSize()) {
        // The nav-bar title carries no id (SPEC §5.1); a screen is confirmed via a content leaf.
        TopAppBar(
            title = { Text("Stable") },
            actions = {
                TextButton(
                    onClick = {
                        status = "loading"
                        scope.launch { status = Net.get(model.apiURL + "/horses") }
                    },
                    modifier = Modifier.aid("stable.refresh"),
                ) { Text("Refresh") }
            },
        )
        LazyColumn(Modifier.weight(1f)) {
            if (model.horses.isEmpty()) {
                item {
                    Text(
                        "No horses",
                        Modifier.padding(16.dp).aid("stable.empty"),
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
            } else {
                items(model.horses, key = { it.id }) { horse ->
                    Text(
                        horse.name,
                        Modifier
                            .fillMaxWidth()
                            .clickable { model.stablePath.add(horse.id) }
                            .padding(16.dp)
                            .aid("stable.row.${horse.id}"),
                    )
                    HorizontalDivider()
                }
            }
        }
        // Status mirrors to stable.status so a scenario can wait on the response before asserting.
        Text(
            "Status: $status",
            Modifier.padding(8.dp).aid("stable.status").stateValue(status),
            style = MaterialTheme.typography.bodySmall,
        )
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun HorseDetailScreen(model: AppModel, id: Int) {
    val horse = model.horse(id)
    var status by remember { mutableStateOf("idle") }
    var favorite by remember { mutableStateOf(false) }
    val scope = rememberCoroutineScope()

    Column(Modifier.fillMaxSize()) {
        TopAppBar(title = { Text(horse?.name ?: "Horse $id") })
        Column(Modifier.padding(16.dp)) {
            // horse.title / horse.id.value are real content (the entity), so they keep their ids
            // even though the nav title does not (SPEC §5.1).
            Text(horse?.name ?: "Horse $id", Modifier.aid("horse.title"), style = MaterialTheme.typography.titleLarge)
            Text("ID: $id", Modifier.aid("horse.id.value").stateValue(id.toString()))

            TextButton(
                onClick = {
                    status = "loading"
                    scope.launch { status = Net.get(model.apiURL + "/horses/$id") }
                },
                modifier = Modifier.aid("horse.fetch"),
            ) { Text("Fetch detail") }
            Text("Status: $status", Modifier.aid("horse.status").stateValue(status))

            // A button-backed toggle; `selected` reflects the state, value mirrors on/off.
            TextButton(
                onClick = { favorite = !favorite },
                modifier = Modifier.aid("horse.favorite").selectedState(favorite),
            ) { Text(if (favorite) "★ Favorite" else "☆ Favorite") }
            Text(
                if (favorite) "Favorited" else "Not favorited",
                Modifier.aid("horse.favorite.value").stateValue(if (favorite) "on" else "off"),
            )
        }
    }
}
