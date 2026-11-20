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

Bajutsu の iOS バックエンドは、iOS シミュレータの中でループバックポートに HTTP を提供する常駐のテスト runner（XCUITest プロセス）を通して、シナリオを実行します。その障害からの復旧には、すでに 3 つの層が出荷されています。

- runner チャネルは、実行途中のクラッシュを乗り切ります。health エンドポイントをポーリングし、冪等な呼び出しを再発行します（[BE-0287](../BE-0287-xcuitest-runner-multitouch-resilience/BE-0287-xcuitest-runner-multitouch-resilience-ja.md)、[BE-0323](../BE-0323-xcuitest-readiness-crash-respawn/BE-0323-xcuitest-readiness-crash-respawn-ja.md)）。
- コールド起動の失敗は、試行のあいだにデバイスを修復します（[BE-0344](../BE-0344-xcuitest-device-recovery/BE-0344-xcuitest-device-recovery-ja.md)）。
- クラッシュを起点とするシナリオ再試行は、respawn の前にデバイスの消去を強制します（[BE-0353](../BE-0353-xcuitest-adb-crash-retry-device-recovery/BE-0353-xcuitest-adb-crash-retry-device-recovery-ja.md)）。

2026 年 8 月に継続的インテグレーション（CI）フリート全体で観測された障害クラスは、この 3 層を同時にすり抜けます。シミュレータの画面キャプチャ系サービスが固まる一方で、runner 自体は健全に見えます。スクリーンショットの読み出しはすべてハングし、再試行が強制する消去は、まったく同じ症状を連れて戻ってきます。

実際に働く 2 つの層、すなわちチャネル内の復旧と消去を強制する再試行は、成功しえない対処に自分の予算を使い切ります。デバイスのデータより多くを変えるただ 1 つの対処、つまり置き換えには、実行途中の経路から到達できません。その結果ジョブは、10 分以上を費やしたうえで、シナリオの判定を 1 つも残さずに失敗します。

本項目は、この行き止まりを数秒で見抜き、再試行が実際に応答できるデバイスの上に着地するようにします。変更は 4 つです。

- runner チャネルが、固まった automation セッションを、復旧ループが想定する一過性のクラッシュと区別できるようにします。固まったセッションでは、health エンドポイントは答えるのに、同じ冪等な呼び出しが再発行のたびにタイムアウトします。固まっていると証明できた時点で、チャネルはパイプラインへ引き渡します。
- チャネルが参照する生存プローブに、runner の出力キャプチャを読ませます。すでに終了した XCTest の実行を、来るはずのない復旧を待ってポーリングし続けることがなくなります。
- クラッシュを起点とするシナリオ再試行に、[BE-0353](../BE-0353-xcuitest-adb-crash-retry-device-recovery/BE-0353-xcuitest-adb-crash-retry-device-recovery-ja.md) の強制消去より上の段を足します。消去を試した後の再試行は、シミュレータそのものを置き換えます。置き換えには、[BE-0344](../BE-0344-xcuitest-device-recovery/BE-0344-xcuitest-device-recovery-ja.md) が消滅したデバイスのために作った機構を再利用します。
- 証跡レイヤーがすでに検出している画面キャプチャの停滞（録画ファイルの成長確認のタイムアウト）が、この置き換えの段を直接選ぶようにします。この停滞は、消去では回復しないと観測された劣化クラスを特定するからです。

## 動機

2026-08-06 から 2026-08-10 のあいだ、iOS の E2E レーンは、XCUITest のシナリオを実際に実行したワークフロー実行のほとんどで、少なくとも 6 つの無関係なブランチにまたがって失敗しました。同じ期間に多数の green な実行があるのは、変更フィルタがシミュレータのジョブをスキップした、ドキュメントのみの変更だからです。失敗したジョブ（`golden`、`bundled-runner`、`visual`、`actuation`、`fault-injection`）は、どれも 1 つの署名を示します。

うち 1 件は、完全なログが残っています。2026-08-09 の、プルリクエスト [#1538](https://github.com/bajutsu-e2e/bajutsu/pull/1538) の `bundled-runner (xcuitest)` ジョブです。最初に、シナリオの録画が開始確認に失敗しました。

```
recordVideo produced no new bytes in runs/20260809-233753/00-stable-catalog-smoke/scenario.mp4 within 20.0s; ...
```

その 15 秒後、runner チャネルは 3 分間続く連鎖に入りました。

1. スクリーンショットの読み出しが、リトライ予算を超えてタイムアウトします。
2. クラッシュ復旧層が、runner の health エンドポイントをポーリングします。
3. health は答えます。
4. 復旧層が、読み出しを再発行します。
5. その読み出しが、再びタイムアウトします。

```
runner channel GET /screenshot failed (attempt 1/3), retrying: timed out
runner channel GET /screenshot failed (attempt 2/3), retrying: timed out
runner channel GET /screenshot: the runner became unreachable past the retry budget — a mid-run crash: ...
runner channel GET /screenshot: the runner recovered from a mid-run crash; re-issuing the idempotent call (recovery 1/3)
runner channel GET /screenshot failed (attempt 1/3), retrying: timed out
```

再発行のループは、一度クラッシュして応答可能な状態で戻ってくる runner のために存在します。[BE-0287](../BE-0287-xcuitest-runner-multitouch-resilience/BE-0287-xcuitest-runner-multitouch-resilience-ja.md) は、ジェスチャの途中でその形を観測しました。しかしこの実行では、HTTP サーバーだけは健全な runner の下で、デバイスの画面キャプチャ系サービスが固まっていました。ループは、読み出しに応答する見込みのないセッションに対して、タイムアウトのはしごと health 待ちからなる復旧サイクルを 3 周分、乗り切ろうとしました。

2 つの場合を分ける信号は、最初のサイクルから出ていました。「復旧した」はずの runner が、同じ冪等な呼び出しの次の再発行で再びタイムアウトするなら、それは断続的な落ち方ではなく、固まっています。最初のタイムアウトから 3 分後、チャネルはようやく諦め、パイプラインのクラッシュ復旧が引き継ぎました。

パイプラインの再試行は、[BE-0353](../BE-0353-xcuitest-adb-crash-retry-device-recovery/BE-0353-xcuitest-adb-crash-retry-device-recovery-ja.md) が出荷したとおりに動きました。リースを破棄し、デバイスの消去（`simctl shutdown`、`erase`、`boot`、再インストール）を強制して respawn したのです。その 3 分後、2 回目の試行は 1 回目を一字一句なぞりました。同じ録画の開始確認がタイムアウトし、同じスクリーンショットの連鎖が走り、同じクラッシュで終わりました。シナリオごとの復旧予算が尽き、ジョブは失敗しました。

```
scenario stable catalog smoke: backend crashed mid-run (attempt 2/3): runner channel GET /screenshot failed: the runner crashed mid-run and did not recover within 60s
##[error]backend crashed mid-run and did not recover within the 300s crash-recovery budget (spent respawning across 2 attempt(s)): ...
```

消去の段は、[BE-0353](../BE-0353-xcuitest-adb-crash-retry-device-recovery/BE-0353-xcuitest-adb-crash-retry-device-recovery-ja.md) が意図して選んだ最初の一手であり、クラッシュ再試行のもっとも強い対処として出荷されました。上のログが示すのは、この対処の届かない劣化クラスです。消去はデバイスのデータをリセットします。しかしこの固まり方はデバイスのキャプチャ系サービスに宿っており、消去されたデバイスは固まったまま戻ってきました。

デバイスのデータより多くを変える出荷済みの対処は、[BE-0344](../BE-0344-xcuitest-device-recovery/BE-0344-xcuitest-device-recovery-ja.md) の置き換えの段（新しく作ったシミュレータ）だけです。この段に到達できるのは、コールド起動が失敗したときに限られます。しかもそのうち、デバイスが `simctl` の一覧から丸ごと消えていたときだけです。respawn が health に答える形で立ち上がってしまう実行途中の固まり方は、どちらの条件も満たしません。

もう 1 つ、この浪費を削るはずだった出荷済みの防御が、働きませんでした。[BE-0323](../BE-0323-xcuitest-readiness-crash-respawn/BE-0323-xcuitest-readiness-crash-respawn-ja.md) は、来るはずのない復旧を待ち続けないように、runner プロセスが消えたらチャネルの復旧を即座に失敗させました。しかしそのプローブが読むのは、`xcodebuild` のプロセスハンドルだけです。

[BE-0305](../BE-0305-driver-resilience-fault-injection/BE-0305-driver-resilience-fault-injection-ja.md) の障害注入の計測は、この盲点をすでに `main` 上に記録しています。kill されたテストホストは、「プロセス終了」の速い経路に乗りません。プローブが読む `xcodebuild` の親プロセスが、ホストより長生きするからです。この計測は「より厳密な生存チェック」を別項目として先送りしました。本項目がその別項目です。

この系統のフレークの以前の調査では、失敗した CI ログ 4 件を調べ、4 件とも同型でした。クラッシュ後に XCTest がシミュレータ内のテストホストを再起動し、テストを 0 件だけ再実行していたのです。実行は終わっていてポートが再び bind されることはないのに、`xcodebuild` は生き続けます。プローブは「生きている」と答え続け、復旧のたびに 60 秒の待ち時間を満額支払います。

キャプチャされた runner の出力は、この状態をそのまま印字しています。コールド起動のゲートがすでに文字列照合している `Test Suite 'All tests'` の行です。実行途中の経路だけが、その出力を読んでいません。

## 詳細設計

設計は 4 つのユニットからなります。

- ユニット 1 と 2 は、runner チャネルの継ぎ目における、独立した検出の修正です。
- ユニット 3 は、再試行に欠けているエスカレーションの段を追加します。
- ユニット 4 は、証跡レイヤーにすでにある停滞シグナルを、ユニット 3 の段の選択へつなぎます。

どのユニットも判定には触れません。すべて**インフラの復旧**の経路と所要時間を変えるだけで、失敗し続けるシナリオは、変わらず大きな声で失敗します。[BE-0049](../BE-0049-determinism-flakiness-audit/BE-0049-determinism-flakiness-audit-ja.md) の「フレークを吸収して許容することはない」という姿勢は、不変です。

1. **固まった automation セッションを runner チャネルで分類する。**
   `bajutsu/drivers/xcuitest.py` の `_with_crash_recovery` は現在、health の復旧を確認したら冪等な呼び出しを再発行し、連続クラッシュ `_MAX_CRASH_RECOVERIES` 回まで繰り返します。このループ自体は本来の対象のために残し、1 つの場合だけを切り出します。health が答え続けるのに、同じ読み出しが**再発行のたびにタイムアウトし続ける**場合です。

   ここでのタイムアウトは、「runner に届いたうえでハングした呼び出し」を意味します。チャネルは、この形を独自のタグで区別しなければなりません。既存の `delivered` タグ（[BE-0207](../BE-0207-xcuitest-channel-transient-retry/BE-0207-xcuitest-channel-transient-retry-ja.md)）は、応答途中の connection reset にも同じ印を付けるため、固まりを特定できるのはハングの形だけだからです。

   健全な状態の 1 つが、しばらくのあいだ、ハングと同じ表面を見せます。[BE-0323](../BE-0323-xcuitest-readiness-crash-respawn/BE-0323-xcuitest-readiness-crash-respawn-ja.md) は runner の XCUITest 操作を直列化し、`/health` は意図的にその直列化を素通りします（「runner は忙しいだけで、死んでいない」）。そのため、長い操作がロックを握るあいだ、並行する読み出しはタイムアウトします。

   2 つの状態は、同じ読み出しの **2 回目**の復旧後タイムアウトで分かれます。ロックを握る操作は、自分自身の呼び出しのリトライのはしごに先に失敗するため、単一の操作はこの窓をまたげません。そこまでハングし続けた読み出しは、固まっています。runner は同じ読み出しを三度受け取って、一度も答えなかったのです。チャネルはその時点で、「固まった automation セッション」を示す固有の診断文とともに、クラッシュエラーを送出します。残りの復旧サイクルと最後の health 待ちを、乗り切ろうとはしません。

   復旧確認の後の接続レベルの失敗（connection refused、応答途中の reset）は、今日の挙動を保ちます。この形こそ、ループが乗り切るべき本物のクラッシュの繰り返しだからです。固まったセッションを救える対処は、パイプラインのデバイスレベルの再試行だけです。チャネルの仕事は、吸収ではなく速やかな引き渡しです。

2. **生存プローブに出力キャプチャを読ませる。** 環境はすでに、チャネルからの「runner プロセスは生きているか」という問いに、`_runner_alive` で答えています。[BE-0323](../BE-0323-xcuitest-readiness-crash-respawn/BE-0323-xcuitest-readiness-crash-respawn-ja.md) が足した継ぎ目です。コールド起動の経路では、環境が runner の出力キャプチャを実行終了マーカー（`_RUN_ENDED_MARKERS`、`Test Suite 'All tests' failed` / `passed` の行）で走査してもいます（`_run_ended_probe`）。

   このユニットは、この 2 つを実行途中の経路で合成します。プロセスが終了しているか、**または**キャプチャが一度でもテスト実行の終了を示したなら、プローブは runner を死んだものとして報告します。

   実行終了の側は**ラッチ**します。2 つの側は、発火の仕方が違うからです。`_run_ended_probe` はエッジトリガで、内部のオフセットを進めながら、マーカーを最初に含んだ読み取り窓からしか理由を返しません。一方、実行途中の生存プローブはレベルトリガで、復旧のたびに問い直されます。ラッチしない合成では、「死んだ」と一度だけ答え、以後のすべての復旧で「生きている」に戻ってしまいます。

   ラッチしたプローブは、スポーンごとに 1 つで、コールドゲートと実行途中の生存プローブが共有します。コールド起動側のインスタンスがすでに同じキャプチャを消費しており、独立した 2 つ目のインスタンスはマーカーを取り合うからです。

   スイート終了の行を印字した実行は、その後に何も提供しません。先の 4 件の調査で、`xcodebuild` がこの状態を生き延びていました。復旧の health 待ちは、60 秒の上限ではなく、プローブの次の読み取りで失敗します。

   マーカーは `xcodebuild` 自身のロケール非依存の出力で、コールドゲートは [BE-0319](../BE-0319-xcuitest-cold-spawn-resilience/BE-0319-xcuitest-cold-spawn-resilience-ja.md) 以来これを文字列照合してきました。将来の Xcode が文言を変えた場合、プローブは今日の満額の待ちに退行するだけで、誤って「死んだ」と報告する側には倒れません。

3. **2 度目のクラッシュ再試行をデバイスの置き換えへエスカレーションする。** [BE-0353](../BE-0353-xcuitest-adb-crash-retry-device-recovery/BE-0353-xcuitest-adb-crash-retry-device-recovery-ja.md) の強制消去は、最初の段のまま残します。より安価な対処で、アプリデータの破損クラスを回復できるからです。しかし強制消去つきの再試行自体がまたバックエンドクラッシュで終わったら、次のリースはエスカレーションします。

   run パイプライン（`bajutsu/runner/pipeline.py` の `run_one` の再試行ループ）が、リースにデバイスの置き換えを要求します。XCUITest 環境はこの要求を、[BE-0344](../BE-0344-xcuitest-device-recovery/BE-0344-xcuitest-device-recovery-ja.md) の消滅デバイスの段と同じ「作成、起動、準備」の経路で満たします。デバイスタイプとランタイムの複製、`bajutsu-recovered-*` の命名、リースを新デバイスへ追随させるプールの貼り替えも、そのままです。

   劣化したデバイスはシャットダウンし、二度とプールへ返しません。消滅したデバイスが今日隔離されるのと同じ形の隔離です。

   置き換えのリースは、その試行に限り強制消去を抑止します。作りたてのデバイスに消すものはなく、そこで `erase=True` を律儀に実行すると、状態が何も変わらないのに、シャットダウンと起動をもう 1 周支払うことになるからです。

   置き換えの要求は、置き換えが誤りか不可能な経路では no-op になります。そうした経路は、それぞれ今日持っているもっとも強い再試行を保ちます。

   - 実機と live の WebDriver エンドポイントは、素の respawn を保ちます。どちらも `erase` precondition 自体を拒否する経路で、だからこそ `erase_precondition_supported` が、すでに消去の段から除外しています。
   - 具体的なシミュレータにピン留めした run（`--udid`、または config でピン留めしたデバイス）は、消去レベルの再試行を保ちます。置き換えは、オペレーターが名指ししたデバイスから run を黙って降ろします。しかも [BE-0344](../BE-0344-xcuitest-device-recovery/BE-0344-xcuitest-device-recovery-ja.md) が、ピン留めの場合について記録した、run ごとに増える `bajutsu-recovered-*` の残留物を生みます。
   - 他のすべてのプラットフォームは、この要求を無視します。ツールと runner は、アプリにもプラットフォームにも依存しないままです。

   既存の上限も変わりません。置き換えは、置き換え前の respawn と同じ `crash_retries` の試行回数、同じシナリオごとと run ごとの復旧予算を消費します。この上限は、到達条件も定めます。デフォルトの `crash_retries = 1` では 3 回目の試行が存在しないため、「2 度目のクラッシュ」のトリガーが働くのは、回数を引き上げたレーン（iOS レーンは `BAJUTSU_CRASH_RETRIES: 2` を設定）だけです。デフォルトの run は、ユニット 4 の停滞シグナル経由でのみ、この段に到達します。

   デフォルト自体は、意図して据え置きます。シナリオ全体の再実行をもう 1 回支払う選択は、従量課金のオンデバイスレーンが自分で引き受けたコストであり、新しいデフォルトにする理由はないからです。

   この段は、`docs/architecture.md` と `docs/run-loop.md` が記述する挙動（クラッシュ再試行のもっとも強い対処）を変えます。両ページとその `docs/ja/` ミラーを、BE-0113 の規範に従って、同じ変更の中で更新します。

4. **録画開始の停滞に置き換えの段を選ばせる。** 証跡レイヤーは、この固まり方の最初の症状を、すでに検出しています。`start_video` は、録画ファイルが `_VIDEO_START_TIMEOUT` の上限以内に成長することを確認し、成長しなければ警告をログに残します。[BE-0348](../BE-0348-absolute-timestamp-recording/BE-0348-absolute-timestamp-recording-ja.md) は、この上限を `BAJUTSU_VIDEO_START_TIMEOUT` として調整可能にすることを提案しています。

   このユニットは、この結果をリースの上に浮かび上がらせます。録画開始の確認でタイムアウトしたシナリオ試行が、その後バックエンドクラッシュで終わったら、次の再試行は強制消去を飛ばして、ユニット 3 の置き換えの段へ直接エスカレーションします。

   この停滞は、消去では回復しないと観測されたキャプチャ系の劣化クラスを特定します。しかも 2 つの対処の所要時間は、もともと同程度です。消去はシャットダウン、消去、起動、再インストールで、置き換えは作成、起動、インストールです。停滞が偽陽性だった場合（遅いだけで健全なエンコーダ）でも、支払うのは、飛ばした消去とほぼ同じ額です。

   このシグナルは、あくまで**復旧の段の選択**に対する助言にとどまります。それ自体が試行を失敗させることはなく、動画証跡を無効にした run では、発生しません。

## 検討した代替案

- **停滞をシグナルとして読む代わりに、再試行では動画証跡を省く。** 再試行から `recordVideo` を外せば、劣化したデバイスの負荷は 1 つ減ります。しかしスクリーンショットの経路が同じキャプチャ系サービスに乗っている以上、再試行は観測されたとおりにハングします。しかも再試行の動画は、その再試行まで失敗したときに人間が必要とする証跡です。ユニット 4 は、証跡を犠牲にせず、同じ情報を停滞から取り出します。
- **出力キャプチャを読む（ユニット 2）代わりに、runner の health 応答にセッションごとの nonce を入れる。** 再起動したテストホストが再び応答を始めた場合、新しい nonce が返るので、「復旧した」と「別物に入れ替わった」をきれいに区別できます。しかし [BE-0344](../BE-0344-xcuitest-device-recovery/BE-0344-xcuitest-device-recovery-ja.md) が Swift 側の起動リトライを退けたのと同じ理由で、退けます。修正が Swift パッケージ側に入るため、どのレーンも rebuild した runner を配らないと効果が出ません。出力キャプチャを読むプローブはロジックコアに入り、すべてのレーンへ一度に届きます。さらに nonce は、計測された故障モードをそもそもカバーできません。テストを 0 件しか再実行しないホストは、比較すべき nonce を一度も返さないからです。
- **最初のクラッシュ再試行からデバイスを置き換え、消去の段を飛ばす。** ユニット 4 が特定する固まり方のクラスには、置き換えへ直行する価値があります。ただしそれは、停滞シグナルがあるときだけです。無条件に最初の再試行で置き換えると、一過性のクラッシュのたびに `bajutsu-recovered-*` デバイスが 1 台生まれ、開発者の Mac の上では蓄積していきます（[BE-0344](../BE-0344-xcuitest-device-recovery/BE-0344-xcuitest-device-recovery-ja.md) 自身の代替案が、この残留物を天秤にかけています）。消去の段は、残留物なしにアプリデータの破損クラスを回復できます。停滞が観測されていないあいだは、最初の段のままにします。
- **復旧の段として CoreSimulator デーモンを再起動する。** この系統のフレークの以前の調査で計測済みです。デーモンの再起動は速く（約 1 秒）非破壊的ですが、調べた 9 件の CI 失敗ログのどれとも一致せず、upstream の runner-images にもこの再起動を勧める報告はありません。唯一擁護できる置き場所は、`simctl shutdown` がハングしたときのエスカレーションで、それは本項目より狭い後続課題です。採用しません。
- **予算と上限をさらに引き上げる。** レーンはすでに、ジョブのタイムアウトを 60 分へ引き上げています。完全なログが残った 1 件は、録画開始の上限をすでに 20 秒へ引き上げた状態で走っていました。この引き上げは、プルリクエスト [#1538](https://github.com/bajutsu-e2e/bajutsu/pull/1538) のレーン設定で、[BE-0348](../BE-0348-absolute-timestamp-recording/BE-0348-absolute-timestamp-recording-ja.md) が提案する override を通じたものです。その 1 件は、時間がどこへ消えるかを示しています。行き止まりを証明する復旧層の中です。予算を増やしても買えるのは沈黙の延長だけで、[BE-0344](../BE-0344-xcuitest-device-recovery/BE-0344-xcuitest-device-recovery-ja.md) も、コールド起動の再試行で同じ結論を計測しています（「より大きな予算が買ったのは、より長い沈黙だった」）。
- **同じ変更で置き換えの段を Android エミュレータへも広げる。** adb バックエンドには、この固まり方をする runner チャネルがそもそもなく、[BE-0353](../BE-0353-xcuitest-adb-crash-retry-device-recovery/BE-0353-xcuitest-adb-crash-retry-device-recovery-ja.md) が、エミュレータプロセスの再起動を別の後続課題としてすでに名指ししています。本項目でも変えません。

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
