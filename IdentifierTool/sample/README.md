# IdentifierTool sample

This sample is a minimal, standalone Android app. It depends on nothing but [`IdentifierTool`](../README.md).
A third-party app adopting the library would take the same shape.

The sample has its own Gradle root, separate from [`demos/showcase/android`](../../demos/showcase/android).
That larger fixture exercises IdentifierTool differently. It uses both the Views and the Compose half,
alongside `BajutsuAndroid`.

`MainActivity.kt` calls `View.accessibilityId` / `View.accessibilityStateValue` on two views. It
declares their names ahead of time in `app/src/main/res/values/ids.xml`. IdentifierTool's own README
describes that Views caveat.

Neither call sits behind a flag, because every IdentifierTool function tags unconditionally. As a
result, this app carries no `noax`-style off switch.

## Run it

```bash
./gradlew :app:installDebug
```

Or open the directory in Android Studio.
