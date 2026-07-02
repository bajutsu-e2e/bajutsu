package com.bajutsu.showcase.compose

import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.RowScope
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Create
import androidx.compose.material.icons.filled.Home
import androidx.compose.material.icons.filled.Lock
import androidx.compose.material.icons.filled.Notifications
import androidx.compose.material.icons.filled.Search
import androidx.compose.material3.Icon
import androidx.compose.material3.NavigationBar
import androidx.compose.material3.NavigationBarItem
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.ExperimentalComposeUiApi
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.semantics.testTagsAsResourceId

// The five-tab main UI (SPEC §5). A bottom NavigationBar is the Android analog of the iOS TabView;
// the tabs that push a detail (Stable, Notices) own their own navigation stack (see the screens).

@Composable
fun RootScreen(model: AppModel) {
    Scaffold(
        modifier = Modifier
            .fillMaxSize()
            .testTagsAsResourceIdRoot(),
        bottomBar = {
            NavigationBar {
                tab(model, Tab.STABLE, "Stable", Icons.Filled.Home)
                tab(model, Tab.SEARCH, "Search", Icons.Filled.Search)
                tab(model, Tab.LOG, "Log", Icons.Filled.Create)
                tab(model, Tab.NOTICES, "Notices", Icons.Filled.Notifications)
                tab(model, Tab.PERMISSIONS, "Permissions", Icons.Filled.Lock)
            }
        },
    ) { padding ->
        Box(Modifier.padding(padding)) {
            when (model.selectedTab) {
                Tab.STABLE -> StableScreen(model)
                Tab.SEARCH -> SearchScreen(model)
                Tab.LOG -> LogScreen(model)
                Tab.NOTICES -> NoticesScreen(model)
                Tab.PERMISSIONS -> PermissionsScreen(model)
            }
        }
    }
}

@Composable
private fun RowScope.tab(model: AppModel, tab: Tab, label: String, icon: ImageVector) {
    NavigationBarItem(
        selected = model.selectedTab == tab,
        onClick = { model.selectedTab = tab },
        icon = { Icon(icon, contentDescription = label) },
        label = { Text(label) },
    )
}

// Enable testTagsAsResourceId at the content root so every Modifier.aid(...) testTag surfaces as a UI
// Automator `resource-id` (BE-0007's Compose id convention). a11y flavor only.
@OptIn(ExperimentalComposeUiApi::class)
private fun Modifier.testTagsAsResourceIdRoot(): Modifier =
    if (BuildConfig.ACCESSIBLE) this.semantics { testTagsAsResourceId = true } else this
