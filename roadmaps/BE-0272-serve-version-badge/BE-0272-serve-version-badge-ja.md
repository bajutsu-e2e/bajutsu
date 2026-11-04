[English](BE-0272-serve-version-badge.md) · **日本語**

# BE-0272 — serve の Web UI のヘッダーに bajutsu 自身のコミットとバージョンを表示する

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0272](BE-0272-serve-version-badge-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0272") |
| 実装 PR | [#1117](https://github.com/bajutsu-e2e/bajutsu/pull/1117), [#1124](https://github.com/bajutsu-e2e/bajutsu/pull/1124) |
| トピック | serve Web UI への CLI 機能の取り込み |
<!-- /BE-METADATA -->

## はじめに

serve の Web UI のヘッダーに、実行中の bajutsu 自身がどのコミットで動いているか、
Git チェックアウトがない場合はどのバージョンで動いているかを示す、常時表示の小さな
バッジを追加します。`bajutsu` というタイトルの横にそっと置き、config 由来バッジ
（[BE-0187](../BE-0187-serve-config-view/BE-0187-serve-config-view-ja.md)）とは区別します。
答える問いも異なり、「読み込んだ config がどこから来たか」ではなく、「このページを配信して
いるツール自体がどのビルドか」です。

## 動機

`CLAUDE.md` が定める並行作業の指針（`make worktree`）により、同じリポジトリの複数のチェックアウトが、それぞれ別のブランチやコミットの上で serve を同時に走らせる状況が生じます。ところが、表示中のページを見ただけでは、それがどのチェックアウトの serve なのかわかりません。

コードベースに存在する唯一のバージョン識別子は `bajutsu.__version__`（`bajutsu/__init__.py`）です。serve のどこにも出ておらず、CLI にも `--version` フラグがありません。

既存のヘッダーバッジが示すのは、config の Git 由来であり、ツール自体の識別情報ではありません。2 つのバッジは同じヘッダーに並ぶため、読み手は両者を混同しやすくなります。

## 詳細設計

**バックエンド。** 稼働中サーバー自身の識別情報を返すエンドポイントを用意します。既存の軽量な status エンドポイントにフィールドを追加する形でもかまいません。返す内容は次のとおりです。

- バージョン文字列を、常に返します。
- プロセスの作業ディレクトリが Git チェックアウトの内側にある場合は、短縮コミット SHA、ブランチ名、dirty 判定（未コミットの変更の有無）も返します。

Git の項目は、Git の plumbing コマンドで取得します。`git rev-parse --short HEAD`、`git rev-parse --abbrev-ref HEAD`、`git status --porcelain` です。決定的で LLM を介さないサブプロセス呼び出しですが、serve にとっては新しい手法です。既存の config 由来表示（`bajutsu/config_source.py` の `source_provenance`）は、bind 時に GitHub API でコミットを解決します。しかも対象は、リモートの `github:` ソースの config に限られます。ローカルファイルの config には、Git 由来の情報がそもそもないからです。Git の項目はリクエストのたびに最新の状態を取得するため、チェックアウトを編集しながら走らせっぱなしにしたセッションでも、表示がずれません。

`.git` が存在しない環境（`pip install bajutsu` の後など）では、レスポンスからコミット、ブランチ、dirty 判定を省略します。フロントエンドはバージョン文字列だけを表示します。

**アクセス制御。** この情報の取得を、すべての訪問者に開くか、ロールで制限するかは、実装時に決めます。注意が要るのはブランチ名です。本リポジトリ自身の慣習（`CLAUDE.md`「One topic per branch」）では、ブランチを `claude/<topic>` や `<user>/<topic>` と命名します。こうした名前は、作業中の BE スラグをそのまま含むことが多いため、ホスティング環境や共有環境でブランチ名をそのまま見せると、何を作業中かが漏れます。

[BE-0187](../BE-0187-serve-config-view/BE-0187-serve-config-view-ja.md) が前例を作りました。BE-0187 は、開示範囲の広い `/api/config/content` の読み取りを `admin` ロールに制限しています。バージョン文字列だけは機微ではありません。妥当な既定は、バージョンを開いたままにし、コミット、ブランチ、dirty 判定（少なくともブランチ名）を `admin` に制限することです。

**フロントエンド。** ヘッダーのテンプレート内で、`bajutsu` というタイトルの横に小さなバッジを配置します（例：`v0.0.0 · a1b2c3d (branch-name)`）。dirty のときは専用のマーカーを添えます。描画は、[BE-0202](../BE-0202-serve-js-modularization/BE-0202-serve-js-modularization-ja.md) で分割された `serve.*.js` モジュールのうち、該当するものが担います。

**プライムディレクティブ。** この経路に LLM は一切関与しません。決定的なサブプロセス呼び出しによる情報取得であり、Tier-2 の run と CI のゲートには影響しません（第一原則）。対象はツール自身の識別情報であり、アプリ固有の config ではないため、アプリ非依存の原則（第三原則）は、ここでは関係しません。

## 検討した代替案

- **ビルド時にコミット SHA をパッケージへ埋め込む方式**（setuptools-scm 的な手法）。pip install されたコピーでも、コミットを報告できるようになります。ただし見送ります。bajutsu はまだ PyPI に公開されておらず（`docs/ci.md` は、リリース前で submodule 経由の配布だと記しています）、フックする先のビルドパイプラインが今のところ存在しないためです。パッケージングと公開が実現した時点で、改めて検討します。
- **ヘッダーバッジではなく、doctor パネル（[BE-0148](../BE-0148-serve-doctor/BE-0148-serve-doctor-ja.md)）の診断項目として追加する案。** 見送ります。doctor は、ユーザーが開いて確認するオンデマンドの readiness 診断です。コミットとバージョンの表示は、診断というより、常時見えているべき定位の手がかりに近いものです。パネルを開かなくても分かる場所に置くべきだと判断しました。
- **専用の「About」モーダル。** 同じ理由で見送ります。クリックしないと見えない場所に置くと、「今目の前の serve がどのコミットか一目で確認したい」という本来の用途を損ないます。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [x] バックエンド：バージョンとコミット、ブランチ、dirty 判定を公開する。GET を 2 本に
      分けました（`bajutsu/serve/operations/version.py`）。`GET /api/version`（公開）と
      `GET /api/version/checkout`（`git` の plumbing、リクエストごとに読み取り）です。
      Git はプロセスの作業ディレクトリではなく bajutsu 自身のパッケージディレクトリを起点に
      読むため、bind した `github:` config のチェックアウトをツール自身のビルドと取り違えません。
- [x] アクセス制御：バージョンは公開のまま、コミット、ブランチ、dirty 判定は
      `bajutsu/serve/authz.py` の GET 早期分岐で `admin` に制限しました（ブランチ名は
      作業中のトピックを含みうるため）。
- [x] フロントエンド：ヘッダーの `#appversion` バッジ（`serve.html.j2`）を、`serve.core.mjs`
      の `loadVersion()` が描画します。バージョンは常に表示し、admin 限定のチェックアウト
      読み取りが成功したときだけ `· <sha> (branch)` と dirty マーカーを添えます。ナビタブの
      並びではなく、`bajutsu` というタイトルの横（`.brandrow`）に配置します。
- [x] ドキュメント：`docs/architecture.md` の実装状況と日本語版に記載しました。

### ログ

- （本 PR）2 本のエンドポイントと authz の制限、ヘッダーバッジを実装し、オペレーション層と
  HTTP のテスト（`tests/serve/test_version_ops.py`、`tests/serve/test_http_version.py`）、
  RBAC のテスト（`tests/serve/test_rbac.py`）を追加しました。状態を実装済みに変更しました。
- [#1124](https://github.com/bajutsu-e2e/bajutsu/pull/1124)：バッジをナビタブの末尾から
  `bajutsu` というタイトルの横（`.brandrow`）へ移しました。マークアップとエンドポイント、
  `loadVersion()` は変更していません。この項目の記述も実態に合わせました。

## 参考

- [BE-0187](../BE-0187-serve-config-view/BE-0187-serve-config-view-ja.md)：このバッジと
  区別している、config 由来の表示（バッジはタイトルの横に置きます）。
- [BE-0202](../BE-0202-serve-js-modularization/BE-0202-serve-js-modularization-ja.md)：
  このバッジが拡張する serve.js のモジュール構造。
- `CLAUDE.md`「Isolate concurrent sessions with worktrees」：複数チェックアウトが
  並行するという、この提案の動機となる場面。
