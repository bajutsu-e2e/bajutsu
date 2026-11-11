[English](BE-0320-ios-system-alert-locale-determinism.md) · **日本語**

# BE-0320 — iOS システムアラートのボタン選択を Simulator のシステム言語によらず決定論的にする

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0320](BE-0320-ios-system-alert-locale-determinism-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0320") |
| 実装 PR | [#1397](https://github.com/bajutsu-e2e/bajutsu/pull/1397) |
| トピック | プラットフォーム対応 |
| 関連 | [BE-0269](../BE-0269-ios-alert-guard-early-wait-intervention/BE-0269-ios-alert-guard-early-wait-intervention-ja.md)、[BE-0276](../BE-0276-scenario-permission-state/BE-0276-scenario-permission-state-ja.md)、[BE-0315](../BE-0315-ios-native-system-alert-handling/BE-0315-ios-native-system-alert-handling-ja.md)、[BE-0316](../BE-0316-ios-permission-alert-step/BE-0316-ios-permission-alert-step-ja.md) |
<!-- /BE-METADATA -->

## はじめに

`handleSystemAlert`（[BE-0316](../BE-0316-ios-permission-alert-step/BE-0316-ios-permission-alert-step-ja.md)）は、iOS の SpringBoard 権限プロンプトのボタンを、画面に表示されているラベルの文字列で特定してタップします。たとえば「通知を許可」ダイアログが該当します。このラベル一致が確実に機能するのは、Simulator のシステム言語が英語になっている場合に限られます。

Bajutsu には、まさにこの目的のために、1 つのプロセスの言語を固定する仕組みがすでにあります。`locale` 設定フィールド（`bajutsu/config/schema.py`、デフォルト値 `en_US`）は、対象アプリ自身の起動引数として `-AppleLocale` と `-AppleLanguages` を渡します（`bajutsu/simctl.py` の `locale_args`）。これにより、ホストマシンの環境設定に左右されず、アプリ自身の文字列が既知の値に固定されます。

SpringBoard は Bajutsu が起動しない別プロセスなので、この起動引数は SpringBoard に届きません。アラートのボタンは、Simulator 本体のシステム言語設定のまま描画されます。したがって `label: "Allow"` と書いたシナリオは、システム言語がたまたま英語の Simulator でだけ動作します。そうでない Simulator では、即座に失敗します。

これは仮定の話ではありません。Simulator のシステム言語は、その Simulator を作成したマシンの設定で決まります。日本語話者のコントリビューター自身の Mac で、日本語になっている可能性を否定する材料はありません。

この項目は、この欠落を 2 つの側面から埋めます。

- **主となる貢献**：`locale` がすでにアプリに与えている決定論を、SpringBoard 自身にまで広げます。コールドスタートのたびに、システム言語を既知の値へ固定します。これにより `label` と `labelMatches` は、シナリオを実行するどのマシン上でも、`locale` の値どおりに解決されます。アプリと SpringBoard のあいだで、言語の認識がずれなくなります。
- **補完となる貢献**：ロケール別のラベル対応表を、小さく用意します。対応表が対象とするのは、この項目がまず扱う 2 つのプロンプトです。通知許可と App Tracking Transparency（ATT）です。この 2 つのプロンプトには、[BE-0276](../BE-0276-scenario-permission-state/BE-0276-scenario-permission-state-ja.md) の権限状態の事前設定が届きません。対応表があれば、「許可する」「拒否する」という意図だけを書きたい作成者が、固定したロケールで実際に表示される文言を手で書き写す必要がなくなります。

## 動機

システムアラートガードのビジョン経路（`bajutsu/agents/alerts.py`）は、スクリーンショットの文字列そのものではなく意味を読み取るため、ボタンがローカライズされていても機能します。`handleSystemAlert` は、意図的にその逆の作り方をしています。[BE-0316](../BE-0316-ios-permission-alert-step/BE-0316-ios-permission-alert-step-ja.md) は、ステップの結果からモデルを締め出すために、ネイティブの accessibility クエリでボタンを解決しています。この設計は、第一の指針（AI は著者と調査者であり、審判にはならない）に沿うものです。

この決定論は、入力が正しい場合にしか成り立ちません。`bajutsu/scenario/models/actions.py` の `HandleSystemAlert` は、ラベルベースの `Selector` フィールド（`label`、`labelMatches`、`index`）だけを許可しています。SpringBoard のアラートボタンは、アプリが割り当てた識別子、trait、value を持たず、表示テキストだけを持つからです。文字どおりのラベルが一致しなくなった時点で、頼れるロケール非依存の手がかりは、他に残っていません。

出荷済みの唯一の実例が、すでにこの脆さを抱えています。`demos/showcase/scenarios/permission_system_alert.yaml` は、`handleSystemAlert: { sel: { label: "Allow" }, timeout: 10 }` で通知プロンプトを許可しています。Simulator のシステム言語が英語でない場合、この文字列だけが、ステップと `AmbiguousSelector` 型の失敗とのあいだにある唯一の防波堤です。

ランナーは今のところ、アラートがどの言語で描画されたかを報告しません。コントリビューターの目に映るのは、「ボタンが一致しなかった」という素っ気ない失敗だけです。原因が Simulator の言語設定にあるという手がかりは、示されません。

[BE-0276](../BE-0276-scenario-permission-state/BE-0276-scenario-permission-state-ja.md) は、`simctl privacy` で権限状態を事前設定します。対象は、Transparency, Consent, and Control（TCC）データベースが管理するプロンプトです。通知許可は TCC サービスではなく、ATT には `simctl` のトグルがそもそもありません。この 2 つのプロンプトは、[BE-0315](../BE-0315-ios-native-system-alert-handling/BE-0315-ios-native-system-alert-handling-ja.md) がリアクティブな解消手段を提案する具体的な動機です。この項目のラベル対応表が最初に対象とするプロンプトでもあります。

`dismissAlerts.instruction` は、今のところビジョンの locator が解釈する自由記述の文字列です。ビジョン経路の他の部分と同じく、ローカライズされたボタンにもすでに対応できています。ところが BE-0315 自身は、この同じフィールドを、作成者が渡す候補ラベルの、決定論的な順序つきリストへ発展させることを提案しています。それが実現すれば、渡されたラベルが SpringBoard の実際の表示と一致するという、この項目と同じ前提を抱え込みます。リストは、この項目の欠落を解決せず、引き継ぐことになります。

SpringBoard の言語という層で直せば、`handleSystemAlert` に今すぐ効きます。BE-0315 が提案するネイティブ経路にも、経路が実現した時点で同じように効きます。どちらの機能も、同じ問題を二重に解決する必要はありません。

## 詳細設計

作業は 4 つに分かれます。Simulator のシステム言語の固定、その結果として得られる契約の文書化、ロケール別の便宜的な対応表の追加、そして実機（実 Simulator）での検証です。

1. **コールドスタートのたびに Simulator のシステム言語を決定論的に解決し、ウォームレジュームはその値が一致する場合だけに絞ります。**

   `XcuitestEnvironment.start`（`bajutsu/platform_lifecycle/environments/xcuitest.py`）は、シナリオが `erase` を必要とするとき、ウォームランナーを再利用せずにコールドリスポーンを強制します。この条件を拡張します。解決したロケールが、ウォームランナーにすでに固定されているロケールと異なる場合も、コールドリスポーンを強制します。SpringBoard がまだ以前のロケールのまま描画しているランナーに、シナリオを乗せてはいけないからです。

   比較して固定する値は、`_launch_params` がアプリ自身の起動引数についてすでに解決している優先順位、`pre.locale or eff.locale` に従います。シナリオ単位の `Preconditions.locale` オーバーライドが、ターゲットの `locale` 設定フィールドにフォールバックします。

   コールドスタートでは、`_prepare_simulator` を拡張し、`boot` のあとに Simulator のシステム全体の言語とリージョンを書き込みます。手段は、`xcodebuild` ベースのスクリーンショットツールがすでに使っている手法と同じです。`xcrun simctl spawn <udid> defaults write -globalDomain AppleLanguages -array <language>` と、対応する `AppleLocale` の書き込みです。

   `simctl spawn` コマンドは、起動済みのデバイスを必要とします。そのため書き込みが Simulator に届くのは、SpringBoard がすでに起動し、起動時点の言語のまま描画しているタイミングです。稼働中のプロセスは、`-globalDomain` への書き込みをその場では取り込みません。そこで、Simulator を一度シャットダウンして、もう一度 `boot` します。2 回目の起動で SpringBoard は、新しく書き込んだ値をもとに立ち上がります。その後に、既存のインストールと権限設定のステップへ進みます。

   再起動によって、コールドスタートやロケール変更によるリスポーンに、起動サイクルが 1 回分増えます。このコストは既知で、上限があります。1 回のスポーンにつき 1 度だけ払うもので、ポーリングの tick ごとには発生しません。したがって、[BE-0315](../BE-0315-ios-native-system-alert-handling/BE-0315-ios-native-system-alert-handling-ja.md) の詳細設計が慎重に守ろうとしているランナーの tick ごとの負荷には、影響しません。

   この再起動が、実際に SpringBoard を新しい言語で描画させることは、ユニット 4 が実機で確認します。ここで述べていることは、off-Simulator では何ひとつ観測できないからです。
2. **その結果として得られる契約を文書化します。** ユニット 1 が実装されれば、`label` と `labelMatches` は、同じ `locale` で実行するどのマシンでも、同じように解決されます。`locale: en_US` のもとで `label: "Allow"` と書いたシナリオ作成者は、CI でも、チームメイトの Mac でも、日本語話者のコントリビューター自身の Simulator でも、まったく同じ挙動を得られます。SpringBoard の言語は、もはやホストマシン固有の事実ではありません。

   この契約は、`docs/configuration.md` の `locale` の説明と、`handleSystemAlert` 自身のドキュメントに明記します。これまでラベルの文言を推測しながら書いていた作成者は、この項目を読んで偶然の産物に気づくのではなく、明文化された保証に基づいてシナリオを書けるようになります。
3. **通知許可と ATT について、既存の selector フィールドに追加する形で、ロケール別ラベル対応表を用意します。**

   ユニット 3 は、対応表の対象を通知許可と ATT に絞ります。この 2 つのプロンプトは、[BE-0276](../BE-0276-scenario-permission-state/BE-0276-scenario-permission-state-ja.md) の TCC に基づく事前設定が防げません。対応表は `locale` をキーとします。その `locale` のもとで SpringBoard が描画する、「許可」「拒否」それぞれのボタンラベルの組を解決します。

   `handleSystemAlert` 既存の `label`、`labelMatches`、`index` フィールドは、他のあらゆるアラートに対して今までどおり機能します。この 2 つのプロンプトについては、シナリオが「許可する」「拒否する」という意図を表現できます。どのロケールの文言がたまたま表示されるかを、書き写す必要はありません。

   対応表の範囲は、意図的に狭くしています。カバーする必要があるのはこの 2 つのプロンプトだけなので、任意の SpringBoard 表示テキストを際限なく翻訳する対象には広がりません。対応表の正しさは、鵜呑みにせず、独立に検証できます。固定した `locale` で 1 度起動して、SpringBoard の実際の表示を確認します。

   対応表がカバーしないロケールやプロンプトは、今日と同じように機能し続けます。シナリオが与えた文字どおりの、ローカライズされていない `label` や `labelMatches` が使われます。
4. **実機（実 Simulator）で検証します。** 検証は 3 つの部分からなります。
   - 異なる `locale` の値（たとえば `en_US` と `ja_JP`）を固定した 2 台の Simulator を起動します。同じ `handleSystemAlert` シナリオをそれぞれに対して実行し、ユニット 1 の再起動を経たあとに、両方が決定論的にプロンプトを閉じることを確認します。この結果は、書き込みと余分な起動が、設定ファイルの中身だけでなく、SpringBoard の描画そのものを変えていることを証明します。
   - ユニット 3 の対応表が、予測どおりのラベルを解決することを確認します。
   - 1 台のランナーの中で、あるロケールのシナリオを実行します。その直後に、`Preconditions.locale` を別の値にオーバーライドした 2 つ目のシナリオを実行します。この不一致が、古い言語のままの 1 つ目のシナリオのウォームランナーの再利用ではなく、コールドリスポーンを強制することを確認します。この確認は、ユニット 1 のコールドスタート時の書き込みだけでなく、ウォームリユースのゲートそのものを検証します。

   Simulator が実際に描画するアラートの文言は、off-Simulator ゲートでは証明できません。このユニットが、ユニット 1 から 3 を「もっともらしい設計」から「実証済みの設計」に変えます。このユニットは、[BE-0316](../BE-0316-ios-permission-alert-step/BE-0316-ios-permission-alert-step-ja.md) と [BE-0315](../BE-0315-ios-native-system-alert-handling/BE-0315-ios-native-system-alert-handling-ja.md) が、それぞれ自身のネイティブタップ機構について持つ実機検証ユニットと、同じ位置づけです。

## 検討した代替案

- **Simulator のシステム言語を固定せず、ロケール別ラベル辞書だけで対応する案。** 唯一の手段としては不採用です。辞書は、OS が提供する正確なボタン文言を、際限なく追い続ける必要が生じます。追う範囲は、Bajutsu が動作しうるあらゆるロケールと、Apple が出荷するあらゆる iOS バージョンです。この問題は、`locale` がすでにアプリの言語を制御しているのと同じやり方で、Simulator の言語そのものを制御すれば、発生源から消せます。ユニット 1 が際限のないメンテナンス負担を取り除いたあとの、狭く追加的なユニット 3 としては採用します。
- **位置ベース（`index`）での選択を、唯一のロケール非依存な手段とする案。** 不採用です。この案は、SpringBoard がプロンプトのボタンをすべてのロケール（右から左に書く言語を含む）で同じ順序に並べるという、未検証の前提に依存しています。加えて、`index` はシナリオのレベルで読み取れる意味を持ちません。`index: 1` は、`"Don't Allow"` のようには、「破壊的な操作をするボタン」だと語ってくれません。`index` は、`handleSystemAlert` の最終手段としての曖昧性解消という既存の役割（[BE-0316](../BE-0316-ios-permission-alert-step/BE-0316-ios-permission-alert-step-ja.md)）にとどめ、2 つ目の役割は負わせません。
- **ロケールが英語以外のすべての実行を、AI のビジョンガードに経由させる案。** 不採用です。ビジョンガードは、スクリーンショットの意味がローカライズ後も読み取れるように存在します。ところが、ローカライズされたすべての実行をそこへ経由させると、[BE-0315](../BE-0315-ios-native-system-alert-handling/BE-0315-ios-native-system-alert-handling-ja.md) が共通経路から取り除こうとしているレイテンシと credential 依存を呼び戻します。第一の指針も、あるステップの合否を決める信号に、モデル呼び出しを置くことを禁じています。
- **`locale` をアプリ側だけのものにとどめ、作成者にターゲットごとのローカライズ済みラベルを手で書かせる案。** これが今日の状態であり、この項目が埋めようとしている欠落そのものです。同一のオープンソースのデモシナリオを、システム言語が異なる Simulator に対して実行すると、2 つのチームは今日、静かに異なる結果を得ます。違いを生むのは、Bajutsu のどこにも表れない開発者マシンの状態です。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解(作業の単位ごとに 1 つ)に対応し、ログには変更内容と時期(古い順)を PR へのリンクと
> ともに記録します。

- [x] ユニット1 — コールドスタートのたびに Simulator のシステム言語を決定論的に解決する(書き込み+再起動で SpringBoard に実際に反映させる)。解決したロケールがすでに固定されている値と一致する場合だけウォームレジュームを許し、`XcuitestEnvironment.start` の既存の `pre.erase` コールドリスポーン条件に倣う。デバイスが解決済みのロケールをすでに返す場合は書き込みを省く。通常の費用は 2 回目の起動ではなく 1 回の読み取りで済む。
- [x] ユニット2 — `docs/configuration.md` と `handleSystemAlert` 自身のドキュメントに、その結果として得られる `label`/`labelMatches` の契約を明文化する。
- [x] ユニット3 — 通知許可と ATT(BE-0276 が届かない2つのプロンプト)について、`label`/`labelMatches`/`index` に追加する形でロケール別ラベル対応表を追加する(置き換えではない)。`sel` の代わりに `prompt` と `choice` で指定する。対応表が扱わない言語では、推測したボタンをタップせず明示的に失敗させる。
- [x] ユニット4 — 実機検証: 異なる `locale` の値を固定した2台の Simulator が、ユニット1の再起動を経たあとに同じ `handleSystemAlert` シナリオを決定論的に閉じること、そして途中のロケールオーバーライドが古い言語のままのウォームレジュームではなくコールドリスポーンを強制することを確認する。

実機の Simulator（iOS 26.5、Xcode 26）で確認した内容は、次のとおりです。

- 書き込みと再起動は、設定ファイルの値だけでなく、SpringBoard の*描画*を変えます。`ja_JP` を固定して再起動すると、ホーム画面が日本語になります（`マップ` / `設定` / `検索`）。`en_US` を固定すると、英語に戻ります。同じロケールを再度固定した場合は、何も書き込まず、追加の起動もしません。
- 出荷済みの `demos/showcase/scenarios/permission_system_alert.yaml` は、`prompt: notifications` と `choice: grant` を指定するようになりました。`en_US`（`Allow` をタップ）でも `ja_JP`（`許可` をタップ）でも成功します。
- ウォームレジュームのゲートも働きます。同じロケールの 2 シナリオを 1 回の run で実行すると、コールドスタートのランナーログは **1 個**しか残りません。同じ組を 2 つのロケールで実行すると **2 個**残ります。ロケールの変更がコールドリスポーンを強制したため、2 番目のシナリオは 1 番目の SpringBoard の言語で実行されませんでした。
- この項目とは無関係な挙動として、iOS は、通知許可のプロンプトに一度応答した直後、同じ bundle id への再度のプロンプトを抑制します。そのため、このシナリオを続けて実行すると、プロンプトが現れない場合もあります。単一のロケールで行った対照実験では、*1 番目*のシナリオで再現しました。原因は、ロケールの影響ではなく OS の挙動です。
- 対応表の値は Apple 自身のものです。Simulator ランタイムが出荷する文字列から読み取りました。通知は、`UserNotificationsServer.framework` の `PERMISSION_ALERT_ALLOW` と `PERMISSION_ALERT_DENY` です。ATT は、`TCC.framework` の `REQUEST_ACCESS_{ALLOW,DENY}_kTCCServiceUserTracking` です。将来のランタイムの値も、信用するのではなく、同じ方法で確認できます。

この項目には、残した制限が 2 つあります。どちらも [`docs/ja/scenarios.md`](../../docs/ja/scenarios.md#テキストではなく意図で指定する) に記載してあります。どちらも、この項目を広げるのではなく、後続の項目で扱うのが妥当です。

- **言語の固定は Simulator 専用です。** `simctl` は、実機の設定を書き換えられません。そのため、`xcuitest.deviceType: device` の target（BE-0238）には固定が効きません。`prompt` / `choice` の形は、画面に出ている保証のない label を解決してしまいます。きちんと塞ぐには、どの環境が言語を固定するかをランナーに伝える必要があります。そうすれば、ランナーは locale を渡さず、その理由でステップを失敗させられます。この変更は platform-lifecycle のプロトコルにまたがり、この項目の担当範囲より広くなります。
- **`systemAlertHandling` の拒否ラベルの初期値は、英語の文字列です。** 英語以外の `locale` を固定すると、`DEFAULT_DISMISSIVE_LABELS` が一致しなくなります。リアクティブなガードのネイティブ経路は `unhandled` を返し、これまでネイティブにタップしていた場面で、視覚ガードへフォールバックします。この初期値を、この項目のロケール別対応表へ通すのが自然な修正です。修正は、この項目ではなく [BE-0315](../BE-0315-ios-native-system-alert-handling/BE-0315-ios-native-system-alert-handling-ja.md) の担当範囲に属します。

## 参考

- [`bajutsu/scenario/models/actions.py`](../../bajutsu/scenario/models/actions.py)：`HandleSystemAlert` です。この項目が埋める欠落の影響を受ける、ラベルベースの `Selector` フィールドだけを `sel` に許可しています。
- [`bajutsu/simctl.py`](../../bajutsu/simctl.py)：`locale_args` です。この項目が Simulator のシステム言語にまで広げる、アプリ側の起動引数の仕組みです。
- [`bajutsu/scenario/models/scenario.py`](../../bajutsu/scenario/models/scenario.py)：`Preconditions.locale` です。ユニット 1 が `bajutsu/config/schema.py` のターゲット単位の `locale` フィールドと突き合わせて解決しなければならない、シナリオ単位のオーバーライドです。`_launch_params` がすでに従っている `pre.locale or eff.locale` という優先順位と同じです。
- [`bajutsu/platform_lifecycle/environments/xcuitest.py`](../../bajutsu/platform_lifecycle/environments/xcuitest.py)：`XcuitestEnvironment.start` の既存の `pre.erase` コールドリスポーン条件です。ユニット 1 のロケール不一致ゲートが、これに倣います。あわせて、ユニット 1 が書き込みと再起動を加えて拡張する `_prepare_simulator` と、再利用する優先順位である `_launch_params` を含みます。
- [`demos/showcase/scenarios/permission_system_alert.yaml`](../../demos/showcase/scenarios/permission_system_alert.yaml)：この項目が埋める脆さを、すでに抱えている出荷済みのシナリオです。
- [BE-0316](../BE-0316-ios-permission-alert-step/BE-0316-ios-permission-alert-step-ja.md)：この項目がロケールに対して安全にする、`handleSystemAlert` ステップです。
- [BE-0315](../BE-0315-ios-native-system-alert-handling/BE-0315-ios-native-system-alert-handling-ja.md)：`instruction` を決定論的な候補ラベルリストへ発展させる提案が実現した際に、この項目の修正を引き継ぐ、ネイティブのリアクティブガードです。
- [BE-0276](../BE-0276-scenario-permission-state/BE-0276-scenario-permission-state-ja.md)：TCC が届かない 2 つのプロンプトについて、この項目のラベル対応表が補完する、権限状態の事前設定項目です。
