[English](BE-0317-rename-dismiss-alerts-to-alert-handling.md) · **日本語**

# BE-0317 — dismissAlerts ガードを、却下も許可も表す alertHandling に改名する

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0317](BE-0317-rename-dismiss-alerts-to-alert-handling-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0317") |
| 実装 PR | [#1331](https://github.com/bajutsu-e2e/bajutsu/pull/1331) |
| トピック | シナリオ記述機能 |
| 関連 | [BE-0269](../BE-0269-ios-alert-guard-early-wait-intervention/BE-0269-ios-alert-guard-early-wait-intervention-ja.md)、[BE-0276](../BE-0276-scenario-permission-state/BE-0276-scenario-permission-state-ja.md) |
<!-- /BE-METADATA -->

## はじめに

本項目は、シナリオのフィールド `dismissAlerts` を `alertHandling` に改名します。改名は、このフィールドの config デフォルトと CLI フラグの面にも及びます。旧来の綴りは非推奨のエイリアスとして残すので、既存のシナリオ、config、コマンドラインはそのまま動き続けます。挙動は変えません。名前だけの変更です。

`alertHandling` は、このガードが実際に行うことを表します。ガードは、アプリにスコープされたアクセシビリティツリーには見えず、タップもできない OS のプロンプトに対する、視覚ベースの復旧手段です。ステップがブロックされると、画面を撮影してモデルにタップ位置を尋ね、返ってきた位置をタップして、ステップを再試行します。ガードは、プロンプトを単に却下する（dismiss）だけではありません。

instruction を与えると、ガードはそこで指定した任意のボタンをタップします。その中には、受け入れる側のボタンも含まれます。設定 `dismissAlerts: { instruction: "tap Allow" }` は、プロンプトを却下せずに権限を許可します。名前は、却下の場合と受け入れの場合の両方を表すべきです。`alertHandling` はそれを満たします。

## 動機

動詞 dismiss は「退ける、却下する」を意味します。そのため `dismissAlerts` は、「プロンプトを却下する」、すなわち却下側のボタンをタップして先へ進むことと読めます。この読み方が表すのは、ガードが行う 2 つのことのうち片方だけです。

ガードの `instruction` フィールドは、指定した任意のボタンをタップします。このフィールドの要となる文書化された使い方は、プロンプトを*受け入れる*ことです。

- `docs/scenarios.md` は、`dismissAlerts: { instruction: "tap Allow" }` を、権限を**許可する**方法として挙げています。
- `bajutsu/scenario/models/scenario.py` にあるフィールド自身の docstring も、同じ「例：`tap Allow` でプロンプトを許可する」を載せています。

フィールドの代表的な文書化された使い方は許可です。それなのに名前が dismiss と言っていると、フィールドが何をするのかについて、読み手を誤解させます。食い違いは、狭い名前とより広い挙動の間にあります。明示的な `handleSystemAlert` ステップを追加する対の提案（`ios-permission-alert-step`）も、最初に `permissionAlert` という名前を試したときに、同じ食い違いに突き当たりました。

2 つ目の理由が、1 つ目を補強します。対の提案は、新しい決定論的なステップ `handleSystemAlert` を追加します。このステップは、著者が選んだ地点で、モデル呼び出しなしにシステムアラートをタップします。ステップは `steps` の中の*アクション*です。ここで改名するガードは、シナリオレベルの*設定*です。

Bajutsu の DSL は、この 2 つを品詞ですでに区別しています。アクションは動詞（`tap`、`wait`、`swipe`）で、シナリオレベルの設定は名詞（`permissions`、`preconditions`）です。そのため、明示的なアクションは動詞句 `handleSystemAlert` を取り、反応的なガードの設定は名詞句 `alertHandling` を取ります。読み手は、よく似た 2 つの名前ではなく、品詞の違いで 2 つの役割を読み分けられます。

この改名は、名前の正確さという理由だけでも実施する価値があります。`handleSystemAlert` との役割の境界がはっきりするのは、根拠ではなくおまけです。

## 詳細設計

提案のレベル感はこの粒度で揃えます。以下のユニットで MECE に分解します。指針となる制約は、既存のシナリオ、config、コマンドラインを一切壊さないことです。改名する各面は、旧来の綴りを、受理される非推奨のエイリアスとして残します。

正規の綴りと非推奨のエイリアスは同じガードにパースされるので、次の 2 つのシナリオは等価です。

```yaml
# 正規（新）
- name: grant the notification prompt
  alertHandling: { instruction: "tap Allow" }
  steps: [ ... ]

# 非推奨の dismissAlerts エイリアス。受理はされるが、一度だけ非推奨通知を出す
- name: grant the notification prompt
  dismissAlerts: { instruction: "tap Allow" }
  steps: [ ... ]

# 真偽値の短縮形は変わらない。alertHandling: false でそのシナリオのガードを切る
```

CLI も改名にならいます。`bajutsu run --alert-handling` / `--no-alert-handling` が正規のフラグです。`--dismiss-alerts` / `--no-dismiss-alerts` は、隠れた非推奨エイリアスとして動き続けます。

- **シナリオスキーマ**：`DismissAlerts` モデルと `Scenario.dismiss_alerts` フィールドを、`AlertHandling` と `alert_handling` に改名します。YAML キー `alertHandling` を正規のエイリアスにします。`dismissAlerts` は、追加で受理する入力エイリアス（Pydantic の `AliasChoices`）として残すので、どちらの綴りで書いたシナリオもパースできます。ダンプしたシナリオは、新しい `alertHandling` キーを出力します。ディスク上の 2 つの形（真偽値の短縮形、または `{ instruction: "..." }`）は変えません。
- **config デフォルトの面**：アプリレベルのデフォルトは、ターゲット config に同じ `dismissAlerts` キーで置かれています（`bajutsu/config/schema.py`。`bajutsu/config/effective.py` と `bajutsu/config/resolve.py` を通じて表に出ます）。同じ要領で、キーを `alertHandling` に改名し、`dismissAlerts` を受理エイリアスとして残します。
- **CLI フラグ**：`--alert-handling` / `--no-alert-handling` を、このフラグを現在持つ 3 つのコマンドすべて（`run`、`record`、`crawl`）の正規フラグにします。`--dismiss-alerts` / `--no-dismiss-alerts` は、同じオプションに対応づける隠れた非推奨エイリアスとして残すので、既存の呼び出しや CI もそのまま動きます。`--alert-instruction`（これも 3 コマンドすべてにあります）は、すでにアラートに中立な読み方なので、そのままにします。`run` ケーパビリティの `claude_flag`（`bajutsu/capabilities.py`）を、正規の綴り `--alert-handling` に更新します。
- **非推奨の告知**：旧来の `dismissAlerts` キーまたは `--dismiss-alerts` フラグが使われたときに、新しい名前を指す非推奨通知を一度だけ出します。通知は、authoring と CLI の経路のログ行です。決定論的な `run` の判定経路には、一切置きません（第一原則）。通知が run の結果を変えることもありません。エイリアスは、正規の名前とまったく同じ挙動です。
- **ドキュメント**：`docs/` とその `docs/ja/` ミラーの、文書化されたすべての言及を `alertHandling` に改名します。`dismissAlerts` が受理される非推奨エイリアスである旨を、短く注記します。

  現時点で、言及は各言語で 11 ファイルにまたがります。`scenarios.md`（節の見出しとフィールド表）、`dsl-grammar.md`、`configuration.md`、`recording.md`、`ai-boundary.md`、`cookbook.md`、`cli.md`、`concepts.md`、`ci.md`、`self-hosting.md`、`architecture.md` です。

  `scenarios.md` の節見出しを改名すると、見出しの slug が変わります。見出しを指すアンカーリンクは、同じ変更で更新しないとリンク切れになります。該当するのは、`scenarios.md` 自身のフィールド表の「下記」リンク、`cli.md`、`recording.md` で、両言語にあります。

  対の `handleSystemAlert` 提案のドキュメントと、足並みを揃えます。2 つの提案のドキュメントは、2 つの機能を名前で衝突させず、役割（反応的なガードと明示的なステップ）で同じ場所で対比します。
- **テスト**：次の 5 つの挙動を検証します。
  - 正規の `alertHandling` と `dismissAlerts` エイリアスが、どちらも同じモデルにパースされること。
  - どちらのキーでも、config デフォルトが効くこと。
  - `run`、`record`、`crawl` のそれぞれで、両方の CLI フラグが効くこと。
  - ダンプが新しいキーを出力すること。
  - 旧来の綴りで、非推奨通知が出ること。

## 検討した代替案

- **`dismissAlerts` のままにし、混同はドキュメントだけで解消する。** 対の `handleSystemAlert` 提案は、反応的なガードと明示的なステップをドキュメントで対比することを、すでに計画しています。対比は、読み手が 2 つの機能を区別する助けになります。主たる問題には何もしません。`dismiss` は、要となる文書化された使い方が許可であるフィールドの、却下側を名指ししています。周囲の文章をいくら足しても、フィールド名そのものが正確になるわけではありません。不十分として却下しました。
- **エイリアスなしで改名する（破壊的変更）。** コードは単純なままですが、`dismissAlerts` や `--dismiss-alerts` を名指しする、既存のシナリオ、ターゲット config、CI コマンドラインをすべて壊します。却下しました。正確さの利得は、利用者を壊すことに見合いません。エイリアスがあれば、その破壊は不要です。
- **別の名前を選ぶ（`alertGuard`、`autoAlerts`）。** `alertGuard` は、既存の「システムアラートガード」という言い回しに合いますが、反応的なガードという枠組みに寄ります。`autoAlerts` は、`handleSystemAlert` との自動対明示の対比を強調しますが、シナリオのフィールドとしては読みが不自然です。`alertHandling` を選んだのは、handling が却下と受け入れの両方の挙動を、どちらにも寄らずに包含するからです。動詞のアクション `handleSystemAlert` と並ぶ、シナリオレベルの設定（名詞句）として自然に読める点も理由です。
- **代わりに `handleSystemAlert` を改名し、衝突を逆側で解く。** 対の提案は、`handleSystemAlert` を、ステップの将来のスコープに合う対象中立な名前として、すでに確定しています。将来のスコープとは、権限プロンプトに限らない任意の SpringBoard アラートです。狭く挙動と食い違う名前は `dismissAlerts` の方なので、改名する価値があるのはこちら側です。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [x] シナリオスキーマ — `AlertHandling` / `alertHandling`、`dismissAlerts` を入力エイリアスとして維持。
- [x] config デフォルトの面 — `alertHandling` キー、`dismissAlerts` エイリアス。
- [x] CLI フラグ — `--alert-handling` を `run`/`record`/`crawl` の正規に、`--dismiss-alerts` を隠れた非推奨エイリアスに。ケーパビリティの `claude_flag`。
- [x] 旧来のキー / フラグに対する非推奨の告知（authoring / CLI 経路のみ）。
- [x] ドキュメント — `docs/` + `docs/ja/` の全言及を改名（各言語 11 ファイル）、見出し slug 変更で
      切れるアンカーリンクを修正、エイリアスを注記、`handleSystemAlert` と対比。
- [x] テスト — 両方の綴りがパースされること、config デフォルト、両フラグ、ダンプが新キーを出力、非推奨通知。

## 参考

- 対の提案 `ios-permission-alert-step`（`handleSystemAlert`）は、このガードの改名後の設定が並ぶ相手である、明示的で決定論的なステップです。両者は品詞（動詞のアクションと名詞の設定）で読み分けられます。2 つの提案のドキュメントは、反応的なガードと明示的なステップを 1 箇所で対比すべきです。
- [BE-0269：iOS アラートガードの wait 途中での早期介入](../BE-0269-ios-alert-guard-early-wait-intervention/BE-0269-ios-alert-guard-early-wait-intervention-ja.md)は、改名の対象となるガードです。
- [BE-0276：シナリオ単位の宣言的な権限状態](../BE-0276-scenario-permission-state/BE-0276-scenario-permission-state-ja.md)は、ガードの決定論的な起動前の対の仕組みです。ガード自身のドキュメントが、すでにこれと対比しています。
- `bajutsu/scenario/models/scenario.py`（`DismissAlerts`）、`bajutsu/agents/alerts.py`、`bajutsu/cli/commands/run.py`、`bajutsu/cli/commands/record.py`、`bajutsu/cli/commands/crawl.py`、`bajutsu/config/schema.py`、`bajutsu/capabilities.py` は、改名が触れる面です。
