[English](BE-0277-docker-build-commit-badge.md) · **日本語**

# BE-0277 — セルフホスト用 Docker イメージにコミットハッシュを埋め込み、バージョンバッジに表示する

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0277](BE-0277-docker-build-commit-badge-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0277") |
| 実装 PR | [#1133](https://github.com/bajutsu-e2e/bajutsu/pull/1133) |
| トピック | Web UI のホスティング |
<!-- /BE-METADATA -->

## はじめに

[BE-0272](../BE-0272-serve-version-badge/BE-0272-serve-version-badge-ja.md) のバージョンバッジを、
セルフホストの Docker デプロイにも拡張する提案です。BE-0272 の Git コマンドによる検出は、参照できる
`.git` チェックアウトが手元にあることが前提ですが、コンテナ内にはそれがありません。`docker build` の
引数でコミットハッシュを埋め込み、`server_checkout()` が Git 検出を空振りしたときにその値へフォール
バックするようにすれば、コンテナ越しのセルフホスト `serve` でも、ローカルの開発チェックアウトと同じ
ように実行中のコミットを表示できます。

## 動機

BE-0272 は、bajutsu 自身のパッケージディレクトリ（`bajutsu/serve/operations/version.py`）を起点にした
Git コマンドでコミット、ブランチ、dirty フラグを読み取っています。その「検討した代替案」の節では、
ビルド時にコミットを埋め込む案について、「bajutsu はまだ PyPI に公開されておらず、今フックできる
ビルドパイプラインがない」という理由で先送りにすると明記していました。セルフホストの Docker デプロイは
まさにそのビルドパイプラインであり、しかもすでに存在します。[`deploy/self-host/Dockerfile`](../../deploy/self-host/Dockerfile)
は、このリポジトリのチェックアウトを対象にすでに `docker build` を実行しているためです。

現状この Dockerfile には `.dockerignore` がなく `COPY . /app` としているため、ビルドコンテキストに
たまたま `.git` が含まれていれば、BE-0272 の検出はそのまま動いてしまいます。ただしこれは偶然動作して
いるだけで、保証された挙動ではありません。

- `.git` をまるごとイメージにコピーするとイメージが肥大化し、コミットのたびに `COPY` レイヤーの
  ビルドキャッシュが壊れます。これはバージョンバッジとは無関係に、それ自体直すべき問題です（詳細は
  「詳細設計」を参照してください）。
- 実際のセルフホストデプロイは、このチェックアウトに対して手元で `docker build .` を実行しているの
  ではありません。このリポジトリの外側にある、デプロイ側の設定によって駆動されています。そのビルド
  コンテキストがどのような形をしているか、`.git` を保持したワーキングツリーを Docker に渡している
  かどうかは、このリポジトリが制御できることでも前提にできることでもありません。

つまり、`.git` がたまたま存在することに頼る設計は、二つの独立した理由で壊れやすい状態にあります。
明示的なビルド引数であれば、このリポジトリが一度定義して文書化しておくだけの契約になり、このリポジトリ
自身の compose スタックであっても外部のデプロイパイプラインであっても、同じ方法で満たせます。イメージ
がたまたま使える `.git` を持っていることを期待するのではなく、ビルドしているその瞬間のコミットを、
ビルド側が明示的に渡す形にします。

## 詳細設計

- **ビルド引数。** [`deploy/self-host/Dockerfile`](../../deploy/self-host/Dockerfile) に
  `ARG GIT_COMMIT=""` を追加し、`ENV BAJUTSU_BUILD_COMMIT=$GIT_COMMIT` としてイメージに埋め込み
  ます。既存の `BAJUTSU_*` 環境変数の命名規則（`bajutsu/config_source.py`、`bajutsu/serve/state.py`
  など）に合わせた名前です。未指定のままなら空文字列のままで、挙動は何も変わりません。後述の
  フォールバックが素通りするだけです。
- **フォールバック時の読み取り。** `server_checkout()`（`bajutsu/serve/operations/version.py`）で、
  `git rev-parse --short HEAD` の読み取りが `None`（`.git` チェックアウトなし）を返したとき、環境変数
  `BAJUTSU_BUILD_COMMIT` を読み、値があれば `commit` としてそれを返し、`branch` は `None`、`dirty` は
  `None` にします。ビルド引数として埋め込んだ値には、作業中のブランチや dirty ツリーという概念
  自体が存在しないため、既知の「クリーン」値をでっち上げるのではなく、既存の「不明」時のデフォルト
  （`None`）のままにします。Git
  による検出は今までどおり最優先で最初に走るため、この経路が動くのは BE-0272 がもともと「報告する
  ものがない」としていたケースに限られます。
  値の出どころをフロントエンドに伝えるため、`source: "git" | "build-arg"` のようなフィールドを追加
  することも検討します。ブランチ名や dirty マーカーがないことは「埋め込み値」であって「クリーンな
  チェックアウト」ではないことを、バッジの見た目で区別できるようにするためです。
- **`.dockerignore`。** リポジトリルートに `.dockerignore` を追加し、`.git`（および `.venv/` や
  `runs/`、`tmp/` などすでに `.gitignore` 対象になっているビルドに無関係なパス）を除外します。これに
  よってビルド引数は、「`.git` が無いときのフォールバック」から「唯一の情報源」に位置づけが変わり
  ます。Docker イメージのコミット情報は、ビルドコンテキストにたまたま含まれていた何かではなく、明示
  的に文書化された入力から来る、という誠実な契約になります。
- **ビルドの配線。** [docs/self-hosting.md](../../docs/self-hosting.md)（および日本語版）に、ビルド
  時にコミットを渡す方法を記載します。たとえば
  `docker build --build-arg GIT_COMMIT=$(git rev-parse HEAD) -f deploy/self-host/Dockerfile .` で、
  同じドキュメントにすでに載っている worker イメージのビルドコマンドと同じ形式です。
  [`docker-compose.yml`](../../deploy/self-host/docker-compose.yml) にはこの Dockerfile をビルドする
  サービスが 2 つあります（`migrate` と `bajutsu`）。バッジのエンドポイントを実際に提供するのは
  `bajutsu` サービスなので、そちらの `build:` に同じ引数を渡すよう拡張します（Compose は `args` を
  呼び出し元シェルの環境変数や `.env` の値から解決できるため）。こうすれば `docker compose build` は
  追加のフラグなしでこの値を拾えます。`migrate` サービスは Alembic を実行するだけで
  `server_checkout()` を提供しないため、ビルド引数は不要です。
  このリポジトリの compose スタックを経由せず、独自に Dockerfile をビルドしている外部のデプロイ
  パイプラインを変更することはこのリポジトリのスコープ外ですが、この item が定義するビルド引数の
  契約こそ、そうしたパイプラインがバッジを動かすために満たすべきものです。個別の外部パイプラインへ
  実際に配線する作業は、そのパイプラインを持つ側のフォローアップになります。
- この経路のどこにも LLM は関与しません。フォールバックは決定的な環境変数の読み取りであり（prime
  directive 1）、対象は per-app の関心事ではなくツール自身のビルド識別子なので、app-agnostic の原則
  （prime directive 3）も関係しません。

## 検討した代替案

- **ライブな `.git` の読み取りより、ビルド引数の値を常に優先する案。** 却下しました。チェックアウト
  ベースの `serve`（BE-0272 が主眼としていた通常の開発ループのケース）は、チェックアウトが編集された
  ままセッションを起動し続けているような場合も含め、リクエストごとの生の状態を反映し続けるべきです。
  未設定のビルド引数がその挙動を覆い隠すことがあってはなりません。Git による検出を常に最優先とし、
  ビルド引数は Git 検出がもともと「報告するものがない」としていた隙間だけを埋めます。
- **setuptools-scm のように、パッケージ自体にバージョンを埋め込む案。** `pip install` した環境でも
  コミットを報告できるようにする、より一般的な案です。これは BE-0272 がすでに先送りにした対象と同じ
  で、ここでも引き続き先送りにします。bajutsu にはまだ持たないビルド／公開パイプラインが必要になる
  ためです。この item は、このリポジトリがすでに持ち制御している Docker の経路
  （`deploy/self-host/Dockerfile`）に絞り、そのパイプラインを必要としない範囲にとどめます。
- **`.dockerignore` の変更を見送り、ビルド引数を純粋なフォールバックとして扱う案。** 検討しました
  が、`.git` をコピー可能なままにしておくと、BE-0272 の偶然の挙動が引き起こすイメージ肥大化とキャッシュ
  破壊の問題が残ったままになり、バージョンバッジもビルドコンテキストにたまたま含まれていた何かに
  静かに依存し続けることになります。両者は同じ Dockerfile を触り、同じ動機（`.git` がたまたま存在
  することに頼らない）を持つため、一つの item でまとめて直すのが小さく一貫した変更になります。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [x] `deploy/self-host/Dockerfile`：`ARG GIT_COMMIT=""` を追加し、`ENV BAJUTSU_BUILD_COMMIT` に
      埋め込みます。
- [x] `bajutsu/serve/operations/version.py`：Git 検出がチェックアウトを見つけられなかったときに
      `server_checkout()` が `BAJUTSU_BUILD_COMMIT` へフォールバックするようにし、フロントエンドの
      バッジが埋め込みコミットを区別して表示できるよう `source: "git" | "build-arg" | null` フィールドを
      追加します。
- [x] ルートの `.dockerignore`：`.git` および他のビルドに無関係な、すでに gitignore 対象のパスを
      除外します。
- [x] `docker-compose.yml`：`build.args` 経由でビルド引数を渡します。
- [x] ドキュメント：`docs/self-hosting.md` とその日本語版に、
      `--build-arg GIT_COMMIT=$(git rev-parse HEAD)` の実行方法を記載します。

ログ：

- [#1133](https://github.com/bajutsu-e2e/bajutsu/pull/1133) で実装：ビルド引数と
  `BAJUTSU_BUILD_COMMIT` の埋め込み、`server_checkout()` のフォールバックと `source` フィールド、
  バッジの tooltip、ルートの `.dockerignore`、compose の `build.args`、そして日英のセルフホスト
  ドキュメントを追加しました。

## 参考

- [BE-0272](../BE-0272-serve-version-badge/BE-0272-serve-version-badge-ja.md) —
  この item が拡張するバージョンバッジと、その Git コマンドによる検出です。「検討した代替案」の節で
  ビルド時の埋め込みをビルドパイプラインの不在を理由に先送りしていましたが、セルフホストの Docker
  デプロイはすでにそのビルドパイプラインです。
- [BE-0016](../BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting-ja.md) —
  この item の Dockerfile が属する、セルフホストの Tier B コントロールプレーン（`deploy/self-host/`）
  です。
- [`deploy/self-host/Dockerfile`](../../deploy/self-host/Dockerfile)、
  [`deploy/self-host/docker-compose.yml`](../../deploy/self-host/docker-compose.yml) —
  この item が変更するファイルです。
