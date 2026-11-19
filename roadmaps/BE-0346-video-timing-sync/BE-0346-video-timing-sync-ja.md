[English](BE-0346-video-timing-sync.md) · **日本語**

# BE-0346 — ステップと通信ログのタイムスタンプを、動画の確認済み開始時刻に合わせる

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0346](BE-0346-video-timing-sync-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0346") |
| 実装 PR | [#1500](https://github.com/bajutsu-e2e/bajutsu/pull/1500) |
| トピック | 検証とカバレッジ |
<!-- /BE-METADATA -->

## はじめに

実行結果のHTMLレポートに出る各[ステップ](../../docs/ja/glossary.md#シナリオのオーサリング)には、そのステップが起きた瞬間まで、シナリオの録画をシークするタイムスタンプが付いています。この提案では、シナリオの動画[インターバル](../../docs/ja/glossary.md#証跡-capturepolicy-trace-triage)ごとに確認済みの開始時刻を持たせます。そのうえで、確認済みの時刻から、レポートのタイムスタンプを計算するすべての箇所へ1つの補正を通します。これにより、シークした先が、クリックした行の示す瞬間と実際に一致するようになります。

## 動機

各ステップのレポート用タイムスタンプは`clock.now() - scenario_start`として計算されます。`scenario_start`は、シナリオの動画録画が始まった直後に一度だけ記録される値です。ところが、この値は動画の先頭フレームが実際に存在する瞬間ではありません。両者の差は、[アクチュエーター](../../docs/ja/glossary.md#driver-backend-actuator-platform)によって逆方向に生じます。

- `adb`アクチュエーター（Android）と`playwright`アクチュエーター（Web）では、動画は`scenario_start`より前に始まります。Androidではアプリ起動中に、Webではブラウザコンテキストの生成時に始まります。レポートのタイムスタンプは実際より小さくなり、シークした先は対象の動作より早い時点に着地します。
- `xcuitest`アクチュエーター（iOS）では、動画は必要になった時点で開始されます。証跡シンクが、録画プロセスをただのサブプロセスとして起動します。Simulatorのコマンドである`simctl io recordVideo`には、実際にフレームを書き込むまでに無視できない遅延があります。そのため`scenario_start`は動画が存在する前に記録され、レポートのタイムスタンプは実際より大きくなり、シークした先は対象の動作より遅い時点に着地します。

このずれを測定するコードは、現在どこにもありません。シナリオの動画を含むレポートは、すべてこのずれを抱えています。

この修正では、`scenario_start`を後から書き換えません。同じ値がシナリオの`duration_s`の計算にも使われるためです。そこで、動画側の補正は`scenario_start`に折り込まず、別のオフセットとして上に重ねます。

iOSだけは例外です。`scenario_start`を記録する前に動画の開始を確認する（詳細設計1〜2）ため、その確認待ち自体が`scenario_start`の記録を遅らせます。そのためiOSの`duration_s`には、この待ち時間が含まれません。`run_scenario`が`scenario_start`を記録するのは`sink.start_scenario_intervals`が返ったあとです。最大で`_VIDEO_START_TIMEOUT`（5秒）の実時間が、run全体の経過時間には乗る一方、シナリオの`duration_s`には現れません。これは、この提案がiOSについて受け入れる意図的なトレードオフです。読み手に推測させないよう、ここで明記します。

## 詳細設計

**1. 各動画インターバルの実際の開始を確認する。** `Interval`（`bajutsu/evidence/intervals.py`）に`true_start: float | None`フィールドを追加します。これは、録画が実際にデータを生成し始めたことを確認できた`time.monotonic()`の値です。確認を試みなかった場合、または確認に失敗した場合は`None`になります。`start_video`（iOS）と`start_screenrecord`（Android）には、それぞれ`confirm_started: bool = False`引数を追加します。この引数が真のとき、両者は録画プロセスを起動したあと、実際に観測できる信号をポーリングします。

- iOSでは、出力ファイルが**この試行の起動前のサイズを超えて**成長するまでポーリングします（`simctl io recordVideo`はそのパスへ順次書き込むためです）。成長を確認できれば、録画が実際にフレームを生成し始めたことになります。起動前のベースラインは、単なる補足ではなく仕組みの要です。クラッシュリトライ（BE-0049）は同じシナリオIDを、したがって同じ対象パスを再利用します。ベースラインがなければ、確定済みの前回試行が残した末尾のバイトが、起きていない開始を最初のポーリングで確認してしまいます。
- Androidでは、`adb.screenrecord_pids_cmd`が、この試行の起動前には存在しなかったpidを報告するまでポーリングします。これは、`_await_screenrecord_stopped`が逆方向（停止確認）ですでに使っている形と同じです。同じベースラインによる保護を、ここでは前回の試行が残した`screenrecord`に対して適用します。ただし、確認できるのはデバイス側のプロセスが存在することだけです。プロセスが存在しても、そのエンコーダーがフレームを出力しているとは限らないため、iOSより弱い保証にとどまります。それでも、ローカルの`adb shell`クライアントが返る瞬間よりは早い実在の信号であり、アプリが起動するよりも前に得られます。

どちらのポーリングも、期限つきの条件待ちであり、固定`sleep`ではありません。プライム・ディレクティブ2（決定性優先）に合致します。ポーリングがタイムアウトした場合は`true_start`を`None`のままにします。このとき補正は現状と同じ無効化（ノーオペレーション）に留まり、当て推量の数値は入りません。

Webアクチュエーターにはポーリングが不要です。`PlaywrightDriver`は、録画対象のページが生成された直後に`time.monotonic()`を記録します。`record_video_dir`はコンテキスト配下のページに対する録画を有効にするだけで、動画そのものはページが生成されるまで存在しません。そのため、記録するタイミングは`new_context()`ではなく`new_page()`の直後です。

**2. 動作に影響する箇所だけで確認を有効にする。** `confirm_started=True`を渡すのは、本番の2箇所です。

- `FileSink._start_simctl_interval`の動画分岐（`bajutsu/evidence/core.py`）。iOSのオンデマンド開始です。
- `AndroidEnvironment._prestart_video`（`bajutsu/platform_lifecycle/environments/android.py`）。

どちらの確認待ちも、シナリオのクリティカルパス上に乗ります。事前録画は`e.launch(...)`の直前に呼ばれ、並行して動くものは何もないため、ポーリングは`am start`も遅らせます。どちらも上限が定まった小さな増分です。プローブの往復1回に加え、信号が現れなかった場合は`_VIDEO_START_TIMEOUT`（5秒）までです。

`AdbDriver.driver_interval`（`bajutsu/drivers/adb.py`）もこの引数を渡します。このドライバーへ直接到達する呼び出し元にも、修正前の無補正な挙動へ黙って後退させず、同じ確認処理を働かせるためです。デバイスプールは、`AndroidEnvironment`が常に事前録画するため、現状ここに到達しません。それ以外の**呼び出し元**はすべてデフォルトの`confirm_started=False`のままです。オプトインは呼び出し箇所ごとであり、シナリオ単位ではありません。

一方で、既存テストのすべてが影響を受けないわけではありません。`tests/test_adb.py`、`tests/test_evidence.py`、`tests/test_adb_lifecycle.py`は、それぞれ変更後の呼び出し箇所向けに、新設した`pgrep`プローブへの応答をスクリプトする必要がありました。応答しないままだと`_VIDEO_START_TIMEOUT`いっぱいまで待たされるためです。

**3. `scenario_start`を書き換えずに補正を反映する。** Androidの動画は事前録画されたインターバルです。シナリオ開始時に新規開始ではなく`intervals.adopt`（`bajutsu/evidence/intervals.py`）で引き取られます。そのため`adopt`は、ラップした元のインターバルの`true_start`を、返す`Interval`にそのまま引き継がなければなりません。引き継がなければ、手順1で確認した値が手順3まで届かず、Androidの修正は無効化のまま何もしません。

`run_scenario`（`bajutsu/orchestrator/loop.py`）では、シナリオのインターバルを開始して`scenario_start`を記録したあと、動画インターバルの`video_start_offset`を求めます。確認済みの`true_start`があれば`video_start_offset = true_start - scenario_start`とし、なければ`0.0`とします。この値を`_LoopConfig`に通し、`_StepRunner._run_one`で`outcome.started_at = max(0.0, (start - scenario_start) - video_start_offset)`として反映します。

Android/Webではオフセットが負になり、各ステップの報告時刻は動画上でより後ろにシフトして、事前録画分の余分な映像を打ち消します。iOSでは、確認処理が完了してから`scenario_start`が記録されるため、残差は小さく収まります（`scenario_start`に触れないこととは異なる点を、動機の節で述べています）。

補正後の絶対的な基準点は`RunResult.video_anchor_s`（`bajutsu/orchestrator/types.py`）として公開し、手順4で再利用します。このフィールドは単純な加算スカラーなので、`report/load.py`の復元処理に変更は要りません。ただし、生の`time.monotonic()`の値であり、生成したプロセスの外では意味を持ちません。そのため`manifest_dict`（`bajutsu/report/manifest.py`）は、この値を永続化するJSONから除外しなければなりません。素朴な`asdict()`に任せると、読み戻したときに意味のない値として書き出されます。

これは追って対処すべき別の不具合ではなく、この修正が意図して生む、目に見える結果の1つです。動画を録画するAndroidまたはWebのシナリオでは、補正後のステップの`started_at`（レポートに表示される経過時間の列も含む）が、シナリオの`duration_s`を超えることがあります。2つの値は、そもそも別のものを測っているためです。動画のタイムラインは実行本体のステップループより前から始まり、`duration_s`はステップループ自体の長さを測ります。`docs/reporting.md`にもこの点を明記し、レビュアーが2つの数値を見比べたときに、修正が誤っている証拠ではなく期待された挙動だと読めるようにします。

**4. 通信ログの基準点も同じ修正で統一する。** `bajutsu/runner/pipeline.py`は、`run_scenario`を呼ぶ前に、独立にずれていく`scenario_start = time.monotonic()`を記録しています。この値は、各通信ログのレポート用タイムスタンプの計算に使われます。使われるのは`run_scenario`が返った後なので、ローカルの記録を`result.video_anchor_s`に置き換え、`_write_network`の引数名も合わせてリネームします。これにより、ステップと通信ログという2つのタイムラインの間にあった、もう1つの小さなずれも無くなります。

**5. ドキュメントを拡張する。** `docs/evidence.md`（および`docs/ja/`の対訳）は、前倒しの録画を`records_video_up_front`を通じてAndroidとWebに限定して記述しています。この提案では、その節に`true_start`とオフセット補正を追記します。`docs/reporting.md`（日本語版も含む）には、次の2点を追記します。

- レポートの`data-t`のシーク先が、生の`scenario_start`ではなく、確認できた、あるいは分かっている範囲でもっとも確からしい動画の開始時刻を基準にしていること。
- 読み手が不具合と誤読しかねない、目に見える結果の1つ（ステップの`started_at`がシナリオの`duration_s`を超えること）。

作業分解（*進捗*にも対応）：

- Unit 1 — `Interval.true_start`、2つのポーリング用ヘルパー、両方の開始関数への`confirm_started`。
- Unit 2 — 本番の2箇所での`confirm_started=True`の配線と、Playwrightドライバーでの`true_start`の記録。
- Unit 3 — `loop.py`での`video_start_offset`補正と`RunResult.video_anchor_s`。
- Unit 4 — `pipeline.py`の基準点統一。
- Unit 5 — 両言語のドキュメント更新。

## 検討した代替案

- **固定の当て推量による起動レイテンシの定数を、iOSで無条件に差し引く方式。** 見送りました。固定の数値は、実行ごとの実際のジッター（ホストの負荷やSimulatorのバージョンなど）を反映できません。現状の未補正の動作と大差ありません。一方、実際の信号をポーリングする方式は、失敗時に`None`へ落ちる条件待ちです。失敗しても現状より悪化せず、信号の確認に成功したときは正しく補正できます。
- **`true_start`を確認せず、完成した動画の長さから事後的に推定する方式。** 見送りました。動画の正確な長さは、録画が`stop()`で確定するまで確実にはわかりません。その時点では、すべてのステップのタイムスタンプがとうに記録されています。この方式では、各ステップの`started_at`を遡って書き直すためのバッファリングが必要になり、シナリオ開始時に一度だけ解決するオフセットでは済みません。
- **iOSのアクチュエーターでも動画を事前録画し、AndroidやWebと同様にコールドスタートの映像を収録する方式。** この提案の対象外としました。`_DeviceEnvironment`（`bajutsu/platform_lifecycle/environments/ios.py`）には、廃止済みの旧`simctl`単体の環境向けに書かれた`_prestart_video`／`_stop_prestarted_video`の配線が残っていました（BE-0290がidbアクチュエーターとその周辺の`IosEnvironment`を撤去しました）。この死んだ配線は別のクリーンアップですでに取り除かれています。今日では、この基底クラスの`prestarted_intervals`は無条件に`[]`を返し、`records_video_up_front`もXCUITestに対して`False`を返します。iOSでも事前録画を始めるべきかどうかは、別の設計判断であり、固有のトレードオフを持ちます。アプリ起動を含むシナリオ動画のカバレッジが広がる一方、今回の確認用ポーリングと同種の、クリティカルパス上のレイテンシが増えます。この修正に含めず、将来の項目に委ねます。
- **シナリオ中盤でドライバーが再起動されたとき（`RelaunchFn`。Webドライバーの障害分離の仕組み）に、`video_start_offset`を再計算する方式。** 再起動はブラウザ全体を`_new_context`ではなく`_starter`経由で入れ替えます。そのため、`PlaywrightDriver`の`true_start`を記録し直すことも、入れ替え後のコンテキストに`record_video_dir`を渡すこともしません。この提案の対象外としました。`video_start_offset`は、再起動の引き金となるステップループが始まるより前に、`sink.start_scenario_intervals`の結果から一度だけ解決されます。したがって、再起動によって解決済みの値が誤ったものになることはありません。一方、再起動は、シンクが後で確定させる動画のブラウザコンテキストそのものを入れ替えます。この動作は、この提案とは無関係に、保存される動画の内容自体をすでに変えています。再起動後のコンテキストの新しい開始時刻へオフセットを再アンカーする対応は、前提となる既存のギャップにも対処する場合に限った、将来の課題とします。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [x] Unit 1 — `Interval.true_start`と、`start_video` / `start_screenrecord`への`confirm_started`。
- [x] Unit 2 — 本番の2箇所での`confirm_started=True`の配線と、Playwrightドライバーでの`true_start`の記録。
- [x] Unit 3 — `loop.py`での`video_start_offset`補正と`RunResult.video_anchor_s`。
- [x] Unit 4 — `pipeline.py`の通信ログ基準点を`video_anchor_s`に統一する。
- [x] Unit 5 — `docs/evidence.md`と`docs/reporting.md`の拡張（両言語）。

## 参考

- [`docs/evidence.md`](../../docs/evidence.md) — この提案が訂正・拡張する証跡サブシステム（インターバル録画、シンクのadopt-on-stopの形）。
- [`docs/reporting.md`](../../docs/reporting.md) — この修正が最終的に役立つレポート機能。シナリオのステップと通信ログが埋めるマニフェストのフィールドを記載しています。
- [BE-0290 — XCUITest を iOS のデフォルトバックエンドにし、idb を撤去する](../BE-0290-xcuitest-default-ios-backend/BE-0290-xcuitest-default-ios-backend-ja.md) — この提案の *検討した代替案* が言及する、いまは使われていない`_prestart_video`の配線の出どころとなった、旧`simctl`単体の環境を撤去したBE項目です。
- [BE-0028 — 証跡ルールの過剰マッチを防ぐ](../BE-0028-evidence-rule-overmatch-guard/BE-0028-evidence-rule-overmatch-guard-ja.md) — 同じ証跡取得サブシステムにおける、別の正確性の修正です。今回のタイムスタンプ層ではなく、キャプチャルール層を対象としています。
