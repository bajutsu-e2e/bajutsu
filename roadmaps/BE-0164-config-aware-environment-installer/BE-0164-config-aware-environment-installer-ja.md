[English](BE-0164-config-aware-environment-installer.md) · **日本語**

# BE-0164 — config を踏まえた環境インストーラー

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0164](BE-0164-config-aware-environment-installer-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0164") |
| 実装 PR | [#679](https://github.com/bajutsu-e2e/bajutsu/pull/679) |
| トピック | doctor / オンボーディング |
<!-- /BE-METADATA -->

## はじめに

Bajutsu のバックエンド非依存なコアは、プロジェクトが使うバックエンドによって、必要な外部要素が異なります。

- iOS Simulator 向けの Homebrew の `idb_companion`。
- web バックエンド向けの Playwright の Chromium ビルド。
- Tier 1 の AI 系パス向けの `anthropic` SDK。

しかし今は、プロジェクトの config を見て、必要なものをそのままインストールしてくれる仕組みがありません。

代わりにあるのは、それぞれ一部分だけをカバーする、独立した3つの仕組みです。`make setup` は Python の
ツールチェーンのみをインストールします。`make deps` は idb のみで、ハードコードされています。
[`scripts/serve.sh`](../../scripts/serve.sh) も idb のみで、しかも `serve` のみです。

本項目は、config を踏まえた単一のインストーラーを提案します。プロジェクトの実効 config を読み、設定済みの
バックエンドが必要とする pip の extra と外部ツールをインストールします。

## 動機

「bajutsu の依存関係はいつインストールされるのか」に、単一の答えはありません。正直なところ、「複数の分断
された仕組みのうち、たまたま何を実行したかによる」というのが実情です。

- **`make setup`** は `uv sync --group dev` を実行します。インストールするのは、Python の
  ツールチェーンと AI フリーの基本パッケージだけで、どのバックエンド向けのものもありません。
- **`make deps`** は iOS 向けの要素を**無条件に**インストールします。[`Brewfile`](../../Brewfile) に対する
  `brew bundle` で `idb_companion` / `xcodegen` を入れ、加えて `uv sync --extra idb` を実行します。
  web バックエンドしか使わないプロジェクトでも、これが走ります。`uv sync --extra web` +
  `playwright install chromium` に相当するターゲットはありません。`--extra ai` / `--extra visual` /
  `--extra mcp` にも同様のものがありません。
- **`scripts/serve.sh`** は、`make deps` の idb チェック（`.venv/bin/idb` と `idb_companion` の有無確認）の
  一部を再実装しています。ただし対象は `bajutsu serve` の起動経路だけです。`bajutsu run`、
  `bajutsu doctor`、`bajutsu record` を直接実行する場合は、この恩恵を受けられません。
- **web バックエンド**で唯一の「インストーラー」は、`doctor` / `preflight.py` がチェック失敗**後に**出す
  対処メッセージです（`uv sync --extra web` / `playwright install chromium`。
  [BE-0024](../BE-0024-doctor-onboarding/BE-0024-doctor-onboarding-ja.md) を参照）。ユーザーは、これを読んで
  手で実行するしかありません。
- `pyproject.toml` には、`ai` / `idb` / `web` / `visual` / `mcp` / `bedrock` / `server` / `db` /
  `oauth` / `schema` / `s3` / `gcs` / `cloud` など、十数個の extra が宣言されています。それぞれ遅延 import と
  ガードの背後にあります。しかし、「*特定の*プロジェクトの config が必要とする extra」と「現在インストール
  済みの extra」を突き合わせて差分を埋める単一の手順はありません。

実務上は、新しく参加した contributor や、自分たちの bajutsu 駆動テストリポジトリを立ち上げるチームが、
run の奥深くでコマンドが失敗して初めて不足に気づきます。失敗の形は、`no available actuator among ['idb']`、
ガードされていない遅延 import からの `ImportError`、`preflight` の失敗などです。そのあと、いくつもある
インストール経路のどれが該当するのかを、逆算しなければなりません。

今後追加されるバックエンドも、同じパターンを一から繰り返しかねません。独自の Makefile ターゲットやシェル
スクリプトを書くことになります。次の候補は Android（[BE-0007](../BE-0007-android-backend/BE-0007-android-backend-ja.md)）と
Flutter（[BE-0008](../BE-0008-flutter-support/BE-0008-flutter-support-ja.md)）です。このロジックを置く、
拡張可能な標準の置き場所が1つあれば、繰り返しを防げます。

これはオンボーディングの領域で、[BE-0024](../BE-0024-doctor-onboarding/BE-0024-doctor-onboarding-ja.md) の
`doctor` / `preflight` と同じです。ただし、意図的に BE-0024 へ統合せず、別項目とします。BE-0024 は診断
（「環境は準備できているか」）をする項目です。本項目は実行（「環境を準備する」）をします。実行は、パッケージ
マネージャを走らせてバイナリをダウンロードするため、質的に異なる能力です。BE-0024 自身の詳細設計にも、
このような能力は包括的な候補として取り込まず、個別に提案する旨が明記されています。

## 詳細設計

作業は、独立した MECE（相互排他的かつ網羅的）な3つに分解できます。

1. **「バックエンド／extra が何を必要とするか」を宣言する単一の情報源。** 今は同じ事実が、独立して
   ずれうる3か所にハードコードされています。Brewfile、`scripts/serve.sh` の有無チェック、
   `preflight.py` の対処メッセージです。3か所とも、idb の要件を別々に記述しています。web バックエンドの
   要件（`uv sync --extra web` + `playwright install chromium`）は、対処メッセージの文章としてしか
   存在せず、機械可読な形はどこにもありません。

   本項目では、バックエンドファミリー（`idb` / `web` / `fake`）とオプション機能（`ai`、`visual`、`mcp`
   など）ごとに、単一のマッピングを導入します。マッピングは次の3つを対応づけます。
   - pip の extra 名。
   - 外部ツールの有無確認（`command -v` によるプローブ）。
   - 不足時のインストール方法（macOS 専用ツールなら Homebrew の formula 参照、web なら
     `playwright install <browser>` の実行、あるいは「不要」）。

   `preflight.py` の対処メッセージも `make deps` も新しいインストーラーも、独自のコピーを持たず、この
   単一のマッピングを参照します。
2. **config を踏まえたインストールステップ。** プロジェクトの実効 config（`run` / `doctor` がすでに
   使っているのと同じ `--config` 解決）から、`targets.*.backend` が参照しているバックエンドを解決します。
   `backends.py` の既存の解決ロジックを再利用します。あわせて、AI プロバイダが設定されているかも
   解決します。そして、そのターゲットが必要とするものだけをインストールします。「idb を無条件に」
   （今日の `make deps`）でも、「すべて」（そのようなオプションは今は存在しません）でもありません。

   各ステップは、`scripts/serve.sh` の既存パターン（`[ ! -x .venv/bin/idb ]`、
   `command -v idb_companion`）にならって冪等にします。すでに存在するものは再インストールせず、
   `make setup` のたびに実行しても安全です。
3. **1つの実装を共有する2つのエントリポイント。** 1つ目は、contributor 向けの Makefile ターゲットです。
   `make setup` と `make deps` を1つにまとめるか、両者と並ぶ `make install` を新設します。今日
   `make setup` を実行するのと同じタイミング、つまり `git clone` の直後に走らせる想定です。

   2つ目は、裏側のロジックを切り出したスクリプトです。`Makefile` 本体にインラインで重複させず、
   Makefile から呼びます。こうすれば実装は1つのままで、下流の各プロジェクトも自分の Makefile から同じ
   ように呼び出せます。`scripts/serve.sh` や `scripts/preflight.sh` がすでにそうなっているのと同じ形です。

本項目は、**ローカルで開発者自身が実行する**セットアップステップにとどめます。ホスティングされたパスや
マルチテナントのパス（`bajutsu serve --backend=server`、アップロードされたバンドルの実行）には、一切
組み込みません。アップロードされた config を代行してパッケージマネージャを走らせ、バイナリを
ダウンロードすると、[BE-0090](../BE-0090-uploaded-config-command-execution/BE-0090-uploaded-config-command-execution-ja.md)
（アップロードされたバンドル config からのコマンド実行を統制し、サンドボックス化する）が意図的に閉じた
境界を越えます。したがって本インストーラーは、人が自分のクローンに対して実行するものです。サーバーが
ネットワーク越しに受け取った config に対して実行するものではありません。

これは、決定論的な `run` / CI ゲートには一切触れません。純粋な環境のブートストラップで、シナリオが
実行される前に走り、pass/fail の判定には組み込まれません（prime directive #1）。

また、インストーラーはバックエンド非依存であり続けます（prime directive #3）。1 のマッピングが新しい
バックエンドの要件を差し込む場所となり、インストーラーのロジックをバックエンドごとに分岐させずに済む
ためです。

## 検討した代替案

**`bajutsu doctor` に `--fix` フラグを足し、診断した不足をその場でインストールさせる**案です。CLI
サブコマンドそのものをインストーラーにする形です。魅力的な補完案ではあります。1 のマッピングさえ
できれば、`doctor` の対処メッセージを表示するだけでなく、同じインストール用の関数を呼び出せるように
なるからです。

ただし、この仕組みは Typer の CLI コマンドの裏に置かれます。このコマンドは、Python パッケージが**すでに**
インストールされ、import 可能になったあとにしか存在しません。本項目が狙うより根本的なギャップには、
応えません。そのギャップとは、`git clone` の直後、Python の環境がまだ何も無い時点で何を実行すればよいか、
という問題です。インストーラーのロジックが一元化されたあとの軽量なフォローアップとしては検討に値
しますが、本項目の代替にはなりません。

**バックエンドごとの場当たり的なスクリプトを今のパターンのまま続ける**案は、却下します。バックエンドが
特殊事情を抱えるたびに、`scripts/<backend>-serve-equivalent.sh` を新設する形です。これは今日の現状そのもの
で、Android / Flutter には通用しません。同じ事実（どの extra か、どの外部ツールか）が、Brewfile、
`scripts/serve.sh`、`preflight.py` の対処メッセージにまたがって重複したままになります。

**何かが必要になった最初の瞬間に、暗黙かつ無言でインストールする**（明示的なステップを一切設けない）案も、
却下します。`scripts/serve.sh` は、すでに idb について、この考え方の狭い版を実装しています。これを
あらゆるコマンド、あらゆるバックエンドに一般化すると、ユーザーがその瞬間に望んでいないのに、パッケージ
マネージャを走らせてバイナリをダウンロードすることになります。既存の狭い範囲の前例より、副作用が大きく、
驚きも大きくなります。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [x] バックエンド／extra → pip extra + 外部ツールの有無確認 + インストールコマンド、という宣言的マッピング
- [x] config を踏まえたインストールステップ（設定済みバックエンドを解決し、必要なものだけをインストール、冪等）
- [x] 共有 Makefile ターゲット + スクリプトのエントリポイント（`make deps` を置き換え／統合し、`git clone` 直後に呼べる）

### ログ

- **2026-07-05**：三つのピースを一つの変更で出荷しました。`bajutsu/requirements.py` が単一の宣言的
  マッピング（backend／capability から pip extra + 外部ツールのプローブ + インストール方法へ）を保持し、
  `preflight.py` の remedy 文字列はここから読むようになりました（ハードコードしていた `_BACKEND_TOOLS`
  と web の remedy 文言は削除）。`bajutsu/provision.py` は config が参照する backend
  （`backends.resolve_actuators` 経由）と設定済みの AI プロバイダを純粋な `InstallPlan` に解決し、
  注入可能な subprocess のシームを通じて冪等に実行します。エントリポイントは `scripts/install.sh` と
  `make install`（`setup` に依存し、`setup` は config 非依存の土台という役割を保ちます）の二つです。
  `make deps` と `scripts/serve.sh` は idb の導入を同じスクリプトへ委譲し、`idb_companion` は `Brewfile`
  からマッピングへ移しました（`Brewfile` はサンプルアプリのビルドツール `xcodegen` だけを残します）。

## 参考

- [`docs/ja/architecture.md`](../../docs/ja/architecture.md)：モジュール一覧と実装状況
- [`README.md`](../../README.md#setup)：現行の Setup / Requirements セクション
- [`Brewfile`](../../Brewfile) · [`scripts/serve.sh`](../../scripts/serve.sh) · [`bajutsu/preflight.py`](../../bajutsu/preflight.py)
- [BE-0024](../BE-0024-doctor-onboarding/BE-0024-doctor-onboarding-ja.md)：doctor / オンボーディング
- [BE-0090](../BE-0090-uploaded-config-command-execution/BE-0090-uploaded-config-command-execution-ja.md)：アップロードされたバンドル config からのコマンド実行を統制し、サンドボックス化する
- [BE-0111](../BE-0111-ai-sdk-optional-dependency/BE-0111-ai-sdk-optional-dependency-ja.md)：AI SDK を extra へ降ろし、決定的ゲートを AI 非依存でインストールできるようにする
