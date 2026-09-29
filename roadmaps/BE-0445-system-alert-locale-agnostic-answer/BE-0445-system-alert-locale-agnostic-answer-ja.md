[English](BE-0445-system-alert-locale-agnostic-answer.md) · **日本語**

# BE-0445 — システムアラートをボタンの役割で答え、Simulator の言語を問わないようにする

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0445](BE-0445-system-alert-locale-agnostic-answer-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **承認済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0445") |
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

- [ ] 作業単位1：言語と iOS バージョンをまたいで同一のボタンの性質を計測します。
- [ ] 作業単位2：安定した性質から役割を解決し、Driver の境界をそれを運べるように拡張します。
- [ ] 作業単位3：`handleSystemAlert` ステップで役割を使い、ラベルの表をフォールバックにします。ガードはラベルの表と明示的な失敗を保ちます。
- [ ] 作業単位4：タップしたラベルと選んだ規則を、ステップの結果に報告します。
- [ ] 作業単位5：実機の Simulator で4言語を検証し、ドキュメントを更新します。

## 参考

- [BE-0320](../BE-0320-ios-system-alert-locale-determinism/BE-0320-ios-system-alert-locale-determinism-ja.md)：本項目が土台にする、Simulator 言語の固定とラベルの表。
- [BE-0316](../BE-0316-ios-permission-alert-step/BE-0316-ios-permission-alert-step-ja.md)：`handleSystemAlert` ステップと SpringBoard へのクエリ。
- [BE-0382](../BE-0382-system-alert-per-prompt-rules/BE-0382-system-alert-per-prompt-rules-ja.md)：リアクティブガードのプロンプト別ルール。
- [BE-0406](../BE-0406-system-alert-declared-prompts/BE-0406-system-alert-declared-prompts-ja.md)：宣言済みプロンプトとパスワード保存の形。
- `bajutsu/common/scenario/system_alerts/_functions.py`：現在のラベルの表。
