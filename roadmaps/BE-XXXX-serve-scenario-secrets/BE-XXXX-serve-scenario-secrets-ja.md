[English](BE-XXXX-serve-scenario-secrets.md) · **日本語**

# BE-XXXX — serve の Web UI からシナリオが宣言したシークレットを設定する

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-XXXX](BE-XXXX-serve-scenario-secrets-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **提案** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| トピック | セキュリティ強化 |
| 関連 | [BE-0032](../BE-0032-secret-variables/BE-0032-secret-variables-ja.md)、[BE-0136](../BE-0136-serve-write-once-secrets/BE-0136-serve-write-once-secrets-ja.md) |
<!-- /BE-METADATA -->

## はじめに

シナリオが必要とするシークレットは、config の `secrets:` に宣言します。これは `${secrets.X}` が
実行時に解決する環境変数名のリストです（BE-0032）。現状、この値を供給する手段はプロセスの環境変数
だけで、`bajutsu` CLI や `serve` がどの環境で起動されたかに依存します。バインド中の config がどの
名前を宣言しているかを確認したり、その値を設定したりする手段は `serve` の Web UI に一切ありません。
この提案は、`serve` が既に持つ書き込み専用の `SecretStore`（BE-0136）をこの用途にも広げます。
アクティブな config が宣言する名前ごとにマスク済みの書き込み専用フィールドを並べる **Secrets** パネル
を追加し、`serve` を起動する前に環境変数を export したり `.env` を手編集したりしなくても、Web UI から
シナリオのシークレットを用意できるようにします。

## 動機

`bajutsu/config/schema.py:234` の `Defaults.secrets`（ターゲットごとに上書き可能で、
`bajutsu/config/resolve.py:165` の `resolve` でマージされます）は環境変数名のリストです。run の開始時、
`_resolve_secrets`（`bajutsu/cli/commands/run.py:231`）は宣言された各名前をプロセスの環境変数から解決し、
`secrets.X` という束縛を作ります。シナリオファイルが持つのは常にトークンだけで、値そのものは持ちません
（BE-0032 の動機）。この設計は値をバージョン管理や証跡の外に
保ちますが、その値をそもそもどうやって環境変数に入れるかについては何も定めていません。そして `serve`
に関する今の答えは「ツールを経由しない」です。

- **ローカルの `serve`**（[BE-0011](../BE-0011-local-web-ui-serve/BE-0011-local-web-ui-serve-ja.md)）は、
  `make serve` を起動したシェルが export した値をそのまま引き継ぎます。`${secrets.LOGIN_PASSWORD}` を
  必要とするシナリオを動かしたい運用者は、それを `export` するか `.env` に書いて起動前に読み込ませる
  しかありません。Record や Replay、Crawl といった他の作業がすべて Web UI の中で完結するのに対して、
  この一手順だけが UI の外に取り残されています。
- **セルフホスティング**（[BE-0016](../BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting-ja.md)）は、
  Secrets の行を「現状の出荷形態: `.env`」と記していて、同じ管理外のギャップを指摘しています。
- `serve` は同じ形の問題を、**運用者向け**の認証情報については既に3つ解決済みです。Claude の
  API キー（[BE-0136](../BE-0136-serve-write-once-secrets/BE-0136-serve-write-once-secrets-ja.md)）、
  `claude-code` の OAuth トークン（[BE-0215](../BE-0215-claude-code-oauth-token-credential/BE-0215-claude-code-oauth-token-credential-ja.md)）、
  Git config ソース用のトークン（[BE-0224](../BE-0224-github-private-repo-config-auth/BE-0224-github-private-repo-config-auth-ja.md)）
  はいずれも Web UI から書き込み専用（マスクされ、二度と平文で読み出せない）で設定できます
  （`bajutsu/serve/operations/config.py:41-56`）。**シナリオ自身**が宣言するシークレットこそ、BE-0136
  が汎用化したストアが本来カバーするはずだった一つ（詳細設計の項目5「一つの名前付きシークレットを
  超えて汎用化する」）でありながら、実際には手が付いていません。

ここを埋めれば、`serve` はシナリオが必要とする認証情報の全体をツールの外に出ることなく揃えられる
ようになります。BE-0136 が運用者向け認証情報に確立した非開示の保証（書き込み専用、マスク済み、
二度と読み出せない）と同じものを、シナリオのシークレットにも適用します。

## 詳細設計

`run` / CI の経路には一切触れません。BE-0136 や BE-0187 と同じく、`serve` の Tier‑1 の運用設定機能の
内側だけで完結します。

**1. バインド中の config から宣言済みの名前を解決する。** `bajutsu/config/resolve.py:141` の
`resolve()` をアクティブな config に対して呼び、各ターゲットの実効的な `secrets:` リストを取得し、和集合を取って
パネルに出す名前の集合とします（シナリオはどのターゲットに対しても走らせられるため）。config が
バインドされていない場合や `secrets:` が空の場合は空のリストになり、パネルには設定すべきものが
何も表示されません。プロバイダが未選択のときの設定パネルと同じ振る舞いです。

**2. 読み取り操作と `GET /api/secrets`。** `[{"name": str, "set": bool, "masked": str | None}, ...]`
を、宣言済みの名前ごとに1件返します。`masked` は `state.for_org(org).secrets.describe(name)` から
取得し、平文の `value` は決して含めません。`api_key_info`（`bajutsu/serve/operations/config.py:183`）
と同じ形です。`GET /api/apikey` と同様、ロールによるゲートは付けません
（`bajutsu/serve/authz.py:169` の `required_role` は、値を含まない describe 系のレスポンスをどの
ロールでも安全と扱っています）。開示されるのは名前の存在と設定済みかどうかだけで、値は含まれません。

**3. 書き込み操作と `POST /api/secrets`。** ボディは `{"name": str, "value": str}` です。解決済みの
宣言集合に含まれない `name` は 400 で拒否します。これにより、このエンドポイントが任意の環境変数を
書き換えられる汎用の書き込みプリミティブになることを防ぎ、バインド中の config 自身が要求している
名前だけを設定可能にします。有効な名前は既存の `SecretStore.set` を通して設定し、あるいは解除します
（空の `value` で解除する契約は既存の3つの名前付きシークレットと同じです）。`admin` ロールでゲートし、
`bajutsu/serve/authz.py:122` の `_ADMIN_PATHS` に `/api/apikey` / `/api/claudecodetoken` /
`/api/gitcredential` と並べて追加します。認証情報を設定する操作は、まさにそのロールの作業です。

**4. 名前から環境変数へのマッピングを拡張する。** `ServeState._env_var_for_secret`
（`bajutsu/serve/state.py:585`）は現状、3つの固定された論理名だけをマッピングし、それ以外は
`active_key_env(self)` にフォールバックします。これはシナリオのシークレット名に対しては静かに
間違った挙動で、本来書き込むべき環境変数ではなく AI キー用の環境変数を上書きしてしまいます。
`name` が解決済みの宣言集合に含まれる場合の分岐を追加し、その場合は環境変数名を `name` そのものと
します（BE-0032 の `secrets:` に並ぶ各エントリは、そもそも環境変数名そのものであり、別の名前の
環境変数にマッピングされる3つの固定論理名とは違います）。`os.environ[name] = value` を呼ぶ前に、
既存の `_valid_key_env_name`（`bajutsu/serve/operations/config.py:77`）で識別子として妥当かどうか、
また `_UNSAFE_ENV_VARS` に含まれるシステム変数でないかどうかを検証します。

**5. UI: 「シナリオのシークレット」セクション。** `bajutsu/templates/serve.core.mjs` にある、Claude
API キー、`claude-code` トークン、Git 認証情報という既存の3ブロックと並べて、`GET /api/secrets` が返す
名前ごとにマスクされた書き込み専用フィールドを1つ描画する新セクションを追加します（リストが空なら
セクションごと非表示にします）。BE-0136 の慣習にならった `renderX` / `applyX` の対と同じ操作感を、
名前ごとに手書きするのではなく宣言済みリストに対するループとして一般化します。ヘッダの config 名が
既に反応しているのと同じイベント、つまりバインド中の config が変わるたびに宣言済みリストを再取得し、
`secrets:` の内容が異なる config に切り替えたときにパネルを更新します。

**6. 保存はどちらのバックエンドでも今すぐ動く。follow-up はセルフホストの消費側だけ。** バックエンド
の切り替えは仕組み（seam）が引き受けるため、上記の2つのエンドポイントは、本項目が入った瞬間に
**両方**のデプロイで動く状態で入ります。

- **ローカルの `serve`。** `EnvSecretStore` はローカル `serve` プロセスの `os.environ` に書き込み
  （項目4の名前 → 環境変数のマッピング）、それを `jobs._spawn_env`（`bajutsu/serve/jobs.py:36`）を
  通じて spawn される `record` / `run` / `crawl` のサブプロセスが引き継ぎます。したがって UI から設定
  した値は保存も消費も追加の配線なしに済み、run のなかで `${secrets.X}` が解決します。
- **セルフホスト（`server` バックエンド）。** `state.for_org(org).secrets` は既にホスト型の
  `DbSecretStore`（`bajutsu/serve/server/secrets.py`）を返します。これは任意の名前付きシークレットを
  org ごとに保存時暗号化（Fernet、`BAJUTSU_SECRETS_KEY` を鍵とする）します。BE-0136 の3番目の項目が、
  まさにこの用途に汎用化できるよう作ったものです。したがって `GET` / `POST /api/secrets` は、
  **セルフホストのデプロイでも、新しい保存コードなしに org ごとのシナリオシークレットを設定し、
  describe します**。同じ暗号化済みの `secrets` テーブル、同じ書き込み専用の保証です。まだ配線されていないのは
  **消費側**です。run はコントロールプレーンのプロセスではなくリモートのワーカー
  （`bajutsu/serve/server/worker_job.py`）で実行されるため、`${secrets.X}` をそこで解決させるには、
  保存された値をそのワーカーが spawn する `bajutsu run` まで届ける必要があります。

**7. セルフホストの消費側の経路（記述する。唯一の follow-up として範囲を切る）。** ならうべき直接の
先例があるため、これは未解決の問いではなく仕様の定まった follow-up です。BE-0229 は既に、org の AI
プロバイダ設定をコントロールプレーン側で enqueue 時に解決し（`dispatch.py:83` の
`resolve_provider_env`）、それを `job.env_overlay` にしてジョブ仕様に載せ、ワーカーが `_spawn_env`
（`jobs.py:49`）で spawn 環境にマージすることで、ワーカー自身が設定を持たずにその org の選択で run を
走らせています。シナリオのシークレットも同じレールに載せます。enqueue 時にコントロールプレーンが org
の宣言済みシークレットを `DbSecretStore` から解決してワーカーへ渡し、ワーカーの `_spawn_env` がそれぞれ
を宣言済みの環境変数名の下に置いて、`bajutsu run` のサブプロセスが `${secrets.X}` を解決します。ここを
別項目とし本項目に畳み込まない理由は、プロバイダオーバーレイでは生じないセキュリティ上の問いがある
ためです。シナリオシークレットの平文がジョブ仕様に載ってキューを流れることになるので、follow-up では
次のどちらかを決める必要があります。(a) `oplog` やマスキングが機微として扱い、キューのペイロードが
決してログに出さない専用のシークレットオーバーレイにする、あるいは (b) ワーカーに
`BAJUTSU_SECRETS_KEY` を渡し、ワーカー自身が org のシークレットを取得して復号することで、平文が
キューを一切流れないようにする、のいずれかです。これはコントロールプレーンとワーカーの信頼境界に関する判断
（[BE-0167](../BE-0167-control-plane-scale-out/BE-0167-control-plane-scale-out-ja.md) の領域）であり、
それ自体で別途レビューされるべきです。BE-0224 がホスト型への注入について引いた境界とちょうど同じです。

**8. テスト。** serve の HTTP テストハーネスを拡張します。`GET /api/secrets` がバインド中の config
の宣言済み名前を反映し（config が変わると更新され)、`value` を一切含まないこと。`POST /api/secrets`
が宣言集合にない名前を 400 で拒否すること。有効な名前に対する設定 → describe の往復がマスク済みの
プレビューだけを返すこと。エンドポイント経由で設定した値が、spawn されたローカル run のサブプロセス
環境に宣言済みの名前で引き継がれること、を確認します。ホスト型のストアには BE-0136 の org ごとの
暗号文の契約テストが既にあるので、シナリオが宣言した名前が `DbSecretStore` を往復すること（設定 →
describe がマスクを返し、`ciphertext` 列が平文を決して持たないこと）を追加し、消費側が follow-up でも
セルフホストの**保存**側は確認済みにします。

**9. ドキュメント。** `docs/web-ui.md` / `docs/ja/web-ui.md`（新しい Secrets パネル）と、
`docs/self-hosting.md` / `docs/ja/self-hosting.md` を更新します。セルフホスティングの Secrets の行は、
シナリオのシークレットについて「現状の出荷形態: `.env`」から、次のように更新します。今の時点で Web UI
から org ごとに設定でき保存時に暗号化される（運用者向け認証情報のために既に用意されている
`BAJUTSU_SECRETS_KEY` を再利用）。そのうえで、セルフホストの run が保存済みのシナリオシークレットを
リモートワーカーで消費する部分は追跡中の follow-up である（項目7）、と注記します。

## 検討した代替案

- **config の `secrets:` 宣言に紐付けず、運用者が任意の名前を自由に入力できる汎用のシークレット
  マネージャにする。** GitHub Actions によく似た自由入力のシークレットストアに近い形です。この提案
  では不採用とします。パネルがシナリオの実際の必要から切り離されてしまい、任意の環境変数を書き換え
  られる書き込みプリミティブを再び持ち込むことになり、宣言済みの名前を打ち間違えたときや、使われなく
  なった古いシークレットが片付かないままになったときにも何もフィードバックがありません。パネルを
  解決済みの `secrets:` リストに紐付けておけば、宣言と実際の設定が常に一致します。宣言されていない
  即興のシークレットが本当に必要になれば、同じ `SecretStore` の仕組みの裏に自由入力のストアを後から
  重ねることもできます。
- **セルフホストのワーカーによるシークレットの*消費*を、この提案で一緒に配線する。** BE-0224 自身の
  先送りと同じ理由で範囲外とします。ただし境界は見た目より狭く、シナリオシークレットを org ごとに
  *保存*する部分は `DbSecretStore` を通じてセルフホストでも既に動きます（詳細設計6）。先送りになるのは
  「保存済みの値をリモートワーカーの spawn する run に注入する」部分だけです。ここに含めると、
  コントロールプレーンとワーカーの信頼境界に関する判断（復号したシークレットをジョブ仕様に載せて流すのか、
  それともワーカーが `BAJUTSU_SECRETS_KEY` を持って自分で復号するのか。詳細設計7）を Web UI の機能に
  畳み込むことになります。この判断
  （[BE-0167](../BE-0167-control-plane-scale-out/BE-0167-control-plane-scale-out-ja.md) の領域）は、
  それ自体で別途レビューされるべきです。
- **`POST /api/secrets` を任意の環境変数名に対して許可し、`admin` ロールだけでゲートする。**
  不採用とします。`serve` の `admin` ロールはあくまで Web UI の運用者であり、シェルではありません。
  `admin` でゲートしていても `os.environ` への無制限の書き込みプリミティブは、「この config が要求
  している名前だけ」に比べて影響範囲が大きく、しかもシナリオが実際に使う機能を何も増やしません。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [ ] バインド中の config が宣言する `secrets:` の名前を解決する（ターゲット横断の和集合）
- [ ] `GET /api/secrets`（読み取り操作）。ロールゲートなし、平文の値を含まない
- [ ] `POST /api/secrets`（書き込み操作）。admin ゲート、未宣言の名前は拒否
- [ ] `ServeState._env_var_for_secret` をシナリオのシークレット名向けに拡張する
- [ ] UI: `serve.core.mjs` に「シナリオのシークレット」セクションを追加し、config 変更時に再取得する
- [ ] 両バックエンドが名前ごとに保存する。ローカルの `EnvSecretStore` と（セルフホストの）
      `DbSecretStore` — seam の切り替えは継承されるため、新しい保存コードではなくテストと検証の項目
- [ ] テスト: 宣言済み名前の反映、未宣言名前の拒否、マスクのみの往復、spawn されたローカル run への
      環境引き継ぎ、ホスト型 `DbSecretStore` の org ごとの往復（`ciphertext` が平文を持たない）
- [ ] ドキュメント更新（`docs/web-ui.md`、`docs/self-hosting.md`、両言語）

**範囲外（follow-up）。** セルフホストの*消費*側だけです。保存済みの org ごとのシナリオシークレットを、
リモートワーカーが spawn する `bajutsu run` に注入して、そこで `${secrets.X}` を解決させる部分です。
シークレットを org ごとに保存する部分はセルフホストでも既に動く（ホスト型の `DbSecretStore` が保存時に
暗号化する）ため、残っているのはコントロールプレーンからワーカーへの注入と、その信頼境界に関する判断
（詳細設計7）であり、BE-0224 が Git config ソース用トークンについて残している同種のギャップと合わせて
follow-up として扱います。

## 参考

`bajutsu/config/schema.py:234`（`Defaults.secrets`）と `bajutsu/config/resolve.py:165`（`resolve` での
マージ）、`bajutsu/cli/commands/run.py:231`（`_resolve_secrets`）、
`bajutsu/serve/state.py:585`（`_env_var_for_secret`）、`bajutsu/serve/operations/config.py:41-56`
（既存の3つの名前付きシークレット）と `:183`（`api_key_info`）、`bajutsu/serve/authz.py:122`
（`_ADMIN_PATHS`）と `:169`（`required_role`）、`bajutsu/serve/secrets.py`（ローカルの `SecretStore`
の仕組み）と `bajutsu/serve/server/secrets.py`（ホスト型の `DbSecretStore`、org ごとの Fernet）、
`bajutsu/serve/jobs.py:36`（`_spawn_env`）と `bajutsu/serve/server/worker_job.py` /
`bajutsu/serve/operations/dispatch.py:83`（`env_overlay`。セルフホストの消費側の経路がならう、BE-0229
の enqueue 時オーバーレイ）、`bajutsu/templates/serve.core.mjs`（既存のマスク済み書き込み専用 UI
ブロック）。関連:
[BE-0032](../BE-0032-secret-variables/BE-0032-secret-variables-ja.md)（シナリオが `${secrets.X}` を
どう消費するか）、[BE-0136](../BE-0136-serve-write-once-secrets/BE-0136-serve-write-once-secrets-ja.md)
（この提案が拡張する書き込み専用 `SecretStore` の仕組み）、
[BE-0016](../BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting-ja.md)（この提案が埋める
「現状の出荷形態: `.env`」というギャップを指摘している）、
[BE-0224](../BE-0224-github-private-repo-config-auth/BE-0224-github-private-repo-config-auth-ja.md)
（ホスト型への注入を follow-up とする先例）。
