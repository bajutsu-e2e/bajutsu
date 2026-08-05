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

Bajutsu の継続的インテグレーション（CI）のジョブのうち 2 つは、`bajutsu run` を通さず `pytest` の
ハーネスから実際の iOS シミュレータを駆動します。ドライバの conformance スイートと、フォールト注入の
スイートです。どちらも 1 台のデバイスを module 単位で共有し、常駐 runner が死んでいるとテストが判断した
時点でその共有 lease を破棄します。次のテストが新しく起動した runner から始められるようにするためです。
この破棄を作ったのは
[BE-0334](../BE-0334-conformance-suite-infra-fault-recovery/BE-0334-conformance-suite-infra-fault-recovery-ja.md)
で、`LeaseHolder.invalidate()` がその実装です。

この破棄は、iOS バックエンドでは runner まで届いていません。破棄が呼ぶのは
[ドライバ](../../docs/ja/glossary.md#driver-backend-actuator-platform)の `close()` であり、
XCUITest のドライバは `close()` を持ちません。したがって runner をホストする `xcodebuild` のプロセスは
動き続け、XCTest はその中で runner を再起動し続けます。次のテストは同じシミュレータの上に 2 つ目の
runner を起動します。自動操作セッションは 1 デバイスに 1 つしか存在できないため、2 つの runner は
互いからセッションを奪い合います。本項目は、runner のプロセスまで届く teardown を lease に持たせます。
破棄した lease は、次の lease が始まる時点で本当にいなくなります。

## 動機

各スイートが module 全体で 1 台のデバイスを共有しているため、破棄しきれなかった lease はその module の
問題になります。破棄が止められなかった runner は、同じ module の次のテストが lease するそのデバイスを
掴んだままだからです。2026-08-05 の
フォールト注入のジョブは、アップロードされたログに 2 つの runner を残していました。時刻を並べると、
両者が同時にデバイスの上にいたことがわかります。

| | 1 つ目の runner（ポート 50870） | 2 つ目の runner（ポート 52816） |
|---|---|---|
| 起動 | 04:14:15 | **04:16:24** |
| 最後の応答 | `Find the Application`、04:16:16 まで | `Find the Application`、およそ 04:16:32 まで |
| その後 | **04:16:33 にホストプロセスを再起動** | 04:16:57 に再起動し、テストを 0 件実行 |

どちらの再起動も同じ行を出力しています。テストをホストしているプロセスが死んで、XCTest が別の
プロセスを起動するときに書かれる行です。

```
Restarting after unexpected exit, crash, or test timeout; summary will include totals from previous launches.
```

2 つ目の runner は、失敗したテストがまさに起動したものです。いくつかの要求に応答してから死んだため、
起動した側のテストはチャネルの接続を拒否され、runner がクラッシュしたと報告しました。

```
FAILED tests/test_fault_injection_ondevice.py::test_a_killed_runner_fails_loudly_with_a_crash_diagnosis
 - XcuitestRunnerCrashError: runner channel GET /elements failed:
   the runner crashed mid-run and did not recover within 60s
```

この失敗が起きたのは、テストの**最初の文**、lease を起動するプロパティ参照です。したがって、テスト自身が
注入するフォールトでは説明がつきません。そのテストが注入するはずだったものは、数行あとに現れます。

説明を与えるのは 1 つ目の runner です。誰もそれを止めていなかったからです。`LeaseHolder.invalidate()`
は `dead.close()` を `try` の中で呼び、その `except Exception` は debug レベルで記録して先へ進みます。
この呼び出しは、実装されていないだけではありません。lease が保持しているインタフェースの外にあります。
lease の値が満たす Protocol である `Driver` は、`close()` をまったく宣言していません。`close()` は別の
Protocol である `BackendLifecycle` のメンバーです。`platform_lifecycle` の環境が、どのバックエンドを
保持しているかを確定させたうえで `cast(base.BackendLifecycle, driver)` を通してそこに届きます。このツリーが出荷する
ドライバは fake、adb、Playwright、XCUITest、XCUITest の live 経路の 5 つで、`close()` を実装しているのは
Playwright のものだけです。ブラウザの場合、閉じる対象のコンテキストはドライバ自身が所有します。iOS と
Android では、runner のプロセスは環境に属します。そのため iOS バックエンドでは `dead.close()` が
`AttributeError` を投げます。`except Exception` がそれを飲み込み、`invalidate()` は何も破棄せずに戻ります。
これが沈黙したままだった理由は 3 つあります。ログの行が debug レベルにあること。mypy がこの呼び出しを
見ないこと。mypy が走る対象は `bajutsu demos scripts` であり、`tests/` を含みません。そしてハーネス自身の
実機不要のテスト（`tests/runner/test_backend_crash_recovery.py`）では、すべての偽のドライバが `close()`
を定義していることです。つまりそれらのケースは、Playwright のドライバだけが持つ形を動かしており、
これらのスイートが lease する XCUITest のものではありません。

したがって、この破棄を説明している 2 か所は、どちらも起きていないことを説明しています。`invalidate()`
自身の docstring は「次に `driver` を参照したときコールド再起動するよう、現在の（死んだ）lease を破棄
する」と書いています。破棄が再 lease する先の fixture に付いた、フォールト注入スイートのコメントも
「killed-runner のケースはこれを破棄するので、次のケースは死んだ runner を引き継ぐのではなく新しい
デバイスへ再起動する」と書いています。

その帰結は、BE-0334 が取り除こうとした失敗の形そのものです。BE-0334 の論拠は次のとおりです。conformance
スイートの実行中に起きたシミュレータの障害は、原因を作りえなかったプルリクエストの必須チェック
`E2E (iOS)` を赤くしてしまいます。だからハーネスがインフラ障害を分類し、run パイプラインと同じように
復旧するべきです。古い runner を生かしたまま返す破棄は、自身の復旧を次の障害へ変えてしまいます。その
破棄が行う再 lease こそが、デバイスの上に 2 つ目の runner を置くからです。

どのチェックに届くかは、スイートによって違います。上のログが出たフォールト注入のレーンは、`actuation`、
`golden`、`visual` と並んで `E2E (iOS)` の集約の `needs:` から意図的に外されており、そこが赤くなっても
マージは止まりません。だからこの欠陥は、ゲートではない信号の上に現れました。conformance スイートは
同じやり方で lease を破棄し、しかもその集約に入っています。したがって同じ欠陥は、そこでは必須チェックに
届きます。

この問題は、特定のブランチに依存しません。同じテストが同じメッセージで
[run 30971636417](https://github.com/bajutsu-e2e/bajutsu/actions/runs/30971636417) でも失敗しており、
そのブランチの変更はここで述べた仕組みに一切触れていません。進行中の変更による退行として読む解釈は、
これで否定できます。上に引用したログは
[run 30971507268](https://github.com/bajutsu-e2e/bajutsu/actions/runs/30971507268) のものです。

## 詳細設計

作業は 3 つのユニットに分かれます。ユニット 1 が修正で、ユニット 2 はこの問題を隠していた沈黙の類型を
止めるもの、ユニット 3 は両者の退行を防ぐ実機不要のテストです。

1. **runner を所有する環境を、lease から teardown できるようにする。** スイートが渡す起動の thunk は、
   ドライバと並べて teardown を返します。`LeaseHolder` はこれによって、`driver.close()` ではなく
   プラットフォーム自身の teardown を通して破棄します。両スイートの `_backend_launch` はすでに
   `launch_driver` を呼んでいます。`launch_driver` は用意済みの `environment` を受け取れるので、呼び出しの
   たびに thunk がそれを組み立てて渡し、その環境とドライバを捕捉した teardown を返します（module 全体で
   保持される fixture ではありません）。これはプラグインの opt-in の
   契約を広げるので、同じ変更のなかで契約も直します。`LeaseHolder` の `launch` の型と、「新しい
   `base.Driver` を返す引数なしの callable」と定めている module の docstring は、どちらも今はドライバだけを
   指しています。本項目の動機が引用した 2 つの記述は、直す必要がありません。ユニット 1 こそが、その記述を
   本当にするからです。環境は再 lease を跨いで
   保持せず、**lease ごとに新しく**作ります。XCUITest の環境を保持すると、以後のコールド起動がすべて
   その場での再起動になり、本項目が扱っている混み合ったホストの上で、コールドの上限（300 秒）ではなく
   レーンのより厳しい再起動の上限（90 秒）が使われてしまいます。加えて `teardown` に、前の lease の
   ドライバを渡すことにもなります。iOS バックエンドでそのとき走る teardown は、`xcodebuild` のホスト
   プロセスを終了させて runner が住む XCTest のセッションを終わらせ、対象アプリを `simctl` で終了させ
   ます。この仕組みはすでに存在していて、これらのスイートから届く道だけがありませんでした。web の環境が
   持つ `teardown` は、すでにブラウザのコンテキストに対するその `close()` です。したがって lease に
   バックエンドごとの分岐は要りません。

   ユニット 1 は、途中で失敗する起動もカバーしなければなりません。`launch_driver` は `env.start` を呼んで
   ドライバを受け取り、続けて `_await_ready` を呼びます。この `_await_ready` は、runner が readiness の
   確認中に死ぬと例外を投げます（`bajutsu/runner/launch.py:79-82`）。今の実装では、この例外は
   `launch_driver` の外へそのまま伝わり、ドライバを呼び出し元へ返しません。そのため呼び出した thunk は
   環境を持っていても、`env.teardown(driver, eff)` に渡すドライバを持たないままになります。run パイプライン
   はすでにこの同じ seam を守っています。`bajutsu/runner/pool.py:371-383` は `except BaseException:` の
   中で環境を teardown し、それでも元の起動エラーをそのまま伝えます。`launch_driver` 自身がこの同じ保護を
   取り込んで呼び出し元すべてに行き渡らせるのか、それともスイート自身の thunk が呼び出しを包んで自ら
   teardown するのかは、ユニット 1 がまだ決めていない選択であり、実装の細部として省略してよいものでは
   ありません。

2. **守られた teardown を切り出し、走れなかったものを飲み込むのをやめる。** **実行中**の破棄は失敗の
   経路で走るため、そこで例外を投げると原因となった障害を隠してしまいます。捕まえること自体は妥当です。
   妥当でないのは、debug レベルで記録することです。**構造的に**不可能な teardown が、その回だけ失敗した
  teardown と見分けられなくなるからです。メソッドの欠落は、保守する人が最初の実行で気付くべきものです。
   ログのレベルを上げて探す行ではありません。

   何を飲み込んでよいかは、run パイプラインがすでに持っている方針です。パイプラインの 3 つの teardown の
   箇所はいずれも、`CalledProcessError` と `OSError`（すでに終了していた runner や、届かない `xcrun`）を
   warning にし、それ以外は表に出します。この方針をハーネスで書き写すと、同じ 2 つの例外クラスの組が
   ツリーの 4 つ目の写しになり、それらを揃えておくものがコメントしかなくなります。そこで書き写す代わりに、
   守られた teardown を 1 つのヘルパーへ切り出し、プールの 3 箇所と lease の両方がそれを呼びます。置き場所は
   `bajutsu/runner/recovery.py` です。ハーネスはすでにパイプラインの再試行回数と復旧の予算を、書き写しでは
   なく import でこのモジュールから借りています。これによって「2 つの復旧の経路は乖離しない」が、散文の
   約束ではなくコードの性質になります。

   配線の欠陥がどこに現れるかは、どちらの経路がヘルパーを呼んだかで決まります。実行中の破棄では warning に
   飲み込みます。この経路の 3 つの呼び出し箇所のうち 2 つは、フォールト注入のスイートの `finally` の中に
   あります。一方は、テストが検査している `BackendCrashError` そのものを守るものです。残る 1 つはプラグイン
   自身の破棄で、どのテストの外でもない場所で走るため、例外が抜けると 1 件のテストを落とすのではなく
   セッションごと中断させてしまうからです。module の**最後**の解放では、配線の欠陥が
   module の teardown を失敗させます。ここには処理中の障害がなく、生き残った runner はジョブの残りへ漏れて
   しまうからです。

3. **teardown を実機なしで固定する。** このハーネスはすでにシミュレータなしで動かせます。
   `tests/runner/test_backend_crash_recovery.py` が `pytester` と偽の起動 thunk でプラグインを駆動して
   いるので、並行するファイルを新しく作るのではなく、このファイルを拡張します。拡張とは、13 個ある内側の
   起動 thunk をすべて新しい契約へ移すことです。これは、偽のドライバが出荷するドライバから再び乖離するのを
   止めることでもあります。確かめるのは次の 8 つです。`invalidate()` が破棄した lease に対して teardown を
   ちょうど 1 回走らせること。成功した最後の解放でも、ちょうど 1 回走らせること。次の `driver` 参照が新しい
   lease を起動すること。実行中の teardown が `CalledProcessError` または `OSError` を投げた場合、warning
   として報告されること。実行中の teardown がそれ以外を投げた場合も、伝播させずに同じ warning にすること。
   最後の解放では配線の欠陥を伝播させ、`CalledProcessError` と `OSError` には warning のままとすること。
   `env.start` のあとで例外を投げる起動でも、その例外が伝わる前に環境を teardown すること。
   一度も起動していない lease は何も片付けないこと。これらは高速なゲートが走らせる決定的なスイートに置き、
   修正がデバイスなしの Linux 上で保たれるようにします。

Android バックエンドも同じ形をしています（`AdbDriver` にも `close()` がありません）。ただし今日
`LeaseHolder` を通してデバイスを lease するスイートは Android にありません。ユニット 1 の seam は、
Android の実機スイートが将来追加されたときに、それが構成上正しくなるようにするものです。本項目に
シミュレータ固有のものは何もありません。スイートが渡す teardown は、そのプラットフォーム自身のものです。

## 検討した代替案

- **XCUITest のドライバに `close()` を実装する。** 差分は最小で、`LeaseHolder` の変更も要りません。
  しかしプロセスの teardown を誤った seam へ置くことになります。ドライバが runner に届くのはループバックの
  ポート経由で、`xcodebuild` のプロセスのハンドルは持っていません。そのプロセスと、その周りの
  シミュレータのライフサイクルを所有しているのは環境です。したがってドライバの `close()` は、ドライバが
  保持していない環境へ手を伸ばすほかなく、seam を逆転させます。プラットフォームごとの立ち上げを環境の
  背後に置いたのは、ドライバにそれを担わせないためでした
  （[BE-0009](../BE-0009-cross-platform-abstractions/BE-0009-cross-platform-abstractions-ja.md)）。
 teardown をすでに所有している環境を lease に持たせる形を採り、この案は退けます。
- **すべてのドライバに `close()` の実装を必須にする。** この欠落を、沈黙する `AttributeError` ではなく
  型エラーにできる点は魅力があります。一方で `BackendLifecycle` の目的と矛盾します。その docstring 自身が
  「呼び出し側のための*型付けの傘*であって、適合の対象ではない」と述べており、狙いは各フックを呼び出し
  側で mypy が検査する事実にすることです。しかも「ライフサイクルを持たないバックエンドに no-op の
  メソッドを書かせずに」と続きます。出荷する 5 つのドライバのうち 4 つには、ドライバ自身に閉じられる
  ものが存在しません。前の案と同じ理由で退けます。
- **フォールト注入のスイート自身が、残った runner を殺す。** このスイートは自分のフォールト注入のために
  runner のプロセスをコマンドラインから探しており、 teardown にもそれを流用できます。ただし問題が現れた
  1 つのジョブだけを直し、復旧したクラッシュごとに lease を破棄する conformance スイートには同じ欠陥を
  残します。しかもプロセスの管理を、それを所有する seam ではなくテストのファイルへ移すことになります。
  1 つの症状に対する回避策として退けます。
- **どちらのスイートもふだんは通るので、そのままにする。** 実際、多くの実行では通ります。この欠陥が
  噛むのは lease を破棄したときだけで、そのためにはまずクラッシュか注入したフォールトが必要です。
  それでも噛んだときに生むのは、原因を作りえなかったプルリクエストの赤いチェックです。BE-0334 が防ぐ
  ために存在する結末そのものが、BE-0334 自身の復旧の経路を通って現れます。フォールト注入のレーンでは
  そのチェックはマージを止めませんが、同じやり方で lease を破棄する conformance スイートでは、それが
  必須の `E2E (iOS)` です。退けます。

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
  ひとつの thunk ファクトリを共有します（バックエンド非依存な `backend_crash_recovery` プラグインの
  外に置きました）。この thunk が lease ごとに新しい environment を作り、driver と一緒にその teardown を
  返すようになりました。これにより `LeaseHolder` は `driver.close()` ではなく、プラットフォーム自身の
  teardown を通して lease を破棄します。ユニット 1 が残していた選択は、`launch_driver` 自身がガードを
  引き受ける方向で決着し、すべての呼び出し側がそれを受け継ぎます。守られた teardown の方針は
  `bajutsu/runner/recovery.py` へ移り、pool の 3 か所、`launch_driver`、lease の破棄が共有します。
  実行中の配線の欠陥は、debug ではなく warning で報告されるようになりました。状態を実装済みへ。

## 参考

- [BE-0334 — 実機 conformance スイートに run パイプラインと同じインフラ障害からの復旧を持たせる](../BE-0334-conformance-suite-infra-fault-recovery/BE-0334-conformance-suite-infra-fault-recovery-ja.md) — lease とその破棄を作った項目。
- [BE-0114 — backend 非依存の挙動を検査する driver conformance suite](../BE-0114-driver-conformance-suite/BE-0114-driver-conformance-suite-ja.md) — lease を修正する対象のスイート。
- [BE-0305 — ドライバ耐障害経路への実機障害注入カバレッジ](../BE-0305-driver-resilience-fault-injection/BE-0305-driver-resilience-fault-injection-ja.md) — 本項目が読むログを残したフォールト注入のスイートを作った項目。
- [BE-0009 — 抽象のクロスプラットフォーム化](../BE-0009-cross-platform-abstractions/BE-0009-cross-platform-abstractions-ja.md) — runner のプロセスを所有する環境の seam。
- `tests/backend_crash_recovery.py` — `LeaseHolder`、その `invalidate()`、試行のあいだに再 lease するプラグイン。
- `tests/runner/test_backend_crash_recovery.py` — ユニット 3 が拡張する実機不要のテスト。
- `bajutsu/drivers/base.py` — `close()` を web のドライバだけが実装している `BackendLifecycle` Protocol と、`close()` を宣言していない `Driver` Protocol。
- `tests/test_driver_conformance_ondevice.py`、`tests/test_fault_injection_ondevice.py` — `LeaseHolder` を通してデバイスを lease する 2 つのスイート。
