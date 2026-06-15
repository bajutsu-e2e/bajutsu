[English](../sample-app.md) · **日本語**

# サンプルアプリ（BajutsuSample フィクスチャ）

> `demos/features/app/` の小さな自己完結 SwiftUI アプリです。Bajutsu の **全プリミティブ**（全ステップ種別・全
> アサーション種別・launch-env フック・deeplink・`os_signpost` 区間）に加え、10 タブの UI ギャラリー
> （Home / Components / Controls / Text / Lists / Gestures / Presentation / Async / System / Network）を
> オンボーディング → ログインの認証フロー越しに網羅するテスト用フィクスチャです。
>
> 詳細は [`demos/features/app/README.md`](../../demos/features/app/README.md) を参照してください。ここでは Bajutsu 機能との対応を要約します。

関連: [scenarios](scenarios.md) ・ [configuration](configuration.md) ・ [codegen](codegen.md) ・ [cli](cli.md)

---

## 位置づけ

- ルートの [`bajutsu.config.yaml`](../../bajutsu.config.yaml) に `sample` アプリとして登録されています。
- bundle id `com.bajutsu.sample` ・ deeplink scheme `bajutsusample`。
- ビルドは XcodeGen + xcodebuild を使います（`project.yml` が真実のソースで、`.xcodeproj`/`build/` は gitignore 済み）。
- シナリオ例は [`demos/features/app/scenarios/`](../../demos/features/app/scenarios) にあります。

```bash
make -C demos/features sample-gen     # xcodegen generate -> BajutsuSample.xcodeproj
make -C demos/features sample-build   # iOS Simulator 向けにコンパイル
```

## launch-env フック

`SIMCTL_CHILD_<NAME>` として渡ります（Bajutsu は `launchEnv` から自動でこの変換を行います・
[drivers](drivers.md#環境管理simctl)）。状態を注入してテストの前提条件を作ります。

| 変数 | 効果 |
|---|---|
| `SAMPLE_UITEST=1` | アニメーション無効化（条件待機を短く保つ） |
| `SAMPLE_SKIP_ONBOARDING=1` | ログイン画面から開始 |
| `SAMPLE_LOGGED_IN=1` | ホーム画面から開始（オンボーディング + ログインを skip） |
| `SAMPLE_SCREEN=settings` | 起動時に設定シートを開く（`SAMPLE_LOGGED_IN` と併用） |
| `SAMPLE_TAB=<name>` | 起動時にタブ選択: `home`（既定）/ `components` / `controls` / `text` / `lists` / `gestures` / `presentation` / `async` / `system` / `network` |
| `SAMPLE_SEED=<n>` | ホームのリスト行を n 件シード（既定 3） |

deeplink: `bajutsusample://settings` / `bajutsusample://home`、およびタブごとに
`bajutsusample://components` / `bajutsusample://controls` / `bajutsusample://text` /
`bajutsusample://lists` / `bajutsusample://gestures` / `bajutsusample://presentation` /
`bajutsusample://async` / `bajutsusample://system`（いずれもログイン済みで開きます）。`network` タブだけは
deeplink を持ちません（`SAMPLE_TAB=network` で開きます）。

## accessibilityIdentifier カタログ

命名規約（`<namespace>.<element>`・[configuration](configuration.md#識別子の命名規約)）に従います。
`auth.*` / `nav.*` は予約名前空間です。動的行（`list.row.<id>` / `lists.row.<id>`）はデータ由来キーで一意化されます。

| 画面 | 主な識別子 |
|---|---|
| Onboarding | `onboarding.title` / `onboarding.start` |
| Login | `auth.email` / `auth.password` / `auth.submit`（両フィールド入力まで disabled） / `auth.error` |
| Home | `home.title` / `home.search` / `home.list` / `home.spinner` / `nav.settings` |
| Counter | `counter.value`（accessibilityValue を公開） / `counter.increment` |
| List rows | `list.row.<id>`（データ由来） |
| Settings | `settings.normalizeToggle`（ON で selected トレイト） / `settings.banner`（変更後に出現） / `settings.reindex` / `settings.status`（value） / `settings.reindexComplete` |
| Controls (`SAMPLE_TAB=controls`) | `ctrl.toggle` / `ctrl.stepper` / `ctrl.slider` / `ctrl.segment` / `ctrl.menu` / `ctrl.button`（各 `*.value` ミラー付き） / `ctrl.buttonDisabled` |
| Text (`SAMPLE_TAB=text`) | `text.basic`（+ `text.basic.value` / `text.count`） / `text.clear` / `text.email` / `text.editor` / `text.required` / `text.error` / `text.submit`（妥当になるまでゲート） / `text.submitted` |
| Lists & Nav (`SAMPLE_TAB=lists`) | `lists.search` / `lists.row.<id>`（swipe-to-delete） / `lists.empty` / `lists.count`（value） / `lists.edit` / `lists.refreshed` / `lists.detail.title`（+ `lists.detail.value`） |
| Gestures (`SAMPLE_TAB=gestures`) | `gest.doubletap`（+ `gest.doubletap.value`） / `gest.pinch`（+ `.value`） / `gest.rotate`（+ `.value`） — double-tap は idb で駆動可能。pinch / rotate は真のマルチタッチが必要なため実機経路は生成 XCUITest を使います |
| Presentation (`SAMPLE_TAB=presentation`) | `pres.openSheet` → `pres.sheet.title` / `pres.sheet.close` ・ `pres.openCover` → `pres.cover.*` ・ `pres.openDialog` → `pres.dialog.value` ・ `pres.showToast` → `pres.toast`（自動消滅 → `wait until gone`） |
| Async (`SAMPLE_TAB=async`) | `async.startProgress` → `async.progress.value` / `async.progress.done` ・ `async.loadFail` → `async.error` → `async.retry` → `async.loaded` ・ `async.search` → `async.debounced.value`（debounce） ・ `async.loadMore` → `async.count` |
| System (`SAMPLE_TAB=system`) | `sys.requestNotif` → `sys.notif.value` / `sys.notif.authorized`（OS プロンプトは SpringBoard 管理 → vision alert guard が処理） ・ `sys.copy` / `sys.paste` → `sys.paste.value`（アプリ内 pasteboard） ・ `sys.share`（システム共有シート） |
| Network (`SAMPLE_TAB=network`) | `net.fetch` / `net.get-query` / `net.post`（redaction 用に秘匿ヘッダ + body を含む） / `net.delete` ・ `net.status`（value） ・ `net.captured.*`（method / status / duration / url を BajutsuKit から読み戻し） — BajutsuKit + `BAJUTSU_COLLECTOR` が必要 |

> ギャラリータブの操作要素はいずれも状態を `*.value` 結果ラベルにミラーします。そのためヘッドレス
> バックエンドでも要素自体を読まずに **value で結果を検証**できます。

## プリミティブとシナリオの対応

各プリミティブがどのシナリオで使われるかを示します（[scenarios](scenarios.md) の文法と対応）。

| プリミティブ | シナリオ |
|---|---|
| tap / type(into) / wait(for) | [`smoke.yaml`](../../demos/features/app/scenarios/smoke.yaml) |
| enabled / disabled | [`auth.yaml`](../../demos/features/app/scenarios/auth.yaml) |
| selected / exists(+negate) / value / capturePolicy | [`settings.yaml`](../../demos/features/app/scenarios/settings.yaml) |
| count / idMatches / 検索フィルタ | [`list.yaml`](../../demos/features/app/scenarios/list.yaml) ・ [`lists.yaml`](../../demos/features/app/scenarios/lists.yaml) |
| longPress / in-app alert(label tap) / swipe(on+direction) | [`components.yaml`](../../demos/features/app/scenarios/components.yaml) |
| video / deviceLog 区間 + os_signpost | [`evidence.yaml`](../../demos/features/app/scenarios/evidence.yaml) |
| Controls ギャラリー（toggle / stepper / slider / picker / menu / button） | [`controls.yaml`](../../demos/features/app/scenarios/controls.yaml) |
| テキスト入力（value + 文字数 / clear / インライン検証） | [`text.yaml`](../../demos/features/app/scenarios/text.yaml) |
| リスト検索 / swipe 削除 / 編集 / pull-to-refresh / push 遷移 / 空状態 | [`lists.yaml`](../../demos/features/app/scenarios/lists.yaml) |
| doubleTap / pinch / rotate（マルチタッチは codegen 経由） | [`gestures.yaml`](../../demos/features/app/scenarios/gestures.yaml) |
| sheet / full-screen cover / confirmationDialog / toast（`wait until gone`） | [`presentation.yaml`](../../demos/features/app/scenarios/presentation.yaml) |
| 進捗 / fail→retry→success / debounce / ページング（条件待機） | [`async.yaml`](../../demos/features/app/scenarios/async.yaml) |
| 通知プロンプト（alert guard） / pasteboard | [`system.yaml`](../../demos/features/app/scenarios/system.yaml) |
| `request` アサーション / HTTP メソッド / 決定的 `mocks` | [`network.yaml`](../../demos/features/app/scenarios/network.yaml) ・ [`network_methods.yaml`](../../demos/features/app/scenarios/network_methods.yaml) ・ [`network_mock.yaml`](../../demos/features/app/scenarios/network_mock.yaml) |
| relaunch（シナリオ途中で env を再注入） | [`relaunch.yaml`](../../demos/features/app/scenarios/relaunch.yaml) |

## E2E と codegen の make ターゲット

実機 Simulator に対する 2 つの経路です（[`Makefile`](../../Makefile)）。`SIM` は booted デバイスを自動検出します。

### `make -C demos/features e2e`（idb バックエンドで run）

```
sample-build → simctl install → bajutsu run --scenario smoke.yaml（idb / --no-erase）→ bajutsu doctor
```

前提条件: booted Simulator・`brew install facebook/fb/idb-companion`・`uv sync --extra idb`。

### UI テストターゲットと make ターゲット

`make -C demos/features ui-test` は **codegen 経由**の経路を実行します。シナリオから XCUITest を生成し、xcodebuild で実行します
（テスト時に bajutsu ランタイム・idb・AI は一切不要です・[codegen](codegen.md)）。

```
bajutsu codegen components.yaml -o BajutsuSampleUITests/ComponentsUITests.swift
  → xcodegen generate → xcodebuild test（scheme: UITests）
```

`project.yml` には `BajutsuSampleUITests`（`bundle.ui-testing`）ターゲットと `UITests` スキームが
定義済みです。生成された
[`ComponentsUITests.swift`](../../demos/features/app/BajutsuSampleUITests/ComponentsUITests.swift) が
コミットされています（codegen の出力例として）。
