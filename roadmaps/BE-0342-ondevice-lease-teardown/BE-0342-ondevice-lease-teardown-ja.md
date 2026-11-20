[English](BE-0342-ondevice-lease-teardown.md) · **日本語**

# BE-0342 — 実機スイートの lease に runner まで届く teardown を持たせる

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0342](BE-0342-ondevice-lease-teardown-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0342") |
| 実装 PR | [#1491](https://github.com/bajutsu-e2e/bajutsu/pull/1491) |
| トピック | プラットフォーム対応 |
| 関連 | [BE-0334](../BE-0334-conformance-suite-infra-fault-recovery/BE-0334-conformance-suite-infra-fault-recovery-ja.md), [BE-0114](../BE-0114-driver-conformance-suite/BE-0114-driver-conformance-suite-ja.md), [BE-0305](../BE-0305-driver-resilience-fault-injection/BE-0305-driver-resilience-fault-injection-ja.md), [BE-0009](../BE-0009-cross-platform-abstractions/BE-0009-cross-platform-abstractions-ja.md) |
<!-- /BE-METADATA -->

## はじめに

Bajutsu の継続的インテグレーション（CI）のジョブのうち 2 つは、`bajutsu run` を通さず、`pytest` のハー
ネスから実際の iOS シミュレータを駆動します。ドライバの conformance スイートと、フォールト注入のスイー
トです。どちらも 1 台のデバイスを module 単位で共有します。常駐 runner が死んでいるとテストが判断し
た時点で、共有している lease を破棄します。次のテストが、新しく起動した runner から始められるように
するためです。この破棄を作ったのは
[BE-0334](../BE-0334-conformance-suite-infra-fault-recovery/BE-0334-conformance-suite-infra-fault-recovery-ja.md)
で、実装は `LeaseHolder.invalidate()` です。

この破棄は、iOS バックエンドでは runner まで届いていません。破棄が呼ぶのは
[ドライバ](../../docs/ja/glossary.md#driver-backend-actuator-platform)の `close()` です。XCUITest の
ドライバは `close()` を持ちません。runner をホストする `xcodebuild` のプロセスは動き続け、XCTest は
そのプロセスの中で runner を再起動し続けます。次のテストは、同じシミュレータの上に 2 つ目の runner
を起動します。自動操作セッションは 1 デバイスに 1 つしか存在できないため、2 つの runner は互いからセ
ッションを奪い合います。

本項目は、runner のプロセスまで届く teardown を lease に持たせます。破棄した lease は、次の lease が
始まる時点で確実に消えます。

## 動機

各スイートは module 全体で 1 台のデバイスを共有します。破棄しきれなかった lease は、その module の問
題になります。破棄が止められなかった runner は、同じ module の次のテストが lease するデバイスを、掴
んだままだからです。2026-08-05 のフォールト注入のジョブは、アップロードされたログに 2 つの runner を
残していました。時刻を並べると、両者が同時にデバイスの上にいたことがわかります。

| | 1 つ目の runner（ポート 50870） | 2 つ目の runner（ポート 52816） |
|---|---|---|
| 起動 | 04:14:15 | **04:16:24** |
| 最後の応答 | `Find the Application`、04:16:16 まで | `Find the Application`、およそ 04:16:32 まで |
| その後 | **04:16:33 にホストプロセスを再起動** | 04:16:57 に再起動し、テストを 0 件実行 |

どちらの再起動も、同じ行を出力しています。テストをホストしているプロセスが死に、XCTest が別のプロセ
スを起動するときに書く行です。

```
Restarting after unexpected exit, crash, or test timeout; summary will include totals from previous launches.
```

2 つ目の runner は、失敗したテストがまさに起動したものです。いくつかの要求に応答してから死にました。
起動したテストはチャネルの接続を拒否され、runner がクラッシュしたと報告しました。

```
FAILED tests/test_fault_injection_ondevice.py::test_a_killed_runner_fails_loudly_with_a_crash_diagnosis
 - XcuitestRunnerCrashError: runner channel GET /elements failed:
   the runner crashed mid-run and did not recover within 60s
```

この失敗が起きたのは、テストの**最初の文**、つまり lease を起動するプロパティ参照です。テスト自身が
注入するフォールトでは、説明がつきません。そのテストが注入するはずだったものは、数行あとに現れます。

説明を与えるのは、1 つ目の runner です。誰もそれを止めていなかったからです。
`LeaseHolder.invalidate()` は、`dead.close()` を `try` の中で呼びます。その `except Exception` は、
debug レベルで記録して先へ進みます。この呼び出しは、実装されていないだけではありません。lease が保持
しているインタフェースの外にあります。

lease の値が満たす Protocol は `Driver` で、`close()` を宣言していません。`close()` は、別の
Protocol である `BackendLifecycle` のメンバーです。`platform_lifecycle` の環境は、どのバックエンドを
保持しているかをプラットフォームの不変条件で確定させたうえで、`cast(base.BackendLifecycle, driver)`
を通して `close()` に届きます。

このツリーが出荷するドライバは、fake、adb、Playwright、XCUITest、XCUITest の live 経路の 5 つです。
`close()` を実装しているのは Playwright のものだけです。ブラウザの場合、閉じる対象のコンテキストをド
ライバ自身が所有します。iOS と Android では、runner のプロセスは環境に属します。

そのため iOS バックエンドでは、`dead.close()` が `AttributeError` を投げます。`except Exception` が
それを飲み込み、`invalidate()` は何も破棄せずに戻ります。この状態が沈黙したままだった理由は、3 つあ
ります。

- ログの行が debug レベルにあること。
- mypy がこの呼び出しを見ないこと。mypy が走る対象は `bajutsu demos scripts` で、`tests/` を含みませ
  ん。
- ハーネス自身の実機不要のテスト（`tests/runner/test_backend_crash_recovery.py`）で、すべての偽のド
  ライバが `close()` を定義していること。これらのケースが動かしているのは、Playwright のドライバだけ
  が持つ形です。スイートが lease する XCUITest の形は、動かしていません。

この破棄を説明している 2 か所は、どちらも、起きていないことを説明しています。`invalidate()` 自身の
docstring は、「次に `driver` を参照したときコールド再起動するよう、現在の（死んだ）lease を破棄する」
と書いています。破棄が再 lease する先の fixture には、フォールト注入スイートのコメントが付いています。
そのコメントも、「killed-runner のケースはこれを破棄するので、次のケースは死んだ runner を引き継ぐの
ではなく新しいデバイスへ再起動する」と書いています。

その帰結は、BE-0334 が取り除こうとした失敗の形そのものです。BE-0334 の論拠は次のとおりです。
conformance スイートの実行中に起きたシミュレータの障害は、原因を作りえなかったプルリクエストの必須チ
ェック `E2E (iOS)` を赤くします。だからハーネスは、インフラ障害を分類し、run パイプラインと同じよう
に復旧するべきです。古い runner を生かしたまま返す破棄は、自身の復旧を次の障害へ変えてしまいます。そ
の破棄が行う再 lease こそが、デバイスの上に 2 つ目の runner を置くからです。

どのチェックに届くかは、スイートによって違います。上のログが出たフォールト注入のレーンは、当時、
`E2E (iOS)` の集約の `needs:` から意図的に外されていました。`actuation`、`golden`、`visual` も同じで
す。そこが赤くなっても、マージは止まりませんでした。この欠陥がゲートではない信号の上に現れたのは、そ
のためです。その後、BE-0305 がこのレーンを必須化しています。

conformance スイートは、同じやり方で lease を破棄します。しかも、その集約に入っています。同じ欠陥は、
そこでは必須チェックに届きます。

この問題は、特定のブランチに依存しません。同じテストが同じメッセージで、
[run 30971636417](https://github.com/bajutsu-e2e/bajutsu/actions/runs/30971636417) でも失敗していま
す。そのブランチの変更は、ここで述べた仕組みに一切触れていません。進行中の変更による退行だという解釈
は、これで否定できます。上に引用したログは、
[run 30971507268](https://github.com/bajutsu-e2e/bajutsu/actions/runs/30971507268) のものです。

## 詳細設計

作業は 3 つのユニットに分かれます。ユニット 1 が修正です。ユニット 2 は、この問題を隠していた沈黙の
類型を止めます。ユニット 3 は、両者の退行を防ぐ実機不要のテストです。

1. **runner を所有する環境を、lease から teardown できるようにする。** スイートが渡す起動の thunk は、
   ドライバと並べて teardown を返します。`LeaseHolder` は、`driver.close()` ではなく、プラットフォー
   ム自身の teardown を通して破棄します。

   両スイートの `_backend_launch` は、すでに `launch_driver` を呼んでいます。`launch_driver` は、用
   意済みの `environment` を受け取れます。そこで thunk は、呼び出しのたびに環境を組み立てて渡し、そ
   の環境とドライバを捕捉した teardown を返します。環境を組み立てるのは thunk であり、module 全体で
   保持される fixture ではありません。

   これはプラグインの opt-in の契約を広げます。そのため、同じ変更のなかで契約も直します。
   `LeaseHolder` の `launch` の型と、module の docstring は、どちらも今はドライバだけを指しています。
   docstring は、「新しい `base.Driver` を返す引数なしの callable」と定めています。本項目の動機が引
   用した 2 つの記述は、直す必要がありません。ユニット 1 こそが、その記述を事実にするからです。

   環境は、再 lease をまたいで保持せず、**lease ごとに新しく**作ります。XCUITest の環境を保持すると、
   以後のコールド起動がすべて、その場での再起動になります。本項目が扱う混み合ったホストの上で、コー
   ルドの上限（300 秒）ではなく、レーンのより厳しい再起動の上限（90 秒）が使われます。加えて
   `teardown` に、前の lease のドライバを渡すことにもなります。

   iOS バックエンドで走る teardown は、2 つの処理を行います。1 つは `xcodebuild` のホストプロセスの
   終了です。これにより、runner が住む XCTest のセッションが終わります。もう 1 つは、対象アプリを
   `simctl` で終了させる処理です。この仕組みはすでに存在していて、これらのスイートから届く道だけがありませんでした。web の環境が持つ
   `teardown` は、すでにブラウザのコンテキストに対する `close()` です。lease にバックエンドごとの分
   岐は要りません。

   ユニット 1 は、途中で失敗する起動もカバーしなければなりません。`launch_driver` は、`env.start` を
   呼んでドライバを受け取り、続けて `_await_ready` を呼びます。`_await_ready` は、runner が
   readiness の確認中に死ぬと、例外を投げます（`bajutsu/runner/launch.py:79-82`）。現状では、この例
   外は、ドライバを呼び出し元へ返さないまま、`launch_driver` の外へ伝わります。呼び出した thunk は、
   環境を持っていても、`env.teardown(driver, eff)` に渡すドライバを持ちません。

   run パイプラインは、この同じ seam をすでに守っています。`bajutsu/runner/pool.py:371-383` は、
   `except BaseException:` の中で環境を teardown し、元の起動エラーはそのまま伝えます。ユニット 1 に
   は、まだ決めていない選択が 1 つあります。`launch_driver` 自身がこの保護を取り込み、呼び出し元すべ
   てに行き渡らせるのか。それとも、スイート自身の thunk が呼び出しを包み、自ら teardown するのか。こ
   の選択は、実装の細部として省略してよいものではありません。

2. **守られた teardown を切り出し、走れなかったものを飲み込むのをやめる。** **実行中**の破棄は、失敗
   の経路で走ります。そこで例外を投げると、原因となった障害を隠してしまいます。そこで捕まえること自
   体は、妥当です。妥当でないのは、debug レベルで記録することです。**構造的に**不可能な teardown が、
   その回だけ失敗した teardown と見分けられなくなるからです。メソッドの欠落は、保守する人が最初の実
   行で気付くべきものです。ログのレベルを上げて探す行にしてはいけません。

   何を飲み込んでよいかは、run パイプラインがすでに持っている方針です。パイプラインの 3 つの
   teardown の箇所は、いずれも、`CalledProcessError` と `OSError` を warning にします。すでに終了し
   ていた runner や、届かない `xcrun` が該当します。それ以外の例外は、表に出します。この方針をハーネ
   スで書き写すと、同じ 2 つの例外クラスの組が、ツリーの 4 つ目の写しになります。それらを揃えておく
   ものは、コメントしかありません。

   そこで、守られた teardown を 1 つのヘルパーへ切り出し、プールの 3 箇所と lease の両方が呼ぶように
   します。置き場所は `bajutsu/runner/recovery.py` です。ハーネスは、パイプラインの再試行回数と復旧
   の予算を、すでにこのモジュールから import で借りています。書き写しではありません。これによって、
   「2 つの復旧の経路は乖離しない」が、散文の約束ではなく、コードの性質になります。

   配線の欠陥がどこに現れるかは、どちらの経路がヘルパーを呼んだかで決まります。実行中の破棄は、配線
   の欠陥を warning に飲み込みます。この経路の呼び出し箇所は 3 つあります。2 つは、フォールト注入ス
   イートの `finally` の中にあります。一方は、テストが検査している `BackendCrashError` を守るもので
   す。残る 1 つは、プラグイン自身の破棄です。どのテストにも属さない場所で走るため、例外が抜けると、
   1 件のテストを落とすのではなく、セッションごと中断させてしまいます。

   module の**最後**の解放では、配線の欠陥が module の teardown を失敗させます。ここには処理中の障害
   がなく、生き残った runner はジョブの残りへ漏れてしまうからです。

3. **teardown を実機なしで固定する。** このハーネスは、シミュレータなしで動かせます。
   `tests/runner/test_backend_crash_recovery.py` が、`pytester` と偽の起動 thunk でプラグインを駆動
   しています。並行するファイルを新しく作るのではなく、このファイルを拡張します。拡張とは、13 個ある
   内側の起動 thunk をすべて新しい契約へ移すことです。偽のドライバが、出荷するドライバから再び乖離す
   るのも止まります。

   確かめるのは、次の 8 つです。

   - `invalidate()` が、破棄した lease に対して teardown をちょうど 1 回走らせること。
   - 成功した最後の解放でも、teardown をちょうど 1 回走らせること。
   - 次の `driver` 参照が、新しい lease を起動すること。
   - 実行中の teardown が `CalledProcessError` または `OSError` を投げた場合、warning として報告され
     ること。
   - 実行中の teardown がそれ以外を投げた場合も、伝播させずに同じ warning にすること。
   - 最後の解放が配線の欠陥を伝播させ、`CalledProcessError` と `OSError` には warning のままとするこ
     と。
   - `env.start` のあとで例外を投げる起動でも、その例外が伝わる前に環境を teardown すること。
   - 一度も起動していない lease は、何も片付けないこと。

   これらは、高速なゲートが走らせる決定的なスイートに置きます。修正は、デバイスなしの Linux 上で保た
   れます。

Android バックエンドも、同じ形をしています（`AdbDriver` にも `close()` がありません）。ただし、
`LeaseHolder` を通してデバイスを lease するスイートは、現在の Android にありません。Android の実機ス
イートが将来追加されたときは、ユニット 1 の seam によって、そのスイートが構成上正しくなります。本項
目にシミュレータ固有のものは、何もありません。スイートが渡す teardown は、そのプラットフォーム自身の
ものです。

## 検討した代替案

- **XCUITest のドライバに `close()` を実装する。** 差分は最小で、`LeaseHolder` の変更も要りません。
  しかし、プロセスの teardown を誤った seam へ置くことになります。ドライバが runner に届くのは、ルー
  プバックのポート経由です。`xcodebuild` のプロセスのハンドルは持っていません。そのプロセスと、その
  周りのシミュレータのライフサイクルを所有しているのは、環境です。ドライバの `close()` は、ドライバ
  が保持していない環境へ手を伸ばすほかなく、seam を逆転させます。プラットフォームごとの立ち上げを環
  境の背後に置いたのは、ドライバにそれを担わせないためでした（[BE-0009](../BE-0009-cross-platform-abstractions/BE-0009-cross-platform-abstractions-ja.md)）。
  teardown をすでに所有している環境を、lease に持たせる形を採り、この案は退けます。
- **すべてのドライバに `close()` の実装を必須にする。** この欠落を、沈黙する `AttributeError` ではな
  く、型エラーにできる点は魅力があります。一方で、`BackendLifecycle` の目的と矛盾します。その
  docstring 自身が、「呼び出し側のための*型付けの傘*であって、適合の対象ではない」と述べています。狙
  いは、各フックを、呼び出し側で mypy が検査する事実にすることです。しかも、「ライフサイクルを持たな
  いバックエンドに no-op のメソッドを書かせずに」と続きます。出荷する 5 つのドライバのうち 4 つには、
  ドライバ自身に閉じられるものが存在しません。前の案と同じ理由で、退けます。
- **フォールト注入のスイート自身が、残った runner を殺す。** このスイートは、自分のフォールト注入の
  ために、runner のプロセスをコマンドラインから探しています。teardown にも、その仕組みを流用できます。
  ただし、問題が現れた 1 つのジョブだけが直ります。復旧したクラッシュごとに lease を破棄する
  conformance スイートには、同じ欠陥が残ります。しかも、プロセスの管理を、それを所有する seam ではな
  く、テストのファイルへ移すことになります。1 つの症状に対する回避策として、退けます。
- **どちらのスイートもふだんは通るので、そのままにする。** 実際、多くの実行では通ります。この欠陥が
  噛むのは lease を破棄したときだけで、そのためには、まずクラッシュか注入したフォールトが必要です。
  それでも噛んだときに生むのは、原因を作りえなかったプルリクエストの赤いチェックです。BE-0334 が防ぐ
  ために存在する結末そのものが、BE-0334 自身の復旧の経路を通って現れます。フォールト注入のレーンでは、
  そのチェックは当時マージを止めませんでした（その後 BE-0305 で必須化されています）。同じやり方で
  lease を破棄する conformance スイートでは、それが必須の `E2E (iOS)` です。退けます。

## 進捗

> 作業の進行に合わせて更新します。チェックリストは*詳細設計*の MECE な作業分解と対応します
> （作業単位ごとに 1 項目）。ログには、何がいつ変わったかを古い順に記録し、PR を紐づけます。

- [x] ユニット 1 — runner を所有する環境を lease ごとに作り、lease から teardown できるようにする。
- [x] ユニット 2 — 守られた teardown を `recovery.py` へ切り出し、実行中の失敗は warning で報告し、
      最後の解放は配線の欠陥を伝播させる。
- [x] ユニット 3 — lease の起動と teardown の seam に対する実機不要のテスト。

ログ:

- 2026-08-05 — ユニット 1 から 3（[#1491](https://github.com/bajutsu-e2e/bajutsu/pull/1491)）。
  両方のオンデバイススイートが、新設した `tests/xcuitest_lease.py` の `xcuitest_lease_launch` という
  ひとつの thunk ファクトリを共有します（バックエンドに依存しない `backend_crash_recovery` プラグインの
  外に置きました）。この thunk が lease ごとに新しい environment を作り、driver と一緒にその teardown を
  返すようになりました。これにより `LeaseHolder` は `driver.close()` ではなく、プラットフォーム自身の
  teardown を通して lease を破棄します。ユニット 1 が残していた選択は、`launch_driver` 自身がガードを
  引き受ける方向で決着し、すべての呼び出し側がそれを受け継ぎます。守られた teardown の方針は
  `bajutsu/runner/recovery.py` へ移り、pool 自身の teardown 箇所（コレクタの起動時ロールバック、
  アクチュエータ切り替え、bridge とコレクタの停止を伴う失敗した lease、同じく bridge とコレクタの停止を
  伴う `release()` 自身の teardown、`shutdown()` のデバイスとコレクタの 2 つのループ）、`launch_driver`、
  lease の破棄が共有します。実行中の配線の欠陥は、debug ではなく warning で報告されるようになりました。
  `shutdown()` は掃除を最後までやり切ってから、最初に捕まえた欠陥を伝播させます。1 台の欠陥で、残りの
  デバイスの runner とコレクタのソケットを取りこぼさないためです。`release()` も lease の通常の終わりで
  同じガードを持ちます。`end_lease` が終わらなかった warm な resident は破棄し（フルの teardown まで
  実施します）、半分だけ終わったアプリを次の lease が引き継がないようにします。その end-of-lease の
  teardown で起きた配線の欠陥も、`release()` からそのまま投げることはできません。scenario 自身の結果を
  上書きし、デバイスを pool へ戻す処理まで飛ばしてしまうからです。そこで欠陥をいったん保持しておき、
  すべての lease が release された後に `shutdown()` がそれを伝播させます。warm な resident を持たない
  backend では、この teardown だけが唯一実行されるものであり、この仕組みがなければそこで起きた欠陥は
  warning のまま流れ続け、run は決して失敗しません。状態を実装済みへ。

## 参考

- [BE-0334 — 実機 conformance スイートに run パイプラインと同じインフラ障害からの復旧を持たせる](../BE-0334-conformance-suite-infra-fault-recovery/BE-0334-conformance-suite-infra-fault-recovery-ja.md) — lease とその破棄を作った項目。
- [BE-0114 — backend 非依存の挙動を検査する driver conformance suite](../BE-0114-driver-conformance-suite/BE-0114-driver-conformance-suite-ja.md) — lease を修正する対象のスイート。
- [BE-0305 — ドライバ耐障害経路への実機障害注入カバレッジ](../BE-0305-driver-resilience-fault-injection/BE-0305-driver-resilience-fault-injection-ja.md) — 本項目が読むログを残したフォールト注入のスイートを作った項目。
- [BE-0009 — 抽象のクロスプラットフォーム化](../BE-0009-cross-platform-abstractions/BE-0009-cross-platform-abstractions-ja.md) — runner のプロセスを所有する環境の seam。
- `tests/backend_crash_recovery.py` — `LeaseHolder`、その `invalidate()`、試行のあいだに再 lease するプラグイン。
- `tests/runner/test_backend_crash_recovery.py` — ユニット 3 が拡張する実機不要のテスト。
- `bajutsu/drivers/base.py` — `close()` を web のドライバだけが実装している `BackendLifecycle` Protocol と、`close()` を宣言していない `Driver` Protocol。
- `tests/test_driver_conformance_ondevice.py`、`tests/test_fault_injection_ondevice.py` — `LeaseHolder` を通してデバイスを lease する 2 つのスイート。
