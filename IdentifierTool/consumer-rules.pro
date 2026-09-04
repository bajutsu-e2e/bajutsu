# A Views-only consumer never links androidx.compose.ui (it's compileOnly, in build.gradle.kts), so
# R8's missing-class check would otherwise fail their build over the compose/ package's own
# references to it. Scoped to the two subpackages that package actually touches (platform, semantics)
# plus the top-level symbols it imports directly, rather than a blanket `androidx.compose.ui.**` —
# that would also silence a real missing-Compose-class error for a consumer that DOES use Compose.
-dontwarn androidx.compose.ui.Modifier
-dontwarn androidx.compose.ui.Modifier$**
-dontwarn androidx.compose.ui.ExperimentalComposeUiApi
-dontwarn androidx.compose.ui.platform.**
-dontwarn androidx.compose.ui.semantics.**
