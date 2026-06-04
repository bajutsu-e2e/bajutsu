[English](../sample-app.md) · **日本語**

# サンプルアプリ（BajutsuSample フィクスチャ）

> `sample/` の小さな自己完結 SwiftUI アプリ。Bajutsu の **全プリミティブ**（全ステップ種別・全
> アサーション種別・launch-env フック・deeplink・`os_signpost` 区間）に加え、小さな UI ギャラリー
> （Controls / Text / Lists タブ）を網羅するテスト用フィクスチャ。
>
> 詳細は [`sample/README.md`](../../sample/README.md)。ここでは Bajutsu 機能との対応を要約する。

関連: [scenarios](scenarios.md) ・ [configuration](configuration.md) ・ [codegen](codegen.md) ・ [cli](cli.md)

---

## 位置づけ

- ルートの [`bajutsu.config.yaml`](../../bajutsu.config.yaml) に `sample` アプリとして登録済み。
- bundle id `com.bajutsu.sample` ・ deeplink scheme `bajutsusample`。
- ビルドは XcodeGen + xcodebuild（`project.yml` が真実、`.xcodeproj`/`build/` は gitignore）。
- シナリオ例は [`sample/scenarios/`](../../sample/scenarios)。

```bash
make sample-gen     # xcodegen generate -> BajutsuSample.xcodeproj
make sample-build   # iOS Simulator 向けにコンパイル
```

## launch-env フック

`SIMCTL_CHILD_<NAME>` として渡る（Bajutsu は `launchEnv` から自動でこの変換をする・
[drivers](drivers.md#環境管理simctl)）。状態を注入してテストの前提を作る。

| 変数 | 効果 |
|---|---|
| `SAMPLE_UITEST=1` | アニメーション無効化（条件待機を締める） |
| `SAMPLE_SKIP_ONBOARDING=1` | ログイン画面から開始 |
| `SAMPLE_LOGGED_IN=1` | ホーム画面から開始（オンボーディング + ログインを skip） |
| `SAMPLE_SCREEN=settings` | 起動時に設定シートを開く（`SAMPLE_LOGGED_IN` と併用） |
| `SAMPLE_TAB=<name>` | 起動時にタブ選択: `home`（既定）/ `components` / `controls` / `text` / `lists` |
| `SAMPLE_SEED=<n>` | ホームのリスト行を n 件シード（既定 3） |

deeplink: `bajutsusample://settings` / `bajutsusample://home`、およびタブごとに
`bajutsusample://components` / `bajutsusample://controls` / `bajutsusample://text` /
`bajutsusample://lists`（いずれもログイン済みで開く）。

## accessibilityIdentifier カタログ

命名規約（`<namespace>.<element>`・[configuration](configuration.md#識別子の命名規約)）に従う。
`auth.*` / `nav.*` は予約名前空間。動的行（`list.row.<id>` / `lists.row.<id>`）はデータ由来キーで一意化。

| 画面 | 主な識別子 |
|---|---|
| Onboarding | `onboarding.title` / `onboarding.start` |
| Login | `auth.email` / `auth.password` / `auth.submit`（両フィールド入力まで disabled） / `auth.error` |
| Home | `home.title` / `home.search` / `home.list` / `home.spinner` / `nav.settings` |
| Counter | `counter.value`（accessibilityValue を露出） / `counter.increment` |
| List rows | `list.row.<id>`（データ由来） |
| Settings | `settings.normalizeToggle`（ON で selected トレイト） / `settings.banner`（変更後に出現） / `settings.reindex` / `settings.status`（value） / `settings.reindexComplete` |
| Controls (`SAMPLE_TAB=controls`) | `ctrl.toggle` / `ctrl.stepper` / `ctrl.slider` / `ctrl.segment` / `ctrl.menu` / `ctrl.button`（各 `*.value` ミラー付き） / `ctrl.buttonDisabled` |
| Text (`SAMPLE_TAB=text`) | `text.basic`（+ `text.basic.value` / `text.count`） / `text.clear` / `text.email` / `text.editor` / `text.required` / `text.error` / `text.submit`（妥当になるまでゲート） / `text.submitted` |
| Lists & Nav (`SAMPLE_TAB=lists`) | `lists.search` / `lists.row.<id>`（swipe-to-delete） / `lists.empty` / `lists.count`（value） / `lists.edit` / `lists.refreshed` / `lists.detail.title`（+ `lists.detail.value`） |

> ギャラリータブの操作要素はいずれも状態を `*.value` 結果ラベルにミラーするため、ヘッドレス
> バックエンドでも要素自体を読まずに **value で結果を検証**できる。

## プリミティブとシナリオの対応

各プリミティブがどのシナリオで使われるか（[scenarios](scenarios.md) の文法と対応）。

| プリミティブ | シナリオ |
|---|---|
| tap / type(into) / wait(for) | [`smoke.yaml`](../../sample/scenarios/smoke.yaml) |
| enabled / disabled | [`auth.yaml`](../../sample/scenarios/auth.yaml) |
| selected / exists(+negate) / value / capturePolicy | [`settings.yaml`](../../sample/scenarios/settings.yaml) |
| count / idMatches / 検索フィルタ | [`list.yaml`](../../sample/scenarios/list.yaml) ・ [`lists.yaml`](../../sample/scenarios/lists.yaml) |
| longPress / in-app alert(label tap) / swipe(on+direction) | [`components.yaml`](../../sample/scenarios/components.yaml) |
| video / deviceLog 区間 + os_signpost | [`evidence.yaml`](../../sample/scenarios/evidence.yaml) |
| Controls ギャラリー（toggle / stepper / slider / picker / menu / button） | [`controls.yaml`](../../sample/scenarios/controls.yaml) |
| テキスト入力（value + 文字数 / clear / インライン検証） | [`text.yaml`](../../sample/scenarios/text.yaml) |
| リスト検索 / swipe 削除 / 編集 / pull-to-refresh / push 遷移 / 空状態 | [`lists.yaml`](../../sample/scenarios/lists.yaml) |

## E2E と codegen の make ターゲット

実機 Simulator に対する 2 つの経路（[`Makefile`](../../Makefile)）。`SIM` は booted デバイスを自動検出。

### `make e2e`（idb バックエンドで run）

```
sample-build → simctl install → bajutsu run smoke.yaml（idb / --no-erase）→ bajutsu doctor
```

前提: booted Simulator・`brew install facebook/fb/idb-companion`・`uv sync --extra idb`。

### UI テストターゲットと make ターゲット

`make ui-test` は **codegen 経由**の経路を回す: シナリオから XCUITest を生成し、xcodebuild で実行する
（テスト時に bajutsu ランタイム・idb・AI は一切不要・[codegen](codegen.md)）。

```
bajutsu codegen components.yaml -o BajutsuSampleUITests/ComponentsUITests.swift
  → xcodegen generate → xcodebuild test（scheme: UITests）
```

`project.yml` には `BajutsuSampleUITests`（`bundle.ui-testing`）ターゲットと `UITests` スキームが
定義済み。生成された
[`ComponentsUITests.swift`](../../sample/BajutsuSampleUITests/ComponentsUITests.swift) が
コミットされている（codegen の出力例として）。
