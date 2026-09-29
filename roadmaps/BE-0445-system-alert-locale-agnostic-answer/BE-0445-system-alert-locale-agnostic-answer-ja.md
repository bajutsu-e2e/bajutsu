[English](BE-0445-system-alert-locale-agnostic-answer.md) · **日本語**

# BE-0445 — システムアラートをボタンの役割で答え、Simulator の言語を問わないようにする

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0445](BE-0445-system-alert-locale-agnostic-answer-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0445") |
| 実装 PR | [#2090](https://github.com/bajutsu-e2e/bajutsu/pull/2090)（作業単位 1〜5。項目を完了） |
| トピック | プラットフォーム対応 |
| 関連 | [BE-0315](../BE-0315-ios-native-system-alert-handling/BE-0315-ios-native-system-alert-handling-ja.md)、[BE-0316](../BE-0316-ios-permission-alert-step/BE-0316-ios-permission-alert-step-ja.md)、[BE-0320](../BE-0320-ios-system-alert-locale-determinism/BE-0320-ios-system-alert-locale-determinism-ja.md)、[BE-0382](../BE-0382-system-alert-per-prompt-rules/BE-0382-system-alert-per-prompt-rules-ja.md)、[BE-0406](../BE-0406-system-alert-declared-prompts/BE-0406-system-alert-declared-prompts-ja.md) |
<!-- /BE-METADATA -->

## はじめに

`handleSystemAlert` ステップとリアクティブな `systemAlertHandling` ガードは、iOS のシステムプロンプト（「通知を許可」など）を、ボタンをタップして片付けます。現在はボタンのラベルを引く表でそのボタンを見つけますが、表が持つ言語は英語と日本語の2つだけです。それ以外の `locale` では、ステップは `UncoveredSystemAlertLocale` で失敗します。`systemAlertHandling.rules` でプロンプトを指定したシナリオも、ランナーがルールを解決する時点で、デバイスを操作する前に同じく失敗します。

本項目は、答え方から言語を取り除きます。ボタンが何をするか（許可か拒否か）を、言語で変わらない性質から識別します。すると、`handleSystemAlert` ステップは、どの `locale` でも同じ方法でプロンプトに答えられます。ガードはラベルの表を使い続けます。ガードはまず、宣言したどのプロンプトが画面にあるかを見分ける必要があり、ラベルなしではそれができないからです。ラベルの表は、そうした性質を持たないプロンプトのフォールバックとしてだけ残します。実装に入る前に、SpringBoard（これらのプロンプトを描画する iOS のシステムプロセス）が実際にどの性質を公開するかを計測する作業単位を置きます。

## 動機

[BE-0320](../BE-0320-ios-system-alert-locale-determinism/BE-0320-ios-system-alert-locale-determinism-ja.md) は、Simulator のシステム言語をシナリオの `locale` に固定し、言語をキーにした表からラベルを引くことで、答えを決定的にしました。この方法が効くのは、表にある言語だけです。`bajutsu/common/scenario/system_alerts/_functions.py` が持つのは `en` と `ja` の項目だけで、`locale: fr_FR` のシナリオは `UncoveredSystemAlertLocale` を送出します。どの言語でもプロンプトを消したい作者は、その言語の行を自分で足すことになります。

表はシナリオの記述ではなく、Apple の文言に合わせて増えていきます。iOS のリリースごとにボタンの文言が変わりえます。パスワード保存プロンプトの2つの形が、すでにその例です。保守の負担は言語、プロンプト、iOS バージョンごとに生じ、行が正しいかどうかは実機の Simulator でしか確かめられません。

BE-0320 は、位置による選択を唯一の仕組みにすることを退けました。SpringBoard がボタンをどの言語でも同じ順に並べるかを、誰も検証していなかったからです。本項目は、これを止まる理由ではなく未解決の計測課題として扱います。順序などの性質が言語をまたいで保たれるなら、その懸念は消えます。保たれないなら、本項目はそう記録し、表を残します。

変更が届いたかどうかは、通知許可のシナリオを `locale: fr_FR`（`en` と `ja` 以外の任意の言語）で実行すれば分かります。変更前はステップが `UncoveredSystemAlertLocale` で失敗します。変更後はプロンプトが閉じられ、レポートにタップした役割が残ります。

## 詳細設計

作業は、SpringBoard が公開するものの計測、そこからの役割の解決、ステップでの役割の利用、タップした役割の報告、実機での検証に分かれます。

1. **言語に依存しないボタンの性質を計測します。** 対象の各プロンプト（notifications、tracking、paste）について、右から左へ書く言語を1つ含む5言語以上に固定した Simulator を起動し、全ボタンの序数、フレーム、アクセシビリティ識別子、要素の種類、値を書き出します。本番のクエリではこの計測ができません。`XcuitestElementProvider.querySystemAlertButtons` が読むのはラベルとフレームだけで、`identifier` と `value` には `nil` を、`traits` には固定の `button` を入れるからです。そこで計測には、各 `XCUIElement` をすべて読む専用のプローブを使います。本番のクエリを変えるのは、計測で安定と確かめた性質についてだけです。言語と iOS バージョンをまたいで同一の性質を記録します。パスワード保存プロンプトは SpringBoard ではなくアプリ内に描画されるので、ラベルの表に残します。結果は本項目の「進捗」に表として残し、作業単位2から4の前提とします。言語で変わる性質は、回避策を探さずに捨てます。
2. **安定した性質から役割を解決します。** `system_alert_label` の隣に、`(prompt, choice)` を、作業単位1で見つけた安定した性質の選択規則（例：「2個のうち序数1のボタン」）へ写す解決処理を加えます。ステップでは作者がプロンプトを明示するので、プロンプトの識別は要りません。`Driver` の境界は、ラベル以上の情報を運ぶ必要があります。`system_alert_labels()` は文字列だけを返し、`handle_system_alert` はセレクタでタップするので、本作業単位は両方を全バックエンドで拡張します。ステップの解決も、補間時（`_resolve_system_alert`）から、表示中のアラートを問い合わせた後へ移します。
3. **役割はステップで使い、ガードはラベルの表のままにします。** `handleSystemAlert` ステップは、`prompt` と `choice` をまず役割の規則で、次にラベルの表で解決します。リアクティブガードは変えません。ガードのルール（[BE-0382](../BE-0382-system-alert-per-prompt-rules/BE-0382-system-alert-per-prompt-rules-ja.md)）はプロンプトを指定し、ガードは画面上のアラートをそれと照合する必要があります。通知、トラッキング、ペーストの各プロンプトはどれもボタンが2個なので、ラベルなしではどれが表示中かを区別できない可能性があります。そのため、ラベルの表にない言語では、ガードのルールはこれまでどおり `UncoveredSystemAlertLocale` で明示的に失敗します。どのルールにも当てはまらないアラートに、役割でタップすることはしません。[BE-0406](../BE-0406-system-alert-declared-prompts/BE-0406-system-alert-declared-prompts-ja.md) はそうした推測によるタップを取り除き、BE-0320 も表にない言語では明示的に失敗させると決めています（プライム・ディレクティブ2）。ステップで `UncoveredSystemAlertLocale` が残るのは、ボタンが安定した性質を持たないプロンプトだけで、メッセージにもその旨を書きます。
4. **タップした役割を報告します。** タップしたボタンのラベルと、それを選んだ規則を、`handleSystemAlert` ステップの結果に記録します。ステップの結果は、現在どちらも持っていません。見慣れない言語の実行でも、何をなぜタップしたかが分かります。ガードの `AlertEvent.label` はガードがタップしたボタンをすでに記録しており、ガードには報告すべき役割の規則が加わりません。
5. **実機の Simulator で検証します。** showcase の権限シナリオを `en_US`、`ja_JP`、`fr_FR`、`ar_SA` で実行し、`grant` と `deny` が異なるボタンをタップし、それぞれに対応する認可状態がアプリ側に残ることを確かめます。`fr_FR` でガードのルールが、これまでどおりデバイスを操作する前に明示的に失敗することも確かめます。`docs/configuration.md` とシナリオのドキュメントを、両言語で更新します。

本項目は BE-0320 の Simulator 言語の固定を変えず、モデル呼び出しも加えません。判定は決定的なランナーが下したままです。

## 検討した代替案

- **ラベルの表に言語を足します。** 最も単純な変更ですが、BE-0320 が唯一の仕組みとしてすでに退けています。安定した性質を持たないプロンプトには表が必要なので、作業単位3のフォールバックとして残します。
- **SpringBoard だけを英語に固定し、アプリは自分の `locale` を保ちます。** 既存の英語の行が全実行に答えます。しかし、1回の実行でアプリとアラートが別の言語を描画します。シナリオがアサートするアラート周辺の画面で、実際のローカライズの不具合が隠れます。
- **未知の言語をビジョンガードに回します。** [BE-0402](../BE-0402-run-alert-guard-drop-vision-fallback/BE-0402-run-alert-guard-drop-vision-fallback-ja.md) が `run` からそのフォールバックを外しました。ステップの結果をモデル呼び出しに決めさせないためです（プライム・ディレクティブ1）。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [x] 作業単位1：言語と iOS バージョンをまたいで同一のボタンの性質を計測します。
  - 実測には、プローブ `SystemAlertProbeUITests` を使いました。showcase の UI テストターゲットに置き、有効化したときだけ動きます。実行は [`misc/measure.sh`](misc/measure.sh) から行いました。組み合わせごとに、新しく作って言語を固定した Simulator を使います。各回は 1 つの ordinal を押し、アプリに残った許可状態を読み取ります。こうして、ラベルに依存せず各 ordinal と役割を対応づけました。生の記録は [`misc/results.jsonl`](misc/results.jsonl) にあります。showcase には ATT のプロンプトを出す手段がなかったので、`perm.requestTracking` ボタンを追加しました。
  - 範囲の変更：5 言語ではなく、英語と日本語を iOS 18.6 と 26.5 で、右から左に書く言語としてアラビア語を 26.5 で測りました。メンテナが対象を英語と日本語に絞り、左右が反転するレイアウトは 2 つの選択を取り違える危険がもっとも大きいので、アラビア語を加えました。

    | 性質 | notifications | tracking | paste | 言語をまたいで同じか |
    |---|---|---|---|---|
    | ボタンの数 | 2 | 2 | 2 | 同じ |
    | deny と grant の ordinal | 0 と 1 | 0 と 1 | 0 と 1 | 30 件すべてで同じ |
    | frame | 横並び | 縦並び | 縦並び | 違う。アラビア語では通知プロンプトの拒否側のボタンが右に来る |
    | identifier | 空 | 空 | 空 | 空なので使えない |
    | value | 空 | 空 | 空 | 空なので使えない |
    | 要素の種類 | button | button | button | 同じだが、2 つのボタンで区別がつかない |

    言語をまたいで変わらず、しかも 2 つのボタンを区別できる性質は ordinal だけです。そこで規則は「拒否は 2 つのうち 1 番目、許可は 2 番目」としました。
- [x] 作業単位2：安定した性質から役割を解決し、Driver の境界をそれを運べるように拡張します。
  - `SystemAlertRole` と `system_alert_role()` を `system_alert_label` の隣に置きました。Driver の境界は変えずに済みました。ステップの待機は、すでに `system_alert_labels()` で画面上のアラートのラベルを順に読んでいます。規則は読むたびにその ordinal のラベルを選び、既存のセレクタの経路で押します。ラベルを選ぶのは、ボタンがちょうど 2 つのときだけです。ステップは補間の時点では `prompt` と `choice` を解決せずに残し、画面上のアラートを照会したあとで規則を解決します。
- [x] 作業単位3：`handleSystemAlert` ステップで役割を使い、ラベルの表をフォールバックにします。ガードはラベルの表と明示的な失敗を保ちます。
  - 順序の変更：規則を先に使うのではなく、ラベルの表が言語を扱える場合は表を、扱えない場合は位置の規則を使います。英語と日本語では、ラベルがボタンだけでなくプロンプトも特定します。そのため、待っているあいだに別の 2 ボタンのアラートが出ても、ステップはそれを押しません。規則は、扱える言語の場合を弱めずに、扱える範囲だけを広げます。locale を持たない `record` の再生も、失敗せずに位置で答えるようになりました。
- [x] 作業単位4：タップしたラベルと選んだ規則を、ステップの結果に報告します。
  - `StepOutcome.system_alert` に `label` と `rule` を記録します。`rule` は `sel`、`label table: <locale>`、`position: button 2 of 2` のいずれかです。manifest は `schemaVersion` 12 になり、レポートに 1 行が出ます。
- [x] 作業単位5：実機の Simulator で4言語を検証し、ドキュメントを更新します。
  - 通知の許可と拒否のシナリオを、iOS 26.5 の Simulator で `bajutsu run` から流しました。言語は `en_US`、`ja_JP`、`fr_FR`、`ar_SA` です。どれも対応する許可状態が残りました。`fr_FR` と `ar_SA` では、ステップが許可で `position: button 2 of 2` を、拒否で `position: button 1 of 2` を報告しました。`fr_FR` の 2 件は、リアクティブなガードを既定のオンのままにしても通りました。`fr_FR` のもとの `systemAlertHandling.rules` の規則は、これまでどおりデバイスを操作する前にシナリオを失敗させました。ドキュメントは `docs/` の 5 ページ（scenarios、configuration、dsl-grammar、reporting、architecture）と、その日本語版を更新しました。

ログ：

- [#2090](https://github.com/bajutsu-e2e/bajutsu/pull/2090)：単位 1〜5。SpringBoard のプロンプトのボタンを実測しました。ラベルの表が扱わない言語の `handleSystemAlert` ステップは、実測した位置の規則で答えるようにしました。押したボタンと規則をステップの結果に記録し、4 言語の Simulator で動作を確かめました。

## 参考

- [BE-0320](../BE-0320-ios-system-alert-locale-determinism/BE-0320-ios-system-alert-locale-determinism-ja.md)：本項目が土台にする、Simulator 言語の固定とラベルの表。
- [BE-0316](../BE-0316-ios-permission-alert-step/BE-0316-ios-permission-alert-step-ja.md)：`handleSystemAlert` ステップと SpringBoard へのクエリ。
- [BE-0382](../BE-0382-system-alert-per-prompt-rules/BE-0382-system-alert-per-prompt-rules-ja.md)：リアクティブガードのプロンプト別ルール。
- [BE-0406](../BE-0406-system-alert-declared-prompts/BE-0406-system-alert-declared-prompts-ja.md)：宣言済みプロンプトとパスワード保存の形。
- `bajutsu/common/scenario/system_alerts/_functions.py`：現在のラベルの表。
