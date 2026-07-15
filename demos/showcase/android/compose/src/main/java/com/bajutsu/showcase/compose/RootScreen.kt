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
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.vector.ImageVector

// The five-tab main UI (SPEC §5). A bottom NavigationBar is the Android analog of the iOS TabView;
// the tabs that push a detail (Stable, Notices) own their own navigation stack (see the screens).

@Composable
fun RootScreen(model: AppModel) {
    // BE-0270: the SHOWCASE_CONFORMANCE launch env swaps the whole five-tab UI for the driver-
    // conformance screen (mirroring the iOS RootView). Reading the observable `conformanceIds` here is
    // what re-renders the screen on a reseed. Otherwise the normal tab app (BE-0079) is untouched.
    model.conformanceIds?.let { ids ->
        ConformanceScreen(ids)
        return
    }
    // BE-0232: the SHOWCASE_GESTURES launch env swaps the whole five-tab UI for the flat pinch/rotate
    // screen (mirroring the iOS RootView). Otherwise the normal tab app (BE-0079) is untouched.
    if (model.gesturesMode) {
        GestureScreen()
        return
    }
    Scaffold(
        modifier = Modifier
            .fillMaxSize()
            .enableTestTagsAsResourceId(),
        bottomBar = {
            NavigationBar {
                tab(model, Tab.STABLE, "stable", "Stable", Icons.Filled.Home)
                tab(model, Tab.SEARCH, "search", "Search", Icons.Filled.Search)
                tab(model, Tab.LOG, "log", "Log", Icons.Filled.Create)
                tab(model, Tab.NOTICES, "notice", "Notices", Icons.Filled.Notifications)
                tab(model, Tab.PERMISSIONS, "perm", "Permissions", Icons.Filled.Lock)
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
private fun RowScope.tab(model: AppModel, tab: Tab, id: String, label: String, icon: ImageVector) {
    NavigationBarItem(
        modifier = Modifier.aid(id),
        selected = model.selectedTab == tab,
        onClick = { model.selectedTab = tab },
        icon = { Icon(icon, contentDescription = label) },
        label = { Text(label) },
    )
}
