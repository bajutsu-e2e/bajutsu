[English](BE-XXXX-serve-web-ui-ant-sso-login.md) · **日本語**

# BE-XXXX — serve の Web UI から `ant` プロバイダにサインインする

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-XXXX](BE-XXXX-serve-web-ui-ant-sso-login-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **提案** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| トピック | AI provider configuration |
| 関連 | [BE-0163](../BE-0163-ant-cli-oauth-provider/BE-0163-ant-cli-oauth-provider-ja.md) |
<!-- /BE-METADATA -->

## はじめに

BE-0163 は、公式の Anthropic CLI が持つブラウザベースの OAuth/SSO 認証情報を `ant` という三つ目の
AI プロバイダとして登録しました。これにより、どの AI パスも API キーなしで Claude の Pro/Max/Console
シートに課金できます。serve の Web UI の **Settings** では `ant` プロバイダを選べますが、サインイン自体は
UI の外に残っていました。パネルは「`serve` を起動したターミナルで `ant auth login` を実行してください」と
案内するだけだったのです。この項目は、そのパネルに **Sign in with SSO** ボタンを加え、Web UI からサインインを
開始できるようにします。認証情報が書かれた時点で、プロバイダのゲートは到達可能に切り替わります。

## 動機

Settings で `ant` を選んだあとにターミナルへ移って `ant auth login` を実行するのは、つなぎ目が不自然です。
運用者はすでに Web UI にいて、CLI が書き込む認証情報は選んだプロバイダがまさに必要とするものであり、Record と
Crawl のゲートが読む到達可能性（`GET /api/provider`）も CLI のサインイン状態をすでに反映しています。足りない
のは、同じパネルからサインインを開始する手段だけです。`ant auth login` は既定の動作でホスト上のブラウザを開き、
CLI 自身がループバックのコールバックを待ち受けます。したがって Web UI の推奨起動方法であるローカルの
`make serve` では、serve は CLI を起動し、運用者がブラウザで認証を終えるのを待って完了をポーリングするだけで
済みます。トークンが serve を経由することはなく、マシンの認証情報は CLI 自身が書き込みます。

## 詳細設計

- **`ant_login` / `ant_login_status` オペレーション**（`bajutsu/serve/operations/config.py`）。
  `ant_login` は、ホスト型やマルチテナントのデプロイを拒否します（403。サインインはサーバ全体が共有する
  マシン単位の認証情報を書き込むためです）。`ant` バイナリが無い場合も拒否し（400。到達可能性チェックと同じ
  案内文を再利用します）、それ以外では注入可能な `ServeState.popen` を通じて `ant auth login` を起動します。
  このとき `stdin` を閉じ、ブラウザとループバックの経路だけで進むようにして、202 を返します。処理中に二度目の
  クリックがあっても、重複したプロセスは起動しません。`ant_login_status` は保持したプロセスをポーリングし、
  `idle`、`running`、`ok`、`error`（CLI の最後の出力行を detail として添えます）を返します。
- **ルーティングと RBAC**（`bajutsu/serve/handler.py`、`bajutsu/serve/authz.py`）。`POST /api/ant/login`
  と `GET /api/ant/login` が二つのオペレーションにつながります。このパスは `_ADMIN_PATHS` に加わるので、
  `/api/provider` と同じく管理者の操作になります。
- **`ServeState` のハンドル**（`bajutsu/serve/jobs.py`）。`ant_login_proc` フィールドが、起動する POST と
  ポーリングする GET のあいだで実行中のプロセスを保持します。
- **Settings の UI**（`bajutsu/templates/serve.html.j2`、`bajutsu/templates/serve.js`）。`ant` の
  セクションに **Sign in with SSO** ボタンとステータス行が加わります。ボタンのラベルと活性状態は
  `GET /api/provider` から導くので（「Signed in ✓」の表示とゲートが食い違いません）、クリックするとサインインを
  開始し、完了までポーリングし、有効なプロバイダを `ant` にそろえ、Record と Crawl のゲートをその場で更新します。
- **ドキュメント**（`docs/web-ui.md`、`docs/ja/web-ui.md`）。削除済みの Claude Code プロバイダを説明したままだった
  Settings の節を、現在の三つのプロバイダと新しい SSO ボタンの説明に直します。

## 検討した代替案

- **リモート serve 向けのヘッドレスサインイン**（`ant auth login --no-browser`。認証 URL を表示してコードを
  貼り戻す方式）。見送りました。二つのリクエストにまたがってプロセスを生かし、stdin を渡す必要があり、リモート
  serve はこの項目の対象外だからです。ボタンはローカル serve に限定します。ホスト型のデプロイでは BE-0163 と
  同じく、運用者が別途ホスト側でサインインします。
- **サインインをターミナルに任せたまま**（BE-0163 の現状）。却下しました。パネルはすでにプロバイダを選ぶので、
  同じ場所からサインインを開始すれば、UI の外に残る唯一の手作業が無くなります。

## 進捗

> 作業の進行に合わせて最新に保ちます。チェックリストは *詳細設計* の MECE な作業分解に対応し（作業単位ごとに
> 一つのボックス）、ログは何がいつ変わったかを（古い順に）PR へのリンクとともに記録します。

- [ ] `ant_login` / `ant_login_status` オペレーション（注入可能な起動シーム付き）
- [ ] `POST` / `GET /api/ant/login` のルーティングと管理者 RBAC への登録
- [ ] `ServeState.ant_login_proc` ハンドル
- [ ] Settings の UI ボタン、ステータス行、ポーリング
- [ ] ドキュメント（英日）の修正と SSO ボタンの記載
- [ ] テスト（ホスト 403、バイナリ欠如 400、起動 202、ステータス遷移、再クリックでの supersede）と status オペレーション

ログ:

- 提案しました。実装は [#705](https://github.com/bajutsu-e2e/bajutsu/pull/705)（オープン、未マージ）で
  ドラフトしています。マージされた時点で、上のチェックを付け、**状態**を *実装済み* に変更し、その PR を
  `実装 PR` に記録します。

## 参考

- [BE-0163](../BE-0163-ant-cli-oauth-provider/BE-0163-ant-cli-oauth-provider-ja.md)：土台となる `ant`
  OAuth プロバイダ。
- [Anthropic CLI](https://github.com/anthropics/anthropic-cli)：ボタンから開始する OAuth/SSO フロー
  `ant auth login`。
