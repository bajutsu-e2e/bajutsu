[English](BE-0272-serve-version-badge.md) · **日本語**

# BE-0272 — serve の Web UI のヘッダーに bajutsu 自身のコミット・バージョンを表示する

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

`CLAUDE.md` が定める並行作業の指針（`make worktree`）により、同じリポジトリの複数
チェックアウトが、それぞれ別のブランチやコミットの上で serve を同時に走らせる状況
が生じます。ところが、表示中のページを見ただけでは、それがどのチェックアウトの
serve なのか分かりません。

コードベースに存在する唯一のバージョン識別子である `bajutsu.__version__`
（`bajutsu/__init__.py`）は serve のどこにも出ておらず、CLI にも `--version`
フラグがありません。

既存のヘッダーバッジは config の Git 由来であり、ツール自体の識別情報ではありません。
同じヘッダーに並ぶだけに、両者は混同しやすい状態です。

## 詳細設計

- **バックエンド**：稼働中サーバー自身の識別情報を返すエンドポイントを用意します
  （または既存の軽量な status エンドポイントにフィールドを追加します）。バージョン
  文字列は常に返し、プロセスの作業ディレクトリが Git チェックアウトの内側にある
  場合は、短縮コミット SHA、ブランチ名、dirty 判定（未コミットの変更有無）も返しま
  す。取得には `git rev-parse --short HEAD`、`git rev-parse --abbrev-ref HEAD`、
  `git status --porcelain` という Git の plumbing コマンドを使います。決定的で LLM を
  介さないサブプロセス呼び出しですが、serve にとっては新しい手法です。既存の config
  由来表示（`bajutsu/config_source.py` の `source_provenance`）は、bind 時に GitHub API
  で解決したコミット SHA を使う方式であり、対象もリモートの `github:` ソースに限られ
  ます（ローカルファイルの config には Git 由来情報がありません）。リクエストのたびに
  最新の状態を取得するため、チェックアウトを編集しながら走らせっぱなしにしたセッション
  でも表示がずれません。
- `.git` が存在しない環境（`pip install bajutsu` の後など）では、レスポンスから
  コミット・ブランチ・dirty 判定を省略し、フロントエンドはバージョン文字列だけを
  表示します。
- **アクセス制御**：この情報取得をすべての訪問者に開くか、ロールで制限するかを実装時に
  決めます。注意が要るのはブランチ名です。本リポジトリ自身の慣習（`CLAUDE.md`
  「One topic per branch」）ではブランチを `claude/<topic>` や `<user>/<topic>` と命名
  し、作業中の BE スラグをそのまま含むことが多いため、ホスティング環境や共有環境で
  そのまま見せると、何を作業中かが漏れます。[BE-0187](../BE-0187-serve-config-view/BE-0187-serve-config-view-ja.md)
  は、開示範囲の広い `/api/config/content` の読み取りを `admin` ロールに制限する前例を
  作りました。バージョン文字列だけは機微ではないため、バージョンは開いたまま、
  コミット・ブランチ・dirty 判定（少なくともブランチ名）は `admin` に制限するのが
  妥当な既定です。
- **フロントエンド**：ヘッダーのテンプレート内、`bajutsu` というタイトルの横に小さな
  バッジを配置します（例：`v0.0.0 · a1b2c3d (branch-name)`）。dirty のときは専用の
  マーカーを添えます。描画は、[BE-0202](../BE-0202-serve-js-modularization/BE-0202-serve-js-modularization-ja.md)
  で分割された `serve.*.js` モジュールのうち、該当するものが担います。
- この経路に LLM は一切関与しません。決定的なサブプロセス呼び出しによる情報取得
  であり、Tier-2 の run・CI ゲートには影響しません（第一原則）。対象はツール自身の
  識別情報であり、アプリ固有の config ではないため、アプリ非依存の原則（第三原則）
  もここでは関係しません。

## 検討した代替案

- **ビルド時にコミット SHA をパッケージへ埋め込む方式**（setuptools-scm 的な手法）
  であれば、pip install されたコピーでもコミットを報告できます。ただし見送ります。
  bajutsu はまだ PyPI に公開されておらず（`docs/ci.md` はリリース前で submodule
  経由の配布だと記しています）、フックする先のビルドパイプラインが今のところ
  存在しないためです。パッケージングと公開が実現した時点で改めて検討します。
- **ヘッダーバッジではなく、doctor パネル（[BE-0148](../BE-0148-serve-doctor/BE-0148-serve-doctor-ja.md)）
  の診断項目として追加する案**も検討しました。doctor はユーザーが開いて確認する、
  オンデマンドの readiness 診断です。コミットやバージョンの表示は、診断というより
  常時見えているべき定位の手がかりに近いため、パネルを開かなくても分かる場所に
  置くべきだと判断しました。
- **専用の「About」モーダル**も検討しましたが、同じ理由で見送ります。クリックしな
  いと見えない場所に置くと、「今目の前の serve がどのコミットか一目で確認したい」
  という本来の用途を損ないます。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [x] バックエンド：バージョンとコミット・ブランチ・dirty 判定を公開する。GET を 2 本に
      分けました（`bajutsu/serve/operations/version.py`）。`GET /api/version`（公開）と
      `GET /api/version/checkout`（`git` の plumbing、リクエストごとに読み取り）です。
      Git はプロセスの作業ディレクトリではなく bajutsu 自身のパッケージディレクトリを起点に
      読むため、bind した `github:` config のチェックアウトをツール自身のビルドと取り違えません。
- [x] アクセス制御：バージョンは公開のまま、コミット・ブランチ・dirty 判定は
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
- [#1124](https://github.com/bajutsu-e2e/bajutsu/pull/1124) — バッジをナビタブの末尾から
  `bajutsu` というタイトルの横（`.brandrow`）へ移しました。マークアップとエンドポイント、
  `loadVersion()` は変更していません。この項目の記述も実態に合わせました。

## 参考

- [BE-0187](../BE-0187-serve-config-view/BE-0187-serve-config-view-ja.md) — このバッジと
  区別している、config 由来の表示（バッジはタイトルの横に置きます）。
- [BE-0202](../BE-0202-serve-js-modularization/BE-0202-serve-js-modularization-ja.md) —
  このバッジが拡張する serve.js のモジュール構造。
- `CLAUDE.md`「Isolate concurrent sessions with worktrees」— 複数チェックアウトが
  並行するという、この提案の動機となる場面。
