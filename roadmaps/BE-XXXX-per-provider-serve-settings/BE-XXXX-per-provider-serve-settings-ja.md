[English](BE-XXXX-per-provider-serve-settings.md) · **日本語**

# BE-XXXX — serve の Web UI で AI プロバイダーごとの設定を持てるようにする

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-XXXX](BE-XXXX-per-provider-serve-settings-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **提案** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| トピック | AI provider configuration |
| 関連 | [BE-0104](../BE-0104-vendor-neutral-ai-backend/BE-0104-vendor-neutral-ai-backend-ja.md)、[BE-0163](../BE-0163-ant-cli-oauth-provider/BE-0163-ant-cli-oauth-provider-ja.md)、[BE-0176](../BE-0176-claude-code-ai-backend/BE-0176-claude-code-ai-backend-ja.md)、[BE-0175](../BE-0175-serve-web-ui-ant-sso-login/BE-0175-serve-web-ui-ant-sso-login-ja.md) |
<!-- /BE-METADATA -->

## はじめに

serve の Web UI の Settings 画面では、登録済みの AI プロバイダー（`api-key` / `bedrock` /
`ant` / `claude-code`、BE-0104）を1つ選び、それとあわせて `model` と推論の `effort`（reasoning
effort、推論に割く度合いを段階指定するパラメータ）を設定します。しかし現状、この2項目はどの
プロバイダーを選んでいても共有される1組の値として保存されており、実際にはどちらもプロバイダー
に紐づく性質を持っています。有効な model の値も、`effort` がそもそも意味を持つかどうかも、
プロバイダーごとに異なります（`claude-code` の CLI は `--effort` を受け付けますが、SDK 経由の
プロバイダーはこれを無視します）。本提案は、プロバイダーごとの設定を保持できるデータ構造を導入
し、Settings 画面をそのデータ構造越しに読み書きするよう変更するものです。これにより、プロバイ
ダーを切り替えても、切り替え前のプロバイダーに設定していた値が失われなくなります。

## 動機

Settings 画面でプロバイダーのドロップダウンを切り替えると、直前に選んでいたプロバイダーの
`model`/`effort` が黙って失われます。両者は serve プロセスの環境変数上の `MODEL_ENV`/
`EFFORT_ENV` という1組の値の裏にあり（`bajutsu/serve/operations/config.py` の
`set_provider`）、別のプロバイダーを保存するとその組がそのまま上書きされるためです。たとえば
`claude-code` に `effort=high` と特定の model を設定したあと、比較のために `api-key` を試し、
再び `claude-code` に戻すと、model と effort は消えています。意図的にクリアされたわけではなく、
`api-key` の保存時にたまたま入っていた値（多くの場合は空）で上書きされてしまっただけです。

現状の実装でも、Bedrock の model だけは専用の枠（`BEDROCK_MODEL_ENV`）を持ち、他の3プロバイダー
が共有する `MODEL_ENV` とは分離されています。Bedrock の model 値はプロバイダー固有の接頭辞が付い
た形式で、他のプロバイダーでは無効な値になるためです。この分離の発想自体は正しいのですが、
適用が中途半端です。`effort` にはこうした専用の枠がなく、また非 Bedrock 系の3プロバイダーも
1つの model 枠を共有したままです。`claude-code`（CLI をサブプロセスとして呼ぶ実装）と
`api-key`/`ant`（Anthropic SDK を使う実装）は別のバックエンドであり、model を共有する理由は
ありません。Bedrock だけに適用されている分離をすべての項目・全プロバイダーに一般化すれば、この
場当たり的な例外を解消しつつ、状態が共有されることによる値の消失も同時に直せます。

## 詳細設計

1. **データ構造。** プロバイダー名をキーとする設定構造を導入します。たとえば
   `dict[str, ProviderSettings]` とし、`ProviderSettings` は `model`、`effort`、そして
   `bedrock` のときだけ `region` を保持します。保持期間は serve プロセスの生存期間中とし、
   現状 `provider`/`model`/`effort` が `os.environ` 上で持っている性質（セッション限りでディ
   スクには書かない。BE-0175 がプロバイダー選択自体についてすでにこの制約を明記しています）
   をそのまま踏襲します。serve の再起動をまたいでこの構造を永続化することは本提案の範囲外とし、
   データ構造と UI 配線に集中するため別項目に切り出します。
2. **読み取り API。** `GET /api/provider`（`provider_info`）は、選択中のプロバイダーの項目
   だけでなく、プロバイダーごとの設定一式を返すようにします。これにより Settings 画面は最初の
   1回ですべてのプロバイダーの `model`/`effort`/`region` 入力欄をあらかじめ埋めておけて、ドロッ
   プダウンを切り替えるたびに通信する必要がなくなります。
3. **書き込み API。** `POST /api/provider`（`set_provider`）は、選択されたプロバイダーの枠
   にだけ書き込み、他のプロバイダーの枠には触れません。選択中のプロバイダーの枠の内容が、
   実行中のジョブがすでに参照している既存の `MODEL_ENV`/`EFFORT_ENV`/`BEDROCK_MODEL_ENV`/
   `AWS_REGION` という環境変数に反映される、という流れ自体は変えません。したがってジョブ起動
   側のコードに変更は不要で、本提案が変えるのは Settings 画面がこれらの値をどう組み立てて書き
   込むかだけです。
4. **Web UI。** プロバイダーの `<select>` の change ハンドラー（`bajutsu/templates/serve.js`）
   を変更し、選び直した先のプロバイダー自身が覚えている値を、取得済みのマップから表示欄
   （`model`/`effort`/region）に反映するようにします。これまでのように、切り替え前のプロバイ
   ダーの値が共有されたテキストボックスにそのまま残っているように見える状態を避けられます。
5. **バリデーション。** 各プロバイダーの枠には、現状のプロバイダーごとのバリデーション
   （Bedrock では model の入力必須、`effort` は `EFFORT_LEVELS` に含まれるか、空白文字を含まな
   いか、など）をそのまま適用します。バリデーション自体はもともと実質的にプロバイダーごとに
   分かれていたので、変わるのはそれが書き込む先のデータ形状だけです。

## 検討した代替案

- **プロバイダーを切り替えるたびに `model`/`effort` を記憶せずクリアする案。** 実装は単純です
  が、本来の不満を解消できません。2つのプロバイダーを行き来して比較したい運用担当者は、切り替
  えるたびに両方の項目を入力し直すことになり、これは本提案がなくそうとしている手間そのものです。
- **`model`/`effort` を serve のランタイム構造ではなく、YAML の `ai:` 設定スキーマ
  （`defaults.ai` / `targets.<name>.ai`、BE-0047）側にプロバイダーごとのマップとして持たせる
  案。** 静的な設定ファイルはすでに1ターゲット=1プロバイダーを前提としており、そこには解消
  すべき曖昧さがありません。今回の混乱は Settings 画面でプロバイダーを対話的に切り替える操作に
  特有のものです。この修正を serve 側に閉じることで、UI だけの問題を解決するために設定スキーマ
  （とその BE-0112 の決定論コア境界）を広げずに済みます。設定ファイル側での複数プロバイダー
  プロファイルは、将来そのユースケースが現れた場合の拡張として残しておきます。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [ ] serve が保持するプロバイダーごとの設定構造を定義する。
- [ ] `GET /api/provider` からプロバイダーごとの設定一式を返す。
- [ ] `POST /api/provider` の書き込みを選択中プロバイダーの枠に限定する。
- [ ] Settings 画面を、取得済みマップからプロバイダーごとに表示欄を切り替えるよう更新する。

## 参考

- [BE-0104](../BE-0104-vendor-neutral-ai-backend/BE-0104-vendor-neutral-ai-backend-ja.md) — 本提案が設定対象とするプロバイダー登録の仕組み（ベンダー中立な AI バックエンド interface）。
- [BE-0047](../BE-0047-ai-data-sovereignty/BE-0047-ai-data-sovereignty-ja.md) — `ai:` 設定ブロックとプロバイダー非依存の AI パスを定めた項目。
- [BE-0163](../BE-0163-ant-cli-oauth-provider/BE-0163-ant-cli-oauth-provider-ja.md) — `ant` CLI による OAuth プロバイダー。
- [BE-0176](../BE-0176-claude-code-ai-backend/BE-0176-claude-code-ai-backend-ja.md) — `claude-code` プロバイダー（`--effort` を受け付ける唯一の系）。
- [BE-0175](../BE-0175-serve-web-ui-ant-sso-login/BE-0175-serve-web-ui-ant-sso-login-ja.md) — serve の Web UI から `ant` にサインインする項目。プロバイダー選択がセッション限りでディスクに書かれない性質をここで明記しています。
- 後続項目「serve の AI プロバイダー設定を再起動をまたいで永続化する」（未採番）は、この構造を
  serve の再起動後も残るようにすることを提案しています。
