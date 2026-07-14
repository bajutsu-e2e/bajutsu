[English](BE-XXXX-hosted-scenario-source-import.md) · **日本語**

# BE-XXXX — Git/zip の設定ソースからホスト型シナリオストアへシナリオを取り込む

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-XXXX](BE-XXXX-hosted-scenario-source-import-ja.md) |
| 提案者 | [@paihu](https://github.com/paihu) |
| 状態 | **提案** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| トピック | config の取得元 |
| 関連 | [BE-0015](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting-ja.md), [BE-0063](../BE-0063-git-config-source/BE-0063-git-config-source-ja.md), [BE-0073](../BE-0073-serve-zip-bundle-upload/BE-0073-serve-zip-bundle-upload-ja.md), [BE-0243](../BE-0243-upload-bundle-durable-storage/BE-0243-upload-bundle-durable-storage-ja.md) |
<!-- /BE-METADATA -->

## はじめに

**server backend**（[BE-0015](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting-ja.md)
が導入したホスト型・マルチテナントの `serve`）では、Git リポジトリから config をバインドする場合
（[BE-0063](../BE-0063-git-config-source/BE-0063-git-config-source-ja.md)）も、アップロードした zip
バンドルから config をバインドする場合
（[BE-0073](../BE-0073-serve-zip-bundle-upload/BE-0073-serve-zip-bundle-upload-ja.md)）も、実際に行われ
るのは `state.config` と `state.cwd` を取得済みのツリーへ向け直すことだけです。そのツリーに既に含まれる
シナリオファイルを、server backend が実際に読み書きしているホスト型のシナリオストアへコピーする処理は、
どこにも存在しません。自己完結したバンドルをアップロードしたユーザーも、Git リポジトリを指定したユーザー
も、シナリオ一覧が空のままになります。バインドしたツリーの中に既存のシナリオファイルがディスク上には存在
していても、ホスト型ストアはその存在を一切知らされていないからです。

本提案は、「バインドしたソースにあるシナリオを、このプロジェクトのホスト型シナリオストアへコピーする」
という明示的な一括インポート操作を追加します。バインド直後にも、あとから随時にも呼び出せるようにし、
チームが既に持っているスイートを、`record` や UI エディタで一つひとつ作り直すことなく実行可能にします。

## 動機

- **報告された不具合。** あるユーザーが `--backend=server` のインスタンスに zip バンドル（config とシナ
  リオ一式）をアップロードしたところ、アップロードと config のバインドは成功したにもかかわらず、シナリオ
  一覧は空のままでした。
- **構造的な理由。** BE-0015 の Phase 1 の計画は、ホスト型バックエンドを「プロジェクトごとにシナリオと
  アプリ設定を Postgres/R2 に保存する」というネイティブなプロジェクトモデルとして描いています。この読み
  書きを担うのが `StorageScenarioStore` / `ObjectScenarioStorage`
  （`bajutsu/serve/server/scenarios.py`）で、内容は常に `<prefix>scenarios/<app>/*.yaml` というオブジェ
  クトストアのキーからだけ解決されます。対して BE-0063 と BE-0073 は、セルフホストの単一テナント serve
  （Tier A）向けに設計されたものであり、そこでは `LocalScenarioStore`
  （`bajutsu/serve/state.py:467`）が `state.cwd` から直接シナリオファイルを読みます。つまり両者はまった
  く別の `ScenarioStore` 実装です。`bind_git_config` や `bind_upload_config` を server backend のプロセス
  上で実行しても、書き換わるのは `state.config` と `state.cwd` だけであり、その組織の `state.scenarios`
  （`make_bundle` が組み立てるオブジェクトストア版）は、バインドしたツリーを一切読みません。BE-0063 も
  BE-0073 も、それぞれ単体では設計どおりに動いています。両者を橋渡しする部分が存在しないことが問題です。
- **なぜ問題なのか。** 既に Git に置かれているスイートや、一回限りの実行のために固めた zip を持ち込むこと
  は、BE-0063 と BE-0073 がまさに解決しようとしている「config の取得元」というユースケースそのものです。
  そうしたソースのバインドに成功したユーザーが、そのシナリオがすぐに一覧に出て実行できることを期待するの
  は自然であり、セルフホストの `serve` では実際にそうなります。
- **放置した場合のコスト。** 現状、ホスト型のシナリオ一覧を埋める唯一の手段は、`record` で新規にシナリオ
  を作成するか、UI エディタに YAML を手で貼り付けることです。既に Git リポジトリやシナリオ一式の zip を
  持っているチームにとっては無駄な行き止まりであり、「アップロードすればシナリオも一緒に取り込まれる」と
  思い込んでいる人には意外な落とし穴になります。

## 詳細設計

**対象範囲。** 本提案は **server backend のみ**
（[BE-0015](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting-ja.md) の Phase 2 以降、
`--backend=server`）を対象とします。セルフホスト/ローカルの backend は、`LocalScenarioStore` が
`state.cwd`（`bajutsu/serve/state.py:467`）に対して遅延解決するため、既に正しく動作しており、突き合わせ
るべき別ストレージ層が存在しません。設計は、関連項目に挙げた二つの config ソースの両方を対象にします。
Git 由来のチェックアウトも、アップロードした zip バンドルも、バインド後はどちらも `state.cwd` 起点の同じ
`scenarios/` ディレクトリに解決されるため、既存の `bind_git_config` / `bind_upload_config` の seam を超え
てソースごとに分岐する必要はなく、一つの実装で両方をカバーできます。

**新しい操作。** `import_source_scenarios(state, org, app)` が、バインドしたチェックアウトの `scenarios`
ディレクトリ（`_scenarios_dir_for` が `state.cwd` に対して既に行っている解決と同じもの）を走査し、各
`*.yaml` を読み込んで、Record の出力や UI からの手動編集が既に使っている
`ObjectScenarioStorage.save(app, ref, text)` の seam をそのまま呼び出します。新しいストレージの seam は
追加せず、既存の書き込み経路に対する呼び出し元を一つ増やすだけです。

**衝突の扱い方（ここが要点です）。** インポートは、ホスト型ストアに既にある、誰かが作成・編集済みのシナ
リオを、黙って上書きしてはなりません。

- **既定は既存優先。** そのアプリのオブジェクトストアに**まだ存在しない名前**だけを書き込みます。既存の
  エントリはそのまま残り、レスポンスには「スキップ（既に存在）」として報告します。
- **明示的な上書きの選択。** チェックボックスや `?overwrite=true` によって、Git 上でシナリオを直した後や、
  新しい zip を作り直した後に、意図的に再同期してホスト側のコピーをソース側の内容へ置き換えられるように
  します。
- **一度きりのコピーであり、常時つながったリンクではありません。** インポートはある時点でのコピーです。
  インポート後は、誰かが再インポートするか UI で編集し直すまで、ホスト型ストアがそのシナリオの正となりま
  す。これは、アップロード自体を「一時的な取得手段」と位置づける
  [BE-0073](../BE-0073-serve-zip-bundle-upload/BE-0073-serve-zip-bundle-upload-ja.md) の考え方と同じです。

**API/UI 面。** `POST /api/scenarios/import-from-source`（組織とアプリでスコープし、
`bind_upload_config` / `bind_git_config` と同様に管理者ロールでのみ許可）を新設し、serve UI の「Open
config」ダイアログのバインド後の確認ステップと、シナリオ一覧のツールバーの両方に「このソースからシナリオ
を取り込む」という操作を用意します。バインド直後にも、あとから随時にも見つけやすくするためです。

**決定性とセキュリティ。** これは純粋なファイルコピーの配線であり、LLM は一切関与しないため、prime
directive 1 を満たします。パスの閉じ込め（`Effective.rebased`）を再利用するため、バインドした config が
チェックアウトの外を指すパスをこの新しい読み取り経路に紛れ込ませることはできません。また、
`StorageScenarioScope.save` が既に適用している `valid_scenario_ref` を、オブジェクトストアのキーになる前
のすべての候補 `ref` に適用するため、安全でないファイル名がオブジェクトストアのプレフィックスをまたぐこと
もありません。

## 検討した代替案

- **重ね合わせ型の `ScenarioStore`。** git/zip のツリーを読み取り専用のベース層とし、オブジェクトストアを
  Record の出力や UI 編集用の上書き層として、`list()` で両者を合成する案です。ソースのツリーを一時点のス
  ナップショットではなく、生きた正とし続けられる点で、より筋の良いアーキテクチャです。ただし、名前が同じ
  上書き層のエントリが、後から更新されたソース側のファイルより優先され続けてしまう、という陳腐化の罠を持
  ち込みます。この問題を正しく解くには（ソース側が上書きされていない場合に限りそちらを優先する、「ソース
  へ戻す」操作を UI に用意する、コンテンツのハッシュ比較で失効させる、など）、それなりの設計と実装のコス
  トが必要で、そのコストに見合う恩恵（生きたツリーからの常に最新の読み取り）は、一度きりのインポート（バ
  インド直後に一度取り込み、ソース側を意図的に直したときに再インポートする）でも、よくある用途はほぼ賄え
  ます。この案は、チームが速く動く Git ブランチをホスト UI に密着させて追随させたいという需要が出てきた
  ときのための、将来の選択肢として残しておきます。今回報告された不具合を解消するためには必要ありません。
- **Git/zip ソースがバインドされているときは、ホスト型の `ScenarioStore` が常に `state.cwd` を直接読むよ
  うにする**（そのプロジェクトについては server backend でも `LocalScenarioStore` を再利用する）案です。
  これは
  [BE-0243](../BE-0243-upload-bundle-durable-storage/BE-0243-upload-bundle-durable-storage-ja.md)
  が config について既に解決した、レプリカ間の局所性の問題をそのまま持ち込んでしまうため採用しません。
  シナリオ一覧を返すどのレプリカも、チェックアウトをローカルに実体化しておく必要が生じますし、config が
  ジョブの起動ごとに一度読まれるのに対し、シナリオ一覧は UI のページを開くたびに読まれるため、リクエスト
  ごとの再実体化（あるいは展開済みツリーの分散キャッシュ）は、既にネットワーク経由でアクセスできるストレ
  ージへ一度だけインポートするのに比べて、負担がずっと大きくなります。
- **バインドのたびに、既存のエントリを問答無用で上書きする、常時自動のインポート。** 前回のバインド以降に
  加えられた UI 編集や Record の出力を、警告なしに消してしまうデータ消失の罠になるため採用しません。上書
  きを選択制にすることで、この罠を避けつつ、インポート自体はワンクリックで行えるようにしています。
- **何もせず、この不整合をドキュメントで説明するにとどめる。** これだけでは解決策にならないため採用しま
  せん。「自分のスイートを持ち込む」ことは BE-0063 と BE-0073 そのものの目的であり、それがまさに、ホスト
  側のファイルシステムにアクセスできないチームが最も使うであろう server backend で機能しなくなってしまい
  ます。これは BE-0073 自身が動機の一つ目に挙げている状況そのものです。

## 進捗

> 作業が進むにつれて最新の状態を保ってください。チェックリストは「詳細設計」の作業分割（MECE）に対応す
> る一項目ずつのボックスとし、ログには変更内容と時期（古い順）を記録し、PR にリンクします。

- [ ] `import_source_scenarios`（既存優先を既定とし、上書きは選択制）を実装し、
      `ObjectScenarioStorage.save` を再利用する。
- [ ] `POST /api/scenarios/import-from-source`（管理者ロール限定）を配線し、インポート/スキップ/上書きの
      件数をレスポンスに含める。
- [ ] serve UI に、バインド後の確認ステップとシナリオ一覧のツールバーの両方から呼べる操作を追加する。
- [ ] テスト：Git 由来・zip 由来のどちらのチェックアウトからも取り込めること、既存優先が既定であること、
      上書きの選択制、安全でない名前が `Effective.rebased` / `valid_scenario_ref` で拒否されること。
- [ ] ドキュメント：`docs/architecture.md`（英日両方）に、server backend のシナリオストレージとバインド
      した config のソースとの関係を記載する。

## 参考

- [DESIGN.md §6.5](../../DESIGN.md) — シナリオは git 管理下のファイルであり、Bajutsu はローカル/CLI の経
  路について独自のストアを持たないという原則。本提案はこれと矛盾しません。橋渡しするのはホスト型プロジェ
  クトのストレージ裏付きの `ScenarioStore` との間だけであり、ホスト型ストアは、バインドしたソースが種を
  まく**実行時のコピー**にとどまり、チームにとっての Git という履歴そのものを置き換えるものではありませ
  ん。
- [BE-0015](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting-ja.md) — 本提案が取り込み先と
  する、ホスト型 `server` backend のプロジェクトごとのネイティブなシナリオストレージ（Postgres/R2）。
- [BE-0063](../BE-0063-git-config-source/BE-0063-git-config-source-ja.md) — 本提案が取り込み元にできる、
  Git 設定ソースのチェックアウト。
- [BE-0073](../BE-0073-serve-zip-bundle-upload/BE-0073-serve-zip-bundle-upload-ja.md) — 本提案が取り込み
  元にできる、アップロードした zip バンドルの展開ツリー。また、本提案の一度きりのインポートという設計が
  倣っている「アップロードは一時的な取得手段である」という考え方の出どころ。
- [BE-0243](../BE-0243-upload-bundle-durable-storage/BE-0243-upload-bundle-durable-storage-ja.md) —
  本提案のインポートと並存する、zip の生バイト列そのものの永続ストレージ。本提案が書き込む、シナリオごと
  のオブジェクトストアのエントリとは別物です。
- `bajutsu/serve/operations/upload.py`（`bind_upload_config`）、`bajutsu/serve/operations/config.py`
  （`bind_git_config`）、`bajutsu/serve/server/scenarios.py`（`ObjectScenarioStorage`、
  `StorageScenarioStore`）、`bajutsu/serve/state.py`（`LocalScenarioStore`、`_scenarios_dir_for`）、
  `bajutsu/serve/__init__.py`（`make_bundle`、`_org_apps`） — 本提案が触れる範囲。
