[English](BE-XXXX-ios-system-alert-locale-determinism.md) · **日本語**

# BE-XXXX — iOS システムアラートのボタン選択を Simulator のシステム言語によらず決定論的にする

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-XXXX](BE-XXXX-ios-system-alert-locale-determinism-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **提案** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| トピック | プラットフォーム対応 |
| 関連 | [BE-0269](../BE-0269-ios-alert-guard-early-wait-intervention/BE-0269-ios-alert-guard-early-wait-intervention-ja.md)、[BE-0276](../BE-0276-scenario-permission-state/BE-0276-scenario-permission-state-ja.md)、[BE-0315](../BE-0315-ios-native-system-alert-handling/BE-0315-ios-native-system-alert-handling-ja.md)、[BE-0316](../BE-0316-ios-permission-alert-step/BE-0316-ios-permission-alert-step-ja.md) |
<!-- /BE-METADATA -->

## はじめに

`handleSystemAlert`([BE-0316](../BE-0316-ios-permission-alert-step/BE-0316-ios-permission-alert-step-ja.md))は、iOS の SpringBoard 権限プロンプト(たとえば「通知を許可」ダイアログ)のボタンを、画面に表示されているラベルの文字列で特定してタップします。このラベル一致が確実に機能するのは、Simulator のシステム言語が英語になっている場合に限られます。Bajutsu には、まさにこの目的のために1つのプロセスの言語を固定する仕組みがすでにあります。`locale` 設定フィールド(`bajutsu/config/schema.py`、デフォルト値 `en_US`)は、対象アプリ自身の起動引数として `-AppleLocale`/`-AppleLanguages` を渡し(`bajutsu/simctl.py` の `locale_args`)、ホストマシンの環境設定に左右されずアプリ自身の文字列を既知の値に固定します。しかし SpringBoard は Bajutsu が起動しない別プロセスであるため、この起動引数は届かず、アラートのボタンは Simulator 本体のシステム言語設定のまま描画されます。したがって `label: "Allow"` と書いたシナリオは、たまたまシステム言語が英語の Simulator でのみ動作し、そうでない Simulator では即座に失敗します。これは仮定の話ではありません。Simulator のシステム言語は、それを作成したマシンの設定に応じて決まり、日本語話者のコントリビューター自身の Mac で日本語になっている可能性を否定する材料はないからです。

この項目は、この欠落を2つの側面から埋めます。主となる貢献は、`locale` がすでにアプリに与えている決定論を SpringBoard 自身にまで広げることです。コールドスタートのたびに、システム言語を既知の値へ固定します。これにより `label`/`labelMatches` はどのマシン上でも `locale` の値どおりに解決され、アプリと SpringBoard のあいだで言語の認識がずれなくなります。補完となる貢献は、この項目がまず対象とする通知許可と App Tracking Transparency (ATT)について、ロケール別のラベル対応表を用意することです。この2つは、[BE-0276](../BE-0276-scenario-permission-state/BE-0276-scenario-permission-state-ja.md)の権限状態の事前設定が届かないプロンプトでもあります。これにより、「許可する」「拒否する」という意図だけを書きたい作成者が、固定したロケールで実際に表示される文言を手で書き写す必要がなくなります。

## 動機

システムアラートガードのビジョン経路(`bajutsu/agents/alerts.py`)は、スクリーンショットの意味を読み取るため、ボタンがローカライズされていても機能します。しかし `handleSystemAlert` は、意図的にその逆の作り方をしています。[BE-0316](../BE-0316-ios-permission-alert-step/BE-0316-ios-permission-alert-step-ja.md) は、ステップの合否からモデルを締め出すために、ネイティブの accessibility クエリでボタンを解決しています。これは第一の指針(AI は著者と調査者であり、審判にはならない)に沿うものです。この決定論は、入力が正しい場合にしか成り立ちません。`bajutsu/scenario/models/actions.py` の `HandleSystemAlert` は、`sel` にラベルベースの Selector フィールド(`label`、`labelMatches`、`index`)だけを許可しています。SpringBoard のアラートボタンは、アプリが割り当てた識別子や trait、value を持たず、表示テキストしかないからです。そのため、文字通りのラベルが一致しなくなった時点で頼れる、ロケール非依存の手がかりが他にありません。

出荷済みの唯一の実例が、すでにこの脆さを抱えています。`demos/showcase/scenarios/permission_system_alert.yaml` は、`handleSystemAlert: { sel: { label: "Allow" }, timeout: 10 }` で通知プロンプトを許可していますが、この文字列だけが、Simulator のシステム言語が英語でない場合の曖昧一致失敗との間にある唯一の防波堤です。ランナーは今のところアラートがどの言語で描画されたかを一切報告しないため、コントリビューターの目に映るのは「ボタンが一致しなかった」という素っ気ない失敗だけで、原因が Simulator の言語設定にあるという手がかりは示されません。

[BE-0276](../BE-0276-scenario-permission-state/BE-0276-scenario-permission-state-ja.md) は、Transparency, Consent, and Control (TCC) データベースが管理するプロンプトについて `simctl privacy` で権限状態を事前設定しますが、通知許可は TCC サービスではなく、ATT には `simctl` のトグルがそもそもありません。この2つのプロンプトこそ、[BE-0315](../BE-0315-ios-native-system-alert-handling/BE-0315-ios-native-system-alert-handling-ja.md) がリアクティブな解消手段を提案する動機であり、この項目のラベル対応表が最初に対象とするプロンプトでもあります。`dismissAlerts.instruction` は、今のところビジョンの locator が解釈する自由記述の文字列であり、ビジョン経路の他の部分と同じく、ローカライズされたボタンにも対応できています。しかし [BE-0315](../BE-0315-ios-native-system-alert-handling/BE-0315-ios-native-system-alert-handling-ja.md) 自身は、この同じフィールドを、作成者が渡す候補ラベルの決定論的な順序リストへと発展させることを提案しています。それが実現すれば、渡されたラベルが SpringBoard の実際の表示と一致するという、この項目と同じ前提を抱え込むことになります。SpringBoard の言語という層で直せば、`handleSystemAlert` には今すぐ効きます。BE-0315 が提案するネイティブ経路にも、それが実現した時点で同じように効き、どちらか一方が同じ問題を二重に解決する必要はなくなります。

## 詳細設計

作業は、Simulator のシステム言語の固定、その結果として得られる契約の文書化、ロケール別ラベル対応表の追加、そして実機(実 Simulator)での検証に分かれます。

1. **コールドスタートのたびに Simulator のシステム言語を決定論的に解決し、ウォームレジュームはその値が一致する場合だけに絞ります。** `XcuitestEnvironment.start`(`bajutsu/platform_lifecycle/environments/xcuitest.py`)は、シナリオが `erase` を必要とするときすでにウォームランナーの再利用をやめてコールドリスポーンを強制しています。この同じ条件を拡張し、解決したロケールがウォームランナーにすでに固定されているものと異なる場合も、コールドリスポーンを強制するようにします。SpringBoard がまだ以前のロケールのまま描画しているランナーに、シナリオを乗せてしまわないようにするためです。比較・固定に使う値は、`_launch_params` がアプリ自身の起動引数についてすでに解決している優先順位、すなわちシナリオ単位の `Preconditions.locale` オーバーライドをターゲットの `locale` 設定フィールドにフォールバックさせる `pre.locale or eff.locale` に、そのまま従います。コールドスタートでは、`_prepare_simulator` を拡張し、`boot` のあとに Simulator のシステム全体の言語とリージョンを書き込みます。手段は、`xcodebuild` ベースのスクリーンショットツールがすでに使っている手法と同じです。`xcrun simctl spawn <udid> defaults write -globalDomain AppleLanguages -array <language>` と、対応する `AppleLocale` の書き込みです。`simctl spawn` コマンドは実行するのに起動済みのデバイスを必要とするため、この書き込みが Simulator に届くのは SpringBoard がすでに起動し、起動時点の言語のまま描画しているタイミングです。しかも稼働中のプロセスは `-globalDomain` への書き込みをその場では取り込みません。そこで、Simulator を一度シャットダウンしてもう一度 `boot` し、2回目の起動時に SpringBoard が新しく書き込んだ値をもとに立ち上がるようにしてから、既存のインストール・権限設定のステップへ進みます。これによりコールドスタートやロケール変更によるリスポーンには起動サイクルが1回分余分に増えます。これは1回のスポーンにつき1度だけ払う既知のコストであり、ポーリングの tick ごとに発生するものではないため、[BE-0315](../BE-0315-ios-native-system-alert-handling/BE-0315-ios-native-system-alert-handling-ja.md) の詳細設計が慎重に守ろうとしているランナーの tick ごとの負荷には影響しません。この再起動が実際に SpringBoard を新しい言語で描画させることを実機で確認するのがユニット4の役目であり、ここで述べていることは off-Simulator ゲートでは何ひとつ観測できません。
2. **その結果として得られる契約を文書化します。** ユニット1が実装されれば、`label`/`labelMatches` は、同じ `locale` で実行するどのマシンでも同じように解決されるようになります。`locale: en_US` のもとで `label: "Allow"` と書いたシナリオ作成者は、CI でもチームメイトの Mac でも、日本語話者のコントリビューター自身の Simulator でも、まったく同じ挙動を得られます。SpringBoard の言語が、もはやホストマシン固有の事実ではなくなるからです。`docs/configuration.md` の `locale` の説明と `handleSystemAlert` 自身のドキュメントに、この保証を明記します。これにより、これまでラベルの文言を推測しながら書いていた作成者は、この項目を読んで偶然の産物に気づくのではなく、最初から明文化された保証に基づいてシナリオを書けるようになります。
3. **通知許可と ATT について、既存の selector フィールドに追加する形でロケール別ラベル対応表を用意します。** この2つは、[BE-0276](../BE-0276-scenario-permission-state/BE-0276-scenario-permission-state-ja.md) の TCC に基づく事前設定が防げない、まさにそのプロンプトであり、それがユニット3の対象をこの2つに絞る理由でもあります。その `locale` のもとで SpringBoard が描画する「許可」「拒否」それぞれのボタンラベルを解決する、`locale` をキーとした対応表を追加します。これにより、`handleSystemAlert` 既存の `label`/`labelMatches`/`index` フィールドは他のあらゆるアラートに対して今までどおり機能したまま、この2つのプロンプトについてはシナリオが「許可する」「拒否する」という意図だけを表現でき、どのロケールの文言をたまたま表示するかを書き写す必要がなくなります。この対応表は意図的に狭い範囲にとどめます。カバーする必要があるのはこの2つのプロンプトだけであり、任意の SpringBoard 表示テキストを際限なく翻訳する対象へと広がることはありません。対応表の正しさも、鵜呑みにするのではなく、固定した `locale` で1度起動して SpringBoard の実際の表示を確認するだけで独立に検証できます。対応表がカバーしないロケールやプロンプトは、今日と同じように、シナリオが与えた文字通りの `label`/`labelMatches`(ローカライズされていないもの)のまま機能し続けます。
4. **実機(実 Simulator)で検証します。** 異なる `locale` の値(たとえば `en_US` と `ja_JP`)を固定した2台の Simulator を起動し、同じ `handleSystemAlert` シナリオをそれぞれに対して実行して、ユニット1の再起動を経たあとに両方が決定論的にプロンプトを閉じることを確認します。これにより、書き込みと余分な起動が実際に SpringBoard の描画を変えていること(設定ファイルの中身が変わるだけではないこと)、そしてユニット3の対応表が予測どおりのラベルを解決していることが証明されます。続けて、1台のランナーの中であるロケールでシナリオを実行します。その直後に `Preconditions.locale` を別の値にオーバーライドした2つ目のシナリオを実行し、その不一致が古い言語のままのウォームランナーの再利用ではなく、コールドリスポーンを強制することを確認します。これはユニット1のコールドスタート時の書き込みだけでなく、ウォームリユースのゲートそのものを検証するものです。Simulator が実際に描画するアラートの文言は off-Simulator ゲートでは証明できないため、このユニットこそが、ユニット1〜3を「もっともらしい設計」から「実証済みの設計」に変えます。これは、[BE-0316](../BE-0316-ios-permission-alert-step/BE-0316-ios-permission-alert-step-ja.md) と [BE-0315](../BE-0315-ios-native-system-alert-handling/BE-0315-ios-native-system-alert-handling-ja.md) がそれぞれ自身のネイティブタップ機構について持つ実機検証ユニットと同じ位置づけです。

## 検討した代替案

- **Simulator のシステム言語を固定せず、ロケール別ラベル辞書だけで対応する案。** 唯一の手段としては不採用です。Bajutsu が動作しうるあらゆるロケールと、Apple が出荷するあらゆる iOS バージョンにわたって、OS が提供する正確なボタン文言を際限なく追い続ける必要が生じるからです。この問題は、`locale` がすでにアプリの言語を制御しているのと同じやり方で、Simulator の言語そのものを制御することで、発生源から消してしまえます。ユニット1が際限のないメンテナンス負担を取り除いたあとの、狭く追加的なユニット3としては採用します。
- **位置ベース(`index`)での選択を、唯一のロケール非依存な手段とする案。** 不採用です。SpringBoard がプロンプトのボタンをすべてのロケール(右から左に書く言語を含む)で同じ順序に並べるという、未検証の前提に依存しているためです。加えて、`index` はシナリオのレベルで読み取れる意味を持ちません。`index: 1` は、`"Don't Allow"` のように「破壊的な操作をするボタン」だとは語ってくれません。`index` は、[BE-0316](../BE-0316-ios-permission-alert-step/BE-0316-ios-permission-alert-step-ja.md) における `handleSystemAlert` の最終手段としての曖昧性解消という既存の役割にとどめ、2つ目の役割を負わせません。
- **ロケールが英語以外のすべての実行を、AI のビジョンガードに経由させる案。** 不採用です。ビジョンガードは、スクリーンショットの意味をローカライズ後も読み取れるように存在します。しかし、ローカライズされたすべての実行をそこへ経由させると、[BE-0315](../BE-0315-ios-native-system-alert-handling/BE-0315-ios-native-system-alert-handling-ja.md) がまさに共通経路から取り除こうとしているレイテンシと credential 依存を呼び戻してしまいます。また、第一の指針は、あるステップの合否を決める信号からモデル呼び出しを締め出すことを求めています。
- **`locale` をアプリ側だけのものにとどめ、作成者にターゲットごとのローカライズ済みラベルを手で書かせる案。** これが今日の状態であり、まさにこの項目が埋めようとしている欠落そのものです。同一のオープンソースのデモシナリオを、システム言語が異なる Simulator に対して実行するだけで、2つのチームが今日、Bajutsu のどこにも表れない開発者マシンの状態に左右されて、静かに異なる結果を得ています。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解(作業の単位ごとに 1 つ)に対応し、ログには変更内容と時期(古い順)を PR へのリンクと
> ともに記録します。

- [ ] ユニット1 — コールドスタートのたびに Simulator のシステム言語を決定論的に解決する(書き込み+再起動で SpringBoard に実際に反映させる)。解決したロケールがすでに固定されている値と一致する場合だけウォームレジュームを許し、`XcuitestEnvironment.start` の既存の `pre.erase` コールドリスポーン条件に倣う。
- [ ] ユニット2 — `docs/configuration.md` と `handleSystemAlert` 自身のドキュメントに、その結果として得られる `label`/`labelMatches` の契約を明文化する。
- [ ] ユニット3 — 通知許可と ATT(BE-0276 が届かない2つのプロンプト)について、`label`/`labelMatches`/`index` に追加する形でロケール別ラベル対応表を追加する(置き換えではない)。
- [ ] ユニット4 — 実機検証: 異なる `locale` の値を固定した2台の Simulator が、ユニット1の再起動を経たあとに同じ `handleSystemAlert` シナリオを決定論的に閉じること、そして途中のロケールオーバーライドが古い言語のままのウォームレジュームではなくコールドリスポーンを強制することを確認する。

## 参考

- [`bajutsu/scenario/models/actions.py`](../../bajutsu/scenario/models/actions.py) — `HandleSystemAlert`。この項目が埋める欠落の影響を受ける、ラベルベースの Selector フィールドだけを `sel` に許可しています。
- [`bajutsu/simctl.py`](../../bajutsu/simctl.py) — `locale_args`。この項目が Simulator のシステム言語にまで広げる、アプリ側の起動引数の仕組みです。
- [`bajutsu/scenario/models/scenario.py`](../../bajutsu/scenario/models/scenario.py) — `Preconditions.locale`。ユニット1が `bajutsu/config/schema.py` のターゲット単位の `locale` フィールドと突き合わせて解決しなければならない、シナリオ単位のオーバーライドです。`_launch_params` がすでに従っている `pre.locale or eff.locale` という優先順位と同じです。
- [`bajutsu/platform_lifecycle/environments/xcuitest.py`](../../bajutsu/platform_lifecycle/environments/xcuitest.py) — `XcuitestEnvironment.start` の既存の `pre.erase` コールドリスポーン条件(ユニット1のロケール不一致ゲートが倣うもの)、ユニット1が書き込みと再起動を加えて拡張する `_prepare_simulator`、そして再利用する優先順位である `_launch_params` です。
- [`demos/showcase/scenarios/permission_system_alert.yaml`](../../demos/showcase/scenarios/permission_system_alert.yaml) — この項目が埋める脆さをすでに抱えている、出荷済みのシナリオです。
- [BE-0316](../BE-0316-ios-permission-alert-step/BE-0316-ios-permission-alert-step-ja.md) — この項目がロケールに対して安全にする、`handleSystemAlert` ステップです。
- [BE-0315](../BE-0315-ios-native-system-alert-handling/BE-0315-ios-native-system-alert-handling-ja.md) — `instruction` を決定論的な候補ラベルリストへ発展させる提案が実現した際に、この項目の修正を引き継ぐ、ネイティブのリアクティブガードです。
- [BE-0276](../BE-0276-scenario-permission-state/BE-0276-scenario-permission-state-ja.md) — TCC が届かない2つのプロンプトについて、この項目のラベル対応表が補完する、権限状態の事前設定項目です。
