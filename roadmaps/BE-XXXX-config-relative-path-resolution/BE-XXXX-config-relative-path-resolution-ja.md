[English](BE-XXXX-config-relative-path-resolution.md) · **日本語**

# BE-XXXX — config が宣言するパスを、その config ファイル基準で解決する

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-XXXX](BE-XXXX-config-relative-path-resolution-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **提案** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| トピック | config の取得元 |
<!-- /BE-METADATA -->

## はじめに

ローカル config のパス系フィールド（`scenarios`、`baselines`、`schemas`、`goldens`、iOS/Android の
`appPath`、`xcuitest.testRunner`）は現在、実行プロセスの作業ディレクトリを基準に解決されており、
それらを宣言している config ファイル自身を基準にはしていません。本提案は、これらのフィールドを
config ファイル自身のディレクトリを基準に解決するようにし、`bajutsu` をどこから実行しても同じ
ファイルが同じように解決されるようにします。ローカル config は operator が信頼できるものなので、
パスは特定の部分木の中に閉じ込めません。config のフォルダの外にある兄弟ディレクトリを指してよい
ようにします。これはリポジトリ自身の config が必要としている挙動です（showcase の
`xcuitest.testRunner` は、`demos/` の兄弟であるリポジトリルート直下の `BajutsuKit/` の下にあります）。
ここが BE-0063 の Git ソース向け rebase との違いです。取得してきた config は信頼できないため、
そちらは閉じ込めたままにします。

## 動機

`_load_effective_with_source`（`bajutsu/cli/_shared.py:174-235`）は、config のパス系フィールドを
呼び出し側の作業ディレクトリではなく安定した基準点に対して rebase する仕組みをすでに持っています。
ただし呼び出すのは config が Git ソースのときだけで、実体化したチェックアウトを `root` として
`eff.rebased(root)` を呼び出します（BE-0063）。config がローカルの場合（`spec is None`）は早期
リターンし、`eff` をそのまま返すため（`_shared.py:229-230`）、YAML に書かれた相対パスは `bajutsu`
がたまたま実行されたディレクトリを基準にしたまま解決されます。

このため、同じ config ファイルでも実行するディレクトリによって挙動が変わります。リポジトリの
ルートから実行すれば問題なく解決するシナリオスイートが、サブディレクトリや別の worktree、あるいは
シェルの作業ディレクトリの慣習が異なる CI ジョブから実行すると、参照先を失う、あるいはもっと悪い
ことに、たまたま存在する無関係なファイルへ静かに解決されてしまいます。`make worktree`（BE-0069）
によって、同じ config を別のチェックアウトパスから実行することは日常的な操作になっており、この
落とし穴はまさにその状況で踏みやすいものです。あるチェックアウトで「動いていた」config が、別の
チェックアウトへそのまま持ち込んだときに同じ作業ディレクトリの慣習が引き継がれるとは限らないから
です。

シナリオ側の参照はすでにこの種のずれを解消済みです。`use:` や `dataFile`（BE-0174）、`setup` は、
参照元のシナリオファイルを基準に解決されます。config 側のパス系フィールドは、宣言元のファイルでは
なく作業ディレクトリを基準にしたまま残っている、唯一の箇所です。

設計を左右するので、前提を一点だけ正確に述べておきます。BE-0063 が基準にするのは config ファイル
自身のディレクトリ**ではなく**、**チェックアウトルート**、すなわち取得したツリーの最上位です。これは
通常 config の親にあたります（`e2e/bajutsu.config.yaml` にある config は、`scenarios` ではなく、
チェックアウトルートから書いた `e2e/scenarios` を解決します）。ローカル config にはチェックアウトが
ないため、代わりの安定した基準点は config ファイル自身のディレクトリになります。両者は関連しますが
別のアンカーであり、この違いこそが、リポジトリルートから実行する前提でリポジトリルート基準のパスを
書いてきたリポジトリ自身の config を、すべて config ディレクトリ基準へ書き直す必要がある理由です
（*詳細設計* を参照）。

閉じ込めについても、「Git ソースであること」ではなく **信頼できるかどうか**で決まる性質です。
リポジトリはすでにこの線引きをしています。取得してきた Git-API config とアップロードされたバンドルは
信頼できないので confinement 付きで rebase され（`bajutsu/serve/operations/config.py:481`、
`bajutsu/serve/operations/upload.py`）、一方でローカルファイルの config は operator が信頼できるものと
して扱われ、confinement は付きません（`bajutsu/serve/operations/config.py:435`、「a local file config is operator-trusted
(BE-0121)」）。ローカル config に confinement を適用すると、showcase の
`xcuitest.testRunner: BajutsuKit/Runner/...` を拒否してしまいます。これは `demos/` の兄弟である
リポジトリルート直下のパスで、`demos/showcase/` からは `../` の逸脱なしには到達できません。そこで
本提案は、この既存の信頼境界に合わせ、ローカルのパスは閉じ込めないままにします。

## 詳細設計

1. **`Effective.rebased()` に `confine` スイッチを追加します。** `rebased(root, *, confine=True)`
   （`bajutsu/config.py:580-634`）は、対象のフィールド（`scenarios`、`baselines`、`schemas`、
   `goldens`、iOS/Android サブ config の `app_path`、`xcuitest.test_runner`）を `root` に対して
   結合する処理をすでに持っています。その逸脱チェック（絶対パスや `../` による逸脱を `ValueError`
   にする、BE-0051 の serve 堅牢化と同じ考え方）を、`confine` によって条件付きにします。既存の
   呼び出し側（Git ソースとアップロードバンドルの経路）は `confine=True` のまま変わりません。
   ローカル config は `confine=False` を渡します。「Git ソースからしか呼ばれない」（`config.py:587`）
   という前提が崩れるので、docstring も更新します。
2. **呼び出し側でローカル config を rebase しますが、`checkout_root` は流用しません。**
   `_load_effective_with_source`（`bajutsu/cli/_shared.py:196-235`）で、`spec is None`（ローカル
   config）のときは、ローカル専用の基準点（`cfg_path.resolve().parent`）に対して
   `eff.rebased(local_root, confine=False)` を呼んで rebase します。ただし、関数の戻り値の 3 番目の
   要素（`checkout_root`）は `None` のまま返し、`root` を両方の用途に使い回しません。
   `checkout_root` は rebase の基準点であると同時に、他の呼び出し側にとっては「Git ソースか
   ローカルか」を示す信号でもあるからです。`run.py` のオンデマンドビルド（`checkout_root is not
   None` で発火する `build_if_missing`）や、`record`／`crawl` の `_refuse_out_in_checkout`
   （読み取り専用チェックアウトへの書き込みを拒むガード）は、いずれもこの値を根拠に判定して
   います。ここでローカル config のディレクトリを `checkout_root` としてそのまま返すと、あらゆる
   ローカル config で Git 専用の挙動が意図せず有効になってしまいます。`source`（Git の来歴情報）は
   どちらにしても `None` のままで、返る `Effective` は config ディレクトリを基準とする絶対パスの
   フィールドを持つようになるので、それらを読む下流の consumer はすべて自動的に作業ディレクトリ
   非依存になります。
3. **意図的に対象外とするフィールド**は、`rebased()` が現状すでに除外しているものに、2 つ加える
   形になります。
   - `build`（`make -C demos/showcase` のようなシェルコマンド）はパス系フィールドではありません。
   - `setup` は、参照元のシナリオファイルを基準にすでに解決されており（BE-0174）、config を基準
     にはしません。
   - `launchServer.cwd` は、起動するサブプロセスに渡す作業ディレクトリであり
     （`bajutsu/runner/launch_server.py:123`）、config が指し示すファイルへの参照ではありません。
     この既定の基準点を変えるかどうかは別の判断（開発サーバーの作業ディレクトリの既定値をどう
     するか）であり、同種の落とし穴だと分かった場合に別項目として扱います。
   - `sandbox.dockerfile` は、すでに `bundle_root` という概念を基準にしており（BE-0090、
     `bajutsu/runner/sandbox.py:130-206`）、作業ディレクトリとも「宣言元のファイル」とも異なる
     第三の基準点なので、本提案の対象外です。
4. **serve のローカル config バインドも合わせて直します。** serve は `_load_effective_with_source`
   を呼ばず、config のパス系フィールドを `state.cwd` を基準に解決します
   （`bajutsu/serve/state.py`、`serve/operations/jobs.py`）。Git・アップロードのバインドはすでに
   `state.cwd` を config のツリーに設定しています。取り残されているのは**ローカル**のバインドだけで、
   これは serve の起動ディレクトリを基準にしています。非 Git の `--config` での起動
   （`bajutsu/cli/commands/serve.py`）と、ファイルブラウザ経由のバインド
   （`serve/operations/config.py:433`）の 2 箇所で `state.cwd = config_path.parent` に設定し、
   Git・アップロードと同じパターンに揃えます。これで serve が起動する run も、in-process の
   scenario／app-path の読み取りも、config ディレクトリを基準に解決されます。`load_config`／
   `resolve` を呼ぶ個々の serve operation はメタデータ（backend、`id_namespaces`、`ai`、`bundleId`）
   しか読まないため、変更は不要です。
5. **これは互換性のための緩衝を設けない、意図的な破壊的変更です。** すべてのローカル config の
   パス系フィールドが、作業ディレクトリ基準から config ディレクトリ基準へ一度の切り替えで変わります。
   フォールバックやオプトアウトのフラグは設けません。リポジトリ自身の config はすべてリポジトリ
   ルート基準で書かれ、リポジトリルートから実行する前提なので、同じ変更の中で config ディレクトリ
   基準へ書き直します。対象は `demos/demo.config.yaml`、`demos/showcase/showcase.config.yaml`
   （`testRunner: ../../BajutsuKit/Runner/...` を含む）、`demos/docs-site/docs-site.config.yaml`、
   `demos/serve-ui/dogfood.config.yaml`、`demos/web/demo.config.yaml` です。on-device の conformance
   テストは、ローダを迂回する生の `resolve()` で `Effective` を組み立てているため
   （`tests/test_driver_conformance_ondevice.py:119`）、同じ `rebased(config_dir, confine=False)` を
   適用します。移行については PR 本文と `docs/configuration.md` で明示します。
6. **ドキュメントを更新します。** `docs/configuration.md:81`（「run の作業ディレクトリ基準」という
   記述）と、現状は Git 限定の rebase として書かれている BE-0063 の節
   （`docs/configuration.md:330-369`）を、いまのルールに沿って書き直します。すなわち、取得元に
   よらず config ディレクトリ基準で解決し、信頼できない（Git-API・アップロード）config だけを
   閉じ込め、operator が信頼できるローカルファイルは閉じ込めない、という形です。
   `docs/ja/configuration.md` も同様に更新します。
7. **テストを追加します。** ローカル config のパス系フィールドが、作業ディレクトリに関わらず config
   ディレクトリを基準に解決されること（別のディレクトリへ chdir して結果が変わらないことを確認）、
   ローカル config が自身のディレクトリの外を指せること（`ValueError` にならない）、そして既存の
   confinement テストと並ぶ `rebased(..., confine=False)` の単体テストを追加します。on-device の
   conformance レーンと demo config の書き換えは、Linux ゲートではなく重い CI レーン（`smoke (idb)`
   ／`E2E`／`xcuitest`／`conformance`）でのみ検証される点を、PR で明示します。

## 検討した代替案

- **config ディレクトリ基準にしつつ confinement も適用する（BE-0063 と完全に同じ挙動）案**：最初に
  検討した形です。リポジトリ自身のレイアウトに突き当たって不採用にしました。showcase の
  `xcuitest.testRunner: BajutsuKit/Runner/...` は `demos/` の兄弟であるリポジトリルート直下を指し、
  `demos/showcase/` からは confinement が拒否する `../` の逸脱なしには表現できません。より根本的には、
  confinement は**信頼できない**取得済み config を閉じ込めるためのもの（BE-0051）であり、ローカル
  ファイルはすでに operator が信頼できるものとして扱われている（BE-0121）ため、これを閉じ込めても
  対応する脅威がないまま摩擦だけが増えます。
- **フィールド単位・config 単位のオプトインフラグでファイル基準の解決を有効にする案**：不採用と
  します。安全な解釈のほうを既定にすべきであり、config の作者が見つけて有効化しなければならない
  ものにすべきではありません。オプトインにすると、設定しない人にはこの落とし穴がそのまま残ります。
- **既定は作業ディレクトリ基準のままにし、ファイル基準へ切り替えるフラグや config キーを追加する
  案**：同じ理由で不採用です。加えて、読み手が保持しなければならない解決ルールが 2 つに増えたまま
  になり、古いほうを退役させられません。
- **`launchServer.cwd` の既定値も config のディレクトリを基準にする案**：ここに含めず見送りとし
  ます。これはサブプロセスの作業ディレクトリという別種のフィールドであり、config が指し示す
  ファイルへの参照ではないため、一緒に扱うと本提案が実際に直そうとしている対象がぼやけます。
  同種の落とし穴だと分かれば、別項目として改めて扱います。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [ ] `Effective.rebased()` に `confine` スイッチを追加し、docstring を更新する。
- [ ] `_load_effective_with_source` でローカル config を
      `eff.rebased(cfg_path.resolve().parent, confine=False)` で rebase する。
- [ ] serve のローカル config バインド（起動 `--config`、ファイルブラウザバインド）を config
      ディレクトリ基準に揃える。
- [ ] リポジトリ内の demo config を config ディレクトリ基準のパスへ書き直す。
- [ ] on-device conformance テストの `Effective` を config ディレクトリ基準で rebase する。
- [ ] `docs/configuration.md` と `docs/ja/configuration.md` を更新する。
- [ ] 作業ディレクトリ非依存と、ローカルパスを閉じ込めないことのテスト、および
      `rebased(confine=False)` の単体テストを追加する。

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
