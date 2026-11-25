[English](BE-0057-rename-apps-to-targets.md) · **日本語**

# BE-0057 — 設定の `apps` キーを `targets` に改名

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0057](BE-0057-rename-apps-to-targets-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0057") |
| 実装 PR | [#211](https://github.com/bajutsu-e2e/bajutsu/pull/211) |
| トピック | ドライバとバックエンドのアーキテクチャ |
<!-- /BE-METADATA -->

## はじめに

Bajutsu は、アプリ固有の設定をすべて `apps.<name>` という 1 つのキーにまとめています。各コマンドは、`--app <name>` でエントリを 1 つ選びます（[DESIGN §8](../../DESIGN.md)、[configuration.md](../../docs/ja/configuration.md)）。

この名前は、iOS Simulator 限定だった頃の名残です。当時、テスト対象は常に iOS アプリでした。その後、Web（Playwright）バックエンドが着地しました（[BE-0041](../BE-0041-web-playwright-backend/BE-0041-web-playwright-backend-ja.md)）。web のテスト対象は「アプリ」ではなく URL です。スキーマは、このために `bundleId` と並べて `baseUrl` をすでに持っています。

本項目は、文法を `apps` から `targets` へ、`--app` を `--target` へ改名します。新しい語は、このキーが保持するもの、つまりプラットフォームを問わないテスト対象を、名前で表します。

## 動機

`apps` は実態に合わない名前になっており、コード自身がすでにそれを回避しています。

- エントリのモデル `AppConfig` は、その分岐を自身のコメントで説明しています。「iOS アプリは bundleId で、web アプリは baseUrl でテスト対象を特定する」というものです。バリデータは、不正なエントリを `app needs bundleId (iOS) or baseUrl (web)` で弾きます。キーは `apps` のままなのに、概念を表す語として、コードは **target** を使っています。
- Android が予定され（[BE-0007](../BE-0007-android-backend/BE-0007-android-backend-ja.md)）、スコープ文自体もマルチプラットフォームへ動かす予定があります（[BE-0010](../BE-0010-update-scope-statement/BE-0010-update-scope-statement-ja.md)）。web サイトや Android パッケージを「アプリ」と呼ぶ無理は、プラットフォームが増えるたびに大きくなります。
- app-agnostic は prime directive です（[DESIGN §2](../../DESIGN.md)）。テスト対象ごとの差分は config に置き、対象が変わってもツール本体は変えません。`targets.<name>` は、この約束をプラットフォーム非依存の語で述べます。`apps.<name>` は、暗黙に iOS を前提にしています。

「target」は、テストツールでテスト対象（system under test）を指す慣用語です。プラットフォームに依存せず、コードとドキュメントがすでに使っている語でもあります（"the target app"）。

改名は、いまのほうが後よりはるかに安く済みます。config の表面はまだ 1 キーで、プロジェクトは 1.0 に達していません。後になると、用語は 3 プラットフォーム分のドキュメントとシナリオに広がります。

## 詳細設計

### 変更する箇所

改名の対象は、*汎用の入れ物とセレクタ*の語で、全レイヤにわたって一貫して行います。互換エイリアスを設けない**一括の切り替え**とします。リポジトリ自身の config、テスト、ドキュメントを、同じ変更で動かします。

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

動かすのは**汎用の語だけ**です。iOS 固有の概念を正しく指しているフィールド名は残します。改名すると、かえって誤った名前になるためです。

- `bundleId`、`appPath`、`deeplinkScheme` は、iOS 固有のもの（bundle identifier、ビルド済み `.app` へのパス、URL スキーム）を指します。汎用のテスト対象を指す語ではありません。web のテスト対象は、これらを省き、`baseUrl` を設定します。
- `scenarios/<name>/` のレイアウトと、テスト対象ごとのシナリオディレクトリは、形を変えません。キーは対象の名前で、この名前は変わりません。
- 決定的なコア（セレクタ解決、オーケストレータ、ランナー、アサーション）には手を付けません。変更するのは、config と CLI の境界にある語だけです。

### 移行（一括の切り替え）

`apps:` と `targets:` を両方受け付ける期間は設けません。外部の config にとっても、変更は機械的です。

- `apps:` → `targets:`（および `orgs.<o>.apps:` → `orgs.<o>.targets:`）
- 各コマンドの `--app <name>` → `--target <name>`
- API を直接叩く利用者は、`/api/apps` → `/api/targets`

更新後の [configuration.md](../../docs/ja/configuration.md) と [cli.md](../../docs/ja/cli.md)、およびリリースノートの 1 行で、移行は足ります。プロジェクトが 1.0 に達しておらず、config の表面が 1 キーであるうちは、影響範囲は小さく収まります。

## 検討した代替案

- **`apps` を残し、web のテスト対象も流用すると書くだけにする。** 却下しました。この語は web と Android で誤解を招きます。コードも、コメントとバリデータのメッセージですでに「target」と述べています。名前が誤ったことを主張しているキーを、ドキュメントでは直せません。
- **非推奨エイリアスを足す（両方を受理し、`apps:` の使用時に警告する）。** 範囲確定の判断に従い、却下しました。両方を受理する期間は、ドキュメントに 2 つの用語を置き、抱える警告経路を増やし、追跡する削除予定を残します。この費用が報われるのは、外部インストールや公開シナリオが一定の規模に達したときだけです。Bajutsu はまだ達していません。いま切り替えるほうが、後で移行するより安く済みます。
- **設定キーだけ改名し、`--app` は残す。** 却下しました。`targets:` と書く config を、`--app` と言う CLI で駆動するのは一貫しません。セレクタのフラグは、選ぶ対象のキーと一致させるべきです。
- **別の語（`subjects`、`systems`、`suts`、`applications`）を使う。** 却下しました。「target」は、E2E ツールでテスト対象を指す確立した語です。プラットフォーム非依存の語として最短で、コードベースですでに使われています。無用な差分と戸惑いを最小にできます。

## 進捗

- [x] 出荷済み。上記の *実装 PR* を参照してください。

## 参考

- [DESIGN §8](../../DESIGN.md)（CLI と設定：per-app とマルチアプリ）、[DESIGN §2](../../DESIGN.md)（app-agnostic の prime directive）
- [configuration.md](../../docs/ja/configuration.md)、[cli.md](../../docs/ja/cli.md)：設定の階層と `--app` フラグ
- `bajutsu/config.py`：`AppConfig`、`Config.apps`、`OrgConfig.apps`、`resolve` / `org_for_app` / `apps_for_org`
- 関連項目：[BE-0010](../BE-0010-update-scope-statement/BE-0010-update-scope-statement-ja.md)（スコープ文の更新。この改名が相乗りするマルチプラットフォームのドキュメント移行）、[BE-0009](../BE-0009-cross-platform-abstractions/BE-0009-cross-platform-abstractions-ja.md)（抽象のクロスプラットフォーム化）、[BE-0041](../BE-0041-web-playwright-backend/BE-0041-web-playwright-backend-ja.md)（Web Playwright バックエンド。「アプリ」を実態に合わない名前にした、着地済みのプラットフォーム）、[BE-0042](../BE-0042-platform-backend-registry/BE-0042-platform-backend-registry-ja.md)（プラットフォーム対応バックエンドレジストリ）
