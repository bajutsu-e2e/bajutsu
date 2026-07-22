[English](BE-0294-codegen-uiautomator-real-compile.md) · **日本語**

# BE-0294 — UI Automator（Kotlin）codegen ターゲットの実コンパイル検証

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0294](BE-0294-codegen-uiautomator-real-compile-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0294") |
| 実装 PR | [#1282](https://github.com/bajutsu-e2e/bajutsu/pull/1282) |
| トピック | codegen 網羅性 |
<!-- /BE-METADATA -->

## はじめに

`bajutsu codegen --emit uiautomator` はシナリオを Kotlin の UI Automator テストへ変換しますが、
生成されたファイルをコンパイルするワークフローや Makefile ターゲット、Gradle ビルドのいずれも存在しません。
`tests/test_codegen_uiautomator.py` の全アサーションは出力ソースを文字列として検査するだけなので、
Kotlin の構文エラーや、実際の `androidx.test.uiautomator` API と食い違う呼び出しがあっても、
テストスイート全体を素通りします。これは3つの codegen ターゲットのうちもっとも手薄な状態です。XCUITest
には必須の `xcodebuild test` ジョブがあり、Playwright にも別途、同水準の実コンパイル検証が提案
されているのに対し、UI Automator にはゲーティングの有無を問わず実コンパイル検証が一切ありません。本項目はそのゲートを追加し、
`android-e2e.yml` が conformance suite 向けにすでに用意しているエミュレータと Gradle ツールチェインを
再利用します。

## 動機

エミッタのユニットテストは、あるステップに対して正しい Kotlin 呼び出し（`By.res(...)`、
`device.findObject(...).click()` など）が出力されることを確認しており、これは変換ロジックに対する
実質的なカバレッジです。しかし、codegen が本来主張していること、すなわち「生成されたファイルが
実際にビルド可能な Android テストである」ことのカバレッジではありません。`import
androidx.test.uiautomator.By` という文字列がテキスト中に存在することは部分一致で証明できますが、
周囲の Kotlin コードが実際にパースできるか、参照している UI Automator の API が固定されたライブラリ
バージョンに存在するか、生成されたテストが実行中のアプリに対して本当に成立するかは何も示しません。
`android-e2e.yml` は conformance suite のために、ショーケースアプリの Compose 版と Views 版、常駐 UI
Automator サーバをすでにビルドしています
（[BE-0208](../BE-0208-android-emulator-e2e-ci/BE-0208-android-emulator-e2e-ci-ja.md)、
[BE-0245](../BE-0245-adb-resident-uiautomator-server/BE-0245-adb-resident-uiautomator-server-ja.md)）。
この Gradle とエミュレータのインフラは codegen の出力とは一切結びついておらず、壊れたエミッタの変更が
静かに出荷されてしまいます。

## 詳細設計

提案の粒度です。作業は以下の単位に沿って MECE に分かれます。

- **生成してフィクスチャとして固定する**：`bajutsu codegen --emit uiautomator` でショーケースの
  シナリオから UI Automator テストを生成し、XCUITest 向けの `ComponentsUITests.swift` と同様に、
  生成された `.kt` ファイルを `demos/showcase` にチェックインします。
- **実際にビルドして実行する**：生成されたテストをショーケース Android プロジェクトの計装テスト
  ソースセットに加え、Gradle でビルドし、`android-e2e.yml` がすでに用意している起動済みエミュレータ
  に対して実行し、成功することを検証します。
- **CI に組み込む**：`xcuitest (codegen)` と同様の `android-e2e.yml` のジョブを追加します。
  [BE-0282](../BE-0282-real-backend-network-coverage/BE-0282-real-backend-network-coverage-ja.md)
  の前例にならい、まずゲート対象外のシグナルとして着地させ、安定を確認してから必須化します。
- **XCUITest の水準に合わせ、それを超えない**：フィクスチャシナリオの範囲を、XCUITest 向け codegen
  ゲートがカバーする DSL 表面（`tap` / `wait` / `type` / 基本的なアサーション）に揃え、3つの
  ターゲットすべてが同じ実コンパイルの床から始まるようにします。

## 検討した代替案

- **デバイス上で実行せず Kotlin コンパイラのみで検証する**：コストは低く、構文エラーや型エラーは
  検出できますが、生成される呼び出しは UI Automator の API 呼び出しであり、その振る舞い（`By.res`
  の解決、`waitForIdle` の意味論）は実機かエミュレータ上で動くアプリに対してしか確認できません。
  コンパイルのみの検証では、コンパイルは通るが何にもマッチしない呼び出しを見逃したままになります。
- **文字列のみのテストスイートをゲートのまま残す**：エミッタの DSL 表面自体はすでに広くカバー
  されており、欠けているのはカバレッジの広さではなく検証の種類です。部分一致のアサーションを
  いくら増やしても、実際の UI Automator API のドリフトは検出できません。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [x] ショーケースのシナリオから UI Automator テストを生成し、生成された `.kt` ファイルをチェックインする。
- [x] Gradle でビルドし、エミュレータに対して実行して成功することを検証する。
- [x] ゲート対象外の `android-e2e.yml` ジョブを追加し、安定後に必須化する。
- [x] フィクスチャの範囲を、XCUITest 向け codegen ゲートがすでにカバーする DSL 表面に揃える。

ログ：

- `codegen_android.yaml` フィクスチャと、チェックインした `CodegenAndroidUITest.kt`、compose
  モジュールの `androidTest` 配線、`e2e-codegen` の Makefile ターゲット、ゲート対象外の
  `uiautomator (codegen)` ジョブを追加しました。高速ゲートのテストが、フィクスチャが `// TODO` を
  含まないこと、チェックイン済みの `.kt` とバイト単位で一致することを検証します。

## 参考

- [BE-0209 — Android codegen エミッタ（Espresso / UI Automator）](../BE-0209-android-codegen-emitter/BE-0209-android-codegen-emitter-ja.md)
- [BE-0208 — Android の実機 e2e を CI に配線する（KVM 経由のエミュレータ）](../BE-0208-android-emulator-e2e-ci/BE-0208-android-emulator-e2e-ci-ja.md)
- [BE-0083 — codegen の emitter を共通のシナリオ走査へ統一する](../BE-0083-codegen-emitter-unification/BE-0083-codegen-emitter-unification-ja.md)
- [BE-0282 — ネットワークのキャプチャ・モック・アサーションを CI で実バックエンド検証する](../BE-0282-real-backend-network-coverage/BE-0282-real-backend-network-coverage-ja.md)
- `bajutsu/codegen/uiautomator.py`、`tests/test_codegen_uiautomator.py`、
  `.github/workflows/android-e2e.yml`
