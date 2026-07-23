[English](BE-XXXX-rename-dismiss-alerts-to-handle-alerts.md) · **日本語**

# BE-XXXX — dismissAlerts ガードを、却下も許可も表す handleAlerts に改名する

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-XXXX](BE-XXXX-rename-dismiss-alerts-to-handle-alerts-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **提案** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| トピック | シナリオ記述機能 |
| 関連 | [BE-0269](../BE-0269-ios-alert-guard-early-wait-intervention/BE-0269-ios-alert-guard-early-wait-intervention-ja.md)、[BE-0276](../BE-0276-scenario-permission-state/BE-0276-scenario-permission-state-ja.md) |
<!-- /BE-METADATA -->

## はじめに

本項目は、シナリオのフィールド `dismissAlerts` を（config のデフォルトと CLI フラグの面もあわせて）
`handleAlerts` に改名します。旧来の綴りは非推奨のエイリアスとして残すので、既存のシナリオ・
config・コマンドラインはそのまま動き続けます。挙動は変えません。名前だけの変更です。`handleAlerts`
は、このガードが実際に行うことを表します。ガードは、アプリにスコープされたアクセシビリティツリーには
見えず、tap もできない OS のプロンプトに対する、視覚ベースの復旧手段です。ステップがブロックされると、
画面を撮影してモデルに tap 位置を尋ね、返ってきた位置を tap してそのステップを再試行します。
そして、プロンプトを単に却下する（dismiss）だけではありません。instruction を与えると、そこで指定した
任意のボタンを tap し、その中には受け入れる側のボタンも含まれます。
`dismissAlerts: { instruction: "tap Allow" }` は、プロンプトを却下するのではなく権限を許可します。
名前は却下の場合と受け入れの場合の両方を表すべきであり、`handleAlerts` はそれを満たします。

## 動機

動詞 dismiss は「退ける、却下する」を意味するので、`dismissAlerts` は「プロンプトを却下する」（却下側の
ボタンを tap して先へ進む）と読めます。しかしそれは、このガードが行う2つのことのうちの片方しか表して
いません。ガードの `instruction` フィールドは任意の指定ボタンを tap し、その要となる
文書化された使い方は、プロンプトを受け入れることです。`docs/scenarios.md` は
`dismissAlerts: { instruction: "tap Allow" }` を権限を**許可する**方法として挙げており、
`bajutsu/scenario/models/scenario.py` のフィールド自身の docstring も、同じ「例：`tap Allow` で
プロンプトを許可する」を載せています。フィールドの代表的な文書化された使い方が許可であるのに、
名前が却下と言っているのは、フィールドが何をするのかについて読み手を誤解させます。狭い名前と
より広い挙動との間のこの食い違いは、明示的な `systemAlert` ステップを追加する対の提案
（`ios-permission-alert-step`）が、最初に `permissionAlert` という名前を試したときに突き当たったものと
同じです。

2つ目の理由が、1つ目を補強します。その対の提案は、著者が選んだ地点でモデル呼び出しなしにシステム
アラートを tap する、新しい決定的なステップ `systemAlert` を追加します。すると `systemAlert` と
`dismissAlerts` は、同じ種類の OS レベルのアラートを正反対の仕組みで操作する、名前のよく似た2つの
機能になります。一方は反応的で AI 駆動のガード、もう一方は明示的でネイティブなステップです。反応的な
ガードを `handleAlerts` に改名すると、2つの名前の隔たりが、2つの挙動の隔たりに見合って広がります。
handle（現れたものに反応して対処する）に対して、`systemAlert`（著者がステップを置いた特定のアラートを
操作する）です。この改名は、名前の正確さという理由だけでも実施する価値があります。`systemAlert` との
混同が減るのは、根拠ではなくおまけです。

## 詳細設計

提案のレベル感はこの粒度で揃えます。以下のユニットで MECE に分解します。指針となる制約は、既存の
シナリオ・config・コマンドラインを一切壊さないことです。改名する各面は、旧来の綴りを受理される非推奨の
エイリアスとして残します。

- **シナリオスキーマ。** `DismissAlerts` モデルと `Scenario.dismiss_alerts` フィールドを
  `HandleAlerts` / `handle_alerts` に改名し、YAML キー `handleAlerts` を正規のエイリアスに、
  `dismissAlerts` を追加で受理する入力エイリアス（Pydantic の `AliasChoices`）として残すので、
  どちらの綴りで書いたシナリオもパースできます。ダンプしたシナリオは新しい `handleAlerts` キーを
  出力します。ディスク上の2つの形（真偽値の短縮形、または `{ instruction: "..." }`）は変えません。
- **config デフォルトの面。** アプリレベルのデフォルトは、ターゲット config
  （`bajutsu/config/schema.py`。`bajutsu/config/effective.py` / `bajutsu/config/resolve.py` を
  通じて表に出ます）に同じ `dismissAlerts` キーで置かれています。同じ要領で `handleAlerts` に
  改名し、`dismissAlerts` を受理エイリアスとして残します。
- **CLI フラグ。** `--handle-alerts` / `--no-handle-alerts` を run と record の正規フラグにし、
  `--dismiss-alerts` / `--no-dismiss-alerts` は同じオプションに対応づける隠れた非推奨エイリアスとして
  残すので、既存の呼び出しや CI もそのまま動きます。`--alert-instruction` はすでにアラートに中立な
  読み方なので、そのままにします。`run` ケーパビリティの `claude_flag`（`bajutsu/capabilities.py`）を
  正規の綴り `--handle-alerts` に更新します。
- **非推奨の告知。** 旧来の `dismissAlerts` キーまたは `--dismiss-alerts` フラグが使われたときに、
  新しい名前を指す非推奨通知を一度だけ出します。通知は authoring / CLI 経路のログ行であり、決定的な
  `run` の判定経路には一切置きません（第一原則）。エイリアスは正規の名前とまったく同じ挙動なので、
  run の結果も変えません。
- **ドキュメント。** `docs/` とその `docs/ja/` ミラーの、文書化されたすべての言及を `handleAlerts` に
  改名し、`dismissAlerts` が受理される非推奨エイリアスである旨を短く注記します。現時点で言及は各言語で
  10 ファイルにまたがります。`scenarios.md`（節の見出しとフィールド表）、`dsl-grammar.md`、
  `configuration.md`、`recording.md`、`ai-boundary.md`、`cookbook.md`、`cli.md`、`concepts.md`、
  `ci.md`、`self-hosting.md` です。`scenarios.md` の節見出しを改名すると slug が変わるので、そこを指す
  アンカーリンク（`scenarios.md` 自身のフィールド表の「下記」リンク、`cli.md`、`recording.md`、両言語）
  も同じ変更で更新しないとリンク切れになります。
  対の `systemAlert` 提案のドキュメントと足並みを揃え、名前で衝突させるのではなく、2つの機能を役割
  （反応的なガードと明示的なステップ）で同じ場所で対比します。
- **テスト。** 正規の `handleAlerts` と `dismissAlerts` エイリアスがどちらも同じモデルにパースされる
  こと、どちらのキーでも config デフォルトが効くこと、両方の CLI フラグ、ダンプが新しいキーを出力する
  こと、旧来の綴りで非推奨通知が出ることを検証します。

## 検討した代替案

- **`dismissAlerts` のままにし、混同はドキュメントだけで解消する。** 対の `systemAlert` 提案は、
  反応的なガードと明示的なステップをドキュメントで対比することをすでに計画しています。それは名前の
  近さの問題には効きますが、主たる問題には何もしません。`dismiss` は、要となる文書化された使い方が
  許可であるフィールドの、却下側だけを名指ししています。周囲の文章をいくら足しても、フィールド名
  そのものが正確になるわけではありません。不十分として却下しました。
- **エイリアスなしで改名する（破壊的変更）。** コードは単純になりますが、`dismissAlerts` /
  `--dismiss-alerts` を名指しする既存のシナリオ・ターゲット config・CI コマンドラインをすべて壊します。
  却下しました。正確さの利得は利用者を壊すことに見合わず、エイリアスがあればその破壊は不要です。
- **別の名前を選ぶ（`alertGuard`、`autoAlerts`）。** `alertGuard` は既存の「システムアラートガード」
  という言い回しに合いますが、反応的なガードという枠組みに寄ります。`autoAlerts` は `systemAlert` との
  自動対明示の対比を強調しますが、シナリオのフィールドとしては読みが不自然です。`handleAlerts` を
  選んだのは、handle が却下と受け入れの両方の挙動をどちらにも寄らずに包含し、`systemAlert` と
  並ぶ動作のフィールドとして自然に読めるからです。
- **代わりに `systemAlert` を改名し、衝突を逆側で解く。** 対の提案は、`systemAlert` を、その将来の
  スコープ（権限プロンプトに限らない任意の SpringBoard アラート）に合う対象中立な名前としてすでに
  確定しています。狭く挙動と食い違う名前は `dismissAlerts` の方なので、改名する価値があるのはこちら側
  です。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [ ] シナリオスキーマ — `HandleAlerts` / `handleAlerts`、`dismissAlerts` を入力エイリアスとして維持。
- [ ] config デフォルトの面 — `handleAlerts` キー、`dismissAlerts` エイリアス。
- [ ] CLI フラグ — `--handle-alerts` を正規に、`--dismiss-alerts` を隠れた非推奨エイリアスに。ケーパビリティの `claude_flag`。
- [ ] 旧来のキー / フラグに対する非推奨の告知（authoring / CLI 経路のみ）。
- [ ] ドキュメント — `docs/` + `docs/ja/` の全言及を改名（各言語 10 ファイル）、見出し slug 変更で
      切れるアンカーリンクを修正、エイリアスを注記、`systemAlert` と対比。
- [ ] テスト — 両方の綴りがパースされること、config デフォルト、両フラグ、ダンプが新キーを出力、非推奨通知。

## 参考

- 対の提案 `ios-permission-alert-step`（`systemAlert`） —— この改名が名前の近さを和らげる相手である、
  明示的で決定的なステップ。2つの提案のドキュメントは、反応的なガードと明示的なステップを1箇所で対比
  すべきです。
- [BE-0269 — iOS アラートガードの wait 途中での早期介入](../BE-0269-ios-alert-guard-early-wait-intervention/BE-0269-ios-alert-guard-early-wait-intervention-ja.md) ——
  改名の対象となるガード。
- [BE-0276 — シナリオ単位の宣言的な権限状態](../BE-0276-scenario-permission-state/BE-0276-scenario-permission-state-ja.md) ——
  ガードの決定的な起動前の対の仕組み。ガード自身のドキュメントがすでにこれと対比しています。
- `bajutsu/scenario/models/scenario.py`（`DismissAlerts`）、`bajutsu/agents/alerts.py`、
  `bajutsu/cli/commands/run.py`、`bajutsu/capabilities.py` —— 改名が触れる面。
