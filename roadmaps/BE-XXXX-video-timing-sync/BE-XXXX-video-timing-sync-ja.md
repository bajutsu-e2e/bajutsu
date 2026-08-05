[English](BE-XXXX-video-timing-sync.md) · **日本語**

# BE-XXXX — ステップと通信ログのタイムスタンプを、動画の確認済み開始時刻に合わせる

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-XXXX](BE-XXXX-video-timing-sync-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| トピック | 検証とカバレッジ |
<!-- /BE-METADATA -->

## はじめに

実行結果のHTMLレポートに出る各[ステップ](../../docs/ja/glossary.md#シナリオのオーサリング)には、そのステップが起きた瞬間まで、シナリオの録画をシークするタイムスタンプが付いています。この提案では、シナリオの動画[インターバル](../../docs/ja/glossary.md#証跡-capturepolicy-trace-triage)ごとに確認済みの開始時刻を持たせ、この確認済みの時刻から、レポートのタイムスタンプを計算するすべての箇所へ1つの補正を通します。これにより、シークした先が、クリックした行が示す瞬間と実際に一致するようになります。

## 動機

各ステップのレポート用タイムスタンプは`clock.now() - scenario_start`として計算されています。`scenario_start`は、シナリオの動画録画が始まった直後に一度だけ記録される値です。ところが、この値は動画の先頭フレームが実際に存在する瞬間ではありません。この差は、[アクチュエーター](../../docs/ja/glossary.md#driver-backend-actuator-platform)によって逆方向に生じます。

- `adb`アクチュエーター（Android）と`playwright`アクチュエーター（Web）では、動画は`scenario_start`より前に始まります。Androidではアプリ起動中に、Webではブラウザーコンテキストの生成時に始まるため、レポートのタイムスタンプは実際より小さくなり、シークした先は対象の動作より早い時点に着地します。
- `xcuitest`アクチュエーター（iOS）では、動画は必要になった時点で開始されます。証跡シンクがただのサブプロセスとして録画プロセスを起動します。Simulatorのコマンドである`simctl io recordVideo`自体に、実際にフレームを書き込むまでの無視できない遅延があるため、`scenario_start`は動画が存在する前に記録され、レポートのタイムスタンプは実際より大きくなり、シークした先は対象の動作より遅い時点に着地します。

このずれを測定するコードは、現在どこにもありません。そのため、シナリオの動画を含むレポートは、すべてこのずれを抱えています。この修正では`scenario_start`を後から書き換えません。同じ値がシナリオの`duration_s`の計算にも使われるためです。したがって、動画側の補正は`scenario_start`に折り込むのではなく、別のオフセットとして上に重ねます。ただしiOSだけは例外です。`scenario_start`が記録される前に動画の開始を確認する（詳細設計1〜2）ため、その確認待ち自体が`scenario_start`を記録するタイミングを遅らせ、iOSの`duration_s`にはその待ち時間が含まれるようになります。これはこの提案がiOSについて受け入れる意図的なトレードオフであり、読み手に推測させず、ここで明記します。

## 詳細設計

**1. 各動画インターバルの実際の開始を確認する。** `Interval`（`bajutsu/evidence/intervals.py`）に`true_start: float | None`フィールドを追加します。これは、録画が実際にデータを生成し始めたことを確認できた`time.monotonic()`の値であり、確認を試みなかった場合、または確認に失敗した場合は`None`です。`start_video`（iOS）と`start_screenrecord`（Android）には、それぞれ`confirm_started: bool = False`引数を追加します。これが真のとき、両者は録画プロセスを起動したあと、実際に観測できる信号をポーリングします。

- iOSでは、出力ファイルの`stat().st_size > 0`をポーリングします（`simctl io recordVideo`はそのパスへ順次書き込むためです）。これは、録画が実際にフレームを生成し始めたことの確認です。
- Androidでは、`adb.screenrecord_pids_cmd`が空でないプロセス一覧を返すまでポーリングします。これは、`_await_screenrecord_stopped`が逆方向（停止確認）ですでに使っている形と同じです。ただし、これが確認できるのはデバイス側のプロセスが存在することだけです。プロセスが存在しても、そのエンコーダーがすでにフレームを出力しているとは限らないため、iOSより弱い保証にとどまります。それでも、ローカルの`adb shell`クライアントが返る瞬間よりは早く、アプリが起動するよりも前の実在の信号です。

どちらのポーリングも、期限つきの条件待ちであり、固定`sleep`ではありません。プライム・ディレクティブ2（決定性優先）に合致します。ポーリングがタイムアウトした場合は`true_start`を`None`のままにし、補正は現状と同じ無効化（ノーオペレーション）に留まります。当て推量の数値を入れることはありません。Webアクチュエーターにはポーリングが不要です。`PlaywrightDriver`は、`new_context()`が返った直後に`time.monotonic()`を記録します。ブラウザーコンテキストの生成にかかる時間は、サブプロセスの起動に比べて無視できるほど小さいためです。

**2. 動作に影響する箇所だけで確認を有効にする。** `confirm_started=True`を渡すのは、ちょうど2箇所です。`FileSink._start_simctl_interval`の動画分岐（`bajutsu/evidence/core.py`、iOSのオンデマンド開始であり、この提案がレイテンシを追加する唯一の箇所です。シナリオのクリティカルパス上ですが、上限が定まった小さな増分です）と、`AndroidEnvironment._prestart_video`（`bajutsu/platform_lifecycle/environments/android.py`、こちらはアプリ起動前の、もともと存在する待機時間の中で行われるため、新たなレイテンシは増えません）です。それ以外の呼び出し元と既存のテストはすべてデフォルトの`confirm_started=False`のままで、影響を受けません。オプトインは呼び出し箇所ごとであり、シナリオ単位ではありません。

**3. `scenario_start`を書き換えずに補正を反映する。** Androidの動画は事前録画されたインターバルであり、シナリオ開始時に新規開始ではなく`intervals.adopt`（`bajutsu/evidence/intervals.py`）でアダプトされます。このため`adopt`は、ラップした元のインターバルの`true_start`を、返す`Interval`にそのまま引き継がなければなりません。引き継がなければ、Unit 1で確認した値がUnit 3まで届かず、Androidの修正は無効化のまま何もしないことになってしまいます。`run_scenario`（`bajutsu/orchestrator/loop.py`）の中では、シナリオのインターバルを開始し`scenario_start`を記録したあと、確認済みの`true_start`が動画インターバルにあるときは`video_start_offset = true_start - scenario_start`を、なければ`0.0`を求めます。この値を`_LoopConfig`に通し、`_StepRunner._run_one`で`outcome.started_at = max(0.0, (start - scenario_start) - video_start_offset)`として反映します。Android/Webでは負のオフセットとなり、各ステップの報告時刻は動画上でより後ろにシフトし、事前録画分の余分な映像を打ち消します。iOSでは、確認処理が完了してから`scenario_start`が記録されるため（動機の節で述べたとおり、これは`scenario_start`に触れないことと同じではありません）、残差は構造的に小さく収まります。この補正後の絶対的な基準点は`RunResult.video_anchor_s`（`bajutsu/orchestrator/types.py`）として公開し、Unit 4で再利用します。単純な加算スカラーのフィールドなので`report/load.py`の復元処理に変更は要りませんが、これは生の`time.monotonic()`の値であり、それを生成したプロセスの外では意味を持ちません。`manifest_dict`（`bajutsu/report/manifest.py`）は、素朴な`asdict()`にこの値を持たせて永続化させず、レポートのJSONから除外しなければなりません。

これは追って対処すべき別の不具合ではなく、この修正が意図して生む1つの見える結果です。動画を録画するAndroidまたはWebのシナリオでは、補正後のステップの`started_at`（レポートに表示される経過時間の列も含む）が、シナリオの`duration_s`を超えることがあります。この2つは、そもそも別のものを測っているためです。動画のタイムラインは、実行本体のステップループより前から始まりますが、`duration_s`はそのステップループ自体の長さを測ります。`docs/reporting.md`にもこの点を明記し、レビュアーが2つの数値を見比べたときに、修正が誤っている証拠ではなく期待された挙動だと読めるようにします。

**4. 通信ログの基準点も同じ修正で統一する。** `bajutsu/runner/pipeline.py`は、`run_scenario`を呼ぶ前に、それ自体が独立にずれていく`scenario_start = time.monotonic()`を記録し、各通信ログのレポート用タイムスタンプの計算に使っています。この記録は`run_scenario`が返った後に使われるので、`result.video_anchor_s`に置き換え、`_write_network`の引数名も合わせてリネームします。これにより、ステップと通信ログという2つのタイムラインの間にあった、もう1つの小さなずれも無くなります。

**5. ドキュメントを訂正・拡張する。** `docs/evidence.md`（および`docs/ja/`の対訳）には、デバイスバックエンドが動画を「`simctl launch` / `am start`より前」に始めると、すべてのデバイスバックエンドに当てはまるかのように書かれています。現状これが当てはまるのはAndroidの`adb`アクチュエーターだけなので、この提案ではその記述を訂正し、`true_start`とオフセット補正についても記載します。`docs/reporting.md`（その日本語版も含む）には、レポートの`data-t`のシーク先が、生の`scenario_start`ではなく、確認済みまたは最善推定の動画開始時刻を基準にしていることを追記します。

作業分解（*進捗*にも対応）：

- Unit 1 — `Interval.true_start`、2つのポーリング用ヘルパー、両方の開始関数への`confirm_started`。
- Unit 2 — 本番の2箇所での`confirm_started=True`の配線と、Playwrightドライバーでの`true_start`の記録。
- Unit 3 — `loop.py`での`video_start_offset`補正と`RunResult.video_anchor_s`。
- Unit 4 — `pipeline.py`の基準点統一。
- Unit 5 — 両言語のドキュメント更新。

## 検討した代替案

- **固定の当て推量による起動レイテンシの定数を、iOSで無条件に差し引く方式。** 見送りました。固定の数値は実行ごとの実際のジッター（ホストの負荷やSimulatorのバージョンなど）を反映できないため、現状の未補正の動作と大差ありません。一方、実際の信号をポーリングする方式であれば、失敗時は`None`に落ち着く条件待ちでしかありません。失敗しても現状より悪化することはなく、信号の確認に成功したときは正しく補正できます。
- **`true_start`を確認せず、完成した動画の長さから事後的に推定する方式。** 見送りました。動画の正確な長さは、録画が`stop()`で確定するまで確実にはわからず、それはすべてのステップのタイムスタンプがすでに記録された、はるか後です。この方式では、各ステップの`started_at`を後から遡って書き直す必要があり、シナリオ開始時に一度だけ解決するオフセットでは済みません。
- **iOSのアクチュエーターでも動画を事前録画し、AndroidやWebと同様にコールドスタートの映像を収録する方式。** この提案の対象外としました。`_DeviceEnvironment`（`bajutsu/platform_lifecycle/environments/ios.py`）には、廃止済みの旧`simctl`単体の環境向けに書かれた`_prestart_video`／`_stop_prestarted_video`の配線が残っていました（BE-0290がidbアクチュエーターとその周辺の環境を撤去しました）。この死んだ配線は別のクリーンアップで既に取り除かれており、今日ではこの基底クラスの`prestarted_intervals`は無条件に`[]`を返し、`records_video_up_front`もXCUITestに対して`False`を返します。iOSでも事前録画を始めるべきかどうかは、それ自体トレードオフを持つ別の設計判断であり（アプリ起動を含むシナリオ動画のカバレッジが広がる一方、今回の確認用ポーリングと同種の、クリティカルパス上のレイテンシが増えます）、この修正に含めず将来の項目に委ねます。
- **シナリオ中盤でドライバーが再起動されたとき（`RelaunchFn`。Webドライバーの障害分離の仕組みで、`BrowserContext`を廃棄して再構築し、`PlaywrightDriver`自身の`true_start`も新たに記録し直します）に、`video_start_offset`を再計算する方式。** この提案の対象外としました。`video_start_offset`は、再起動の引き金となるステップループが始まるより前、`sink.start_scenario_intervals`の結果から一度だけ解決されます。したがって、再起動によってこの解決済みの値が誤ったものになることはありません。一方で、再起動が行う「シンクが後で確定させる動画のブラウザーコンテキストそのものを入れ替える」という動作は、この提案とは無関係に、保存される動画の内容自体を変えてしまいます。再起動後のコンテキストの新しい開始時刻へオフセットを再アンカーする対応は、その前提となる既存のギャップに対処したうえでの、将来の課題とします。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [x] Unit 1 — `Interval.true_start`と、`start_video` / `start_screenrecord`への`confirm_started`。
- [x] Unit 2 — 本番の2箇所での`confirm_started=True`の配線と、Playwrightドライバーでの`true_start`の記録。
- [x] Unit 3 — `loop.py`での`video_start_offset`補正と`RunResult.video_anchor_s`。
- [x] Unit 4 — `pipeline.py`の通信ログ基準点を`video_anchor_s`に統一する。
- [x] Unit 5 — `docs/evidence.md`と`docs/reporting.md`の訂正・拡張（両言語）。

## 参考

- [`docs/evidence.md`](../../docs/evidence.md) — この提案が訂正・拡張する証跡サブシステム（インターバル録画、シンクのadopt-on-stopの形）。
- [`docs/reporting.md`](../../docs/reporting.md) — この修正が最終的に役立つレポート機能。シナリオのステップと通信ログが埋めるマニフェストのフィールドを記載しています。
- [BE-0290 — XCUITest を iOS のデフォルトバックエンドにし、idb を撤去する](../BE-0290-xcuitest-default-ios-backend/BE-0290-xcuitest-default-ios-backend-ja.md) — この提案の *検討した代替案* が言及する、いまは使われていない`_prestart_video`の配線の出どころとなった、旧`simctl`単体の環境を撤去したBE項目です。
- [BE-0028 — 証跡ルールの過剰マッチを防ぐ](../BE-0028-evidence-rule-overmatch-guard/BE-0028-evidence-rule-overmatch-guard-ja.md) — 同じ証跡取得サブシステムにおける、別の正確性の修正です。今回のタイムスタンプ層ではなく、キャプチャルール層を対象としています。
