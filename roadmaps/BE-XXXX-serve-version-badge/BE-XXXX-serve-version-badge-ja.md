[English](BE-XXXX-serve-version-badge.md) · **日本語**

# BE-XXXX — serve の Web UI のヘッダーに bajutsu 自身のコミット・バージョンを表示する

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-XXXX](BE-XXXX-serve-version-badge-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **提案** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| トピック | serve Web UI への CLI 機能の取り込み |
<!-- /BE-METADATA -->

## はじめに

serve の Web UI のヘッダーに、実行中の bajutsu 自身がどのコミットで動いているか、
Git チェックアウトがない場合はどのバージョンで動いているかを示す、常時表示の小さな
バッジを追加します。既存の config 由来バッジ（[BE-0187](../BE-0187-serve-config-view/BE-0187-serve-config-view-ja.md)）
の隣に置きますが、答える問いは異なります。「読み込んだ config がどこから来たか」で
はなく、「このページを配信しているツール自体がどのビルドか」です。

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
  `git status --porcelain` という Git の plumbing コマンドを使います。これは既存の
  config 由来表示がすでに使っている、低コストで LLM を介さない方式と同じであり、
  リクエストのたびに最新の状態を取得するため、チェックアウトを編集しながら
  走らせっぱなしにしたセッションでも表示がずれません。
- `.git` が存在しない環境（`pip install bajutsu` の後など）では、レスポンスから
  コミット・ブランチ・dirty 判定を省略し、フロントエンドはバージョン文字列だけを
  表示します。
- **フロントエンド**：ヘッダーのテンプレート内、config 由来バッジの隣に小さな
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

- [ ] バックエンド：Git チェックアウト時のコミット・ブランチ・dirty 判定を含め、
      バージョン情報を status エンドポイントで公開する。
- [ ] フロントエンド：config 由来バッジの隣にバッジを描画する。
- [ ] ドキュメント：出荷後、`docs/architecture.md` の実装状況（および日本語版）に
      記載する。

## 参考

- [BE-0187](../BE-0187-serve-config-view/BE-0187-serve-config-view-ja.md) — このバッジ
  が隣に並ぶ、config 由来の表示。
- [BE-0202](../BE-0202-serve-js-modularization/BE-0202-serve-js-modularization-ja.md) —
  このバッジが拡張する serve.js のモジュール構造。
- `CLAUDE.md`「Isolate concurrent sessions with worktrees」— 複数チェックアウトが
  並行するという、この提案の動機となる場面。
