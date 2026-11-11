[English](BE-0181-crawl-continuation.md) · **日本語**

# BE-0181 — クロールを途中から再開できるようにする（Web UI 対応、フロンティア全体の再開）

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0181](BE-0181-crawl-continuation-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0181") |
| 実装 PR | [#765](https://github.com/bajutsu-e2e/bajutsu/pull/765) |
| トピック | 自律クロール |
<!-- /BE-METADATA -->

## はじめに

クロール（BE-0038）を、後から Web UI で、そのクロールが生成した run に対して再開できるようにします。
今は、クロールを開始したブラウザタブの中でしか、続きを探索できません。

足りないものは二つあります。

1. Web UI にある「枝刈りされたブランチを再開する」機能は、開始したタブのメモリ上の状態
   （`crawlRunId`）が生きているあいだしか使えません。ページを再読み込みするか、別の run を開き直すと
   使えなくなります。
2. `--max-screens` や `--max-steps` で止まったクロールを、その先まで続けて探索する手段がありません。
   再開できるのは、名前を指定した単一の枝刈りブランチだけで、残りのフロンティア全体は再開できません。

この提案は両方を解消します。保存済みマップから種を作る処理は、すでにバックトラック機構を再利用する
作りです。そのため、並列ワーカーによる継続探索も無償で手に入ります。

## 動機

[BE-0038](../BE-0038-autonomous-crawl-exploration/BE-0038-autonomous-crawl-exploration-ja.md) が実装した
クロールの再開は、ただ一つの用途に限られています。**枝刈りされたグローバルコントロール**
（[`Pruned`](../../bajutsu/crawl.py)）の再開です。これは、タブやナビゲーションのように複数画面に共通する
操作を、すでに他の画面が探索済みとして省略したものを指します。

`bajutsu crawl --resume-src <fp> --resume-key <key> --out <既存の run>` は、その画面までリプレイして、
省略された一つの操作を探索します（[`bajutsu/cli/commands/crawl.py`](../../bajutsu/cli/commands/crawl.py)）。
Web UI のクロールグラフでは、枝刈りされた操作がすべて取り消し線付きで表示されます。タップすると同じ
リクエストが送られます（[`bajutsu/templates/serve.js`](../../bajutsu/templates/serve.js) の
`resumePruned`）。

実際に「後から」使おうとすると、二つの課題が残ります。

**一つ目は、Web UI の再開機能が、クロールを開始したタブの中でしか使えないことです。** `resumePruned` は
`runId: crawlRunId` を送ります。このモジュール変数は、そのページで `/api/crawl` を呼び出した（あるいは
直近で再開した）応答によってのみ設定されます。Crawl タブを開き、5 分前や 5 日前に完了した run を
`runs/<id>/screenmap.json` から読み込んで、枝刈りされたブランチをタップする、という使い方は今の実装では
できません。`crawlRunId` が `null` のままなので、タップするたびに「再開対象の run がありません」という
失敗になります。

Replay タブは、シナリオ実行について同じ課題をすでに解決しています。`history` から run 一覧を組み立てる
run ピッカーを持っているからです（[`serve.js`](../../bajutsu/templates/serve.js) の `loadHistory`）。
Crawl タブには、過去の screen map を開き直す手段が、これに相当するものとしてありません。

**二つ目は、予算切れで止まったクロールを、その先まで探索する手段がないことです。** ある程度の規模の
アプリでは、`stop_reason` は `"completed"` よりも `"max_screens"` や `"max_steps"` になることのほうが
ずっと多くなります。予算はまさにそのために存在します。今は「もっと奥まで探索したい」と思っても、
`--max-screens` や `--max-steps` を大きくして、クロール全体を最初からやり直すしかありません。
すでに発見済みの画面のマップ上の位置はすべて捨てられ、入口画面から歩き直すことになります。

`--resume-src`/`--resume-key` は、ここでは役に立ちません。名前を指定した一つのブランチしか再開できず、
「前回の run が探索し残したすべて」を再開する手段ではないからです。

この二つの課題は、一つの対処で解消できます。エンジンには、**フロンティア全体の継続探索**を持たせます。
これは、未試行の操作が残っているすべての画面を再開するモードで、一つのブランチだけではありません。
Web UI には、任意の過去の run を、再開系の機能が前提としている状態に読み込む手段を用意します。

継続探索が単一の指名されたブランチに縛られなくなれば、それをワーカー一つに縛る理由もなくなります。
既存の複数ワーカーのプール（[BE-0064](../BE-0064-parallel-crawl/BE-0064-parallel-crawl-ja.md) /
[BE-0077](../BE-0077-parallel-web-crawl/BE-0077-parallel-web-crawl-ja.md)）は、そのまま適用できます。

## 詳細設計

### 新たな永続化なしにフロンティアを再構築する

`ScreenMap` は、フロンティア全体の再開に必要な情報をすでに永続化しています。BE-0038 が、レポートや
ライブグラフ表示のために、もともとそう設計していたからです。

- **`paths: dict[fingerprint, tuple[Action, ...]]`**：発見済みの各画面へ到達する、リプレイ可能な正準パス。
- **`plan: dict[fingerprint, list[str]]`**：各画面に残っている未試行の操作を、人間が読める説明文として
  保持したもの（グラフ表示のために常に最新化されています）。

スキーマの変更は不要です。`plan[fp]` が空でない画面 `fp` を再開するには、次の4手順を踏みます。

1. `paths[fp]` を、クリーンな `reset()` の直後からリプレイします。ライブクロール中に `select_next_work` の
   既存のバックトラック処理がすでに行っている手順と同じです。
2. `driver.query()` を呼びます。
3. `candidate_actions(elements)`（[`bajutsu/crawl.py`](../../bajutsu/crawl.py)）で、決定的に候補を計算します。
4. `.describe()` が `plan[fp]` にまだ残っている操作と一致する候補だけを残します。

残った集合は、元のクロールが止まった時点でまだ試していなかった操作そのものです。画面の同一性と候補の
順序は、要素ツリーの純粋な関数として決まります（[`crawl.py`](../../bajutsu/crawl.py) のモジュール
docstring）。再開時に再導出しても、最初の run が次に試したはずの候補と一致します。`plan` が約束していた
内容からずれる心配はありません。

### エンジン：フロンティア全体を継続探索するモード

`crawl()` は、すでに `base_map` を受け取ってウォームスタートできます。単一ブランチの再開は、さらに
`seed_path`/`seed_ops` を渡して一つの画面のフロンティアだけを種にし、意図的に `extra_workers` を無効化
しています（「再開は単一ブランチの歩行だから」）。

ここに、`base_map` のもう一つの使い方を追加します。`seed_path`/`seed_ops` を渡さずに `base_map` だけを
渡した場合、`crawl()` は `_bootstrap()` を省略します。マップの画面はすでにすべて判明しているからです。
代わりに、上記の方法で再構築した画面ごとの操作を、`coord.pending[fp]` にすべて投入します。

あとは、既存の `select_next_work` のバックトラック処理が、保留中のエントリのうち最も安いものを選び、
そこまでリプレイして続きを探索します。この処理は、クロールの途中でいつも行っているのと同じように、
複数ワーカーを複数ルートのフロンティアへ振り向けます。`extra_workers` をこのモードのために特別扱いする
必要はありません。

停止条件（`max_screens`/`max_steps`/`completed`）は変わりません。継続探索は通常、前回の run が突き当たった
予算を引き上げてから行います。前回の `stop_reason` が `max_steps` で、画面数の予算に余裕が残っていた場合は、
同じ `--max-screens` のまま継続することにも意味があります。

### CLI：`--continue`

`bajutsu crawl --out <既存の run> --continue [--max-screens N] [--max-steps N] [--workers N]` を追加します。
単一ブランチを再開する `--resume-src`/`--resume-key` とは別のフラグで、こちらの意味はそのまま維持します。
`--continue` と `--resume-src`/`--resume-key` は同時指定できません（CLI が両方の指定を拒否します）。

`--continue` は `screenmap.json` を読み込んで、フロンティア全体を再構築します。そのうえで、`--udid`/
`--workers` でサイズを決める通常のワーカープールを、新規クロールとまったく同じように動かします。これは、
単一ブランチの再開では得られなかった、並列での継続探索です。

### Web UI

- **過去の run を Crawl タブに読み込み直す。** Replay タブの `history` 由来のピッカー
  （[`serve.js`](../../bajutsu/templates/serve.js) の `loadHistory`）にならい、Crawl パネルに run ピッカーを
  追加します。run を選ぶと、既存の `loadGraph(runId)` でその `screenmap.json` を読み込み、
  `crawlRunId = runId` を設定します。これにより、枝刈りされたブランチの「タップして再開」機能が、
  それを生成したタブに限らず、任意の過去の run で使えるようになります。
- **「続きを探索する」操作を追加する。** 計画ツリーの枝刈りブランチの行の隣に、「続きを探索する」操作を
  追加します。この操作は、単一の `resumeSrc`/`resumeKey` ではなく、新しい `continue: true` モードを送ります。
  既存の `#crawl-maxscreens`/`#crawl-maxsteps` の入力欄をそのまま再利用するので、同じリクエストの中で
  予算を引き上げられます。`start_crawl`
  （[`bajutsu/serve/operations/dispatch.py`](../../bajutsu/serve/operations/dispatch.py)）には、既存の
  `resuming` の分岐と並べて `continue` の分岐を追加します。この分岐は、`continue_crawl` フラグを
  `crawl_command` から CLI の `--continue` まで橋渡しします。

## 検討した代替案

- **予算を撤廃し、クロールが尽きるまで自動的に継続探索する。** 却下します。予算は、デバイスの稼働時間と
  AI ガイドの呼び出し回数を、意図的に制限するために存在します。明示的に確認できる継続探索という一手順を
  残せば、単一ブランチの再開がすでに確立している「求められたときだけ追加で費用をかける」という歯止めを
  保てます。
- **`paths` と `plan` から再開時に再構築するのではなく、未試行の `Action` の一覧をそのまま
  `screenmap.json` に永続化する。** 動作はしますが、実際の要素ツリーと同期を保たなければならない状態が
  増え、スキーマの変更も必要になります。リプレイのたびに `candidate_actions` で再計算するコストは、
  1 画面あたりクエリ 1 回分です。エンジンがもともと前提としている決定性の保証（同じ要素からは同じ候補が
  得られる）をそのまま再利用でき、スキーマの変更も一切不要です。
- **フロンティア全体の継続探索を実装せず、Web UI の「開始したタブでしか再開できない」不具合だけを直す。**
  枝刈りブランチの課題は解決します。しかし、クロールが最初の予算を使い切るもっと一般的な状況、つまり
  「もっと奥まで探索したい」には対応できません。run ピッカーさえあれば、続きを探索するボタンは同じ
  リクエストのバリエーションにすぎないため、両方をまとめて出荷するほうが安上がりです。
- **クライアント側だけの対処（`crawlRunId` を `localStorage` に保存する）。** 同じタブでの再読み込みには
  対応できます。しかし、数日後に**別の** run のマップを開き直す（「あのクロールに戻ってさらに先へ進める」）
  という実際の用途には対応できません。`runs/` を裏付けとする本物の run ピッカーが、どのみち必要です。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [x] エンジン：`paths` と `plan` からのフロンティア全体の再構築（スキーマ変更なし）と、`crawl()` の
      `base_map` のみ（`seed_ops` なし）による継続探索の経路。
- [x] エンジン：フロンティア全体の継続探索で `extra_workers`（並列再開）を許可する。
- [x] CLI：`bajutsu crawl` に `--continue` フラグを追加する（`--resume-src`/`--resume-key` とは
      同時指定不可）。
- [x] Web UI：過去の run の `screenmap.json` を Crawl タブに読み込み直し、その run で枝刈りブランチを
      再開する（読み取り専用の run ピッカーは BE-0180 が出荷済みで、本項目はそれを探索するための明示的な
      解除を追加します）。
- [x] Web UI：`dispatch.py` の `start_crawl` まで配線した「続きを探索する」操作。

ログ：

- フロンティア全体の継続探索を、一通り実装しました。変更は次のとおりです。
  - [`crawl.py`](../../bajutsu/crawl.py)：エンジンの再構築と継続探索の経路、および並列継続探索のサポート。
  - [`cli/commands/crawl.py`](../../bajutsu/cli/commands/crawl.py)：`--continue` フラグ
    （`--resume-src`/`--resume-key` とは同時指定不可）。
  - [`dispatch.py`](../../bajutsu/serve/operations/dispatch.py) と `crawl_command`：`continue` の分岐。
  - [`serve.js`](../../bajutsu/templates/serve.js)：「続きを探索する」操作と、BE-0180 の読み取り専用の
    履歴表示を意図的に抜ける枝刈りブランチの再開。
- [BE-0180](../BE-0180-crawl-history-viewer/BE-0180-crawl-history-viewer-ja.md) は、Crawl タブの run
  ピッカー（意図的に読み取り専用）をすでに出荷していました。これと整合させ、gap #1 は、選択時にピッカーを
  起動可能にするのではなく、明示的な解除としました。
- 決定的な Web UI の e2e カバレッジを追加しました（BE-0189 の serve-UI dogfood）。内側の serve の
  `--runs` が指すコミット済みの crawl-history フィクスチャ（`demos/serve-ui/fixtures/crawl-runs`）と、
  `demos/serve-ui/scenarios/crawl-history.yaml` を追加しています。後者は、過去の run が読み取り専用で
  開き直されること（past-crawl バッジ）と、フロンティアが残る場合に「続きを探索する」コントロールが出る
  ことをアサートします。AI 駆動の continue / resume のクリックは決定的な網の外に置き、コントロールの
  提示だけをアサートします。

## 参考

- [`bajutsu/crawl.py`](../../bajutsu/crawl.py)：`ScreenMap`（`paths`、`plan`、`pruned`）、
  `candidate_actions`、`_Coordinator.select_next_work`、`crawl()`。
- [`bajutsu/cli/commands/crawl.py`](../../bajutsu/cli/commands/crawl.py)：この提案が拡張する、既存の
  `--resume-src`/`--resume-key` による単一ブランチの再開。
- [`bajutsu/serve/operations/dispatch.py`](../../bajutsu/serve/operations/dispatch.py)、
  [`bajutsu/templates/serve.js`](../../bajutsu/templates/serve.js)：`start_crawl`、`resumePruned`、
  `loadHistory`（Replay タブにある run ピッカーの先例）。
- [BE-0038 — 自律クロール探索（App Explorer 風）](../BE-0038-autonomous-crawl-exploration/BE-0038-autonomous-crawl-exploration-ja.md)：
  枝刈りブランチの再開を導入した項目。本提案はこれを一般化します。
- [BE-0064 — 複数シミュレータでの並列クロール](../BE-0064-parallel-crawl/BE-0064-parallel-crawl-ja.md)、
  [BE-0077 — 複数ブラウザでの並列 Web クロール](../BE-0077-parallel-web-crawl/BE-0077-parallel-web-crawl-ja.md)：
  フロンティア全体の継続探索がそのまま再利用するワーカープール。
- [BE-0092 — クロール調整役をクラスに切り出す](../BE-0092-crawl-coordinator-extraction/BE-0092-crawl-coordinator-extraction-ja.md)：
  本提案のフロンティア投入処理が組み込まれる `_Coordinator`。
