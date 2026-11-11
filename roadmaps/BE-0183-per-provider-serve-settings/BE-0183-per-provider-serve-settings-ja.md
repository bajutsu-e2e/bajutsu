[English](BE-0183-per-provider-serve-settings.md) · **日本語**

# BE-0183 — serve の Web UI で AI プロバイダーごとの設定を持てるようにする

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0183](BE-0183-per-provider-serve-settings-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0183") |
| 実装 PR | [#762](https://github.com/bajutsu-e2e/bajutsu/pull/762) |
| トピック | AI プロバイダ設定 |
| 関連 | [BE-0104](../BE-0104-vendor-neutral-ai-backend/BE-0104-vendor-neutral-ai-backend-ja.md)、[BE-0163](../BE-0163-ant-cli-oauth-provider/BE-0163-ant-cli-oauth-provider-ja.md)、[BE-0176](../BE-0176-claude-code-ai-backend/BE-0176-claude-code-ai-backend-ja.md)、[BE-0175](../BE-0175-serve-web-ui-ant-sso-login/BE-0175-serve-web-ui-ant-sso-login-ja.md) |
<!-- /BE-METADATA -->

## はじめに

serve の Web UI の Settings 画面では、AI プロバイダーを1つ選びます。選べるのは登録済みの
`api-key` / `bedrock` / `ant` / `claude-code` です（BE-0104）。あわせて `model` と、推論の `effort`（reasoning
effort、推論に割く度合いを段階指定するパラメータ）も設定します。

現状、この2項目はどのプロバイダーを選んでいても共有される1組の値として保存されています。しかし、
どちらもプロバイダーに紐づく性質を持っています。有効な model の値も、`effort` が意味を持つかどうかも、
プロバイダーごとに異なります。`claude-code` の CLI は `--effort` を受け付けますが、SDK 経由の
プロバイダーは無視します。

本提案は、プロバイダーごとの設定を保持するデータ構造を導入します。Settings 画面は、そのデータ構造を
通して読み書きします。これにより、プロバイダーを切り替えても、切り替え前のプロバイダーに設定した値が
失われなくなります。

## 動機

Settings 画面のドロップダウンでプロバイダーを切り替えると、直前の `model` と `effort` が黙って
失われます。両者は、serve の環境変数 `MODEL_ENV`/`EFFORT_ENV` の1組に格納されます
（`set_provider`、`bajutsu/serve/operations/config.py`）。別のプロバイダーを保存すると、この組が
上書きされます。

たとえば、`claude-code` に `effort=high` と特定の model を設定したとします。比較のために `api-key`
を試し、再び `claude-code` に戻すと、model と effort は消えています。意図的にクリアされたわけでは
ありません。`api-key` の保存時にたまたま入っていた値（多くの場合は空）で、上書きされただけです。

現状の実装でも、Bedrock の model だけは専用の枠（`BEDROCK_MODEL_ENV`）を持ちます。他の3プロバイダーが
共有する `MODEL_ENV` とは別の枠です。Bedrock の model 値は、プロバイダー固有の接頭辞が付いた形式で、
他のプロバイダーでは無効だからです。この分離の発想自体は正しいのですが、適用が中途半端です。

`effort` には専用の枠がありません。非 Bedrock 系の3プロバイダーも、1つの model 枠を共有したままです。
`claude-code`（CLI をサブプロセスとして呼ぶ実装）と `api-key`/`ant`（Anthropic SDK を使う実装）は
別のバックエンドであり、model を共有する理由はありません。

Bedrock だけに適用されている分離を、すべての項目と全プロバイダーに一般化します。そうすれば、場当たり的な
例外がなくなり、状態の共有による値の消失も同時に直ります。

## 詳細設計

1. **データ構造。** プロバイダー名をキーとする設定構造を導入します。たとえば
   `dict[str, ProviderSettings]` とし、`ProviderSettings` は `model`、`effort`、そして `bedrock` の
   ときだけ `region` を保持します。保持期間は serve プロセスの生存期間中です。現状 `provider`/
   `model`/`effort` が `os.environ` 上で持つ性質、つまりセッション限りでディスクには書かない性質を
   踏襲します。BE-0175 が、プロバイダー選択自体についてこの制約をすでに明記しています。serve の
   再起動をまたいだ永続化は本提案の範囲外です。データ構造と UI 配線に集中するため、別項目に切り出します。
2. **読み取り API。** `GET /api/provider`（`provider_info`）は、選択中のプロバイダーの項目だけでなく、
   プロバイダーごとの設定一式を返します。これにより Settings 画面は、最初の1回ですべてのプロバイダーの
   `model`/`effort`/`region` 入力欄をあらかじめ埋めておけます。ドロップダウンを切り替えるたびに通信する
   必要はありません。
3. **書き込み API。** `POST /api/provider`（`set_provider`）は、選択されたプロバイダーの枠にだけ書き込み、
   他のプロバイダーの枠には触れません。選択中のプロバイダーの枠の内容は、既存の `MODEL_ENV`/
   `EFFORT_ENV`/`BEDROCK_MODEL_ENV`/`AWS_REGION` という環境変数に反映されます。実行中のジョブがすでに
   参照している変数なので、ジョブ起動側のコードに変更は不要です。本提案が変えるのは、Settings 画面が
   これらの値をどう組み立てて書き込むかだけです。
4. **Web UI。** プロバイダーの `<select>` の change ハンドラー（`bajutsu/templates/serve.js`）を変更します。
   選び直した先のプロバイダー自身が覚えている値を、取得済みのマップから表示欄（`model`/`effort`/region）
   に反映します。切り替え前のプロバイダーの値が、共有されたテキストボックスにそのまま残っているように
   見える状態を避けられます。
5. **バリデーション。** 各プロバイダーの枠には、現状のプロバイダーごとのバリデーションをそのまま適用します。
   Bedrock では model の入力が必須で、`effort` は `EFFORT_LEVELS` に含まれるか、空白文字を含まないか、
   などを検査します。バリデーション自体はもともと実質的にプロバイダーごとに分かれていました。変わるのは、
   書き込み先のデータ形状だけです。

## 検討した代替案

- **プロバイダーを切り替えるたびに `model`/`effort` を記憶せずクリアする案。** 実装は単純ですが、本来の
  不満を解消できません。2つのプロバイダーを行き来して比較したい運用担当者は、切り替えるたびに両方の項目を
  入力し直すことになります。この手間こそ、本提案がなくそうとしているものです。
- **`model`/`effort` を YAML の `ai:` 設定スキーマ側に持たせる案。** serve のランタイム構造ではなく、
  `defaults.ai` / `targets.<name>.ai`（BE-0047）にプロバイダーごとのマップとして置きます。
  静的な設定ファイルは、すでに1ターゲット=1プロバイダーを前提としています。そこには解消すべき曖昧さが
  ありません。今回の混乱は、Settings 画面でプロバイダーを対話的に切り替える操作に特有のものです。この
  修正を serve 側に閉じれば、UI だけの問題のために設定スキーマ（とその BE-0112 の決定論コア境界）を
  広げずに済みます。設定ファイル側での複数プロバイダープロファイルは、将来そのユースケースが現れた場合の
  拡張として残しておきます。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [x] serve が保持するプロバイダーごとの設定構造を定義する。
- [x] `GET /api/provider` からプロバイダーごとの設定一式を返す。
- [x] `POST /api/provider` の書き込みを選択中プロバイダーの枠に限定する。
- [x] Settings 画面を、取得済みマップからプロバイダーごとに表示欄を切り替えるよう更新する。

ログ:

- [#762](https://github.com/bajutsu-e2e/bajutsu/pull/762)：`ProviderSettings` データクラスと
  `ServeState.provider_settings` を追加しました。`GET /api/provider` は、プロバイダーごとの設定を
  `providers` として返します（現在有効なプロバイダーの枠は環境変数から埋めます）。`POST /api/provider`
  の書き込みは、選択中プロバイダーの枠だけに限定しつつ、その枠をジョブが読む環境変数へ引き続き
  反映します。あわせて Settings の JavaScript を作り直し、取得したマップを保持して、プロバイダーの
  切り替え時に model・effort・region の各欄を差し替えるようにしました。

## 参考

- [BE-0104](../BE-0104-vendor-neutral-ai-backend/BE-0104-vendor-neutral-ai-backend-ja.md)：本提案が設定対象とするプロバイダー登録の仕組み（ベンダー中立な AI バックエンド interface）。
- [BE-0047](../BE-0047-ai-data-sovereignty/BE-0047-ai-data-sovereignty-ja.md)：`ai:` 設定ブロックとプロバイダー非依存の AI パスを定めた項目。
- [BE-0163](../BE-0163-ant-cli-oauth-provider/BE-0163-ant-cli-oauth-provider-ja.md)：`ant` CLI による OAuth プロバイダー。
- [BE-0176](../BE-0176-claude-code-ai-backend/BE-0176-claude-code-ai-backend-ja.md)：`claude-code` プロバイダー（`--effort` を受け付ける唯一の系）。
- [BE-0175](../BE-0175-serve-web-ui-ant-sso-login/BE-0175-serve-web-ui-ant-sso-login-ja.md)：serve の Web UI から `ant` にサインインする項目。プロバイダー選択がセッション限りでディスクに書かれない性質を、ここで明記しています。
- 後続項目「serve の AI プロバイダー設定を再起動をまたいで永続化する」（未採番）は、この構造を serve の再起動後も残すことを提案しています。
