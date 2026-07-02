package com.bajutsu.showcase.compose

import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp

// Tab: Search (SPEC §5.2). Filters the shared catalog by name, case-insensitive.
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun SearchScreen(model: AppModel) {
    val matches = model.horses(matching = model.searchQuery)

    Column(Modifier.fillMaxSize()) {
        TopAppBar(title = { Text("Search") })
        Row(Modifier.padding(16.dp)) {
            OutlinedTextField(
                value = model.searchQuery,
                onValueChange = { model.searchQuery = it },
                label = { Text("Search horses") },
                singleLine = true,
                // ASCII keyboard so typed Latin text is not mangled by an active IME (parity with iOS).
                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Ascii),
                modifier = Modifier.weight(1f).aid("search.field"),
            )
            TextButton(onClick = { model.searchQuery = "" }, modifier = Modifier.aid("search.clear")) { Text("Clear") }
        }
        Text(
            "Matches: ${matches.size}",
            Modifier.padding(horizontal = 16.dp).aid("search.count").stateValue(matches.size.toString()),
            style = MaterialTheme.typography.bodySmall,
        )
        LazyColumn(Modifier.weight(1f)) {
            if (matches.isEmpty()) {
                item {
                    Text(
                        "No matches",
                        Modifier.padding(16.dp).aid("search.results-empty"),
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
            } else {
                items(matches, key = { it.id }) { horse ->
                    Text(horse.name, Modifier.fillMaxWidth().padding(16.dp).aid("search.row.${horse.id}"))
                    HorizontalDivider()
                }
            }
        }
    }
}
