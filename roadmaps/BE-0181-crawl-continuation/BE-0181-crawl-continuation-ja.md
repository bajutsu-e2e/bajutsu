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
| トピック | クロール性能 / スケールアウト |
<!-- /BE-METADATA -->

## はじめに

クロール（BE-0038）を、開始したその場だけでなく、後から Web UI で当該の run に対して再開できるように
します。今は二つのことができません。一つは、Web UI がすでに持っている「枝刈りされたブランチを再開する」
機能が、開始したブラウザタブの状態（`crawlRunId`）が生きているあいだしか使えないことです。ページを
再読み込みしたり、別の run を開き直したりすると使えなくなります。もう一つは、`--max-screens` や
`--max-steps` で止まったクロールを、その先まで続けて探索する手段がないことです。名前を指定した単一の
枝刈りブランチしか再開できません。この提案は両方を解消します。保存済みマップから再開する処理はすでに
バックトラック機構を再利用する作りになっているため、あわせて並列ワーカーによる継続探索も無償で手に
入ります。

## 動機

[BE-0038](../BE-0038-autonomous-crawl-exploration/BE-0038-autonomous-crawl-exploration-ja.md) が実装した
クロールの再開は、**枝刈りされたグローバルコントロール**（タブやナビゲーションのように複数画面に共通する
操作を、すでに他の画面が探索済みとして省略したもの。[`Pruned`](../../bajutsu/crawl.py) として記録され
ます）を再開する、ただ一つの用途に限られています。`bajutsu crawl --resume-src <fp> --resume-key <key> --out <既存の run>`
は、その画面までリプレイしたうえで省略された一つの操作を探索します
（[`bajutsu/cli/commands/crawl.py`](../../bajutsu/cli/commands/crawl.py)）。Web UI のクロールグラフ
でも、枝刈りされた操作はすべて取り消し線付きで表示され、タップすると同じリクエストが送られます
（[`bajutsu/templates/serve.js`](../../bajutsu/templates/serve.js) の `resumePruned`）。

実際に「後から」使おうとすると、二つの課題が残ります。

**一つ目は、Web UI の再開機能がクロールを開始したタブの中でしか使えないことです。** `resumePruned` は
`runId: crawlRunId` を送りますが、これはそのページで `/api/crawl` を呼び出した（あるいは直近で再開
した）応答によってのみ設定される、モジュール変数です。Crawl タブを開いて、5 分前や 5 日前に完了した
run を `runs/<id>/screenmap.json` から読み込み、枝刈りされたブランチをタップする、という使い方は今の
実装ではできません。`crawlRunId` が `null` のままなので、タップするたびに「再開対象の run がありません」
という失敗になります。Replay タブはシナリオ実行について同じ課題をすでに解決しており、`history` から
run 一覧を組み立てる run ピッカーを持っています（[`serve.js`](../../bajutsu/templates/serve.js) の
`loadHistory`）。Crawl タブには、過去の screen map を開き直す手段が相当するものとしてありません。

**二つ目は、予算切れで止まったクロールをその先まで探索する手段がないことです。** ある程度の規模のアプリ
では、`stop_reason` は `"completed"` よりも `"max_screens"` や `"max_steps"` になることのほうがずっと
多く、予算はまさにそのために存在します。今は「もっと奥まで探索したい」と思っても、`--max-screens` や
`--max-steps` を大きくしてクロール全体を最初からやり直すしかなく、すでに発見済みの画面のマップ上の位置
はすべて捨てられ、入口画面から歩き直すことになります。`--resume-src`/`--resume-key` はここでは役に
立ちません。名前を指定した一つのブランチしか再開できず、「前回の run が探索し残したすべて」を再開する
手段ではないからです。

この二つの課題は、一つの対処で解消できます。**フロンティア全体の継続探索**（未試行の操作が残っている
すべての画面を再開する。一つのブランチだけではない）をエンジンに持たせ、Web UI には任意の過去の run を
再開系の機能が前提としている状態に読み込む手段を用意します。継続探索が単一の指名されたブランチに縛ら
れなくなれば、それをワーカー一つに縛る理由もなくなります。既存の複数ワーカーのプール
（[BE-0064](../BE-0064-parallel-crawl/BE-0064-parallel-crawl-ja.md) /
[BE-0077](../BE-0077-parallel-web-crawl/BE-0077-parallel-web-crawl-ja.md)）はそのまま適用できます。

## 詳細設計

### 新たな永続化なしにフロンティアを再構築する

`ScreenMap` は、フロンティア全体の再開に必要な情報をすでに永続化しています。BE-0038 がレポートやライブ
グラフ表示のために、もともとそう設計していたからです。

- **`paths: dict[fingerprint, tuple[Action, ...]]`**：発見済みの各画面へ到達する、リプレイ可能な正準パス。
- **`plan: dict[fingerprint, list[str]]`**：各画面に残っている未試行の操作を、人間が読める説明文として
  保持したもの（グラフ表示のために常に最新化されています）。

スキーマの変更は不要です。`plan[fp]` が空でない画面 `fp` を再開するには、`paths[fp]` をクリーンな
`reset()` の直後からリプレイし（これはライブクロール中の `select_next_work` の既存のバックトラック処理が
すでに行っている手順と同じです）、`driver.query()` を呼んで、`candidate_actions(elements)`
（[`bajutsu/crawl.py`](../../bajutsu/crawl.py)）で決定的に候補を計算し、その `.describe()` が
`plan[fp]` にまだ残っている操作と一致するものだけを残します。これは、元のクロールが止まった時点でまだ
試していなかった操作の集合そのものです。画面の同一性と候補の順序は要素ツリーの純粋な関数として決まるため
（[`crawl.py`](../../bajutsu/crawl.py) のモジュール docstring）、再開時に再導出しても最初の run が
次に試したはずの候補と一致し、`plan` が約束していた内容からずれる心配はありません。

### エンジン：フロンティア全体を継続探索するモード

`crawl()` はすでに `base_map` を受け取ってウォームスタートできます。単一ブランチの再開はさらに
`seed_path`/`seed_ops` を渡して一つの画面のフロンティアだけを種にし、意図的に `extra_workers` を無効化
しています（「再開は単一ブランチの歩行だから」）。ここに、`base_map` のもう一つの使い方を追加します。
`seed_path`/`seed_ops` を渡さずに `base_map` だけを渡した場合、`_bootstrap()` を省略し（マップの画面は
すでにすべて判明しています）、代わりに上記の方法で再構築した画面ごとの操作を `coord.pending[fp]` に
すべて投入します。あとは、既存の `select_next_work` のバックトラック処理（保留中のエントリのうち
最も安いものを選び、そこまでリプレイして続きを探索する）が、クロールの途中でいつも行っているのと
同じように、複数ワーカーを複数ルートのフロンティアへ振り向けます。`extra_workers` をこのモードのために
特別扱いする必要はありません。停止条件（`max_screens`/`max_steps`/`completed`）は変わりません。継続探索
は通常、前回の run が突き当たった予算を引き上げてから行いますが、前回の `stop_reason` が `max_steps` で
画面数の予算に余裕が残っていた場合は、同じ `--max-screens` のまま継続することにも意味があります。

### CLI：`--continue`

`bajutsu crawl --out <既存の run> --continue [--max-screens N] [--max-steps N] [--workers N]` を
追加します。単一ブランチを再開する `--resume-src`/`--resume-key`（意味はそのまま維持します）とは別の
フラグです。`--continue` は `screenmap.json` を読み込んでフロンティア全体を再構築し、`--udid`/
`--workers` でサイズを決める通常のワーカープールを、新規クロールとまったく同じように動かします。これは
単一ブランチの再開では得られなかった、並列での継続探索です。`--continue` と
`--resume-src`/`--resume-key` は同時指定できません（CLI が両方の指定を拒否します）。

### Web UI

- **過去の run を Crawl タブに読み込み直す。** Replay タブの `history` 由来のピッカー
  （[`serve.js`](../../bajutsu/templates/serve.js) の `loadHistory`）にならい、Crawl パネルに run
  ピッカーを追加します。run を選ぶと、既存の `loadGraph(runId)` でその `screenmap.json` を読み込み、
  `crawlRunId = runId` を設定します。これにより、枝刈りされたブランチの「タップして再開」機能が、
  それを生成したタブに限らず、任意の過去の run で使えるようになります。
- **「続きを探索する」操作を追加する。** 計画ツリーの枝刈りブランチの行の隣に、単一の `resumeSrc`/
  `resumeKey` ではなく、新しい `continue: true` モードを送る「続きを探索する」操作を追加します
  （既存の `#crawl-maxscreens`/`#crawl-maxsteps` の入力欄をそのまま再利用し、同じリクエストの中で
  予算を引き上げられるようにします）。`start_crawl`
  （[`bajutsu/serve/operations/dispatch.py`](../../bajutsu/serve/operations/dispatch.py)）に、既存の
  `resuming` の分岐と並べて `continue` の分岐を追加し、`continue_crawl` フラグを `crawl_command` から
  CLI の `--continue` まで橋渡しします。

## 検討した代替案

- **予算を撤廃し、クロールが尽きるまで自動的に継続探索する。** 却下します。予算はデバイスの稼働時間と
  AI ガイドの呼び出し回数を意図的に制限するために存在します。明示的に確認できる継続探索という一手順を
  残すことで、単一ブランチの再開がすでに確立している「求められたときだけ追加で費用をかける」という
  歯止めを保てます。
- **`paths` と `plan` から再開時に再構築するのではなく、未試行の `Action` の一覧をそのまま
  `screenmap.json` に永続化する。** 動作はしますが、実際の要素ツリーと同期を保たなければならない状態が
  増え、スキーマの変更も必要になります。リプレイのたびに `candidate_actions` で再計算するコストは
  1 画面あたりクエリ 1 回分だけで、エンジンがもともと前提としている決定性の保証（同じ要素からは同じ候補
  が得られる）をそのまま再利用でき、スキーマの変更も一切不要です。
- **フロンティア全体の継続探索を実装せず、Web UI の「開始したタブでしか再開できない」不具合だけを直す。**
  枝刈りブランチの課題は解決しますが、「もっと奥まで探索したい」という、クロールが最初の予算を使い切る
  もっと一般的な状況には対応できません。run ピッカーさえあれば、続きを探索するボタンは同じリクエストの
  バリエーションにすぎないため、両方をまとめて出荷する方が安上がりです。
- **クライアント側だけの対処（`crawlRunId` を `localStorage` に保存する）。** 同じタブでの再読み込みには
  対応できますが、数日後に**別の** run のマップを開き直す（「あのクロールに戻ってさらに先へ進める」）
  という実際の用途には対応できません。`runs/` を裏付けとする本物の run ピッカーがどのみち必要です。

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

- フロンティア全体の継続探索を一通り実装しました。[`crawl.py`](../../bajutsu/crawl.py) にエンジンの
  再構築と継続探索の経路、および並列継続探索のサポートを追加し、
  [`cli/commands/crawl.py`](../../bajutsu/cli/commands/crawl.py) に `--continue` フラグ
  （`--resume-src`/`--resume-key` とは同時指定不可）を、
  [`dispatch.py`](../../bajutsu/serve/operations/dispatch.py) と `crawl_command` に `continue`
  の分岐を、[`serve.js`](../../bajutsu/templates/serve.js) に「続きを探索する」操作と、BE-0180 の
  読み取り専用の履歴表示を意図的に抜ける枝刈りブランチの再開を追加しました。すでに Crawl タブの run
  ピッカー（意図的に読み取り専用）を出荷していた
  [BE-0180](../BE-0180-crawl-history-viewer/BE-0180-crawl-history-viewer-ja.md) と整合させ、gap #1 は
  選択時にピッカーを起動可能にするのではなく、明示的な解除としました。
- 決定的な Web UI の e2e カバレッジを追加しました（BE-0189 の serve-UI dogfood）。内側の serve の
  `--runs` が指すコミット済みの crawl-history フィクスチャ（`demos/serve-ui/fixtures/crawl-runs`）と、
  過去の run が読み取り専用で開き直し（past-crawl バッジ）、フロンティアが残る場合に「続きを探索する」
  コントロールを出すことをアサートする `demos/serve-ui/scenarios/crawl-history.yaml` を追加しました。
  AI 駆動の continue / resume のクリックは決定的な網の外に置き、コントロールの提示だけをアサートします。

## 参考

- [`bajutsu/crawl.py`](../../bajutsu/crawl.py) — `ScreenMap`（`paths`、`plan`、`pruned`）、
  `candidate_actions`、`_Coordinator.select_next_work`、`crawl()`。
- [`bajutsu/cli/commands/crawl.py`](../../bajutsu/cli/commands/crawl.py) — この提案が拡張する、既存の
  `--resume-src`/`--resume-key` による単一ブランチの再開。
- [`bajutsu/serve/operations/dispatch.py`](../../bajutsu/serve/operations/dispatch.py)、
  [`bajutsu/templates/serve.js`](../../bajutsu/templates/serve.js) — `start_crawl`、`resumePruned`、
  `loadHistory`（Replay タブにある run ピッカーの先例）。
- [BE-0038 — 自律クロール探索（App Explorer 風）](../BE-0038-autonomous-crawl-exploration/BE-0038-autonomous-crawl-exploration-ja.md)
  — 枝刈りブランチの再開を導入した項目。本提案はこれを一般化します。
- [BE-0064 — 複数シミュレータでの並列クロール](../BE-0064-parallel-crawl/BE-0064-parallel-crawl-ja.md)、
  [BE-0077 — 複数ブラウザでの並列 Web クロール](../BE-0077-parallel-web-crawl/BE-0077-parallel-web-crawl-ja.md)
  — フロンティア全体の継続探索がそのまま再利用するワーカープール。
- [BE-0092 — クロール調整役をクラスに切り出す](../BE-0092-crawl-coordinator-extraction/BE-0092-crawl-coordinator-extraction-ja.md)
  — 本提案のフロンティア投入処理が組み込まれる `_Coordinator`。
