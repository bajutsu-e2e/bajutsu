[English](BE-0164-config-aware-environment-installer.md) · **日本語**

# BE-0164 — config を踏まえた環境インストーラー

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0164](BE-0164-config-aware-environment-installer-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **提案** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0164") |
| トピック | doctor / オンボーディング |
<!-- /BE-METADATA -->

## はじめに

Bajutsu のバックエンド非依存なコア部分は、実際に使うバックエンドによって必要な外部要素が異なります。
iOS Simulator 向けの Homebrew の `idb_companion`、web バックエンド向けの Playwright の Chromium ビルド、
Tier 1 の AI 系パス向けの `anthropic` SDK などです。しかし今は、プロジェクトの config を見て必要なものを
そのままインストールしてくれる仕組みがありません。代わりにあるのは、それぞれ一部分だけをカバーする独立した
3 つの仕組みです。`make setup`（Python のツールチェーンのみ）、`make deps`（idb のみ、ハードコード）、
[`scripts/serve.sh`](../../scripts/serve.sh)（idb のみ、`serve` のみ）です。本項目は、プロジェクトの
実効 config を読み、設定済みのバックエンドが実際に必要とする pip の extra と外部ツールをインストールする、
単一の config を踏まえたインストーラーを提案します。

## 動機

「bajutsu の依存関係はいつインストールされるのか」に単一の答えはありません。正直なところ「複数の分断された
仕組みのうち、たまたま何を実行したかによる」というのが実情です。

- **`make setup`** は `uv sync --group dev` を実行するだけで、Python のツールチェーンと AI フリーの
  基本パッケージしかインストールしません。どのバックエンド向けにも何もインストールしません。
- **`make deps`** は iOS 向けの要素だけを（[`Brewfile`](../../Brewfile) に対する `brew bundle` で
  `idb_companion` / `xcodegen` を、加えて `uv sync --extra idb` を）**無条件に**インストールします。
  web バックエンドしか使わないプロジェクトでもこれが走ります。`uv sync --extra web` +
  `playwright install chromium` に相当するターゲットはなく、`--extra ai` / `--extra visual` /
  `--extra mcp` にも同様のものがありません。
- **`scripts/serve.sh`** は `make deps` の idb チェック（`.venv/bin/idb` と `idb_companion` の有無確認）の
  一部を再実装していますが、それは `bajutsu serve` の起動経路に限られます。`bajutsu run` や
  `bajutsu doctor`、`bajutsu record` を直接実行する場合はこの恩恵を受けられません。
- **web バックエンド**については、`doctor` / `preflight.py` がチェック失敗**後に**出す対処メッセージ
  （`uv sync --extra web` / `playwright install chromium`。[BE-0024](../BE-0024-doctor-onboarding/BE-0024-doctor-onboarding-ja.md)
  参照）だけが唯一の「インストーラー」であり、それを読んで手で実行するしかありません。
- `pyproject.toml` には `ai` / `idb` / `web` / `visual` / `mcp` / `bedrock` / `server` / `db` /
  `oauth` / `schema` / `s3` / `gcs` / `cloud` など十数個の extra が宣言され、それぞれ遅延 import と
  ガードの背後にありますが、「*特定の*プロジェクトの config が必要とする extra」と「現在インストール
  済みの extra」を突き合わせて差分を埋める単一の手順はありません。

実務上の影響として、新しく参加した contributor（あるいは自分たちの bajutsu 駆動テストリポジトリを
立ち上げようとするチーム）は、run の奥深くでコマンドが失敗して初めて不足に気づきます。
`no available actuator among ['idb']`、ガードされていない遅延 import からの `ImportError`、
`preflight` の失敗といった形です。そのあと、いくつもあるインストール経路のどれが該当するのかを
逆算しなければなりません。そして今後追加される Android（[BE-0007](../BE-0007-android-backend/BE-0007-android-backend-ja.md)）や
Flutter（[BE-0008](../BE-0008-flutter-support/BE-0008-flutter-support-ja.md)）のバックエンドも、
この同じパターン（独自の Makefile ターゲットやシェルスクリプトを一から書くこと）を繰り返しかねません。
このロジックが乗る唯一の拡張可能な置き場所がない限り、そうなります。

これは明らかにオンボーディングの領域であり、[BE-0024](../BE-0024-doctor-onboarding/BE-0024-doctor-onboarding-ja.md)
の `doctor` / `preflight` と同じ領域です。しかし意図的に BE-0024 には統合せず、別項目とします。
BE-0024 が診断する（「環境は準備できているか」）のに対し、本項目は実行する（「環境を準備する」）ものであり、
パッケージマネージャを走らせバイナリをダウンロードするという、質的に異なる能力だからです。BE-0024 自身の
詳細設計にも、今後は受け皿とせず個別に提案する旨が明記されています。

## 詳細設計

作業は独立した MECE（相互排他的かつ網羅的）な 3 つに分解できます。

1. **「バックエンド／extra が何を必要とするか」を宣言する単一の情報源。** 今は同じ事実が独立して
   ずれうる 3 か所にハードコードされています。Brewfile + `scripts/serve.sh` の有無チェック +
   `preflight.py` の対処メッセージが、いずれも idb の要件を別々に記述しています。web バックエンドの
   要件（`uv sync --extra web` + `playwright install chromium`）に至っては対処メッセージの文章として
   しか存在せず、機械可読な形はどこにもありません。本項目では、バックエンドファミリー（`idb` / `web` /
   `fake`）とオプション機能（`ai`、`visual`、`mcp` など）ごとに、pip の extra 名・外部ツールの有無確認
   （`command -v` によるプローブ）・不足時のインストール方法（macOS 専用ツールなら Homebrew の formula
   参照、web なら `playwright install <browser>` の実行、あるいは「不要」）を対応づける単一のマッピングを
   導入します。`preflight.py` の対処メッセージも `make deps` も新しいインストーラーも、それぞれ独自の
   コピーを持つのではなく、この単一のマッピングを参照します。
2. **config を踏まえたインストールステップ。** プロジェクトの実効 config（`run` / `doctor` がすでに
   使っているのと同じ `--config` 解決）から、`targets.*.backend` が実際に参照しているバックエンド
   （`backends.py` の既存の解決ロジックを再利用）と、AI プロバイダが設定されているかを解決し、その
   ターゲットが必要とするものだけをインストールします。「idb を無条件に」（今日の `make deps`）でも
   「すべて」（そのようなオプションは今は存在しない）でもありません。各ステップは `scripts/serve.sh` の
   既存パターン（`[ ! -x .venv/bin/idb ]`、`command -v idb_companion`）にならい冪等にし、すでに存在する
   ものを再インストールせず、`make setup` のたびに実行しても安全にします。
3. **1 つの実装を共有する 2 つのエントリポイント。** 1 つ目は contributor 向けの Makefile ターゲット
   です。`make setup` と `make deps` を 1 つにまとめるか、両者と並ぶ `make install` を新設し、
   今日 `make setup` を実行するのと同じタイミング、つまり `git clone` の直後に走らせる想定です。
   2 つ目は、その裏側のロジックを切り出したスクリプトです（`Makefile` 本体にインラインで重複させない）。
   こうすることで実装は 1 つのままとなり、
   下流の各プロジェクトが自分の Makefile から同じように呼び出せます。`scripts/serve.sh` や
   `scripts/preflight.sh` がすでにそうなっているのと同じ形です。

本項目は**ローカルで開発者自身が実行する**セットアップステップにとどめ、ホスティングされたパスや
マルチテナントのパス（`bajutsu serve --backend=server`、アップロードされたバンドルの実行）には一切
組み込みません。アップロードされた config を代行してパッケージマネージャを走らせバイナリをダウンロード
することは、[BE-0090](../BE-0090-uploaded-config-command-execution/BE-0090-uploaded-config-command-execution-ja.md)
（アップロードされたバンドル config からのコマンド実行を統制し、サンドボックス化する）が意図的に閉じた境界を
越えてしまいます。したがって本インストーラーは、人が自分のクローンに対して実行するものであり、
サーバーがネットワーク越しに受け取った config に対して実行するものではありません。

これは決定論的な `run` / CI ゲートには一切触れません。純粋な環境のブートストラップであり、シナリオが
実行される前に走り、pass/fail の判定に組み込まれることはありません（prime directive #1）。また、
1 のマッピングこそが新しいバックエンドの要件を差し込む場所となるため、インストーラーのロジックを
バックエンドごとに分岐させることなく、バックエンド非依存であり続けます（prime directive #3）。

## 検討した代替案

**`bajutsu doctor` に `--fix` フラグを足し、診断した不足をその場でインストールさせる**案。CLI サブ
コマンドそのものをインストーラーにする形です。魅力的な補完案ではあります。1 のマッピングさえできれば、
`doctor` の対処メッセージを表示するだけでなく同じインストール用の関数を呼び出せるようになるからです。
ただしこの仕組みは、Python パッケージが**すでに**インストールされ import 可能になったあとにしか存在
しない Typer の CLI コマンドの裏に隠れてしまい、本項目が狙うより根本的なギャップ（`git clone` の直後、
Python の環境がまだ何も無い時点で何を実行すればよいか）には応えません。インストーラーのロジックが
一元化されたあとの軽量なフォローアップとしては検討に値しますが、本項目の代替にはなりません。

**バックエンドごとの場当たり的なスクリプトを今のパターンのまま続ける**（バックエンドが特殊事情を抱える
たびに `scripts/<backend>-serve-equivalent.sh` を新設する）案は却下します。まさに今日の現状そのもの
であり、Android / Flutter には通用せず、同じ事実（どの extra か、どの外部ツールか）が Brewfile、
`scripts/serve.sh`、`preflight.py` の対処メッセージにまたがって重複したままになります。

**何かが必要になった最初の瞬間に、暗黙かつ無言でインストールする**（明示的なステップを一切設けない）案も
却下します。`scripts/serve.sh` はすでに idb について、この考え方の狭い版を実装済みですが、それを
（あらゆるコマンド、あらゆるバックエンドに向けて）さらに一般化してしまうと、ユーザーがその瞬間に望んで
いないにもかかわらずパッケージマネージャを走らせバイナリをダウンロードすることになり、既存の狭くスコープ
された前例よりもずっと大きく驚きの大きい副作用になります。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [ ] バックエンド／extra → pip extra + 外部ツールの有無確認 + インストールコマンド、という宣言的マッピング
- [ ] config を踏まえたインストールステップ（設定済みバックエンドを解決し、必要なものだけをインストール、冪等）
- [ ] 共有 Makefile ターゲット + スクリプトのエントリポイント（`make deps` を置き換え／統合し、`git clone` 直後に呼べる）

## 参考

- [`docs/ja/architecture.md`](../../docs/ja/architecture.md) — モジュール一覧と実装状況
- [`README.md`](../../README.md#setup) — 現行の Setup / Requirements セクション
- [`Brewfile`](../../Brewfile) · [`scripts/serve.sh`](../../scripts/serve.sh) · [`bajutsu/preflight.py`](../../bajutsu/preflight.py)
- [BE-0024](../BE-0024-doctor-onboarding/BE-0024-doctor-onboarding-ja.md) — doctor / オンボーディング
- [BE-0090](../BE-0090-uploaded-config-command-execution/BE-0090-uploaded-config-command-execution-ja.md) — アップロードされたバンドル config からのコマンド実行を統制し、サンドボックス化する
- [BE-0111](../BE-0111-ai-sdk-optional-dependency/BE-0111-ai-sdk-optional-dependency-ja.md) — AI SDK を extra へ降ろし、決定的ゲートを AI 非依存でインストールできるようにする
