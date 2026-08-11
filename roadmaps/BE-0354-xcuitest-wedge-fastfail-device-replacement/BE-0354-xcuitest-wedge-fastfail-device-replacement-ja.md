[English](BE-0354-xcuitest-wedge-fastfail-device-replacement.md) · **日本語**

# BE-0354 — 応答しなくなった XCUITest セッションを速やかに見抜き、再発したクラッシュ再試行ではデバイスを置き換える

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0354](BE-0354-xcuitest-wedge-fastfail-device-replacement-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0354") |
| 実装 PR | [#1557](https://github.com/bajutsu-e2e/bajutsu/pull/1557) |
| トピック | Platform support |
| 関連 | [BE-0344](../BE-0344-xcuitest-device-recovery/BE-0344-xcuitest-device-recovery-ja.md), [BE-0353](../BE-0353-xcuitest-adb-crash-retry-device-recovery/BE-0353-xcuitest-adb-crash-retry-device-recovery-ja.md), [BE-0323](../BE-0323-xcuitest-readiness-crash-respawn/BE-0323-xcuitest-readiness-crash-respawn-ja.md), [BE-0319](../BE-0319-xcuitest-cold-spawn-resilience/BE-0319-xcuitest-cold-spawn-resilience-ja.md), [BE-0305](../BE-0305-driver-resilience-fault-injection/BE-0305-driver-resilience-fault-injection-ja.md) |
<!-- /BE-METADATA -->

## はじめに

Bajutsu の iOS バックエンドは、iOS シミュレータの中でループバックポートに HTTP を提供する常駐の
テスト runner（XCUITest プロセス）を通してシナリオを実行します。その障害からの復旧には、すでに
3 つの層が出荷されています。runner チャネルは、実行途中のクラッシュを health エンドポイントへの
ポーリングと冪等な呼び出しの再発行で乗り切ります
（[BE-0287](../BE-0287-xcuitest-runner-multitouch-resilience/BE-0287-xcuitest-runner-multitouch-resilience-ja.md)、
[BE-0323](../BE-0323-xcuitest-readiness-crash-respawn/BE-0323-xcuitest-readiness-crash-respawn-ja.md)）。
コールド起動の失敗は、試行のあいだにデバイスを修復します
（[BE-0344](../BE-0344-xcuitest-device-recovery/BE-0344-xcuitest-device-recovery-ja.md)）。そして
クラッシュを起点とするシナリオ再試行は、respawn の前にデバイスの消去を強制します
（[BE-0353](../BE-0353-xcuitest-adb-crash-retry-device-recovery/BE-0353-xcuitest-adb-crash-retry-device-recovery-ja.md)）。
2026 年 8 月に継続的インテグレーション（CI）フリート全体で観測された障害クラスは、この 3 層を
同時にすり抜けます。シミュレータの画面キャプチャ系サービスが固まる一方で runner 自体は健全に
見え、スクリーンショットの読み出しはすべてハングし、再試行が強制する消去はまったく同じ症状を
連れて戻ってきます。実際に働く 2 つの層（チャネル内の復旧と消去を強制する再試行）は、成功
しえない対処に自分の予算を使い切ります。デバイスのデータより多くを変えるただ 1 つの対処
（置き換え）には実行途中の経路から到達できないため、ジョブは 10 分以上を費やした末に、シナリオの
判定を 1 つも残さずに失敗します。

本項目は、この行き止まりを数秒で見抜き、再試行が実際に応答できるデバイスの上に着地するように
します。まず runner チャネルは、「health エンドポイントは答えるのに同じ冪等な呼び出しが
再発行のたびにタイムアウトする」状態を一過性のクラッシュと区別し、固まっていると証明でき
次第、復旧ループで吸収せずパイプラインへ引き渡すようにします。次に、チャネルの参照する生存プローブが
runner の出力キャプチャを読むようにし、すでに終了した XCTest の実行を、来るはずのない復旧を
待ってポーリングし続けないようにします。さらに、クラッシュを起点とするシナリオ再試行に、
[BE-0353](../BE-0353-xcuitest-adb-crash-retry-device-recovery/BE-0353-xcuitest-adb-crash-retry-device-recovery-ja.md)
の強制消去より上の段を足します。消去を試した後の再試行はシミュレータそのものを置き換え、その
置き換えには
[BE-0344](../BE-0344-xcuitest-device-recovery/BE-0344-xcuitest-device-recovery-ja.md) が消滅した
デバイスのために作った機構を再利用します。証跡レイヤーがすでに検出している画面キャプチャの停滞
（録画ファイルの成長確認のタイムアウト）は、消去では回復しないと観測された劣化クラスを特定する
ので、この置き換えの段を直接選ばせます。

## 動機

2026-08-06 から 2026-08-10 のあいだ、iOS の E2E レーンは、XCUITest のシナリオを実際に実行した
ワークフロー実行のほとんどで、少なくとも 6 つの無関係なブランチにまたがって失敗しました。同じ
期間に多数の green な実行があるのは、変更フィルタがシミュレータのジョブをスキップした
ドキュメントのみの変更だからです。失敗したジョブ（`golden`、`bundled-runner`、`visual`、
`actuation`、`fault-injection`）はどれも 1 つの署名を示しており、うち 1 件は完全なログが
残っています。2026-08-09 の、プルリクエスト
[#1538](https://github.com/bajutsu-e2e/bajutsu/pull/1538) の `bundled-runner (xcuitest)` ジョブ
です。最初に、シナリオの録画が開始確認に失敗しました。

```
recordVideo produced no new bytes in runs/20260809-233753/00-stable-catalog-smoke/scenario.mp4 within 20.0s; ...
```

その 15 秒後、runner チャネルは 3 分間続く連鎖に入りました。スクリーンショットの読み出しが
リトライ予算を超えてタイムアウトすると、クラッシュ復旧層は runner の health エンドポイントを
ポーリングします。health は答え、復旧層は読み出しを再発行し、その読み出しは再びタイムアウト
します。この繰り返しです。

```
runner channel GET /screenshot failed (attempt 1/3), retrying: timed out
runner channel GET /screenshot failed (attempt 2/3), retrying: timed out
runner channel GET /screenshot: the runner became unreachable past the retry budget — a mid-run crash: ...
runner channel GET /screenshot: the runner recovered from a mid-run crash; re-issuing the idempotent call (recovery 1/3)
runner channel GET /screenshot failed (attempt 1/3), retrying: timed out
```

再発行のループは、一度クラッシュして応答可能な状態で戻ってくる runner のために存在します
（[BE-0287](../BE-0287-xcuitest-runner-multitouch-resilience/BE-0287-xcuitest-runner-multitouch-resilience-ja.md)
はジェスチャの途中でまさにその形を観測しました）。しかしこの実行では、HTTP サーバーだけは健全な
runner の下でデバイスの画面キャプチャ系サービスが固まっていたため、読み出しに応答する見込みの
ないセッションに対して、タイムアウトのはしごと health 待ちからなる復旧サイクルを 3 周分
乗り切ろうとしました。2 つの場合を分ける信号は最初のサイクルから出ていました。「復旧した」はずの
runner が、同じ冪等な呼び出しの次の再発行で再びタイムアウトするなら、それは断続的な落ち方では
なく、固まっているのです。最初のタイムアウトから 3 分後、チャネルはようやく諦め、パイプラインの
クラッシュ復旧が引き継ぎました。

パイプラインの再試行は、
[BE-0353](../BE-0353-xcuitest-adb-crash-retry-device-recovery/BE-0353-xcuitest-adb-crash-retry-device-recovery-ja.md)
が出荷したとおりに動きました。リースを破棄し、デバイスの消去（`simctl shutdown`、`erase`、
`boot`、再インストール）を強制して respawn したのです。その 3 分後、2 回目の試行は 1 回目を
一字一句なぞりました。同じ録画の開始確認がタイムアウトし、同じスクリーンショットの連鎖が走り、
同じクラッシュで終わりました。シナリオごとの復旧予算が尽き、ジョブは失敗しました。

```
scenario stable catalog smoke: backend crashed mid-run (attempt 2/3): runner channel GET /screenshot failed: the runner crashed mid-run and did not recover within 60s
##[error]backend crashed mid-run and did not recover within the 300s crash-recovery budget (spent respawning across 2 attempt(s)): ...
```

消去の段は
[BE-0353](../BE-0353-xcuitest-adb-crash-retry-device-recovery/BE-0353-xcuitest-adb-crash-retry-device-recovery-ja.md)
が意図して選んだ最初の一手であり、クラッシュ再試行のもっとも強い対処として出荷されました。
上のログが示すのは、その対処の届かない劣化クラスです。消去はデバイスのデータをリセットしますが、
この固まり方はデバイスのキャプチャ系サービスに宿っており、消去されたデバイスは固まったまま
戻ってきました。デバイスのデータより多くを変える出荷済みの対処は
[BE-0344](../BE-0344-xcuitest-device-recovery/BE-0344-xcuitest-device-recovery-ja.md) の
置き換えの段（新しく作ったシミュレータ）だけですが、その段に到達できるのはコールド起動が失敗した
とき、しかもそのうちデバイスが `simctl` の一覧から丸ごと消えていたときに限られます。respawn が
health に答える形で立ち上がってしまう実行途中の固まり方は、どちらの条件も満たしません。

もう 1 つ、この浪費を削るはずだった出荷済みの防御が働きませんでした。
[BE-0323](../BE-0323-xcuitest-readiness-crash-respawn/BE-0323-xcuitest-readiness-crash-respawn-ja.md)
は、来るはずのない復旧を待ち続けないように、runner プロセスが消えたらチャネルの復旧を即座に
失敗させました。しかしそのプローブが読むのは `xcodebuild` のプロセスハンドルだけです。
[BE-0305](../BE-0305-driver-resilience-fault-injection/BE-0305-driver-resilience-fault-injection-ja.md)
の障害注入の計測は、この盲点をすでに `main` 上に記録しています。kill されたテストホストは
「プロセス終了」の速い経路に乗らない（プローブが読む `xcodebuild` の親プロセスがホストより長生き
する）という観測で、「より厳密な生存チェック」を別項目として先送りしました。本項目がその別項目
です。この系統のフレークの以前の調査（失敗した CI ログ 4 件、4 件とも同型）では、クラッシュ後に XCTest が
シミュレータ内のテストホストを再起動し、テストを 0 件だけ再実行する様子が見つかっています。
実行は終わっていてポートが再び bind されることはないのに `xcodebuild` は生き続けるため、
プローブは「生きている」と答え続け、復旧のたびに 60 秒の待ち時間を満額支払います。キャプチャ
された runner の出力はこの状態をそのまま印字しており（コールド起動のゲートがすでに文字列照合
している `Test Suite 'All tests'` の行）、実行途中の経路だけが、その出力を読んでいません。

## 詳細設計

4 つのユニットに分かれます。ユニット 1 と 2 は runner チャネルの継ぎ目における独立した検出の
修正、ユニット 3 は再試行に欠けているエスカレーションの段の追加、ユニット 4 は証跡レイヤーに
すでにある停滞シグナルをユニット 3 の段の選択へつなぐ配線です。どのユニットも判定には触れません。
すべて**インフラの復旧**の経路と所要時間を変えるだけで、失敗し続けるシナリオは変わらず大きな声で
失敗します（[BE-0049](../BE-0049-determinism-flakiness-audit/BE-0049-determinism-flakiness-audit-ja.md)
の「フレークを吸収して許容することはない」という姿勢は不変です）。

1. **固まった automation セッションを runner チャネルで分類する。**
   `bajutsu/drivers/xcuitest.py` の `_with_crash_recovery` は現在、health の復旧を確認したら
   冪等な呼び出しを再発行し、連続クラッシュ `_MAX_CRASH_RECOVERIES` 回まで繰り返します。この
   ループ自体は本来の対象のために残し、1 つの場合だけを切り出します。health が答え続けるのに、
   同じ読み出しが**再発行のたびにタイムアウトし続ける**場合です。ここでのタイムアウトは
   「runner に届いたうえでハングした呼び出し」を意味します。この形はチャネルが独自のタグで区別
   しなければなりません。既存の `delivered` タグ
   （[BE-0207](../BE-0207-xcuitest-channel-transient-retry/BE-0207-xcuitest-channel-transient-retry-ja.md)）
   は応答途中の connection reset にも同じ印を付けるため、固まりを特定できるのはハングの形だけ
   だからです。もう 1 つ、健全な状態がしばらくのあいだ同じ表面を見せます。
   [BE-0323](../BE-0323-xcuitest-readiness-crash-respawn/BE-0323-xcuitest-readiness-crash-respawn-ja.md)
   は runner の XCUITest 操作を直列化し、`/health` は意図的にその直列化を素通りする
   （「runner は忙しいだけで、死んでいない」）ので、長い操作がロックを握るあいだ、並行する
   読み出しはタイムアウトします。2 つの状態は、同じ読み出しの **2 回目**の復旧後タイムアウトで
   分かれます。ロックを握る操作は自分自身の呼び出しのリトライのはしごに先に失敗するため、単一の
   操作はこの窓をまたげません。そこまでハングし続けた読み出しは固まっています。runner は同じ
   読み出しを三度受け取って一度も答えなかったのですから、チャネルはその時点で「固まった
   automation セッション」を示す固有の診断文とともにクラッシュエラーを送出し、残りの復旧サイクル
   と最後の health 待ちを乗り切ろうとはしません。復旧確認の後の接続レベルの失敗
   （connection refused、応答途中の reset）は今日の挙動を保ちます。その形こそ、ループが乗り切る
   べき本物のクラッシュの繰り返しだからです。固まったセッションを救える対処はパイプラインの
   デバイスレベルの再試行だけなので、チャネルの仕事は吸収ではなく速やかな引き渡しです。

2. **生存プローブに出力キャプチャを読ませる。** 環境はすでに、チャネルからの「runner プロセスは
   生きているか」という問い（[BE-0323](../BE-0323-xcuitest-readiness-crash-respawn/BE-0323-xcuitest-readiness-crash-respawn-ja.md)
   が足した継ぎ目 `_runner_alive`）に答えており、コールド起動の経路では runner の出力キャプチャを
   実行終了マーカー（`_RUN_ENDED_MARKERS`、`Test Suite 'All tests' failed` / `passed` の行）で
   走査してもいます（`_run_ended_probe`）。この 2 つを実行途中の経路でも合成します。プロセスが
   終了しているか、**または**キャプチャが一度でもテスト実行の終了を示したなら、プローブは runner を
   死んだものとして報告します。実行終了の側は**ラッチ**します。2 つの側は発火の仕方が違うから
   です。`_run_ended_probe` はエッジトリガで、内部のオフセットを進めながら、マーカーを最初に含んだ
   読み取り窓からしか理由を返しません。一方、実行途中の生存プローブはレベルトリガで、復旧の
   たびに問い直されます。ラッチしない合成では「死んだ」と一度だけ答え、以後のすべての復旧で
   「生きている」に戻ってしまいます。ラッチしたプローブはスポーンごとに 1 つで、コールドゲートと
   実行途中の生存プローブが共有します。コールド起動側のインスタンスがすでに同じキャプチャを消費
   しており、独立した 2 つ目のインスタンスはマーカーを取り合うからです。スイート終了の行を印字
   した実行がその後に何かを提供することは
   ありません（先の 4 件の調査で `xcodebuild` がこの状態を生き延びていました）。これで復旧の
   health 待ちは、60 秒の上限ではなくプローブの次の読み取りで失敗します。マーカーは
   `xcodebuild` 自身のロケール非依存の出力で、コールドゲートは
   [BE-0319](../BE-0319-xcuitest-cold-spawn-resilience/BE-0319-xcuitest-cold-spawn-resilience-ja.md)
   以来これを文字列照合してきました。将来の Xcode が文言を変えた場合、プローブは今日の満額の
   待ちに退行するだけで、誤って「死んだ」と報告する側には倒れません。

3. **2 度目のクラッシュ再試行をデバイスの置き換えへエスカレーションする。**
   [BE-0353](../BE-0353-xcuitest-adb-crash-retry-device-recovery/BE-0353-xcuitest-adb-crash-retry-device-recovery-ja.md)
   の強制消去は最初の段のまま残します。より安価な対処で、アプリデータの破損クラスを回復できる
   からです。しかし強制消去つきの再試行自体がまたバックエンドクラッシュで終わったら、次のリースは
   エスカレーションします。run パイプライン（`bajutsu/runner/pipeline.py` の `run_one` の再試行
   ループ）がリースにデバイスの置き換えを要求し、XCUITest 環境はそれを、
   [BE-0344](../BE-0344-xcuitest-device-recovery/BE-0344-xcuitest-device-recovery-ja.md) の
   消滅デバイスの段と同じ「作成、起動、準備」の経路で満たします。デバイスタイプとランタイムの
   複製、`bajutsu-recovered-*` の命名、リースを新デバイスへ追随させるプールの貼り替えも、その
   ままです。劣化したデバイスはシャットダウンし、二度とプールへ返しません。消滅したデバイスが
   今日隔離されるのと同じ形の隔離です。置き換えのリースは、その試行に限り強制消去を抑止します。
   作りたてのデバイスに消すものはなく、そこで `erase=True` を律儀に実行すると、状態が何も変わら
   ないのにシャットダウンと起動をもう 1 周支払うことになるからです。置き換えの要求は、置き換えが
   誤りか不可能な経路では no-op になり、そうした経路はそれぞれ今日持っているもっとも強い再試行を
   保ちます。実機と live の WebDriver エンドポイントは素の respawn を保ちます。どちらも `erase`
   precondition 自体を拒否する経路で、だからこそ `erase_precondition_supported` がすでに消去の段
   から除外しています。具体的なシミュレータにピン留めした run（`--udid`、または config でピン留め
   したデバイス）は消去レベルの再試行を保ちます。置き換えは、オペレーターが名指ししたデバイスから
   run を黙って降ろし、しかも
   [BE-0344](../BE-0344-xcuitest-device-recovery/BE-0344-xcuitest-device-recovery-ja.md) がまさに
   ピン留めの場合について記録した、run ごとに増える `bajutsu-recovered-*` の残留物を生むからです。
   他のすべてのプラットフォームはこの要求を無視するので、ツールと runner はアプリにも
   プラットフォームにも依存しないままです。既存の上限も変わりません。置き換えは、置き換え前の
   respawn と同じ `crash_retries` の試行回数、同じシナリオごとと run ごとの復旧予算を消費します。
   この上限は到達条件も定めます。デフォルトの `crash_retries = 1` では 3 回目の試行が存在しない
   ため、「2 度目のクラッシュ」のトリガーが働くのは回数を引き上げたレーン（iOS レーンは
   `BAJUTSU_CRASH_RETRIES: 2` を設定）だけで、デフォルトの run はユニット 4 の停滞シグナル経由
   でのみこの段に到達します。デフォルト自体は意図して据え置きます。シナリオ全体の再実行をもう
   1 回支払う選択は、従量課金のオンデバイスレーンが自分で引き受けたコストであり、新しい
   デフォルトにする理由はないからです。この段は `docs/architecture.md` と `docs/run-loop.md` が
   記述する挙動（クラッシュ再試行のもっとも強い対処）を変えるので、両ページとその `docs/ja/`
   ミラーを、BE-0113 の規範に従って同じ変更の中で更新します。

4. **録画開始の停滞に置き換えの段を選ばせる。** 証跡レイヤーは、この固まり方の最初の症状を
   すでに検出しています。`start_video` は録画ファイルが `_VIDEO_START_TIMEOUT` の上限以内に成長
   することを確認し（[BE-0348](../BE-0348-absolute-timestamp-recording/BE-0348-absolute-timestamp-recording-ja.md)
   がこの上限を `BAJUTSU_VIDEO_START_TIMEOUT` として調整可能にする提案です）、成長しなければ警告を
   ログに残します。この結果をリースの上に
   浮かび上がらせます。録画開始の確認でタイムアウトしたシナリオ試行がその後バックエンドクラッシュで
   終わったら、次の再試行は強制消去を飛ばしてユニット 3 の置き換えの段へ直接エスカレーション
   します。この停滞は、消去では回復しないと観測されたキャプチャ系の劣化クラスを特定するうえ、
   2 つの対処の所要時間はもともと同程度です（消去はシャットダウン、消去、起動、再インストール。
   置き換えは作成、起動、インストール）。したがって停滞が偽陽性だった場合（遅いだけで健全な
   エンコーダ）でも、支払うのは飛ばした消去とほぼ同じ額です。このシグナルはあくまで**復旧の段の
   選択**に対する助言にとどまります。それ自体が試行を失敗させることはなく、動画証跡を無効にした
   run では単に発生しません。

## 検討した代替案

- **停滞をシグナルとして読む代わりに、再試行では動画証跡を省く。** 再試行から `recordVideo` を
  外せば劣化したデバイスの負荷は 1 つ減りますが、スクリーンショットの経路が同じキャプチャ系
  サービスに乗っている以上、再試行は観測されたとおりにハングします。しかも再試行の動画は、その
  再試行まで失敗したときに人間が必要とするまさにその証跡です。ユニット 4 は、証跡を犠牲にせず
  同じ情報を停滞から取り出します。
- **出力キャプチャを読む（ユニット 2）代わりに、runner の health 応答にセッションごとの nonce を
  入れる。** 再起動したテストホストが再び応答を始めた場合、新しい nonce が返るので、「復旧した」と
  「別物に入れ替わった」をきれいに区別できます。しかし
  [BE-0344](../BE-0344-xcuitest-device-recovery/BE-0344-xcuitest-device-recovery-ja.md) が Swift 側の
  起動リトライを退けたのと同じ理由で退けます。修正が Swift パッケージ側に入るため、どのレーンも
  rebuild した runner を配らないと効果が出ない一方、出力キャプチャを読むプローブはロジックコアに
  入り、すべてのレーンへ一度に届きます。さらに nonce は、計測された故障モードをそもそも
  カバーできません。テストを 0 件しか再実行しないホストは、比較すべき nonce を一度も返さない
  からです。
- **最初のクラッシュ再試行からデバイスを置き換え、消去の段を飛ばす。** ユニット 4 が特定する
  固まり方のクラスには、置き換えへ直行する価値があります。ただしそれは停滞シグナルがあるとき
  だけです。無条件に最初の再試行で置き換えると、一過性のクラッシュのたびに `bajutsu-recovered-*`
  デバイスが 1 台生まれ、開発者の Mac の上では蓄積していきます
  （[BE-0344](../BE-0344-xcuitest-device-recovery/BE-0344-xcuitest-device-recovery-ja.md) 自身の
  代替案がこの残留物を天秤にかけています）。消去の段は残留物なしにアプリデータの破損クラスを
  回復できるので、停滞が観測されていないあいだは最初の段のままにします。
- **復旧の段として CoreSimulator デーモンを再起動する。** この系統のフレークの以前の調査で計測済み
  です。デーモンの再起動は速く（約 1 秒）非破壊的ですが、調べた 9 件の CI 失敗ログのどれとも
  一致せず、upstream の runner-images にもこの再起動を勧める報告はありません。採用しません。
  唯一擁護できる置き場所は `simctl shutdown` がハングしたときのエスカレーションで、それは本項目
  より狭い後続課題です。
- **予算と上限をさらに引き上げる。** レーンはすでにジョブのタイムアウトを 60 分へ引き上げて
  おり、完全なログが残った 1 件は録画開始の上限をすでに 20 秒へ引き上げた状態で走っていました。
  この引き上げはプルリクエスト [#1538](https://github.com/bajutsu-e2e/bajutsu/pull/1538) の
  レーン設定で、
  [BE-0348](../BE-0348-absolute-timestamp-recording/BE-0348-absolute-timestamp-recording-ja.md)
  が提案する override を通じたものです。その 1 件は、時間がどこへ消えるかを示しています。行き止まりを証明する復旧層の中です。予算を増やしても買えるのは沈黙の延長だけで、
  [BE-0344](../BE-0344-xcuitest-device-recovery/BE-0344-xcuitest-device-recovery-ja.md) も
  コールド起動の再試行で同じ結論を計測しています（「より大きな予算が買ったのは、より長い沈黙
  だった」）。
- **同じ変更で置き換えの段を Android エミュレータへも広げる。** adb バックエンドには、この
  固まり方をする runner チャネルがそもそもなく、
  [BE-0353](../BE-0353-xcuitest-adb-crash-retry-device-recovery/BE-0353-xcuitest-adb-crash-retry-device-recovery-ja.md)
  がエミュレータプロセスの再起動を別の後続課題としてすでに名指ししています。本項目でも変え
  ません。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [x] ユニット 1 — タイムアウト（ハング）した呼び出しを接続レベルの失敗と別のタグで区別し、
      同じ読み出しの 2 回目の復旧後タイムアウトを固まったセッションとして分類して、固有の
      診断文とともにパイプラインへ引き渡す。
- [x] ユニット 2 — 実行終了マーカーのキャプチャプローブを実行途中の生存プローブへ合成し
      （ラッチし、スポーンごとに 1 つのインスタンスをコールドゲートと共有する）、終了済みの
      テスト実行が復旧の health 待ちを速やかに失敗させるようにする。
- [x] ユニット 3 — `run_one` のクラッシュ再試行に置き換えのエスカレーションを追加する。消滅
      デバイスの段の作成、命名、プールの貼り替えを再利用し、置き換えの試行では強制消去を抑止し、
      置き換えたデバイスを隔離する。erase を拒否する経路は素の respawn を、ピン留めした
      シミュレータは消去の再試行を保つ。`docs/architecture.md` と `docs/run-loop.md`
      （および `docs/ja/` ミラー）のクラッシュ復旧の記述を同じ変更で更新する。
- [x] ユニット 4 — 録画開始確認のタイムアウトをリースの上に浮かび上がらせ、その試行自身の
      クラッシュ再試行で置き換えの段を選ばせる。

## 参考

- [BE-0344 — XCUITest のコールド起動の再試行のあいだにシミュレータを修復する](../BE-0344-xcuitest-device-recovery/BE-0344-xcuitest-device-recovery-ja.md) — ユニット 3 が再利用する置き換えの機構とプールの貼り替え。
- [BE-0353 — バックエンドクラッシュの再試行でデバイス復旧を強制し、run 全体のクラッシュ復旧時間に上限を設ける](../BE-0353-xcuitest-adb-crash-retry-device-recovery/BE-0353-xcuitest-adb-crash-retry-device-recovery-ja.md) — 本項目がその上へエスカレーションする消去の段。
- [BE-0305 — ドライバ耐障害経路への実機障害注入カバレッジ](../BE-0305-driver-resilience-fault-injection/BE-0305-driver-resilience-fault-injection-ja.md) — ユニット 2 の盲点を記録し、より厳密な生存チェックを先送りした計測。その障害注入レーンは、ユニット 1 と 2 をオンデバイスで検証できる既存のハーネスでもあります。
- [BE-0323 — readiness ゲート中の runner クラッシュから XCUITest のコールド起動を回復する](../BE-0323-xcuitest-readiness-crash-respawn/BE-0323-xcuitest-readiness-crash-respawn-ja.md) — ユニット 2 が拡張する生存プローブの継ぎ目。
- [BE-0319 — XCUITest のコールド runner 起動を診断可能で自己修復的にする](../BE-0319-xcuitest-cold-spawn-resilience/BE-0319-xcuitest-cold-spawn-resilience-ja.md) — ユニット 2 が読む、デフォルトで有効な runner 出力のキャプチャ。
- [BE-0287 — 多点タッチ操作下での XCUITest runner チャネルの耐障害性](../BE-0287-xcuitest-runner-multitouch-resilience/BE-0287-xcuitest-runner-multitouch-resilience-ja.md) — ユニット 1 が固まった場合を切り出す、チャネル内の復旧ループ。
- [BE-0207 — XCUITest ランナーチャネルを一過性のタイムアウトに強くする](../BE-0207-xcuitest-channel-transient-retry/BE-0207-xcuitest-channel-transient-retry-ja.md) — ユニット 1 が手掛かりとする `delivered` タグ。
- [BE-0348 — 動画・ステップ・通信ログのタイムスタンプを絶対時刻(壁時計)で記録する](../BE-0348-absolute-timestamp-recording/BE-0348-absolute-timestamp-recording-ja.md) — ユニット 4 が読む、調整可能な録画開始確認。
- [BE-0049 — 決定性／フレーキネス監査](../BE-0049-determinism-flakiness-audit/BE-0049-determinism-flakiness-audit-ja.md) — すべてのユニットが守る「吸収しない」姿勢。
- 2026-08-09 の、プルリクエスト [#1538](https://github.com/bajutsu-e2e/bajutsu/pull/1538) の `bundled-runner (xcuitest)` ジョブ（[run 31241662509](https://github.com/bajutsu-e2e/bajutsu/actions/runs/31241662509)）— *動機* で引用した、完全なログが残った 1 件。
- `bajutsu/drivers/xcuitest.py` — チャネルの復旧ループ（ユニット 1）と生存プローブの継ぎ目（ユニット 2）。
- `bajutsu/platform_lifecycle/environments/xcuitest.py` — 実行終了のキャプチャプローブと置き換えの段。
- `bajutsu/runner/pipeline.py`、`bajutsu/runner/pool.py` — クラッシュ再試行のループと、置き換えに追随するデバイスごとの状態。
- `bajutsu/evidence/intervals.py`、`bajutsu/evidence/core.py` — ユニット 4 が浮かび上がらせる録画開始の確認。
