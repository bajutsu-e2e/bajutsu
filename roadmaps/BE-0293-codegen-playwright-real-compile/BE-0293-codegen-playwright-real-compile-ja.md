[English](BE-0293-codegen-playwright-real-compile.md) · **日本語**

# BE-0293 — Playwright（TypeScript）codegen ターゲットの実コンパイル検証

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0293](BE-0293-codegen-playwright-real-compile-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装中** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0293") |
| 実装 PR | [#1277](https://github.com/bajutsu-e2e/bajutsu/pull/1277) |
| トピック | codegen 網羅性 |
<!-- /BE-METADATA -->

## はじめに

`bajutsu codegen --emit playwright` はシナリオを TypeScript の Playwright テストへ変換しますが、
生成されたファイルを実際にコンパイルして実行する工程はリポジトリのどこにもありません。
`tests/test_codegen_playwright.py` の全アサーションは、出力ソースを文字列として検査しているだけです。
本項目はこの欠落したゲートを追加します。シナリオから Playwright テストを生成し、実際の
`@playwright/test` ランナーで実ブラウザに対して実行し、成功することを検証します。XCUITest 向け
codegen がすでに持つ実コンパイルゲート（`ios-e2e.yml` の `xcuitest (codegen)` ジョブ）に倣う形です。

## 動機

エミッタのユニットテストは、それ自体が扱う水準では十分に厚みがあります。あるステップに対して
正しい TypeScript 呼び出し（`page.getByTestId(...)`、`expect(...).toBeVisible()` など）が出力される
ことは確認できます。しかし確認できないのは、codegen が本来主張していること、すなわち「生成された
ファイルが実際に動く本物のネイティブテストである」という点です。`import { test, expect } from
'@playwright/test';` という文字列がテキスト中に存在することは部分一致で証明できても、そのファイルが
実際の `tsconfig` の下でコンパイルできるか、連鎖するメソッド呼び出しがインストール済みの
`@playwright/test` の実際の API 表面に対して解決するか、生成されたアサーションが実ページに対して
本当に成立するかは何も示しません。メソッド名の誤り、テンプレートリテラルの崩れ、あるいは実際の
Playwright API からドリフトしたエミッタの変更は、`tests/test_codegen_playwright.py` の 453 行すべてを
素通りし、ユーザーが生成されたファイルを実際に動かして初めて表面化します。

このギャップを埋めるワークフローや Makefile ターゲットは今のところ存在しません。`demos/web` 自体の
`e2e` ターゲットは、codegen の出力を経由せず Bajutsu 自身のドライバ層から直接 Playwright backend を
駆動しており、この検証の代わりにはなりません。XCUITest 向け codegen はすでにこの型を実証済みです。
`demos/showcase/Makefile` の `ui-test` ターゲットが Swift ファイルを生成し、`xcodegen` でビルドし、
実際の `xcodebuild test` で実行しており、しかもこのジョブは必須の CI チェックです。Playwright には
対応する工程が一切ない一方、検証コストは2つのターゲットのうちむしろ安価です。Simulator や macOS
ランナーも不要で、`demos/web` がすでに導入済みの Chromium だけで足ります。

## 詳細設計

提案の粒度です。作業は以下の単位に沿って MECE に分かれます。

- **生成してフィクスチャとして固定する**：`bajutsu codegen --emit playwright` で `demos/web` の
  シナリオから Playwright テストを生成し、XCUITest ターゲットの `ComponentsUITests.swift` と同様に、
  生成された `.spec.ts` をリポジトリにチェックインします。
- **実際に実行する**：`demos/web` がすでに導入済みの Chromium に対して、実際の `@playwright/test`
  ランナーで生成された spec を実行し、成功することを検証します。`tsc --noEmit` による構文チェックの
  みでは代替できません（理由は「検討した代替案」を参照）。
- **CI に組み込む**：`ui-test` 同様の Makefile ターゲットと、`xcuitest (codegen)` と同じ
  `web-e2e.yml` のジョブを追加します。
  [BE-0282](../BE-0282-real-backend-network-coverage/BE-0282-real-backend-network-coverage-ja.md)
  の前例にならい、まず非 gating の signal として着地させ、安定を確認してから必須化します。
- **XCUITest の水準に合わせ、それを超えない**：フィクスチャシナリオの範囲は、現行の XCUITest 向け
  codegen ゲートが対象とする DSL 表面（`tap` / `wait` / `type` / 基本的なアサーション）に揃え、両
  ターゲットが同じ実コンパイルの床から始まるようにします。この床を超えて各エミッタの*コンパイル
  対象* DSL 範囲を広げることは、これに続く別の課題です。

## 検討した代替案

- **`@playwright/test` を実行せず `tsc --noEmit` のみで型検査する**：コストは低く、構文エラーや型
  エラーは検出できますが、ファイルがコンパイルできることしか証明できません。生成されたセレクタや
  操作、アサーションが実ページに対して本当に成立するかまでは示しません。これはまさに、XCUITest
  向けゲートが Swift の構文チェックだけでなく実際の `xcodebuild test` を選んだ理由と同じであり、
  その理由はそのまま当てはまります。
- **文字列のみのテストスイートをゲートのまま残す**：エミッタの DSL 表面自体は広くカバーされており、
  問題はカバレッジの広さではなく検証の種類にあります。部分一致のアサーションをいくら増やしても、
  実際の `@playwright/test` API のドリフトは検出できません。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [x] `demos/web` のシナリオから Playwright テストを生成し、生成された `.spec.ts` をチェックインする。
- [x] 実際の `@playwright/test` ランナーで実ブラウザに対して実行し、成功することを検証する。
- [ ] Makefile ターゲットとゲート対象外の `web-e2e.yml` ジョブを追加し、安定後に必須化する。
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

## 参考

- [BE-0083 — codegen の emitter を共通のシナリオ走査へ統一する](../BE-0083-codegen-emitter-unification/BE-0083-codegen-emitter-unification-ja.md)
- [BE-0054 — Web backend の完成（リッチな capability と並列実行）](../BE-0054-web-backend-completion/BE-0054-web-backend-completion-ja.md)
- [BE-0282 — ネットワークのキャプチャ・モック・アサーションを CI で実バックエンド検証する](../BE-0282-real-backend-network-coverage/BE-0282-real-backend-network-coverage-ja.md)
- `bajutsu/codegen/playwright.py`、`tests/test_codegen_playwright.py`、
  `demos/showcase/Makefile`（`ui-test` ターゲット、XCUITest 側の前例）、
  `.github/workflows/ios-e2e.yml`（`xcuitest (codegen)` ジョブ）
