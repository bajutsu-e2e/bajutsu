[English](BE-0174-scenario-ref-path-containment.md) · **日本語**

# BE-0174 — シナリオの component／data 参照をスイートのルート内に封じ込める

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0174](BE-0174-scenario-ref-path-containment-ja.md) |
| 提案者 | [@hirosassa](https://github.com/hirosassa) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0174") |
| 実装 PR | _pending_ |
| トピック | セキュリティ強化 |
<!-- /BE-METADATA -->

## はじめに

シナリオが展開する際に参照するファイル、すなわち `use:` の component 参照とデータ駆動の CSV 参照を、
そのシナリオが属するスイートの中に封じ込めます。これにより、シナリオがローダに自分の木の外のファイルを
読ませることはできなくなります。デバイス不要のローダ（`bajutsu/scenario` の `load_expanded_scenarios`／
`expand_components`／`expand_data`）は、各参照をシナリオファイルのディレクトリを起点に `base / ref` で
解決しますが、封じ込めの確認がありません。絶対パスや `../` の連鎖は、プロセスが読めるどこへでも届きます。
決定的でアプリ非依存です。参照が不正なら、あいまいなセレクタと同じように、その場で明確に失敗します。

## 動機

このローダに至る経路は一つではありません。CLI では `coverage`、`audit`、`trace --explain` がスイートを
まるごと展開します。serve Web UI では、Coverage タブ（[BE-0146](../BE-0146-serve-coverage/BE-0146-serve-coverage-ja.md)）
と既存の `run`／`audit` オペレーションが同じことをします。そのいずれもが、シナリオの
`use: { component: <参照> }` と CSV の `data:` 参照を、素の `base / ref` で、そのパスがどこに着地するかの
制限なしに解決します。

これは、シナリオファイルが完全には信頼できない場面では、パストラバーサルによる読み取りの足がかりに
なります。`use: { component: ../../../../etc/passwd }`（あるいは絶対パス）を持つシナリオは、ローダに
そのファイルを開かせます。しかも不正な対象はエラーメッセージに現れます。`_parse_yaml_named` が該当ファイル
名を示し、パースエラーがファイルの断片を出しうるため、失敗の経路は読み取るだけでなく中身を**漏らし**えます。
BE-0146 の Coverage タブがこの点に気付くきっかけになりましたが、問題はその変更より前からあり、それに固有
でもありません。同じローダが CLI と serve の他の経路の土台になっています。

Bajutsu は「呼び出し側がルート配下の**内容**を握る一方で、参照の**形**は封じ込める」という考え方を、すでに
別の場所で第一級の関心事として扱っています。run のアーティファクトストアは、あらゆる `rel` を `runs_dir`
に封じ込めます（`bajutsu/serve/artifacts.py`）。また
[BE-0051](../BE-0051-serve-hardening-for-hosting/BE-0051-serve-hardening-for-hosting-ja.md) は、`/api/run`
でクライアントが指定するシナリオの**パス**を封じ込めました。本項目は、その一段内側にある同種の穴、すなわち
シナリオファイルの**内側**の参照をふさぎます。これが最も効くのは、config やスイートを必ずしも運用者が書いて
いない場面です。アップロードされたバンドル（[BE-0073](../BE-0073-serve-zip-bundle-upload/BE-0073-serve-zip-bundle-upload-ja.md)）、
Git から取り込む config（[BE-0063](../BE-0063-git-config-source/BE-0063-git-config-source-ja.md)）、
マルチテナントのホスティング（[BE-0108](../BE-0108-hosted-config-source-restriction/BE-0108-hosted-config-source-restriction-ja.md)）
がそれにあたります。

## 詳細設計

共有ローダに封じ込めの確認を置き、読み取る前にすべての参照へ適用します。決定的で、合否には関わりません。

- **読み込みごとの封じ込めルート。** 参照は今までどおり `base / ref` で解決し、そのうえで、解決した
  **実パス**（シンボリックリンクを辿ったもの）が許可されたルートの中にとどまることを求めます。ルートは
  スイート自身のディレクトリ木、すなわち読み込みを始めた scenarios ディレクトリです。したがって、その木の
  下でシナリオと共有する component は今までどおり解決します。
- **正当な相対参照は許し、外への脱出は拒む。** `use: { component: ../components/login.yaml }` のような
  共有 component の配置は実在するパターンで、**ルートの中にとどまる限り**は動き続けなければなりません。
  拒むのは、ルートの外へ解決される参照、絶対パス、外を指すシンボリックリンクの三つ、つまり参照が木を
  出ていく道です。拒否は該当参照を示す明確で決定的なエラーとし、そのエラーは対象ファイルの中身を出しては
  なりません（読み取りだけでなく漏洩もふさぎます）。
- **ひとつの絞り込み点で、すべての呼び出し元に効かせる。** 確認は共有ローダ
  （`bajutsu/scenario/load_expanded.py` と `expand_components`／`expand_data` の参照解決）に置きます。
  これにより、CLI（`coverage`／`audit`／`trace`）も serve（Coverage、`run`、`audit`）も、呼び出し元ごとの
  コードなしにこれを受け継ぎます。呼び出し元は封じ込めルート（自分の scenarios ディレクトリ）を渡し、
  強制はローダが担います。
- **アプリ非依存で AI を使わない。** ルートはスイートを読み込んだ場所から来るもので、アプリ固有の知識では
  ありません。確認はパスの純粋な比較で、モデルは使わず、合否の経路には決して乗りません。

## 検討した代替案

* **デプロイ側に委ねる（スイートの作者を信頼する）。** 不採用です。ローダは信頼できない config の経路
  （アップロード／Git／ホスティング）から到達され、「すべてが信頼できるから安全」という性質こそ、BE-0051 が
  兄弟の `/api/run` 経路で取り除いたものです。読み取りに漏洩まで伴う足がかりを、それらの面に開けたまま
  にはできません。
* **参照中の `..` を一律に禁止する。** 不採用です。正当な `../components/shared.yaml` の配置を壊します。
  正しい境界は**解決後にルート内へ収まっているか**であり、これは内側にとどまる `..` を許し、外へ脱出する
  `..` を拒みます。トークンそのものの禁止は、厳しすぎると同時に（シンボリックリンク経由で）緩すぎます。
* **serve の入口だけを守る。** 不採用です。穴は共有ローダにあるので、一つの呼び出し元だけを守っても CLI
  と serve の他の経路が開いたままになり、ずれを招きます。修正は、アーティファクトストアの `rel` 封じ込めと
  同じく、絞り込み点に置くべきです。
* **読み込み全体をコンテナで sandbox する。** ここではスコープ外です。それはアップロードの実行に対する
  姿勢（[BE-0073](../BE-0073-serve-zip-bundle-upload/BE-0073-serve-zip-bundle-upload-ja.md)）であり、パスの
  封じ込めの確認は、読み取りに対する常時有効で見合った修正であって、実行時の隔離を必要としません。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [x] 共有の参照解決（`load_expanded_scenarios`／`expand_components`／`expand_data`）に封じ込めの確認を
      加える。実パスへ解決し、呼び出し元が渡すスイートのルート内にとどまることを求め、絶対パスと
      シンボリックリンクによる脱出を、中身を出さないエラーで拒む
- [x] 各呼び出し元（CLI の `coverage`／`audit`／`trace`、serve の Coverage／`run`／`audit`）から封じ込め
      ルートをローダへ渡す
- [x] 高速なテストスイートで押さえる。ルート内の正当な `../components/…` 参照は今までどおり読み込め、
      絶対パス、ルート外への `../` の連鎖、シンボリックリンクによる脱出は、いずれもファイルの中身を出さずに
      拒まれる

### ログ

- 共有の絞り込み点は `bajutsu/scenario/load_expanded.py` の `contained_ref(root, base, ref)` です。
  `base / ref` を実パス（シンボリックリンクを辿ったもの）へ解決し、呼び出し元のスイートのルート内に
  とどまることを求め、外れる場合は参照名だけを示す中身を出さない `ValueError` で拒みます。
  `load_expanded_scenarios`（既定のルートはファイルのディレクトリ）と `load_scenarios_dir`（ルートは
  scenarios ディレクトリ）が component と CSV の両方の解決をこれに通すので、CLI の
  `coverage`／`audit`／`trace --explain` と serve の Coverage タブがこれを受け継ぎます。CLI の `run`
  経路（`_expand_file`）も同じヘルパを共有し、serve の `run` はこれを subprocess で駆動します。serve の
  `audit` は参照を展開せず 1 ファイルをパースするだけなので、封じ込める参照はありません。
- 補足：`run` の `setup:` プレリュード参照は今も素の `base / ref` で解決します。その封じ込めは自然な
  兄弟のフォローアップとし、本項目が宣言する component／data のスコープには含めていません。

## 参考

* `bajutsu/scenario/load_expanded.py`、`bajutsu/scenario/expand.py`（この変更が封じ込める共有ローダと、
  `use:`／CSV の参照解決）。
* `bajutsu/serve/artifacts.py`（既存の先例。run 相対の `rel` を一箇所で `runs_dir` に封じ込めています）。
* [BE-0146 — serve Web UI で E2E カバレッジマップを見る](../BE-0146-serve-coverage/BE-0146-serve-coverage-ja.md)
  （このレビュー、PR [#702](https://github.com/bajutsu-e2e/bajutsu/pull/702) がきっかけとなった面）、
  [BE-0051 — serve のホスティング向けハードニング](../BE-0051-serve-hardening-for-hosting/BE-0051-serve-hardening-for-hosting-ja.md)
  （クライアント指定のシナリオ**パス**を封じ込めた、本参照封じ込めの兄弟）、
  [BE-0073 — serve への zip バンドルアップロード](../BE-0073-serve-zip-bundle-upload/BE-0073-serve-zip-bundle-upload-ja.md)、
  [BE-0063 — Git を config のソースにする](../BE-0063-git-config-source/BE-0063-git-config-source-ja.md)、
  [BE-0108 — ホスティング時の config ソース制限](../BE-0108-hosted-config-source-restriction/BE-0108-hosted-config-source-restriction-ja.md)
  （これを重要にする、信頼できない config の面）。
* [CLAUDE.md](../../../CLAUDE.md)、[DESIGN.md](../../../DESIGN.md)（決定性優先＝不正な参照は推測せず明確に
  失敗する、そして確認が保つアプリ非依存の境界）。
