[English](BE-0269-ios-alert-guard-early-wait-intervention.md) · **日本語**

# BE-0269 — wait ステップ中のシステムアラートガードの介入を早める

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0269](BE-0269-ios-alert-guard-early-wait-intervention-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0269") |
| 実装 PR | [#1122](https://github.com/bajutsu-e2e/bajutsu/pull/1122) |
| トピック | プラットフォーム対応 |
<!-- /BE-METADATA -->

## はじめに

システムアラートガード（`bajutsu/alerts.py` の `SystemAlertGuard.dismiss`）は、OS レベルのプロンプト（iOS の「パスワードを保存」などが典型です）を片付けます。このプロンプトは idb のアプリスコープのアクセシビリティクエリからは見えないため、実行を静かにブロックします。現在このガードは、ステップが実際に失敗したあとにしか動きません。`wait` ステップの場合、`timeout` の予算全体を使い切るまでガードは介入できません。予算は多くが 10〜30 秒で、BE-0231 による smoke CI レーンの `BAJUTSU_MIN_WAIT_TIMEOUT` 下限を含めれば少なくとも 20 秒です。ブロックしているアラートが待機開始から数秒で現れていても、この遅れは変わりません。

本提案は、`wait` 自身のポーリングループの内側で、この予算よりずっと短い独立した間隔で、ブロッキングアラートの有無を確認します。待機全体を無駄にしたあとではなく、アラートを検知できた時点でガードを動かします。

## 動機

`bajutsu/orchestrator/loop.py:550` は、アラートガード（`on_blocked`）を `if not ok` の条件、つまり `_run_step_body` がすでに失敗を返したあとにだけ呼ぶよう組んでいます。`wait` ステップでは、`_wait()`（`bajutsu/orchestrator/waits.py:72`）がその失敗に達するのは、ステップの `timeout` から組み立てた `deadline` に達したときです。

たとえば、`wait: {for: ..., timeout: 20}` というシナリオで「パスワードを保存」のようなプロンプトが待機開始 2 秒後に現れたとします。待機は崩れた要素ツリーを残り 18 秒近くポーリングし続け、そのあとでようやくガードが画面を見ます。実行中に現れるシステムアラートは、一つひとつがほぼ `wait` の `timeout` いっぱいを純粋な待ち時間として費やします。システムプロンプトが複数現れるシナリオ（あるいはスイート）ほど積み重なり、正しさの向上を伴わずに、ローカルでも CI でも実行時間を引き延ばします。ガードが 2 秒で動くか 18 秒で動くかで結果は変わらず、遅いだけです。

この修正は、プライムディレクティブ 1（AI は合否を判定しない）とプライムディレクティブ 2（決定性優先。固定の `sleep` を使わない）を損なうわけにはいきません。最終的なステップの合否は、`_wait()` に既にある決定的な条件チェックから決まる必要があります。ガードが行う AI vision の呼び出しは、頻度をまれかつ有界に保つ必要があります。`_POLL`（`bajutsu/orchestrator/waits.py:14`）が定める 0.05 秒ごとのポーリングティックで毎回呼ぶ形にはできません。`ClaudeAlertLocator.locate`（`bajutsu/alerts.py:163`）は実際にネットワーク越しの往復を行うため、その間隔で動かすには遅すぎ、コストも見合いません。

## 詳細設計

1. **既存の検知器を再利用した、安価で決定的な事前チェックを置きます。** ブロッキングする SpringBoard レベルのプロンプトには、観測可能な特徴があります。idb のアクセシビリティクエリはフォアグラウンドのアプリにスコープされているため、システムアラートが立つと、クエリされる要素ツリーは操作可能な内容を持たない裸のウィンドウへと崩れます。

   この判定は、リポジトリに `shows_app_ui(elements) -> bool`（`bajutsu/elements.py`）としてすでに存在します。この関数は、「単一のウィンドウノード」という素朴な判定より精緻です。docstring によると、`application` 以外の要素が `identifier` または `label` を持つ画面をアプリの UI と見なします。そのため、アクセシビリティ識別子を持たない（ラベルや座標で駆動する）アプリ、たとえば showcase の `-noax` 系を、ブロックされた画面と取り違えません。かつてガードを毎回誤って起動させていた不具合を避ける配慮です。

   画面をポーリングするすべての待機分岐（`for`、`gone`、`screenChanged`、`settled`）は、すでにポーリングのたびに `driver.query()` を呼んで結果を保持しています。`settled` を担う `_wait_settled` の docstring は、システムアラートに覆われた画面を settle 済みと扱わないことを明記しています（`bajutsu/orchestrator/waits.py:142`）。したがって事前チェックは、取得済みのツリーに対する `not shows_app_ui(elements)` であり、追加のクエリコストはありません。新しい検知器を作らず既存のものを再利用することで、2 つの検知ロジックが食い違っていく心配がなくなります。

   ネットワークを対象とする `request`（`WaitRequest`）の分岐は、意図的にスコープ外とします。この分岐は画面ではなく観測したネットワーク通信をポーリングするため、この手がかりが当てはまらないからです。事前チェックは決定的なトリガーであり、合否は決めません。判断するのは、ガードに画面を見てもらう価値があるかどうかだけです。
2. **検知してすぐには動かず、連続するポーリングで確認します。** ツリーが 1 回だけ崩れて見えるのは、本物のアラートではなく一時的なレンダリングの一コマである場合もあります。そのため、この特徴が数回連続して現れたときにだけ「おそらくブロックされている」と判断します。この方法は、`_wait_settled` が使っている `_SETTLE_POLLS` の考え方（`bajutsu/orchestrator/waits.py:15,142`）と同じです。追加される遅延は、決まった小さいポーリング回数分に収まります（`_POLL = 0.05` のもとでは 1 秒に遠く届きません）。新たな `sleep` を際限なく足すことにはなりません。
3. **待機の途中でガードを呼び、同じ待機に戻します。** `_wait()`（および `settled` を担う `_wait_settled`）に、ガード呼び出しをオプションで差し込めるようにします。現在、このガード呼び出しは `loop.py:550` のステップ再試行レベルにしかありません。検知が確定した時点で、`_wait()` は `deadline` を待たずに直接ガードを呼びます。

   dismiss に成功したら、元の条件に対するポーリングを **同じ** `deadline` に対して再開します。早く動くようになるのは介入のタイミングだけです。ステップが失敗を許される `timeout` の予算そのものは変わりません。

   record 経路には、すでに同じ形があります。`record.py` の `clear_blocking` が、ポーリングして `not shows_app_ui` で判定し、ガードを呼んでリトライします。crawl 経路も `bajutsu/cli/commands/crawl.py` の配線を通じて、このループを再利用しています。ここでの作業は、確立したループを新たに作り直さず、決定的な `run` / `wait` 経路へ持ち込むことです。
4. **ガード呼び出しの頻度に上限を設けます。** `ClaudeAlertLocator.locate` は実際の AI vision 呼び出しなので、1 回の待機の中で呼べる回数に上限を設けます。呼び出しの間隔を最低これだけ空ける、待機あたりの最大呼び出し回数を決める、あるいは両方です。崩れた形が持続的な誤検知だった場合や、dismiss してもプロンプトが消えなかった場合でも、ポーリングループが AI 呼び出しを連打するループにはなりません。上限に達したら、`_wait()` は今の挙動に戻ります。つまり `deadline` までポーリングを続け、`loop.py:550` の既存のステップ末尾のガード呼び出しに、もう一度だけ機会を委ねます。
5. **既存のオプトインの範囲内で `wait` ステップに絞ります。** ガードはすでに、アラートの dismiss が有効なときにしか動きません（`--dismiss-alerts`、`bajutsu/cli/commands/run.py:324` のガードファクトリ）。この変更が動かすのは、すでに有効なガードが最初のチャンスを得る **タイミング** だけです。オプトインしていないシナリオに、新しく AI 呼び出しを既定で持ち込むことはありません。

## 検討した代替案

- **`_POLL` を短くし、AI vision ガードを毎ティック呼ぶ。** 採用しません。`_POLL` は、アラートのリスクが一切ないものも含め、すべての待機の条件チェック間隔を決めています。実際の Claude vision 呼び出しを約 20 Hz で走らせると、コストが見合わず、内包するポーリング間隔よりずっと遅くなります。ホットパスの速度が、テスト対象のアプリではなく AI バックエンドで決まってしまいます。
- **ツリーを見ず、時間だけを基準にガードを定期的に呼ぶ（たとえば 2 秒ごと）。** よりシンプルですが、崩れた形が一度も現れない待機でも AI 呼び出しを消費します。本提案の決定的な事前チェックは、取得済みのデータを使うので確実に安く、`alerts.py` 自身が述べている失敗の特徴とも一致します。そのため、盲目的なタイマーより優先します。
- **idb にアラートの有無を直接問い合わせる。** そのような手段はありません。`alerts.py` の docstring のとおり、idb のアクセシビリティクエリは設計上アプリにスコープされているため、SpringBoard レベルのプロンプトは見えません。崩れたツリーという特徴は、このリポジトリが使っていない豊富な API の代わりではなく、現時点で得られる最善の手がかりです。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [x] ユニット 1 — 既存の `shows_app_ui`（`bajutsu/elements.py`）を再利用した決定的な事前チェックを、
      すでに取得済みのポーリング結果から、システムアラートが**足止め**しうる画面ポーリングの分岐
      （`for` / `screenChanged` / `settled`）に対して置きました。提案の一覧から狭めています。`gone` は
      ガードしません。崩れたツリーはそのまま「消えた」を満たしてすぐ返るため、タイムアウトを浪費せず、
      ガードするには「消えた」を空画面では成立しないと再定義することになり、スコープ外だからです。
      ネットワークを対象とする `request` の分岐も従来どおりスコープ外です。
- [x] ユニット 2 — 検知結果を数回連続のポーリング（`_SETTLE_POLLS` にならった `_GUARD_DEBOUNCE_POLLS`）で
      確認してから動かします。
- [x] ユニット 3 — `_wait()`（および `_wait_settled`）にガード呼び出しを差し込み、確認できた時点で
      その場でガードを呼び、元の `deadline` に対してポーリングを再開します。
- [x] ユニット 4 — 1 回の待機あたりのガード呼び出しに、間隔（`_GUARD_COOLDOWN`）と最大回数
      （`_GUARD_MAX_ATTEMPTS`）の上限を設けました。上限に達したときは一度だけログを残し、待機自身の
      タイムアウトへ戻します。

### ログ

- [#1122](https://github.com/bajutsu-e2e/bajutsu/pull/1122)：4 つのユニットを `bajutsu/orchestrator/waits.py`（`_AlertGuardGate` と、`on_blocked` /
  `alerts` を `_wait` / `_wait_settled` へ通す配線）と `bajutsu/orchestrator/loop.py`（配線。ステップ
  末尾のリトライは意図的にミッドウェイトのガードを再武装しないため、1 ステップの AI vision 呼び出しは
  `_GUARD_MAX_ATTEMPTS` + 1 に収まります）で実装しました。`tests/orchestrator/test_waits.py` で検証しています。

## 参考

- [`bajutsu/alerts.py`](../../bajutsu/alerts.py)：`SystemAlertGuard`、`ClaudeAlertLocator`、
  および崩れたツリーという失敗の特徴を述べたモジュール docstring。
- [`bajutsu/orchestrator/waits.py`](../../bajutsu/orchestrator/waits.py)：`_wait`、`_POLL`、
  `_adaptive_sleep`、`_wait_settled`（`_SETTLE_POLLS` による確認の前例）。
- [`bajutsu/elements.py`](../../bajutsu/elements.py)：このアイテムが再利用する既存の崩れたツリー検知器
  `shows_app_ui`（前例。素朴な「単一のウィンドウノード」判定より精緻）。
- [`bajutsu/record.py`](../../bajutsu/record.py)：record 経路の
  ポーリング → `not shows_app_ui` → ガード起動 → リトライのループ `clear_blocking`。ユニット 3 はこの形を
  `run` / `wait` 経路へ持ち込みます。crawl 経路は
  [`bajutsu/cli/commands/crawl.py`](../../bajutsu/cli/commands/crawl.py) を通じて再利用します。
- [`bajutsu/orchestrator/loop.py:550`](../../bajutsu/orchestrator/loop.py)：現在ステップ末尾
  にしかない `on_blocked` の呼び出しを、`wait` ステップについてはこのアイテムで前倒しします。
- [BE-0231](../BE-0231-smoke-idb-first-wait-settling/BE-0231-smoke-idb-first-wait-settling-ja.md)：
  smoke CI レーンで現在の最悪ケースを少なくとも約 20 秒にしている `BAJUTSU_MIN_WAIT_TIMEOUT` 下限。
- [BE-0118](../BE-0118-wait-for-contract-unification/BE-0118-wait-for-contract-unification-ja.md)：
  このアイテムの `_wait()` への変更が土台とする `wait_for` のポーリング契約。
