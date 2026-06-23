[English](BE-0057-rename-apps-to-targets.md) · **日本語**

# BE-0057 — 設定の `apps` キーを `targets` に改名

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0057](BE-0057-rename-apps-to-targets-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| 実装 PR | [#211](https://github.com/bajutsu-e2e/bajutsu/pull/211) |
| トピック | プラットフォーム拡張（着手済みスライス） |
<!-- /BE-METADATA -->

## はじめに

Bajutsu はアプリ固有の設定をすべて `apps.<name>` という 1 つのキーにまとめ、各コマンドは `--app <name>` で対象を 1 つ選びます（[DESIGN §8](../../../DESIGN.md)、[configuration.md](../../../docs/ja/configuration.md)）。この名前は iOS Simulator 限定だった頃の名残で、当時はテスト対象が常に iOS アプリでした。その後 Web（Playwright）バックエンドが着地し（[BE-0041](../BE-0041-web-playwright-backend/BE-0041-web-playwright-backend-ja.md)）、web のテスト対象は「アプリ」ではなく URL です。スキーマはこのために `bundleId` と並べて `baseUrl` を既に持っています。本項目は文法を `apps` から `targets` へ（`--app` を `--target` へ）改名し、このキーが実際に保持しているもの、すなわちプラットフォームを問わないテスト対象を、名前で正しく表します。

## 動機

`apps` は実態に合わない名前になっており、コード自身が既にそれを回避しています。

- エントリのモデル `AppConfig` は、その分岐を自身のコメントで「iOS アプリは bundleId で、web アプリは baseUrl でテスト対象を特定する」と説明し、バリデータは不正なエントリを `app needs bundleId (iOS) or baseUrl (web)` で弾きます。キーは `apps` のままなのに、概念を表す語としてコードは **target** を使っています。
- Android が予定され（[BE-0007](../BE-0007-android-backend/BE-0007-android-backend-ja.md)）、スコープ文自体もマルチプラットフォームへ動かす予定がある（[BE-0010](../BE-0010-update-scope-statement/BE-0010-update-scope-statement-ja.md)）なか、web サイトや Android パッケージを「アプリ」と呼ぶ無理は増す一方です。
- app-agnostic は prime directive です（[DESIGN §2](../../../DESIGN.md)）。テスト対象ごとの差分は config に置き、ツール本体は対象が変わっても不変に保ちます。`targets.<name>` はこの約束をプラットフォーム非依存の語で述べますが、`apps.<name>` は暗黙に iOS を前提にしています。

「target」はテストツールでテスト対象（system under test）を指す慣用語で、プラットフォームに依存せず、しかも上記のとおりコードとドキュメントが既に使っている語です（"the target app"）。config の表面がまだ 1 キーで、プロジェクトが 1.0 に達していないいま改名するほうが、用語が 3 プラットフォーム分のドキュメントとシナリオに広がった後よりはるかに安く済みます。

## 詳細設計

### 変更する箇所

*汎用的な入れ物とセレクタ* の語を、全レイヤで一貫して改名します。互換エイリアスを設けない **一括の切り替え** とし、リポジトリ自身の config・テスト・ドキュメントを同じ変更で動かします。

| レイヤ | 現在 | 変更後 |
|---|---|---|
| 設定キー（ルート） | `apps:` マップ | `targets:` マップ |
| 設定キー（org） | `orgs.<o>.apps: [...]`（org が持つ対象名） | `orgs.<o>.targets: [...]` |
| スキーマのモデル | `class AppConfig` | `class TargetConfig` |
| スキーマのフィールド | `Config.apps`、`OrgConfig.apps`、`Effective.app` | `Config.targets`、`OrgConfig.targets`、`Effective.target` |
| 解決関数 | `resolve(config, app)`、`org_for_app`、`apps_for_org` | `resolve(config, target)`、`org_for_target`、`targets_for_org` |
| CLI フラグ | `run` / `record` / `crawl` / `doctor` / `codegen` / `triage` の `--app <name>` | `--target <name>` |
| CLI 引数名 | `app_name` | `target_name` |
| serve HTTP | `GET /api/apps` | `GET /api/targets` |
| serve ヘルパ | `list_apps`、`app_build_info`、`app_scenarios_dir`、`_app_forbidden`、`list_apps_payload` | `target` 名の対応物 |
| serve.js | `#app` / `#rec-app` / `#crawl-app`、`/api/apps` の fetch | `#target` / …、`/api/targets` |
| MCP ツール | `bajutsu_run(app=…)`、`bajutsu_doctor(app=…)` | `target=…` |
| エラー / ヘルプ文 | 「unknown app …」「define apps.\<x\>」「(set apps.\<x\>.scenarios…)」 | 「unknown target …」「targets.\<x\>」 |
| サンプル config | `demos/*/…config.yaml`、`tests/resources/…` の `apps:` | `targets:` |
| ドキュメント（英日） | ドキュメント全体の `apps.<name>`、`--app`、「per-app」 | `targets.<name>`、`--target`、「per-target」 |

### 変更しない箇所

動かすのは **汎用の語だけ** です。iOS 固有の概念を正しく指しているフィールド名は残します。改名するとかえって誤った名前になるためです。

- `bundleId`、`appPath`、`deeplinkScheme` は iOS 固有のもの（bundle identifier、ビルド済み `.app` へのパス、URL スキーム）を指し、汎用のテスト対象を指す語ではありません。web のテスト対象はこれらを省き、`baseUrl` を設定するだけです。
- `scenarios/<name>/` のレイアウトとテスト対象ごとのシナリオディレクトリ。キーは対象の名前であり、これは変わりません。
- 決定的なコア（セレクタ解決、オーケストレータ、ランナー、アサーション）は手を付けません。これは config / CLI 境界での名前の変更だけです。

### 移行（一括の切り替え）

`apps:` / `targets:` を両方受け付ける期間は設けません。外部の config にとっても変更は機械的です。

- `apps:` → `targets:`（および `orgs.<o>.apps:` → `orgs.<o>.targets:`）
- 各コマンドの `--app <name>` → `--target <name>`
- API を直接叩く利用者は `/api/apps` → `/api/targets`

更新後の [configuration.md](../../../docs/ja/configuration.md) / [cli.md](../../../docs/ja/cli.md) と、リリースノートの 1 行で移行は足ります。プロジェクトが 1.0 に達しておらず、config の表面が 1 キーであるうちは、影響範囲は小さく収まります。

## 検討した代替案

- **`apps` を残し、web のテスト対象も流用すると書くだけにする。** 却下しました。この語は web と Android で実際に誤解を招き、コードは既にコメントとバリデータのメッセージで「target」と述べています。名前が誤ったことを主張しているキーを、ドキュメントでは直せません。
- **非推奨エイリアスを足す（両方を受理し、`apps:` 使用時に警告する）。** 範囲確定の判断に従い却下しました。両方受理の期間は、ドキュメントに 2 つの用語、抱える警告経路、追跡する削除予定を生みます。その費用が報われるのは、外部インストールや公開シナリオといった規模に達したときだけで、Bajutsu はそこに達していません。いま綺麗に切り替えるほうが、後で移行するより安く済みます。
- **設定キーだけ改名し、`--app` は残す。** 却下しました。`targets:` と書く config を `--app` と言う CLI で駆動するのは一貫しません。セレクタのフラグは、選ぶ対象のキーと一致させるべきです。
- **別の語（`subjects` / `systems` / `suts` / `applications`）。** 却下しました。「target」は E2E ツールでテスト対象を指す確立した語で、プラットフォーム非依存の語として最短で、しかも既にコードベースで使われている語なので、無用な差分と戸惑いを最小にします。

## 参考

- [DESIGN §8](../../../DESIGN.md)（CLI と設定: per-app / マルチアプリ）、[DESIGN §2](../../../DESIGN.md)（app-agnostic の prime directive）
- [configuration.md](../../../docs/ja/configuration.md)、[cli.md](../../../docs/ja/cli.md)（設定の階層と `--app` フラグ）
- `bajutsu/config.py`（`AppConfig`、`Config.apps`、`OrgConfig.apps`、`resolve` / `org_for_app` / `apps_for_org`）
- 関連項目: [BE-0010](../BE-0010-update-scope-statement/BE-0010-update-scope-statement-ja.md)（スコープ文の更新。この改名が相乗りするマルチプラットフォームのドキュメント移行）、[BE-0009](../BE-0009-cross-platform-abstractions/BE-0009-cross-platform-abstractions-ja.md)（抽象のクロスプラットフォーム化）、[BE-0041](../BE-0041-web-playwright-backend/BE-0041-web-playwright-backend-ja.md)（Web Playwright バックエンド。「アプリ」を実態に合わない名前にした、着地済みのプラットフォーム）、[BE-0042](../../implemented/BE-0042-platform-backend-registry/BE-0042-platform-backend-registry-ja.md)（プラットフォーム対応バックエンドレジストリ）
