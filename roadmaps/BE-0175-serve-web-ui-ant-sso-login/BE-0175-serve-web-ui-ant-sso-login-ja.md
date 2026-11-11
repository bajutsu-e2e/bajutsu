[English](BE-0175-serve-web-ui-ant-sso-login.md) · **日本語**

# BE-0175 — serve の Web UI から `ant` プロバイダにサインインする

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0175](BE-0175-serve-web-ui-ant-sso-login-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0175") |
| 実装 PR | [#705](https://github.com/bajutsu-e2e/bajutsu/pull/705) |
| トピック | AI プロバイダ設定 |
| 関連 | [BE-0163](../BE-0163-ant-cli-oauth-provider/BE-0163-ant-cli-oauth-provider-ja.md) |
<!-- /BE-METADATA -->

## はじめに

BE-0163 は、公式の Anthropic CLI が持つブラウザベースの OAuth/SSO 認証情報を、`ant` という三つ目の AI プロバイダとして登録しました。これにより、どの AI パスも API キーなしで Claude の Pro/Max/Console シートに課金できます。

serve の Web UI の **Settings** では `ant` プロバイダを選べますが、サインイン自体は UI の外に残っていました。パネルは、`serve` を起動したターミナルで `ant auth login` を実行するよう案内するだけでした。

この項目では、パネルに **Sign in with SSO** ボタンを加え、Web UI からサインインを開始できるようにします。認証情報が書かれた時点で、プロバイダのゲートは到達可能に切り替わります。

## 動機

Settings で `ant` を選んだあとにターミナルへ移って `ant auth login` を実行するのは、つなぎ目が不自然です。運用者はすでに Web UI にいます。CLI が書き込む認証情報は、選んだプロバイダが必要とするものそのものです。Record と Crawl のゲートが読む到達可能性（`GET /api/provider`）も、CLI のサインイン状態をすでに反映しています。足りないのは、同じパネルからサインインを*開始*する手段です。

`ant auth login` は、既定の動作でホスト上のブラウザを開き、CLI 自身がループバックのコールバックを待ち受けます。Web UI の推奨起動方法であるローカルの `make serve` では、serve は次の3つをすれば済みます。CLI を起動し、運用者がブラウザで認証を終えるのを待ち、完了をポーリングします。マシンの認証情報は CLI 自身が書き込むので、トークンが serve を経由することはありません。

## 詳細設計

- **`ant_login` / `ant_login_status` オペレーション**（`bajutsu/serve/operations/config.py`）

  `ant_login` は、ホスト型やマルチテナントのデプロイを拒否します（403）。サインインは、サーバ全体が共有するマシン単位の認証情報を書き込むためです。`ant` バイナリが無い場合も拒否します（400。到達可能性チェックと同じ案内文を再利用します）。それ以外では、注入可能な `ServeState.popen` を通じて `ant auth login` を起動し、202 を返します。このとき `stdin` を閉じ、ブラウザとループバックの経路だけで進むようにします。

  処理中に二度目のクリックがあった場合は、待ち状態のプロセスを supersede します。serve は古いプロセスを終了して新しいプロセスを起動し直し、同時に二つは動かしません。放置されたブラウザ認証のせいで、CLI 自身のタイムアウトまでボタンが固まることはなく、すぐにやり直せます。

  `ant_login_status` は、保持したプロセスをポーリングし、`idle`、`running`、`ok`、`error` を返します。`error` には、CLI の最後の出力行を detail として添えます。
- **ルーティングと RBAC**：`POST /api/ant/login` と `GET /api/ant/login` は、2つのオペレーションにつながります。このパスは `_ADMIN_PATHS` に加わるので、`/api/provider` と同じく管理者の操作になります。実装は `bajutsu/serve/handler.py` と `bajutsu/serve/authz.py` にあります。
- **`ServeState` のハンドル**（`bajutsu/serve/jobs.py`）：`ant_login_proc` フィールドが、実行中のプロセスを保持します。起動する POST とポーリングする GET が、このフィールドを介してプロセスを受け渡します。
- **Settings の UI**：`ant` のセクションに、**Sign in with SSO** ボタンとステータス行が加わります。ボタンのラベルと活性状態は `GET /api/provider` から導くので、「Signed in ✓」の表示とゲートは食い違いません。

  クリックするとサインインを開始し、完了までポーリングします。その後、有効なプロバイダを `ant` にそろえ、Record と Crawl のゲートをその場で更新します。実装は `bajutsu/templates/serve.html.j2` と `bajutsu/templates/serve.js` にあります。
- **ドキュメント**（`docs/web-ui.md`、`docs/ja/web-ui.md`）：Settings の節は、削除済みの Claude Code プロバイダを説明したままでした。現在の3つのプロバイダと、新しい SSO ボタンの説明に直します。

## 検討した代替案

- **リモート serve 向けのヘッドレスサインイン**（`ant auth login --no-browser`。認証 URL を表示してコードを貼り戻す方式）：見送りました。2つのリクエストにまたがってプロセスを生かし、stdin を渡す必要があります。リモート serve はこの項目の対象外でもあります。ボタンはローカル serve に限定します。ホスト型のデプロイでは、BE-0163 と同じく、運用者が別途ホスト側でサインインします。
- **サインインをターミナルに任せたまま**（BE-0163 の現状）：却下しました。パネルはすでにプロバイダを選ぶので、同じ場所からサインインを開始すれば、UI の外に残る唯一の手作業がなくなります。

## 進捗

> 作業の進行に合わせて最新に保ちます。チェックリストは *詳細設計* の MECE な作業分解に対応し（作業単位ごとに
> 一つのボックス）、ログは何がいつ変わったかを（古い順に）PR へのリンクとともに記録します。

- [x] `ant_login` / `ant_login_status` オペレーション（注入可能な起動シーム付き）
- [x] `POST` / `GET /api/ant/login` のルーティングと管理者 RBAC への登録
- [x] `ServeState.ant_login_proc` ハンドル
- [x] Settings の UI ボタン、ステータス行、ポーリング
- [x] ドキュメント（英日）の修正と SSO ボタンの記載
- [x] テスト（ホスト 403、バイナリ欠如 400、起動 202、ステータス遷移、再クリックでの supersede）と status オペレーション

ログ:

- [#708](https://github.com/bajutsu-e2e/bajutsu/pull/708) で提案しました。
- [#705](https://github.com/bajutsu-e2e/bajutsu/pull/705)：機能を実装します。`ant_login` /
  `ant_login_status` オペレーションと `/api/ant/login` のルーティング、管理者 RBAC への登録、
  `ServeState.ant_login_proc` ハンドル、Settings の **Sign in with SSO** ボタン（再クリックで supersede）、
  ドキュメントの修正、テストです。この項目を *実装済み* に変更します。

## 参考

- [BE-0163](../BE-0163-ant-cli-oauth-provider/BE-0163-ant-cli-oauth-provider-ja.md)：土台となる `ant`
  OAuth プロバイダ。
- [Anthropic CLI](https://github.com/anthropics/anthropic-cli)：ボタンから開始する OAuth/SSO フロー
  `ant auth login`。
