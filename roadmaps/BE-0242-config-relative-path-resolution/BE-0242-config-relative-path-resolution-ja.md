[English](BE-0242-config-relative-path-resolution.md) · **日本語**

# BE-0242 — config が宣言するパスを、その config ファイル基準で解決する

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0242](BE-0242-config-relative-path-resolution-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0242") |
| 実装 PR | [#980](https://github.com/bajutsu-e2e/bajutsu/pull/980) |
| トピック | config の取得元 |
<!-- /BE-METADATA -->

## はじめに

ローカル config には、パス系のフィールドが 6 つあります。`scenarios`、`baselines`、`schemas`、`goldens`、iOS／Android の `appPath`、`xcuitest.testRunner` です。これらは現在、実行プロセスの作業ディレクトリを基準に解決されます。宣言している config ファイル自身は、基準になっていません。

本提案は、これらのフィールドを config ファイル自身のディレクトリを基準に解決します。`bajutsu` をどこから実行しても、同じファイルが同じように解決されます。

ローカル config は、operator が信頼できるものです。そのため、パスを特定の部分木の中に閉じ込めません。config のフォルダの外にある兄弟ディレクトリを指してよいことにします。リポジトリ自身の config が、この挙動を必要としています。showcase の `xcuitest.testRunner` は、`demos/` の兄弟であるリポジトリルート直下の `BajutsuKit/` の下にあります。

ここが、BE-0063 の Git ソース向け rebase との違いです。取得してきた config は信頼できないため、そちらは閉じ込めたままにします。

## 動機

`_load_effective_with_source`（`bajutsu/cli/_shared.py:174-235`）は、config のパス系フィールドを、呼び出し側の作業ディレクトリではなく、安定した基準点に対して rebase する仕組みをすでに持っています。ただし、この rebase を行うのは、config が Git ソースのときだけです。実体化したチェックアウトを `root` として、`eff.rebased(root)` を呼びます（BE-0063）。

config がローカルの場合（`spec is None`）は、関数が早期リターンし、`eff` をそのまま返します（`_shared.py:229-230`）。YAML に書かれた相対パスは、`bajutsu` がたまたま実行されたディレクトリを基準にしたまま解決されます。

このため、同じ config ファイルでも、実行するディレクトリによって挙動が変わります。リポジトリのルートから実行すれば問題なく解決するシナリオスイートが、別の場所から実行すると、参照先を失います。別の場所とは、サブディレクトリ、別の worktree、シェルの作業ディレクトリの慣習が異なる CI ジョブです。もっと悪い場合は、たまたま存在する無関係なファイルへ、気づかないうちに解決されます。

`make worktree`（BE-0069）によって、同じ config を別のチェックアウトパスから実行することは、日常的な操作になっています。この落とし穴は、まさにその状況で踏みやすいものです。あるチェックアウトで動いていた config が、別のチェックアウトで同じように動くとは限りません。作業ディレクトリの慣習が、引き継がれないことがあるからです。

シナリオ側の参照は、すでにこの種のずれを解消済みです。`use:` や `dataFile`（BE-0174）、`setup` は、参照元のシナリオファイルを基準に解決されます。config 側のパス系フィールドだけが、宣言元のファイルではなく、作業ディレクトリを基準にしたまま残っています。

設計を左右する前提を、一点だけ正確に述べておきます。BE-0063 が基準にするのは、config ファイル自身のディレクトリ**ではありません**。**チェックアウトルート**、すなわち取得したツリーの最上位です。これは通常、config の祖先にあたります。`e2e/bajutsu.config.yaml` にある config は、`scenarios` ではなく、チェックアウトルートから書いた `e2e/scenarios` を解決します。

ローカル config には、チェックアウトがありません。代わりの安定した基準点は、config ファイル自身のディレクトリになります。2 つの基準点は、関連はしますが別のものです。この違いこそが、リポジトリ自身の config をすべて config ディレクトリ基準へ書き直す必要がある理由です。これらの config は、リポジトリルートから実行する前提で、リポジトリルート基準のパスを書いてきました（*詳細設計* を参照）。

閉じ込めについても、決め手は「Git ソースであること」ではなく、**信頼できるかどうか**です。リポジトリは、すでにこの線引きをしています。

- 取得してきた Git-API config とアップロードされたバンドルは、信頼できません。confinement 付きで rebase されます（`bajutsu/serve/operations/config.py:471`、`bajutsu/serve/operations/upload.py`）。
- ローカルファイルの config は、operator が信頼できるものとして扱われ、confinement は付きません（`bajutsu/serve/operations/config.py:435`、「a local file config is operator-trusted (BE-0121)」）。

ローカル config に confinement を適用すると、showcase の `xcuitest.testRunner: BajutsuKit/Runner/...` を拒否します。このパスは `demos/` の兄弟であるリポジトリルート直下を指し、`demos/showcase/` からは `../` の逸脱なしに到達できません。そこで本提案は、この既存の信頼境界に合わせ、ローカルのパスを閉じ込めないままにします。

## 詳細設計

1. **`Effective.rebased()` に `confine` スイッチを追加します。**
   - `rebased(root, *, confine=True)`（`bajutsu/config.py:580-634`）は、対象のフィールドを `root` に対して結合する処理を、すでに持っています。対象は、`scenarios`、`baselines`、`schemas`、`goldens`、iOS／Android サブ config の `app_path`、`xcuitest.test_runner` です。
   - 逸脱チェックを、`confine` によって条件付きにします。このチェックは、絶対パスや `../` による逸脱を `ValueError` にします。BE-0051 の serve 堅牢化と同じ考え方です。
   - 既存の呼び出し側（Git ソースとアップロードバンドルの経路）は、`confine=True` のまま変わりません。ローカル config は `confine=False` を渡します。
   - 「Git ソースからしか呼ばれない」（`config.py:587`）という前提が崩れるので、docstring も更新します。
2. **呼び出し側でローカル config を rebase しますが、`checkout_root` は流用しません。**
   - `_load_effective_with_source`（`bajutsu/cli/_shared.py:196-235`）で、`spec is None`（ローカル config）のときは、ローカル専用の基準点（`cfg_path.resolve().parent`）に対して `eff.rebased(local_root, confine=False)` を呼びます。
   - 関数の戻り値の 3 番目の要素（`checkout_root`）は、`None` のまま返します。`root` を両方の用途に使い回しません。
   - `checkout_root` は、rebase の基準点であるだけではありません。他の呼び出し側にとっては、「Git ソースかローカルか」を示す信号でもあります。`run.py` のオンデマンドビルド（`checkout_root is not None` で発火する `build_if_missing`）と、`record`／`crawl` の `_refuse_out_in_checkout`（読み取り専用チェックアウトへの書き込みを拒むガード）は、この値を根拠に判定しています。ここでローカル config のディレクトリを返すと、あらゆるローカル config で、Git 専用の 2 つの挙動が意図せず有効になります。
   - `source`（Git の来歴情報）は、どちらにしても `None` のままです。返る `Effective` は、config ディレクトリを基準とする絶対パスのフィールドを持ちます。それらを読む下流の consumer は、すべて自動的に作業ディレクトリ非依存になります。
3. **意図的に対象外とするフィールド。** `rebased()` が現状すでに除外しているものに、2 つ加えます。
   - `build` は、`make -C demos/showcase` のようなシェルコマンドです。パス系フィールドではありません。
   - `setup` は、参照元のシナリオファイルを基準に、すでに解決されています（BE-0174）。config は基準になりません。
   - `launchServer.cwd` は、起動するサブプロセスに渡す作業ディレクトリです（`bajutsu/runner/launch_server.py:123`）。config が指し示すファイルへの参照ではありません。この既定の基準点を変えるかどうかは、開発サーバーの作業ディレクトリの既定値をどうするか、という別の判断です。同種の落とし穴だと分かった場合に、別項目として扱います。
   - `sandbox.dockerfile` は、すでに `bundle_root` という概念を基準にしています（BE-0090、`bajutsu/runner/sandbox.py:130-206`）。作業ディレクトリとも「宣言元のファイル」とも異なる、第三の基準点です。本提案の対象外とします。
4. **serve のローカル config バインドも合わせて直します。**
   - serve は `_load_effective_with_source` を呼びません。config のパス系フィールドを、`state.cwd` を基準に解決します（`bajutsu/serve/state.py`、`serve/operations/jobs.py`）。Git とアップロードのバインドは、すでに `state.cwd` を config のツリーに設定しています。
   - 取り残されているのは**ローカル**のバインドだけで、serve の起動ディレクトリを基準にしています。該当するのは、非 Git の `--config` での起動（`bajutsu/cli/commands/serve.py`）と、ファイルブラウザ経由のバインド（`serve/operations/config.py:433`）です。
   - この 2 箇所で `state.cwd = config_path.parent` に設定し、Git・アップロードと同じパターンに揃えます。serve が起動する run も、in-process の scenario／app-path の読み取りも、config ディレクトリを基準に解決されます。
   - `load_config`／`resolve` を呼ぶ個々の serve operation は、メタデータ（backend、`id_namespaces`、`ai`、`bundleId`）しか読みません。変更は不要です。
5. **これは互換性のための緩衝を設けない、意図的な破壊的変更です。**
   - すべてのローカル config のパス系フィールドが、作業ディレクトリ基準から config ディレクトリ基準へ、一度の切り替えで変わります。フォールバックやオプトアウトのフラグは設けません。
   - リポジトリ自身の config は、すべてリポジトリルート基準で書かれ、リポジトリルートから実行する前提です。同じ変更の中で、config ディレクトリ基準へ書き直します。対象は `demos/demo.config.yaml`、`demos/showcase/showcase.config.yaml`（`testRunner: ../../BajutsuKit/Runner/...` を含む）、`demos/docs-site/docs-site.config.yaml`、`demos/serve-ui/dogfood.config.yaml`、`demos/web/demo.config.yaml` です。
   - on-device の conformance テストは、ローダを迂回する生の `resolve()` で `Effective` を組み立てています（`tests/test_driver_conformance_ondevice.py:119`）。同じ `rebased(config_dir, confine=False)` を適用します。
   - 移行については、PR 本文と `docs/configuration.md` で明示します。
6. **ドキュメントを更新します。**
   - `docs/configuration.md:81`（「run の作業ディレクトリ基準」という記述）と、現状は Git 限定の rebase として書かれている BE-0063 の節（`docs/configuration.md:330-369`）を、いまのルールに沿って書き直します。
   - ルールは次のとおりです。取得元によらず、config ディレクトリ基準で解決します。信頼できない（Git-API・アップロード）config だけを閉じ込めます。operator が信頼できるローカルファイルは、閉じ込めません。
   - `docs/ja/configuration.md` も、同様に更新します。
7. **テストを追加します。** 3 種類のカバレッジを足します。
   - ローカル config のパス系フィールドが、作業ディレクトリに関わらず config ディレクトリを基準に解決されること。別のディレクトリへ chdir して、結果が変わらないことを確認します。
   - ローカル config が、自身のディレクトリの外を指せること。`ValueError` になりません。
   - 既存の confinement テストと並ぶ、`rebased(..., confine=False)` の単体テスト。

   on-device の conformance レーンと demo config の書き換えは、Linux ゲートではなく、重い CI レーン（`smoke (idb)`／`E2E`／`xcuitest`／`conformance`）でのみ検証されます。この点を、PR で明示します。

## 検討した代替案

- **config ディレクトリ基準にしつつ confinement も適用する案（BE-0063 と完全に同じ挙動）。** 最初に検討した形です。リポジトリ自身のレイアウトに突き当たって、不採用にしました。
  - showcase の `xcuitest.testRunner: BajutsuKit/Runner/...` は、`demos/` の兄弟であるリポジトリルート直下を指します。`demos/showcase/` からは、confinement が拒否する `../` の逸脱なしには表現できません。
  - より根本的には、confinement は**信頼できない**取得済み config を閉じ込めるためのものです（BE-0051）。ローカルファイルは、すでに operator が信頼できるものとして扱われています（BE-0121）。これを閉じ込めても、対応する脅威がないまま、摩擦だけが増えます。
- **フィールド単位・config 単位のオプトインフラグで、ファイル基準の解決を有効にする案。** 不採用とします。安全な解釈のほうを既定にすべきです。config の作者が見つけて有効化しなければならないものにすべきではありません。オプトインにすると、設定しない人には、この落とし穴がそのまま残ります。
- **既定は作業ディレクトリ基準のままにし、ファイル基準へ切り替えるフラグや config キーを追加する案。** 同じ理由で不採用です。加えて、読み手が保持しなければならない解決ルールが 2 つに増えたままになります。古いほうを退役させられません。
- **`launchServer.cwd` の既定値も、config のディレクトリを基準にする案。** ここに含めず、見送りとします。これは、サブプロセスの作業ディレクトリという別種のフィールドです。config が指し示すファイルへの参照ではありません。一緒に扱うと、本提案が実際に直そうとしている対象がぼやけます。同種の落とし穴だと分かれば、別項目として改めて扱います。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [x] `Effective.rebased()` に `confine` スイッチを追加し、docstring を更新する。
- [x] `_load_effective_with_source` でローカル config を
      `eff.rebased(cfg_path.resolve().parent, confine=False)` で rebase する。
- [x] serve のローカル config バインド（起動 `--config`、ファイルブラウザバインド）を config
      ディレクトリ基準に揃える。
- [x] リポジトリ内の demo config を config ディレクトリ基準のパスへ書き直す。
- [x] on-device conformance テストの `Effective` を config ディレクトリ基準で rebase する。
- [x] `docs/configuration.md` と `docs/ja/configuration.md` を更新する。
- [x] 作業ディレクトリ非依存と、ローカルパスを閉じ込めないことのテスト、および
      `rebased(confine=False)` の単体テストを追加する。

[#980](https://github.com/bajutsu-e2e/bajutsu/pull/980) で対応しました。

## 参考

- [BE-0063 — Git リポジトリ + ref から config（とシナリオ一式）を読み込む](../BE-0063-git-config-source/BE-0063-git-config-source-ja.md)
  本提案が拡張する `Effective.rebased()` の仕組みの出どころです。信頼できない Git ソースでは
  confinement を維持します。
- [BE-0174 — シナリオのコンポーネント参照とデータ参照をスイートルート配下に閉じ込める](../BE-0174-scenario-ref-path-containment/BE-0174-scenario-ref-path-containment-ja.md)
  シナリオ側での「参照元のファイルを基準に解決する」という先例です。
- [BE-0051 — serve のホスティング向け堅牢化](../BE-0051-serve-hardening-for-hosting/BE-0051-serve-hardening-for-hosting-ja.md)
  `rebased()` の逸脱チェックが踏襲している、パスの閉じ込めという考え方の出どころで、信頼できない
  取得元向けには維持します。
- [BE-0090 — アップロードされたバンドル config からのコマンド実行を統制・サンドボックス化する](../BE-0090-uploaded-config-command-execution/BE-0090-uploaded-config-command-execution-ja.md)
  本提案が手を付けない、別種の基準点である `bundle_root` の出どころです。
- `docs/configuration.md:81`、`docs/configuration.md:330-369`：更新対象となる、作業ディレクトリ
  基準の記述と BE-0063 の Git rebase の節。
