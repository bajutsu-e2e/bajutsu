# IdentifierTool sample

A minimal, standalone Android app. It depends on nothing but
[`IdentifierTool`](../README.md) — the shape a third-party app adopting it would use.
It has its own Gradle root, separate from
[`demos/showcase/android`](../../demos/showcase/android). That larger fixture exercises
IdentifierTool differently. It uses both the Views and the Compose half, alongside `BajutsuAndroid`.

`MainActivity.kt` calls `View.accessibilityId` / `View.accessibilityStateValue` on two views. It
declares their names ahead of time in `app/src/main/res/values/ids.xml`. IdentifierTool's own
README describes that Views caveat. Neither call sits behind a flag: every IdentifierTool function
tags unconditionally. This app carries no `noax`-style off switch as a result.

## Run it

```bash
./gradlew :app:installDebug
```

or open the directory in Android Studio.
