[English](BE-XXXX-absolute-timestamp-recording.md) · **日本語**

# BE-XXXX — 動画・ステップ・通信ログのタイムスタンプを絶対時刻(壁時計)で記録する

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-XXXX](BE-XXXX-absolute-timestamp-recording-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **提案** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| トピック | 検証とカバレッジ |
| 関連 | [BE-0346](../BE-0346-video-timing-sync/BE-0346-video-timing-sync-ja.md) |
<!-- /BE-METADATA -->

## はじめに

[BE-0346](../BE-0346-video-timing-sync/BE-0346-video-timing-sync-ja.md)は、シナリオの動画
[インターバル](../../docs/ja/glossary.md#証跡-capturepolicy-trace-triage)ごとに確認済みの開始時刻
(`Interval.true_start`)を持たせ、そこから一度だけ、プロセス内で`time.monotonic()`をもとに解決した
1つのオフセットを通じて、各ステップと通信ログのレポート用タイムスタンプをこの時刻に紐づけました。
本項目はこのアンカリングの仕組み自体は維持しつつ、何を記録するかを変えます。シナリオの実行中に
すでに相対値である「動画開始からの経過秒数」を`StepOutcome.started_at`や`network.json`の
`startedAt`へ焼き込むのではなく、すべてのイベントが実際に起きた絶対的な壁時計時刻を記録し、
レポートの動画シークバーに必要な相対オフセットは、それらの絶対値から描画時に一度だけ計算します。

## 動機

これには関連する2つの問題があります。

**1. 確認ポーリングのタイムアウトの再発が、BE-0346が修正したまさにそのズレを再び引き起こします。**
`_await_video_file_growing` / `_await_screenrecord_started`(`bajutsu/evidence/intervals.py`)は、
固定の`_VIDEO_START_TIMEOUT = 5.0`という締め切りまでポーリングして`true_start`を確認します。これは
ハードコードされた定数であり、`bajutsu/platform_lifecycle/environments/xcuitest.py`にある4つの
兄弟タイムアウト(`BAJUTSU_XCUITEST_STARTUP_TIMEOUT`とその仲間たち)がすべて環境変数で上書き可能で、
負荷の高いmacOS CIランナーがローカルより明確に遅いという理由で`.github/workflows/ios-e2e.yml`側で
引き上げられているのとは対照的です。実際に、あるCI実行がまさにこの問題に遭遇しました。

```
recordVideo produced no new bytes in runs/20260806-073350/02-filter-narrows-the-catalog/scenario.mp4
within 5.0s; step/network timestamps stay uncorrected for this scenario's video
```

ポーリングがタイムアウトすると`true_start`は`None`のままとなり(設計どおり、
`_resolve_video_start_offset`(`bajutsu/orchestrator/loop.py`)は推測せずに`0.0`へフォールバック
します)、そのシナリオのレポートタイムスタンプは、BE-0346が防ぐはずだった素朴な
`step_start - run_start`というズレへと、静かに退行します。`_VIDEO_START_TIMEOUT`は、この一族の
タイムアウトの中で唯一、兄弟たちがすでに受けている「環境変数化とCI側の調整」という扱いを受けて
いないものです。

**2. `RunResult.video_anchor_s`は、プロセスが終了すると意図的に復元不能になります。** これは生の
`time.monotonic()`の値であり、`manifest_dict`(`bajutsu/report/manifest.py`)は、モノトニックな
値は別プロセスで読み返しても意味を持たないという理由で、これを永続化される`manifest.json`から
明示的に除外しています。その結果、シナリオ実行中に`StepOutcome.started_at`や`network.json`の
`startedAt`へ焼き込まれる相対オフセットが、生き残るタイミング関係の**唯一の**写しになります。
これがもし`None`の`true_start`から計算されていたら(上記の問題1)、オフセットの計算に誤った前提が
あったら、あるいはアンカーの解決方法を将来改善したいと思ったら——シナリオを再実行せずに、あとから
レポートのタイムスタンプを計算し直す方法はありません。正しい答えに必要な生のタイミングデータが、
そもそも保持されていないためです。

両者はどちらも同じ根に行き着きます。現行の設計は、実行中にプロセス内で一度だけ補正を計算し、その
補正から導かれた値だけを保持します。元になった生のデータは、決して残されません。

## 詳細設計

**Unit 1 — `Clock`には折り込まず、注入可能な壁時計アンカーを追加する。** `run_scenario`
(`bajutsu/orchestrator/loop.py`)はすでに`scenario_start = clock.now()`、つまり実行中のあらゆる
経過時間・タイムアウト判定に使われる`time.monotonic()`の値を記録しています。これはそのまま変更
しません。壁時計はNTP補正で後方に飛ぶことがあり、`wait`がタイムアウトしたかどうかを判定する処理が
それを使ってはいけないためです。これとは別に、独立した2つ目の記録として`scenario_wall_start`を
追加します。取得元は注入可能なコールバック(`WallClock = Callable[[], float]`、デフォルトは
`time.time`)とし、`Clock` Protocolを拡張するのではなく、`bajutsu/evidence/intervals.py`の`Spawn`・
`adb.RunFn`など、このコードベースが単一の関数を注入する際にすでに使っている慣習に倣います。
`Clock`を拡張すると、テストスイート全体に散らばる`FakeClock`・`TrackingClock`・`_LogicalClock`・
`_AdvancingClock`(おおよそ10数ファイル)のテストダブルすべてに、タイミング判定には不要なメソッドを
追加させることになってしまいます。以降、任意のmonotonicインスタント`t`は
`scenario_wall_start + (t - scenario_start)`で壁時計時刻に変換できます。これは純粋な派生値なので、
テストでは壁時計用のコールバックを固定した値にして、その値を直接検証できます。

**Unit 2 — `StepOutcome.started_at`を絶対エポック時刻にする。** `_StepRunner._run_one`
(`bajutsu/orchestrator/loop.py`)は現在、`outcome.started_at = max(0.0, (start - scenario_start) -
video_start_offset)`——記録時点で補正済みの相対オフセット——を計算しています。これを
`outcome.started_at = scenario_wall_start + (start - scenario_start)`に変更します。動画による補正は
まだ適用しない、ステップの絶対的な壁時計開始時刻です(補正の適用はUnit 5に移します)。

**Unit 3 — `RunResult.video_anchor_s`を絶対的なアンカーにし、manifestからの除外をやめる。**
`_resolve_video_start_offset`が持つ既存のmonotonic演算のロジックと、既存のフォールバック規則
(確認済みの`true_start`がなければ`0.0`、正のオフセットならログを出して`0.0`)は変更しません。この
ロジックはすでに実証済みであり、結果の記録方法を変えるためにロジック自体を変える必要はありません。
変わるのは`RunResult.video_anchor_s`が持つ値だけです。`scenario_start + video_start_offset`
(monotonicインスタント)ではなく、`scenario_wall_start + video_start_offset`(絶対的な壁時計
インスタント。`video_start_offset`は秒単位の単純な差分なので、どちらのエポックに足しても構いません)
になります。`manifest_dict`(`bajutsu/report/manifest.py`)は、今日これを除外している
`d.pop("video_anchor_s", None)`の行を取り除きます。永続化して読み返しても意味を持つ値になる——
これが本項目の要点です。

**Unit 4 — 通信ログのタイムスタンプも絶対インスタントとして保存する。** `bajutsu/runner/pipeline.py`の
`_write_network`は現在、`received - video_anchor_s - duration`という相対値を、書き込み時点で一度
計算して`network.json`に直接書き込んでいます。これを、`video_anchor_s`と同じアンカーペア変換を
使って求めた、その通信の絶対的な`startedAt`を書き込むように変更します。これにより`network.json`も、
Unit 3で`manifest.json`が得るのと同じ「生データとしての性質」を持つようになります。

**Unit 5 — 相対秒数への変換は、レポート描画時にのみ行う。** `bajutsu/report/rows.py`の
`_step_run_row`は現在、`out.started_at`をそのまま`data-t`シーク属性用の「動画からの相対秒数」として
読み取っています。これを、描画時に`data_t = out.started_at - run_result.video_anchor_s`を計算する
方式に変更します。パイプライン全体の中で、動画相対の数値を生成する箇所はここだけになり、
(アンカー解決方法を改善したときや、実行のはるか後に`manifest.json`を読み返したときに)その数値を
最初から計算し直す必要がある箇所も、ここだけになります。

**Unit 6 — `_VIDEO_START_TIMEOUT`を設定可能にする。** `bajutsu/evidence/intervals.py`に
`_VIDEO_START_TIMEOUT_ENV = "BAJUTSU_VIDEO_START_TIMEOUT"`と、解決用の関数
`_video_start_timeout() -> float`を追加します。`bajutsu/platform_lifecycle/environments/xcuitest.py`
の`_runner_startup_timeout()` / `_recovery_timeout()`と全く同じパターン(環境変数を優先、未設定または
非数値ならコンパイル時のデフォルトにフォールバック、`max(0.0, float(raw))`)に倣います。本番の2つの
呼び出し箇所(`start_video`、`start_screenrecord`)は、解決済みの値を明示的に渡します。関数の
デフォルト引数はimport時に束縛されるため、呼び出しごとに環境変数を反映できません——これは
`tests/test_intervals.py`が、モジュール定数へのパッチではなく明示的な引数で締め切りを操作している
理由でもあります。`.github/workflows/ios-e2e.yml`のワークフローレベル`env:`ブロックでこの値を
引き上げます。4つの兄弟タイムアウトがすでに受けているのと同じ扱いです。このUnitはUnit 1〜5とコードを
共有しませんが、それらが手を入れるのと全く同じ確認/アンカーの経路に触れるため、無関係な1行修正として
別出しにせず、本項目の範囲に含めます。

**Unit 7 — manifestスキーマとバイリンガルドキュメント。** `manifest.json`の`schemaVersion`を
引き上げます。`docs/reporting.md` / `docs/ja/reporting.md`には、`started_at` / `video_anchor_s` /
`startedAt`が絶対エポック時刻という新しい意味を持つこと、レポートの相対シークオフセットが
保存された値ではなく描画時の導出値になったことを記載します。`docs/evidence.md` /
`docs/ja/evidence.md`には、既存の`true_start`の節と並べて壁時計アンカーペアの仕組みと、新しい
`BAJUTSU_VIDEO_START_TIMEOUT`による上書きを記載します。

作業分解(*進捗*にも対応):

- Unit 1 — 壁時計アンカー(`scenario_wall_start`)。`WallClock`コールバック経由で注入する。
- Unit 2 — `StepOutcome.started_at`を絶対エポック時刻にする。
- Unit 3 — `RunResult.video_anchor_s`を絶対的なアンカーにし、`manifest_dict`からの除外をやめる。
- Unit 4 — 通信ログのタイムスタンプを絶対インスタントとして保存する。
- Unit 5 — `bajutsu/report/rows.py`が動画相対の`data-t`を描画時に計算するようにする。
- Unit 6 — `BAJUTSU_VIDEO_START_TIMEOUT`環境変数での上書きを追加し、
  `.github/workflows/ios-e2e.yml`で引き上げる。
- Unit 7 — manifestの`schemaVersion`を引き上げ、`docs/reporting.md` / `docs/evidence.md`を
  両言語で更新する。

## 検討した代替案

- **相対オフセット方式のまま、`video_anchor_s`をmanifestから除外するのだけをやめる案。** 見送り
  ました。`video_anchor_s`は依然として生の`time.monotonic()`の値であり、別プロセス(あるいは別の
  マシン)で読み返しても意味を持ちません。永続化しても何も変わりません。本当のギャップは、実行を
  越えて生き残る、プロセスをまたいでも意味のある生のタイミングデータがそもそも存在しないことに
  あり、manifestからの除外だけを直しても、そこには対処できません。
- **壁時計アンカーの代わりに、完成した録画から`ffprobe`などでフレーム単位のタイムコードを抽出する
  案。** BE-0346自身の*検討した代替案*が事後的な長さの推定を却下しているのと同じ理由で見送りました。
  動画の正確なタイミングは、`stop()`が録画を確定するまで確実にはわからず、それはすべてのステップの
  タイムスタンプがすでに必要になる、はるか後です。この方式では、一度だけ早い段階で解決して後に
  持ち越す値ではなく、各ステップの`started_at`を後から遡って書き直す必要が生じます。
- **変更されない`time.monotonic()`の経路の上に壁時計アンカーを重ねるのではなく、実行中の経過時間・
  タイムアウト判定に`time.time()`を直接使う案。** 見送りました。壁時計は実行中に後方へ調整される
  ことがあり(NTP補正、手動の時刻変更)、`wait`のタイムアウトやステップの`duration_s`を壊しかね
  ません。あらゆるタイミングの*判定*には`time.monotonic()`を使い続け、本項目が追加するシナリオ
  ごとに一度だけのアンカーにのみ`time.time()`を使うことで、このリスクをゼロに保てます。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [ ] Unit 1 — 壁時計アンカー(`scenario_wall_start`)。`WallClock`コールバック経由で注入する。
- [ ] Unit 2 — `StepOutcome.started_at`を絶対エポック時刻にする。
- [ ] Unit 3 — `RunResult.video_anchor_s`を絶対的なアンカーにし、`manifest_dict`からの除外を
      やめる。
- [ ] Unit 4 — 通信ログのタイムスタンプを絶対インスタントとして保存する。
- [ ] Unit 5 — `bajutsu/report/rows.py`が動画相対の`data-t`を描画時に計算するようにする。
- [ ] Unit 6 — `BAJUTSU_VIDEO_START_TIMEOUT`環境変数での上書きを追加し、
      `.github/workflows/ios-e2e.yml`で引き上げる。
- [ ] Unit 7 — manifestの`schemaVersion`を引き上げ、`docs/reporting.md` / `docs/evidence.md`を
      両言語で更新する。

## 参考

- [BE-0346 — ステップと通信ログのタイムスタンプを、動画の確認済み開始時刻に合わせる](../BE-0346-video-timing-sync/BE-0346-video-timing-sync-ja.md) —
  本項目が基盤とし、保存形式を改訂する対象となる確認/アンカーの仕組みです。置き換えるのではなく、
  その上に築きます。
- [`docs/evidence.md`](../../docs/evidence.md) — 本項目が壁時計アンカーと設定可能なタイムアウトで
  拡張する、確認済み開始時刻の節を持つ証跡サブシステムです。
- [`docs/reporting.md`](../../docs/reporting.md) — 本項目の絶対タイムスタンプが最終的に役立つ
  レポート機能です。シナリオのステップと通信ログが埋めるマニフェストのフィールドを記載しています。
