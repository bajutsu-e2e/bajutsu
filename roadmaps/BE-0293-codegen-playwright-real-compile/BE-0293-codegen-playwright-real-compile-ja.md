[English](BE-0293-codegen-playwright-real-compile.md) · **日本語**

# BE-0293 — Playwright（TypeScript）codegen ターゲットの実コンパイル検証

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0293](BE-0293-codegen-playwright-real-compile-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0293") |
| 実装 PR | [#1277](https://github.com/bajutsu-e2e/bajutsu/pull/1277)、[#1314](https://github.com/bajutsu-e2e/bajutsu/pull/1314) |
| トピック | codegen 網羅性 |
<!-- /BE-METADATA -->

## はじめに

`bajutsu codegen --emit playwright` はシナリオを TypeScript の Playwright テストへ変換します。
しかし、生成されたファイルを実際にコンパイルして実行する工程は、リポジトリのどこにもありません。
`tests/test_codegen_playwright.py` の全アサーションが、出力ソースを文字列として検査しているだけだからです。

本項目は、欠落しているゲートを追加します。このゲートは、シナリオから Playwright テストを生成し、
実際の `@playwright/test` ランナーで実ブラウザに対して実行し、成功することを検証します。
XCUITest 向け codegen がすでに持つ実コンパイルゲート、つまり `ios-e2e.yml` の
`xcuitest (codegen)` ジョブに倣う形です。

## 動機

エミッタのユニットテストは、それ自体が扱う水準では十分に厚みがあります。あるステップに対して
正しい TypeScript 呼び出し（`page.getByTestId(...)`、`expect(...).toBeVisible()` など）が出力される
ことは確認できます。しかし、codegen が本来主張していること、つまり生成されたファイルが実際に動く
本物のネイティブテストであることは、確認できません。

`import { test, expect } from '@playwright/test';` という文字列がテキスト中に存在することは、
部分一致で証明できます。次の3点については、何も示しません。

- そのファイルが、実際の `tsconfig` の下でコンパイルできるか
- 連鎖するメソッド呼び出しが、インストール済みの `@playwright/test` の実際の API 表面に対して解決するか
- 生成されたアサーションが、実ページに対して成立するか

次の3種類の欠陥は、`tests/test_codegen_playwright.py` の 453 行すべてを素通りします。
メソッド名の誤り、テンプレートリテラルの崩れ、実際の Playwright API からドリフトしたエミッタの変更です。欠陥が表面化するのは、ユーザーが
生成されたファイルを実際に動かしたときです。

このギャップを埋めるワークフローや Makefile ターゲットは、今のところ存在しません。`demos/web` 自体の
`e2e` ターゲットは、実行時に Bajutsu 自身のドライバ層から Playwright backend を直接駆動します。
codegen の出力は経由しないので、この検証の代わりにはなりません。

XCUITest 向け codegen は、すでにこの型を実証済みです。`demos/showcase/Makefile` の `ui-test`
ターゲットが Swift ファイルを生成し、`xcodegen` でビルドし、実際の `xcodebuild test` で実行します。
このジョブは必須の CI チェックです。Playwright には対応する工程が一切ありません。それでいて、検証
コストは2つのターゲットのうち安価な側です。Simulator と macOS ランナーは要らず、`demos/web` が
すでに導入済みの Chromium だけで足ります。

## 詳細設計

提案の粒度です。作業は以下の単位に沿って MECE に分かれます。

- **生成してフィクスチャとして固定する**：`bajutsu codegen --emit playwright` で、`demos/web` の
  シナリオから Playwright テストを生成します。XCUITest ターゲットの `ComponentsUITests.swift` と
  同様に、生成された `.spec.ts` をリポジトリにチェックインします。
- **実際に実行する**：`demos/web` がすでに導入済みの Chromium に対して、実際の `@playwright/test`
  ランナーで生成された spec を実行し、成功することを検証します。`tsc --noEmit` による構文チェック
  だけでは代替できません（理由は「検討した代替案」を参照）。
- **CI に組み込む**：`ui-test` 同様の Makefile ターゲットと、`xcuitest (codegen)` と同じ
  `web-e2e.yml` のジョブを追加します。
  [BE-0282](../BE-0282-real-backend-network-coverage/BE-0282-real-backend-network-coverage-ja.md)
  の前例にならい、まず非 gating の signal として着地させます。安定を確認してから必須化します。
- **XCUITest の水準に合わせ、それを超えない**：フィクスチャシナリオの範囲を、現行の XCUITest 向け
  codegen ゲートの DSL 表面（`tap` / `wait` / `type` / 基本的なアサーション）に揃えます。
  これにより、両ターゲットが同じ実コンパイルの床から始まります。この床を超えて各エミッタの
  *コンパイル対象* DSL 範囲を広げることは、これに続く別の課題です。

## 検討した代替案

- **`@playwright/test` を実行せず `tsc --noEmit` のみで型検査する**：コストは低く、構文エラーや
  型エラーは検出できます。ただし、証明できるのはファイルがコンパイルできることだけです。生成された
  セレクタ、操作、アサーションが実ページに対して成立するかまでは示しません。同じギャップがあるため、
  XCUITest 向けゲートは Swift の構文チェックではなく実際の `xcodebuild test` を選びました。その理由は、
  そのまま当てはまります。
- **文字列のみのテストスイートをゲートのまま残す**：エミッタの DSL 表面は、すでに広くカバーされて
  います。問題はカバレッジの広さではなく、検証の種類にあります。部分一致のアサーションをいくら
  増やしても、実際の `@playwright/test` API のドリフトは検出できません。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [x] `demos/web` のシナリオから Playwright テストを生成し、生成された `.spec.ts` をチェックインする。
- [x] 実際の `@playwright/test` ランナーで実ブラウザに対して実行し、成功することを検証する。
- [x] Makefile ターゲットとゲート対象外の `web-e2e.yml` ジョブを追加し、安定後に必須化する。
- [x] フィクスチャの範囲を、XCUITest 向け codegen ゲートがすでにカバーする DSL 表面に揃える。

ログ：

- `scenarios/smoke.yaml`（XCUITest 向け codegen ゲートがカバーする `tap` / `type` / `wait` /
  `exists` / `value` の床）から `demos/web/codegen/smoke.spec.ts` を生成してチェックインし、あわせて
  ピン留めした `@playwright/test` ランナー（`codegen/package.json`、`codegen/playwright.config.ts`）
  を追加しました。`codegen-e2e` Makefile ターゲット（再生成し、実ランナーで実 Chromium に対して
  spec を実行してから、ドリフトで失敗させる）と、`web-e2e.yml` のゲート対象外の signal ジョブ
  `codegen (playwright)` を追加しました。このジョブは意図的にまだ必須の `E2E (web)` アグリゲータの
  `needs` には入れていません。`network (playwright)` がそうだったように、CI で安定を確認してから
  昇格させます。
- `codegen (playwright)` ジョブを signal から必須へ昇格させました。`E2E (web)` アグリゲータの
  `needs` とその失敗判定ループに `web-codegen` を追加し、codegen の実コンパイル失敗がマージを
  ブロックするようにしました。`ios-e2e.yml` のすでに必須である `xcuitest (codegen)` の web 版に
  当たります。着地以降の `web-e2e` の全実行で安定していたため、`network (playwright)` がたどった
  signal → 必須の経路（BE-0282）に倣いました。これで本項目は完了します。

## 参考

- [BE-0083 — codegen の emitter を共通のシナリオ走査へ統一する](../BE-0083-codegen-emitter-unification/BE-0083-codegen-emitter-unification-ja.md)
- [BE-0054 — Web backend の完成（リッチな capability と並列実行）](../BE-0054-web-backend-completion/BE-0054-web-backend-completion-ja.md)
- [BE-0282 — ネットワークのキャプチャ・モック・アサーションを CI で実バックエンド検証する](../BE-0282-real-backend-network-coverage/BE-0282-real-backend-network-coverage-ja.md)
- `bajutsu/codegen/playwright.py`、`tests/test_codegen_playwright.py`、
  `demos/showcase/Makefile`（`ui-test` ターゲット、XCUITest 側の前例）、
  `.github/workflows/ios-e2e.yml`（`xcuitest (codegen)` ジョブ）
