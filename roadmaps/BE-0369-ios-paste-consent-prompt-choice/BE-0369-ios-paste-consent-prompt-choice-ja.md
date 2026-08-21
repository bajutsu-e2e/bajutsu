[English](BE-0369-ios-paste-consent-prompt-choice.md) · **日本語**

# BE-0369 — iOS システムアラートの prompt/choice 対応表にペースト許諾プロンプトを追加する

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0369](BE-0369-ios-paste-consent-prompt-choice-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0369") |
| 実装 PR | [#1652](https://github.com/bajutsu-e2e/bajutsu/pull/1652) |
| トピック | プラットフォーム対応 |
| 関連 | [BE-0320](../BE-0320-ios-system-alert-locale-determinism/BE-0320-ios-system-alert-locale-determinism-ja.md)、[BE-0316](../BE-0316-ios-permission-alert-step/BE-0316-ios-permission-alert-step-ja.md)、[BE-0315](../BE-0315-ios-native-system-alert-handling/BE-0315-ios-native-system-alert-handling-ja.md)、[BE-0052](../BE-0052-device-state-timezone-clipboard-shake/BE-0052-device-state-timezone-clipboard-shake-ja.md)、[BE-0276](../BE-0276-scenario-permission-state/BE-0276-scenario-permission-state-ja.md) |
<!-- /BE-METADATA -->

## はじめに

`handleSystemAlert`([BE-0316](../BE-0316-ios-permission-alert-step/BE-0316-ios-permission-alert-step-ja.md))
向けに BE-0320 が用意した、ロケールに依存しない `prompt` / `choice` の対応表に、この項目は3つ目のプロンプト
`paste` を追加します。ペースト許諾プロンプトは、`UIPasteboard.general` の内容を別プロセスから読み取る際に
iOS が表示するシステムアラートで、読み取りを許可するか拒否するかをその場で尋ねます。BE-0320 は、この対応表の
対象を意図的に2つのプロンプト(通知許可と App Tracking Transparency (ATT))に絞りました。
[BE-0276](../BE-0276-scenario-permission-state/BE-0276-scenario-permission-state-ja.md)
の事前権限設定がどちらも事前に答えられないプロンプトだったからです。ペースト許諾プロンプトも同じ系列に属します。
`simctl privacy` はこのプロンプトに届かず、ほかの事前設定で答えられることも知られていません。「他のアプリ
からのペースト」というアプリごとの設定をアプリの外から事前に設定できるかどうかは、ユニット3で確かめる未解決の
問いです。そのため許可または拒否したい
作成者は今のところ、ロケールに依存する文字通りの `sel: { label: "Allow Paste" }` しか使えません。この項目の
貢献は2つあります。1つは、BE-0320 が最初の2つのプロンプトについて埋めたのと同じやり方で、このラベル対応表の
欠落を埋めることです。もう1つは、アプリの外からペーストボードへ書き込んでから内側で読み戻すという、実際に別
プロセスを経由するペーストを検証する最初の showcase フィクスチャを追加することです。Bajutsu 自身の
Permissions タブは今のところ、まさにこのケースを避けて通っています。

## 動機

実際のペーストの流れでは、テスト対象アプリの外側にある何かが書き込んだ内容を読み取ります。Notes からコピー
したクーポンコード、Safari から共有したリンク、あるいは bajutsu のシナリオ自身が使う BE-0052 の
`setClipboard`(アプリのプロセスの外から Simulator のペーストボードへ書き込む仕組み)などです。iOS は
まさにこのケースにシステムアラートで応じます。iOS 16 以降、直前に別のプロセスが書き込んだペーストボードの
内容を読み取ろうとすると、読み取りを許可するか拒否するかを尋ねる確認ダイアログが表示されます。ただし書き込み
元が同一のアプリである場合を除きます。アプリの外からペーストボードへ書き込み、テスト対象アプリの内側でそれを
読み戻すシナリオは、そのままこのペースト許諾プロンプトに突き当たります。

このプロンプトに事前に答える手段は知られていません。BE-0276 の権限プリセットは、アプリのプロセスが起動する前に、
`simctl privacy` を通じて Transparency, Consent, and Control (TCC) の状態を書き込みます。しかしペースト許諾
プロンプトは、通知許可(そもそも TCC のサービスではありません)や ATT(`kTCCServiceUserTracking` として TCC に
属しますが、`simctl` のトグルがありません)と同じく、`simctl privacy` の手が届きません。
[BE-0315](../BE-0315-ios-native-system-alert-handling/BE-0315-ios-native-system-alert-handling-ja.md)
は、まさにこの2つを `simctl privacy` が届かないプロンプトとして挙げており、ペースト許諾プロンプトも同じ分類
に属します。したがって唯一知られている決定論的な仕組みは、BE-0315 と BE-0316 がすでにこの2つのために用意した
仕組み、すなわち SpringBoard アラートへのネイティブなタップです。ほかのすべての selector と同じ
`resolve_unique` の規律で解決します。

この仕組みは、BE-0316 が出荷された時点から、ペースト許諾プロンプトを一般的に解消できる状態にありました。
`bajutsu/agents/alerts.py` は、ビジョンガードが認識する例としてすでに "Allow Paste" を挙げていますし、
シナリオは今日でも `handleSystemAlert: { sel: { label: "Allow Paste" } }` や
`systemAlertHandling: { instruction: ["Allow Paste"] }` と書けます。ただし BE-0320 が Simulator のシステム
言語を固定しているのは、`handleSystemAlert` のラベル一致が文字通りの一致だからであり、英語の文字列は英語
ロケールの Simulator でしか解決しません。これは BE-0320 が通知許可と ATT について、`grant` と `deny` を
手で書き写すのではなく Simulator 自身が出荷する文字列から解決することで埋めた、まさにその脆さです。ペースト
許諾プロンプトは今のところ、この脆さをそのまま受け継いでいます。

しかも、その文字通りの経路すら、まだ一度も検証されていません。
[`demos/showcase/ios/swiftui/Sources/PermissionsView.swift`](../../demos/showcase/ios/swiftui/Sources/PermissionsView.swift)
自身のコメントは、別プロセスが書き込んだペーストボードを読み取るとこのプロンプトが表示されると明言しています。
そのうえで showcase の System セクションは、このプロンプトを意図的に避けています。`Copy` ボタンと `Paste`
ボタンは同じアプリの中で書き込みと読み取りを完結させるため、往復はこのプロンプトを一切表面化させません
(ソースコード自身のコメントの言葉を借りれば、値が「静かに読み戻される」だけです)。実際に別プロセスを経由するペースト、
まさに BE-0052 の `setClipboard` が存在する理由になっているケースを確かめたい作成者には、今日のところ手本に
なるシナリオが1つもありません。

## 詳細設計

作業は5つに分かれます。ロケール別対応表の拡張、既存の Permissions タブを使った showcase フィクスチャの作成、
実機(実 Simulator)での検証、ドキュメントの更新、そして新しい項目を高速なテストスイートで押さえることです。
このうち検証は最初から独立したユニットとして扱います。BE-0052 の `setClipboard` が、実際のアプリ間ペーストと同じようにこのプロンプトを引き起こすかどうかを、
この提案自身ではまだ確認していないからです。

1. **`bajutsu/scenario/system_alerts.py` のプロンプト対応表に3つ目の項目を追加します。** `SystemAlertPrompt`
   という `Literal` 型に、今日の `"notifications"` と `"tracking"` に加えて `"paste"` を追加します。
   `_Prompts` という `TypedDict` にも対応する `paste` キーを追加し、ほかの2つと同じように、中途半端に埋まった
   項目を型エラーにします。`_LABELS["paste"]` には、言語のサブタグごとの `grant` / `deny` のラベルの組を
   持たせます。このモジュール自身の docstring は、今日の2つのプロンプトそれぞれについて、どのフレームワーク
   のどの `Localizable.strings` のキーから文言を書き写したかを明記しています(`UserNotificationsServer.framework`
   と `TCC.framework`)。実装するセッションは、3つ目の項目を書く前に、ペースト許諾アラートについても同じやり方
   で出荷済みの文言を見つけ出す必要があります。文言を推測してはいけません。この対応表がユニット3で実機に
   よって確認されるのは、まさにそのためです。このモジュールの docstring、`SystemAlertPrompt` の上のコメント、
   そして `bajutsu/scenario/models/actions.py` の `HandleSystemAlert` 自身の docstring は、いずれも「2つの
   プロンプト」と明記しています。`make check` はこの記述を `Literal` と突き合わせないため、同じ変更で3箇所とも
   書き換えます。そうしなければ、ユニット1は docstring が自身の型と矛盾したモジュールを出荷することになります。
   モジュールのそれ以外の振る舞いは変わりません。`system_alert_label`
   の解決、`UncoveredSystemAlertLocale` という失敗の扱い、`covered_languages` はいずれも、本体を変えなくても
   3つ目のプロンプトへそのまま一般化します。`HandleSystemAlert`(`bajutsu/scenario/models/actions.py`)も、
   すでに import している型を広げる以外にスキーマの変更は要りません。`sel` / `label` / `labelMatches` /
   `index` は、この対応表が扱わないすべてのアラートに対して、すでに2つのプロンプトについてそうしているのと
   同じく、今までどおり機能します。
2. **アプリの外からこのプロンプトへ到達するシナリオを、新しいアプリ側 UI を足すのではなく、既存のフィクス
   チャを再利用して用意します。** Permissions タブの既存の `sys.paste` ボタンは、`Copy` ボタンが先に押されて
   いることに依存せず、`UIPasteboard.general.string` を無条件に読み取ります
   ([`PermissionsView.swift`](../../demos/showcase/ios/swiftui/Sources/PermissionsView.swift)、
   [`PermissionsController.swift`](../../demos/showcase/ios/uikit/Sources/PermissionsController.swift))。
   そのため `Copy` の代わりに `setClipboard` でペーストボードを書き込むだけで、同じボタンが別プロセス経由の
   プロンプトを引き起こすようになります。アプリ側の新しいコントロールも BajutsuKit の変更も要らず、新しい
   シナリオファイルを1つ足すだけで済みます。

   ```yaml
   - name: grant the paste-consent prompt with handleSystemAlert
     tags: [systemalert]              # ローカルの一括実行から外すためのタグ
     systemAlertHandling: false       # ガードを切ることで、タップ失敗が run の失敗になる(BE-0316 の考え方)
     preconditions:
       launchEnv: { SHOWCASE_UITEST: "1" }
     steps:
       - setClipboard: { text: "bajutsu-cross-clip" }                    # アプリの外から書き込む(BE-0052)
       - tap: { label: "Permissions", traits: [button] }
       - scroll: { to: { id: [sys.paste.value, sys_paste_value] }, direction: down }
       - tap: { id: [sys.paste, sys_paste] }                              # アプリが UIPasteboard.general.string を読み取る
       - handleSystemAlert: { prompt: paste, choice: grant, timeout: 10 } # 許諾プロンプトを承認する
       # アプリが値を公開するのは読み取りが返ったあとで、アラートとは別プロセスの出来事です。
       # 検証と競合させず、条件待ちで受けます(第二の指針)。
       - wait: { for: { id: [sys.paste.value, sys_paste_value], value: "bajutsu-cross-clip" }, timeout: 10 }
     expect:
       - value: { sel: { id: [sys.paste.value, sys_paste_value] }, equals: "bajutsu-cross-clip" }
   ```

   [BE-0316](../BE-0316-ios-permission-alert-step/BE-0316-ios-permission-alert-step-ja.md) の前例に
   ならい、このシナリオは既存の `system.yaml` や `permission.yaml` に加えるのではなく専用のファイルにとどめ、
   BE-0316 自身が作った `systemalert` タグを再利用します。Android の `smoke (adb)` ジョブは固定されたシナリオ
   の一覧を実行するため、このファイルを名指ししない限り実行対象になりません。同じタグは、シナリオ単位の権限
   状態をリセットしないローカルの一括実行(`run-swiftui` / `run-uikit`)からも、すでにこのフィクスチャを除外
   しています。CI へは、ジョブの `scenarios:` の一覧に新しいファイルを名指しして組み込みます。CI のジョブは
   どれもシナリオファイルを明示的に名指ししており、タグを付けるだけでは何も実行対象に加わらないからです。
   最初に置くのは、ゲートではない `actuation (xcuitest)` ジョブです。このジョブは、まだ gating の `run`
   ジョブに入る資格を得ていない実機カバレッジの着地場所であり(BE-0218)、`setClipboard` を使う `device.yaml`
   もここで動いています。`run` の一覧へは、デバイスの状態を乱さないよう権限系のシナリオの後ろに置く形で、
   ユニット3がプロンプトの表示を実証してから昇格させます。いきなり `run` に置けば、この項目自身が未検証と
   認めている機構に必須の `E2E` ゲートを預けることになります。プロンプトが出なければ `handleSystemAlert`
   ステップがタイムアウトし、無関係なすべての PR で必須チェックを赤くしてしまいます。どちらのジョブに置く
   場合も、同じ変更で、そのジョブが実行するファイル数とシナリオ数を数えているヘッダーのコメントも更新します。
3. **実機で検証し、検証が成り立たなかった場合の代替案も名指しします。** ここには、off-Simulator のゲートで
   は証明できない、確認できていない事実が2つあります。1つは、`simctl pbcopy` による書き込みが「別プロセス」
   として十分な強さで扱われ、iOS がそもそもこのプロンプトを出すかどうかです(Simulator 自身の制御チャネルを
   通した書き込みは、あるアプリが別のアプリへペーストボードの内容を渡すのと同じコード経路ではありません)。
   もう1つは、この対応表が対象とする各言語について、iOS のペースト許諾アラートが実際に出荷しているボタンの
   文言です。実装するセッションは、実機の Simulator を起動してユニット2が示す手順を実行し、プロンプトが実際
   に表示されることを確かめてから、その文言をユニット1の対応表へ書き写す必要があります。もし `setClipboard`
   がこのプロンプトを引き起こさないとわかった場合、ユニット2のフィクスチャには、ペーストボードへ本当に別
   プロセスとして書き込む仕組みが必要になります。これはより重いフィクスチャ(2つ目の demo ターゲット、ある
   いは bajutsu 自身のデバイス制御チャネルの外で行う何らかの別プロセスの書き込み)であり、この項目はそれを
   実装するセッションのための未解決の設計上の問いとして残します。BE-0052 自身が、`setTimezone` と `shake`
   について信頼できる仕組みが見つからなかったときに、それぞれ別の項目へ切り出したのと同じ考え方です。うまく
   いくように見えるだけのステップは出荷しません。実機の Simulator がすでに起動しているあいだに、同じセッション
   は「他のアプリからのペースト」という設定(後述の「検討した代替案」を参照)を、たとえば対象アプリの
   identifier に対する `defaults write` で、アプリの外から事前に設定できる
   かどうかも、コストをかけずに確かめるべきです。もし事前に設定できるとわかった場合は、この項目が防止策を
   退けた判断を検証しないまま放置せず、その旨を報告してください。
4. **ドキュメント。** [`docs/scenarios.md`](../../docs/scenarios.md#naming-the-intent-instead-of-the-text)
   の「テキストではなく意図で指定する」節と、[`docs/dsl-grammar.md`](../../docs/dsl-grammar.md) の
   `handleSystemAlert` の生成規則(このフィールドを `prompt: notifications|tracking` と書き出しています)、
   そして両方の日本語訳に、ユニット3が文言を確認した時点で `notifications`、
   `tracking` と並べて `paste` を追加します。`make check` はこの生成規則をモデルと突き合わせないため、
   `scenarios.md` だけを対象にすると、`paste` を黙って省いたまま、スキーマが受け付けないかのように読める
   リファレンスが残ります。`docs/scenarios.md` がすでに述べている2つの限界(言語の固定が Simulator 専用で
   あること、リアクティブなガードの初期値が英語のままであること)は、この3つ目のプロンプトにもそのまま
   当てはまるため、あらためて書き直す必要はありません。
5. **テスト。** [`tests/scenario/test_system_alerts.py`](../../tests/scenario/test_system_alerts.py)
   で `paste` を検証します。このファイルは今日のプロンプトを直接書き出しており、
   `test_every_covered_language_answers_both_choices` は `("notifications", "tracking")` を回し、
   ラベルの期待値は手書きの `parametrize` の一覧です。このファイルに触れずに `_LABELS` へ項目を足すと、
   gate は緑のまま、中途半端に埋まった項目を防ぐ検査(`grant` が `KeyError` を投げるあいだ `deny` だけが
   解決してしまう事態を止める検査)が新しいプロンプトを一切見ません。ユニット3の実機検証では代われません。
   `make check` は Simulator のない Linux で走るからです。

## 検討した代替案

- **名指ししたプロンプトを1つずつ広げるのではなく、任意の SpringBoard アラートまで対応表を広げる案。**
  不採用です。BE-0316 自身が「権限プロンプトだけでなく、あらゆる SpringBoard アラートを扱う」という広げ方を
  退けた理由と同じです。際限のない翻訳表は、Apple が出荷する正確な文言をあらゆるアラート、あらゆるロケール
  について追い続ける必要が生じ、BE-0320 が自分自身の2つのプロンプトについて退けたのと同じ保守負担を抱え
  ます。名前を付けたプロンプトを1つずつ、実機で独立に検証できる形で追加するほうが、鵜呑みにせずに
  済みます。
- **汎用の `sel: { label: "Allow Paste" }` という文字通りの指定だけに頼る案(今日の現状)。** 唯一の答えとして
  は不採用です。この案は、BE-0320 が通知許可と ATT について埋めたのとまったく同じ脆さ、英語の文字列が英語
  ロケールの Simulator でしか解決しないという脆さを、実際に許可したくなるプロンプトについてそのまま受け継ぎ
  ます。
- **`setClipboard` の代わりに、専用の2アプリ構成のフィクスチャを組む案。** 2つ目の demo ターゲットが本当に
  別プロセスとしてペーストボードへ書き込めば、`simctl pbcopy` が「別プロセス」として十分に扱われプロンプトを
  引き起こすかというユニット3の未解決の問いを避けられます。最初から採用するのではなく、ユニット3がすでに
  名指ししている代替案として保留します。より重いフィクスチャであり、`setClipboard` がもし機能するなら新しい
  demo ターゲットは要らないため、まず試す価値があります。
- **BE-0276 が TCC に基づく権限プロンプトを防いでいるのと同じように、このプロンプトそのものを防ぐ案。**
  不採用です。BE-0315 が通知許可と ATT について同じ案を退けた理由と同じです。iOS 16.1 以降、設定アプリは
  アプリごとに「他のアプリからのペースト」という設定を用意しており(確認する、許可する、許可しないの3つから
  選べます)、まさにこのアラートを制御しています。ただし、この設定を Simulator 上でアプリの外から事前に
  設定できるかどうかは、BE-0320 が `AppleLanguages` を事前に設定しているのと同じ意味で、わかっていません。
  それが確かめられるまでは、リアクティブなタップが今のところ唯一機能するとわかっている仕組みです。事前設定が
  可能だとわかれば、そのときはあらためて別の項目で防ぐ手段を扱えます。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [x] ユニット1 — `bajutsu/scenario/system_alerts.py` に3つ目のプロンプト `paste` を追加しました(スキーマ、
      `_Prompts` / `_LABELS`、`HandleSystemAlert` の型を広げる)。あわせて「2つのプロンプト」と書いている
      モジュールの docstring、`SystemAlertPrompt` の上のコメント、`HandleSystemAlert` の docstring も書き換えました。
- [x] ユニット2 — `demos/showcase/scenarios/paste_system_alert.yaml` を追加しました。`setClipboard` で
      ペーストボードを書き込み、既存の Permissions タブを通じて読み戻します。BE-0316 のフィクスチャと同じく
      `systemalert` タグを付け、ゲート対象外の `actuation (xcuitest)` ジョブに置いています。gating の `run` への
      昇格は意図的に後続の作業へ回しました。このレーンで初めてプロセスをまたぐペーストなので、必須チェックが
      これに依存する前に、CI 自身のホストで安定性を確かめるためです(BE-0218)。フィクスチャには設計が見通して
      いなかった変更が1つ必要でした。ユニット3を参照してください。
      その後の作業でフィクスチャを4本に広げました。対象の locale のもとでの `grant` と `deny`、続けて
      `locale: ja_JP` のもとでの同じ2本です。許可する1本だけでは、英語の拒否側のラベルと、日本語の
      2つのラベルを、ユニット5の表だけで確かめている状態が残るからです。拒否側には showcase の
      変更がもう1つ必要でした。拒否された読み取りは nil を返すため、アプリは `""` ではなく `(none)` を
      公開します。`(none)` の公開により、拒否された読み取りが、待って確かめられる条件になります。
- [x] ユニット3 — 起動中の Simulator(iPhone 17、iOS 26.5)で、SwiftUI と UIKit の両方の showcase アプリに対し、
      `en_US` と `ja_JP` の両方で検証しました。
- [x] ユニット4 — ドキュメント: `docs/scenarios.md` の「テキストではなく意図で指定する」節、
      `docs/dsl-grammar.md` の `handleSystemAlert` の生成規則、`docs/ci.md` の `actuation` ジョブの説明に
      `paste` を追加し、3つとも日本語訳も更新しました。`demos/showcase/SPEC.md`(日英両方)には、ユニット3が
      要求したメインスレッド外での読み取りを記載しました。
- [x] ユニット5 — テスト: `tests/scenario/test_system_alerts.py` で `paste` を検証します。半端な項目を
      検出するガードは、手書きの組ではなく `SystemAlertPrompt` 自身からプロンプトを取るようにしました。
      4つ目のプロンプトを `_LABELS` に足しても、ガードが3つしか見ないままになることはありません。

### ユニット3 が明らかにしたこと

提案が未確定としていた3点と、想定していなかった1点です。

- **`setClipboard` はプロンプトを引き起こします。** iOS は `simctl pbcopy` の書き込みを別プロセスの
  `CoreSimulatorBridge` に帰属させるため、アプリ自身の読み取りが「"Showcase SwiftUI" would like to paste
  from "CoreSimulatorBridge"」を上げます。重い2アプリ構成の代替フィクスチャは不要でした。同意は読み取りの
  たびに求められるので、1台の端末で何度でも実行できます。
- **ボタンの文字列**は `DragUI.framework/<lang>.lproj/Localizable.strings` の
  `PASTE_AUTHORIZATION_BUTTON_ALLOW` / `_DENY` から来ます。モジュールがすでに挙げている2つの並びに立つ、
  3つ目のフレームワークです。値は `Allow Paste` / `Don’t Allow Paste` と `ペーストを許可` /
  `ペーストを許可しない` です。iOS 18.6 と 26.5 のランタイム、`en` / `en_GB` / `en_AU` のいずれでも同一でした。
  英語の拒否ラベルには、活字体のアポストロフィ(U+2019)が入ります。この対応表が存在する理由そのものです。
- **この設定はアプリの外から事前に設定できません。** iOS は同意を TCC の `kTCCServicePasteboard` として
  記録します。しかし `simctl privacy` はこのサービスの切り替え手段を持ちません。ATT とまったく同じ形です。
  Simulator の `TCC.db` を直接書き換えてもプロンプトは抑止されず、システムが行を元に戻しました。
  *検討した代替案* が予防的な機構を退けた判断は、未解決の問いではなく実測に立つものになりました。
- **同期的な読み取りは、設計が提案したフィクスチャをデッドロックさせます。** `UIPasteboard.general.string` は
  プロンプトが出ているあいだ呼び出し元をブロックします。showcase がメインスレッドで読んでいたため、
  `sys.paste` への XCUITest の tap が返らず、run は実行中のランナークラッシュと診断しました。プロンプトに
  応答するはずの `handleSystemAlert` ステップは、そもそも実行されません。修正は showcase アプリごとに1箇所で、
  メインスレッド外で読み、読み取りが返った時点で値を公開します。設計自身の `wait` ステップがすでに前提として
  いた挙動です。`system.yaml` にも対応する条件待ちを足しました。アプリ内で完結する往復も非同期になったためです。

## 参考

- [BE-0320 — iOS システムアラートのボタン選択を Simulator のシステム言語によらず決定論的にする](../BE-0320-ios-system-alert-locale-determinism/BE-0320-ios-system-alert-locale-determinism-ja.md) —
  この項目が3つ目のプロンプトを追加して広げる `prompt` / `choice` 対応表です。
- [BE-0316 — iOS 権限プロンプトのアラートを扱う、シナリオ途中の明示的なステップ](../BE-0316-ios-permission-alert-step/BE-0316-ios-permission-alert-step-ja.md) —
  `handleSystemAlert`、そしてあらゆるアラートを一度に扱おうとする広げ方を退け、名前を付けた1つのプロンプト
  ずつ追加するという前例です。
- [BE-0315 — BE-0316 の SpringBoard 経路を再利用し、リアクティブなアラートガードを決定論的かつネイティブに
  する](../BE-0315-ios-native-system-alert-handling/BE-0315-ios-native-system-alert-handling-ja.md) —
  この項目のフィクスチャが検証するネイティブな解消経路、そして TCC の届かないプロンプトにはリアクティブな
  仕組みが必要だという前例です。
- [BE-0052 — デバイス状態のプリミティブ: タイムゾーン、クリップボード、シェイク](../BE-0052-device-state-timezone-clipboard-shake/BE-0052-device-state-timezone-clipboard-shake-ja.md) —
  この項目のフィクスチャが土台にする、別プロセスからペーストボードへ書き込む `setClipboard`、そして仕組みが
  本当に機能するかどうかを決めつけずに残すという前例です。
- [BE-0276 — シナリオ単位で宣言する権限状態](../BE-0276-scenario-permission-state/BE-0276-scenario-permission-state-ja.md) —
  このプロンプトが外れている、事前設定される TCC のプリセットです。通知許可や ATT と同じです。
- [`bajutsu/scenario/system_alerts.py`](../../bajutsu/scenario/system_alerts.py) — この項目のユニット1が
  拡張する、ロケール別の対応表です。
- [`bajutsu/scenario/models/actions.py`](../../bajutsu/scenario/models/actions.py) — `HandleSystemAlert`。
  `prompt` フィールドが新しい `Literal` 型に広がります。
- [`demos/showcase/scenarios/permission_system_alert.yaml`](../../demos/showcase/scenarios/permission_system_alert.yaml) —
  ユニット2が倣う、フィクスチャファイルと CI 組み込みの前例です。
- [`demos/showcase/ios/swiftui/Sources/PermissionsView.swift`](../../demos/showcase/ios/swiftui/Sources/PermissionsView.swift)、
  [`demos/showcase/ios/uikit/Sources/PermissionsController.swift`](../../demos/showcase/ios/uikit/Sources/PermissionsController.swift) —
  ユニット2がアプリ側のコード変更なしに再利用する、既存の `sys.paste` / `sys.paste.value` のコントロールです。
