# A Views-only consumer never links androidx.compose.ui (it's compileOnly, in build.gradle.kts), so
# R8's missing-class check would otherwise fail their build over the compose/ package's own
# references to it. Only two Compose subpackages are wildcarded (platform, semantics), plus the
# top-level symbols compose/ imports directly, rather than a blanket `androidx.compose.ui.**`.
# Residual cost: consumerProguardFiles rules merge into the consumer's whole R8 configuration, so a
# consumer that DOES use Compose also loses the missing-class error for anything under those two
# packages — narrower than the blanket rule, not free of it.
-dontwarn androidx.compose.ui.Modifier
-dontwarn androidx.compose.ui.Modifier$**
-dontwarn androidx.compose.ui.ExperimentalComposeUiApi
-dontwarn androidx.compose.ui.platform.**
-dontwarn androidx.compose.ui.semantics.**
