[English](BE-0229-per-org-provider-settings-resolution.md) · **日本語**

# BE-0229 — serve の AI プロバイダー設定を組織ごとに実行時に解決する

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0229](BE-0229-per-org-provider-settings-resolution-ja.md) |
| 提案者 | [@hirosassa](https://github.com/hirosassa) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0229") |
| 実装 PR | [#955](https://github.com/bajutsu-e2e/bajutsu/pull/955) |
| トピック | AI プロバイダ設定 |
| 関連 | [BE-0184](../BE-0184-persist-serve-ai-provider-settings/BE-0184-persist-serve-ai-provider-settings-ja.md), [BE-0183](../BE-0183-per-provider-serve-settings/BE-0183-per-provider-serve-settings-ja.md), [BE-0015](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting-ja.md) |
<!-- /BE-METADATA -->

## はじめに

現在の serve の Web UI は、AI プロバイダーの選択、model、reasoning effort をプロセス全体で共有する形で
解決しています。設定を保存すると `os.environ` に書き込まれます。`bajutsu/serve/operations/config.py` の
`_apply_provider_env` が、`PROVIDER_ENV`、`MODEL_ENV`、`EFFORT_ENV` と Bedrock 用のスロットを設定します。
AI の各経路は、共有された 1 つのプロセス環境から値を読み取ります。起動されるジョブも、その環境を継承します。

ホスティングされたマルチテナントの serve（BE-0015）では、成果物、シナリオ、ベースライン、secret が
すでに組織単位で分離されています。分離には組織ごとの `StoreBundle` を使い、リクエストの組織から
`ServeState.org_of` で解決します。AI プロバイダーの選択だけが、プロセス全体のままです。

本項目では、プロバイダー、model、effort を実行時に組織ごとに解決します。各組織の `record`、triage、
下書き生成の経路は、共有の 1 つではなく、その組織が保存した選択を使うようになります。BE-0184 は、組織ごとの
DB ベースのストアを保留したときに、この仕組みを前提として挙げていました。

## 動機

BE-0184 は、プロバイダー設定の永続化を実装したうえで、組織ごとの DB ベースのストア版を意図して保留
しました。その理由は次のとおりです。「現状の serve ではプロバイダー・model・effort がプロセス全体で
解決され（`os.environ` と単一の `ServeState.provider_settings` マップ）、組織単位になっていないため、
組織ごとのストアを用意しても組織単位で読み出す値が存在しない」。本項目が埋めるのは、この隙間です。

組織ごとの実行時解決がなければ、1 つのホスティングされた serve プロセスを共有する 2 つの組織は、1 つの
プロバイダー、model、effort の選択を共有します。最後に保存した人の選択が全員に適用され、BE-0015 が
他のすべての状態を持つ面に与えているテナント分離が崩れます。

BE-0184 が保留した組織ごとの DB ベースのストアは、組織単位で読み出す経路ができて初めて意味を持ちます。
本項目が、その読み出し経路にあたります。

## 詳細設計

1. **組織ごとの設定状態。** 単一の `ServeState.provider_settings` マップと選択中プロバイダーを、組織を
   キーとする構造に置き換えます。解決には、リクエストハンドラーが組織ごとの `StoreBundle` のために
   すでに計算している `org_of` を使います。ローカルの serve は組織が `default` の 1 つだけです。
   そのため、今日と同じ形（バンドル 1 つ、設定スロット 1 つ）を保ちます。
2. **共有プロセス環境ではなくリクエストごとの解決。** 現状 `os.environ` からプロバイダー、model、effort
   を読み取っている AI の各経路は、リクエスト元の組織の設定から解決するように変えます。ジョブは親の
   環境を継承する子プロセスとして起動されます。したがって、解決した組織ごとの値は、共有の `os.environ`
   を書き換えずに、そのジョブ自身の環境（ジョブ単位の環境オーバーレイ）として渡す必要があります。
   そうしないと、ある組織の保存が他のすべての組織のジョブに漏れます。ここが要となる変更で、下記の
   ストアの作業はこれに依存します。
3. **BE-0184 が保留した組織ごとの DB ベースのストアを配線する。** 組織ごとの解決が入ったうえで、
   DB ベースの `ProviderSettingsStore` 版（`DbSecretStore` と並ぶ形）を追加します。secret store と
   ジョブ記録がすでに使っているリポジトリの継ぎ目を通じて、組織ごとに読み書きします。これにより、
   ホスティング環境で保存した選択が、組織ごとに再起動をまたいで残ります。
4. **ローカルとのパリティとゼロコンフィグを変えない。** ローカルの serve は、組織が `default` の 1 つで、
   データベースを持ちません。BE-0184 のファイルベースの `LocalProviderSettingsStore` と、今日の解決の挙動を
   そのまま保ちます。何も永続化されていないときの AI なしのゼロコンフィグ経路（BE-0101）も、同様に
   手を付けません。

## 検討した代替案

- **プロセス全体の環境変数のまま、serve プロセスごとに 1 つのプロバイダー選択で妥協する。** 却下しました。
  ホスティングされたマルチテナント環境で、テナント分離が静かに崩れます。他のすべての状態を持つ面
  （成果物、シナリオ、secret）はすでに組織ごとです。プロバイダー選択だけを共有のままにすると、ある運用者の
  保存が、他のすべての組織の AI 実行を変えてしまいます。
- **組織ごとの実行時解決を先にせず、組織ごとの DB ベースのストアだけを追加する。** これは BE-0184 が保留した
  項目を直接やる案です。却下しました。BE-0184 が記録した理由のとおり、保存した値を組織ごとに読み出す先が
  ないため、効果のない状態を永続化することになります。実行時解決が前提であり、だからこそ別項目としています。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [x] 組織ごとのプロバイダー設定状態を、組織ごとの `StoreBundle` と同じキーで導入する。
- [x] プロバイダー・model・effort をリクエスト／ジョブごとに組織の設定から解決し、共有プロセス環境を書き
  換えるのではなくジョブ単位の環境オーバーレイとして渡す。
- [x] 組織ごとの DB ベースの `ProviderSettingsStore` 版（BE-0184 が保留した項目）を追加する。
- [x] ローカルとのパリティ（組織は `default` の 1 つ、ファイルベースのストア）とゼロコンフィグ経路が変わら
  ないことを確認する。

### ログ

- 組織ごとのプロバイダー解決とジョブ単位の環境オーバーレイ、DB ベースのストアを実装しました。
  `ServeState.provider_settings`（組織をキーとする）、`serve.operations.config`（`provider_env` ／
  `resolve_provider_env` ／遅延読み込みの `_org_settings`）、`_spawn_env`（管理対象の環境変数を消して
  からオーバーレイを適用）、`_register_and_dispatch` の接続点、ワーカーのジョブ仕様、マイグレーション
  `0011_provider_settings` を通して配線しています。ローカル serve とゼロコンフィグ経路は変わりません。
  ([#955](https://github.com/bajutsu-e2e/bajutsu/pull/955))

## 参考

- [BE-0184](../BE-0184-persist-serve-ai-provider-settings/BE-0184-persist-serve-ai-provider-settings-ja.md)：serve の AI プロバイダー設定を再起動をまたいで永続化する。本提案の前提であり、組織ごとの DB ベースのストアを保留した項目です。
- [BE-0183](../BE-0183-per-provider-serve-settings/BE-0183-per-provider-serve-settings-ja.md)：serve の Web UI で AI プロバイダーごとの設定を持てるようにする。
- [BE-0136](../BE-0136-serve-write-once-secrets/BE-0136-serve-write-once-secrets-ja.md)：serve 向けの write-once の secret store。組織ごとの DB ベースのストレージの先例です。
- [BE-0015](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting-ja.md)：Web UI のホスティング公開。組織ごとの分離が必要になる文脈です。
- [BE-0101](../BE-0101-ai-free-zero-config/BE-0101-ai-free-zero-config-ja.md)：AI を使う経路と使わない経路を明確に分け、ゼロコンフィグの非 AI 経路を用意する。
