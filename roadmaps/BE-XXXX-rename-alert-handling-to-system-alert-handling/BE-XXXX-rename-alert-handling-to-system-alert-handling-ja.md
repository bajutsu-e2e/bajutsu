[English](BE-XXXX-rename-alert-handling-to-system-alert-handling.md) · **日本語**

# BE-XXXX — alertHandling ガードを、扱う対象を表す systemAlertHandling に改名する

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-XXXX](BE-XXXX-rename-alert-handling-to-system-alert-handling-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| トピック | シナリオ記述機能 |
| 関連 | [BE-0317](../BE-0317-rename-dismiss-alerts-to-alert-handling/BE-0317-rename-dismiss-alerts-to-alert-handling-ja.md)、[BE-0316](../BE-0316-ios-permission-alert-step/BE-0316-ios-permission-alert-step-ja.md) |
<!-- /BE-METADATA -->

## はじめに

本項目は、シナリオのフィールド `alertHandling`（config のデフォルトと CLI フラグの面もあわせて）を
`systemAlertHandling` に改名します。`alertHandling` と、その前身である `dismissAlerts` は非推奨の
エイリアスとして残すので、既存のシナリオ・config・コマンドラインはそのまま動き続けます。挙動は
変えません。名前だけの変更であり、
[BE-0317](../BE-0317-rename-dismiss-alerts-to-alert-handling/BE-0317-rename-dismiss-alerts-to-alert-handling-ja.md)
が `dismissAlerts` を `alertHandling` に改名した道の延長線上にあります。`systemAlertHandling` は、
対のステップである
[BE-0316](../BE-0316-ios-permission-alert-step/BE-0316-ios-permission-alert-step-ja.md) の
`handleSystemAlert` と同じ精度でガードの対象を名指しします。どちらも「system alert」を明示するため、
読み手は品詞の違いだけで両者を読み分けられます。明示的なステップは動詞句 `handleSystemAlert`、
反応的なガードの設定は名詞句 `systemAlertHandling` です。両者が「system」の有無だけで違う、似た名前
のままではありません。

## 動機

`alertHandling` はそれ単体でも自然に読めますが、**どの種類**のアラートを扱うのかは名前から
わかりません。シナリオ DSL は今、近い関係にある2つのものを名指ししています。ここで改名するリアクティブ
なガードと、BE-0316 の `handleSystemAlert` ステップです。後者は、作者が選んだ地点で SpringBoard の
権限プロンプトを tap します。両者はどちらも同じ種類の対象、すなわちアプリにスコープされた
アクセシビリティツリーには見えない SpringBoard レベルのシステムアラートに作用し、BE-0316 自身の
ドキュメントもすでにこの2つをリアクティブとプロアクティブの対として対比しています。`handleSystemAlert`
を先に読んだ読み手が数行後に `alertHandling` に出会うと、後者の名前が前者の対象を短く言い換えたものだと、
文脈だけから推測することになります。2つの名前は「alert」という一般語以外に共通する部分文字列を
持ちません。`docs/scenarios.md` で「system alert」を検索すると `handleSystemAlert` と
`alertHandling` を導入する地の文はヒットしますが、フィールドのキー自体はヒットしません。対のステップが
使う語でフィールド表や docs を検索した作者は、目当てのフィールドを見落とします。

`systemAlertHandling` は、[BE-0317](../BE-0317-rename-dismiss-alerts-to-alert-handling/BE-0317-rename-dismiss-alerts-to-alert-handling-ja.md)
が前回の改名を解消したのと同じ方法でこの隙間を埋めます。周囲の地の文が説明せざるを得なかったことを、
名前自身に直接語らせるのです。BE-0317 が確立したパターンを本項目はそのまま引き継いでいます。すなわち、
名詞句として読めるシナリオレベルの設定を、既存のシナリオ・config・コマンドラインを一切壊さずに、
旧来の綴りを非推奨のエイリアスとして残しながら改名するという型です。本項目は、同じフィールドに同じ型を
もう一度適用するだけであり、動機も変わりません。読み手はフィールドが何をするかを、隣の段落からではなく
名前そのものから読み取れるべきです。

## 詳細設計

提案のレベル感はこの粒度で揃えます。以下のユニットで MECE に分解します。指針となる制約は BE-0317 から
そのまま引き継ぎます。既存のシナリオ・config・コマンドラインを一切壊さないことです。改名する各面は、
`alertHandling`（BE-0317 の正規名）と `dismissAlerts`（BE-0317 が置き換えた元の名前）の両方を、受理
される非推奨のエイリアスとして残すので、3段のエイリアス連鎖が1つのガードに解決されます。

正規の綴りと2つの非推奨エイリアスは同じガードにパースされるので、次の3つのシナリオは等価です。

```yaml
# 正規（新）
- name: grant the notification prompt
  systemAlertHandling: { instruction: "tap Allow" }
  steps: [ ... ]

# 非推奨の alertHandling エイリアス（BE-0317 のかつての正規名）。受理はされるが、
# 一度だけ非推奨通知を出す
- name: grant the notification prompt
  alertHandling: { instruction: "tap Allow" }
  steps: [ ... ]

# 非推奨の dismissAlerts エイリアス（BE-0317 より前の元の名前）。受理はされるが、
# 一度だけ非推奨通知を出す
- name: grant the notification prompt
  dismissAlerts: { instruction: "tap Allow" }
  steps: [ ... ]

# 真偽値の短縮形は変わらない。systemAlertHandling: false でそのシナリオのガードを切る
```

CLI も改名にならいます。`bajutsu run --system-alert-handling` / `--no-system-alert-handling` が
正規のフラグで、`--alert-handling` / `--no-alert-handling` と `--dismiss-alerts` /
`--no-dismiss-alerts` は隠れた非推奨エイリアスとして動き続けます。

- **シナリオスキーマ。** `AlertHandling` モデルと `Scenario.alert_handling` フィールドを
  `SystemAlertHandling` / `system_alert_handling` に改名し、YAML キー `systemAlertHandling` を
  正規のエイリアスに、`alertHandling` / `dismissAlerts` を追加で受理する入力エイリアス（Pydantic の
  `AliasChoices`）として残すので、3通りの綴りのどれで書いたシナリオもパースできます。ダンプした
  シナリオは新しい `systemAlertHandling` キーを出力します。ディスク上の2つの形（真偽値の短縮形、または
  `{ instruction: "..." }`）は変えません。
- **config デフォルトの面。** アプリレベルのデフォルトは、今日はターゲット config
  （`bajutsu/config/schema.py`。`bajutsu/config/effective.py` / `bajutsu/config/resolve.py` を
  通じて表に出ます）に `alertHandling` キーで置かれています。同じ要領で `systemAlertHandling` に
  改名し、`alertHandling` / `dismissAlerts` を受理エイリアスとして残します。
- **CLI フラグ。** `--system-alert-handling` / `--no-system-alert-handling` を、このフラグを現在
  持つ3つのコマンドすべて（`run`、`record`、`crawl`）の正規フラグにし、`--alert-handling` /
  `--no-alert-handling` と `--dismiss-alerts` / `--no-dismiss-alerts` は同じオプションに対応づける
  隠れた非推奨エイリアスとして残すので、既存の呼び出しや CI もそのまま動きます。`--alert-instruction`
  （これも3コマンドすべてにあります）はすでにアラートに中立な読み方なので、そのままにします。`run`
  ケーパビリティの `claude_flag`（`bajutsu/capabilities.py`）を正規の綴り `--system-alert-handling`
  に更新します。
- **serve のリクエストフラグ。** `bajutsu/serve/operations/dispatch.py` のリクエストボディ用フラグ
  読み取りは、正規の `systemAlertHandling` JSON キーをまず読み、次に `alertHandling`、次に
  `dismissAlerts` にフォールバックします。シナリオ / config のエイリアスと同じ3段の連鎖なので、
  保存済みのフロントエンド状態やサードパーティの `/api/run` クライアントもそのまま動きます。serve の
  UI テンプレート（`bajutsu/templates/serve.*.mjs`）は正規のキーを送信します。
- **非推奨の告知。** `alertHandling` / `dismissAlerts` キー、または `--alert-handling` /
  `--dismiss-alerts` フラグが使われたときに、新しい名前を指す非推奨通知を一度だけ出します。通知は
  authoring / CLI 経路のログ行であり、決定的な `run` の判定経路には一切置きません（第一原則）。
  エイリアスは正規の名前とまったく同じ挙動なので、run の結果も変えません。
- **ドキュメント。** `docs/` とその `docs/ja/` ミラーの、文書化されたすべての言及を
  `systemAlertHandling` に改名し、`alertHandling` と `dismissAlerts` が受理される非推奨エイリアス
  である旨を短く注記します。`scenarios.md` の節見出しを改名すると slug が変わるので、そこを指す
  アンカーリンク（`scenarios.md` 自身のフィールド表の「下記」リンク、`cli.md`、`recording.md`、両言語）
  も同じ変更で更新します。
- **デモ。** showcase 自身のシナリオファイル・config・コメント（`demos/showcase/scenarios/*.yaml`、
  `demos/showcase/showcase.config.yaml`、`.github/workflows/ios-e2e.yml` の CI ワークフロー
  コメント）を正規の綴りに更新し、リポジトリ自身のフィクスチャが非推奨エイリアスではなく新しい名前を
  使うようにします。
- **テスト。** 正規の `systemAlertHandling` と、`alertHandling` / `dismissAlerts` の両エイリアスが
  どちらも同じモデルにパースされること、3つのキーのどれでも config デフォルトが効くこと、`run`・
  `record`・`crawl` それぞれで3つの CLI フラグすべてが効くこと、ダンプが新しいキーを出力すること、
  serve のフォールバック連鎖、旧来のどちらの綴りでも非推奨通知が出ることを検証します。

## 検討した代替案

- **`alertHandling` のままにし、ドキュメントで対比を示す。** `docs/scenarios.md` はすでに
  `systemAlertHandling`（改名前は `alertHandling`）と `handleSystemAlert` を並べて置き、地の文で
  リアクティブとプロアクティブの違いを説明しています。これは周囲の段落を読む読み手には役立ちますが、
  フィールド表・エラーメッセージ・「system alert」の docs 内 `grep` のように、フィールド名だけを
  目にする読み手には何も助けになりません。BE-0317 が同等の代替案に使ったのと同じ理由で、不十分として
  却下しました。
- **エイリアスなしで改名する（破壊的変更）。** コードは単純になりますが、`alertHandling` /
  `--alert-handling` を名指しする（そして間接的には、より古い `dismissAlerts` の綴りをまだ使っている）
  既存のシナリオ・ターゲット config・CI コマンドラインをすべて壊します。却下しました。正確さの利得は
  利用者を壊すことに見合わず、3段のエイリアス連鎖があればその破壊は不要です。
- **別の名前を選ぶ（`systemAlert`、`alertGuard`）。** `systemAlert` は「handling」を落とすので、
  それを扱う設定ではなく、アラートそのものを指す名詞に読めてしまいます。フィールドが**する**ことより、
  フィールドが**何であるか**に寄っています。`alertGuard` は他所の地の文で使う「システムアラート
  ガード」という言い回しには合いますが、`handleSystemAlert` とは文法的に対になりません。
  `systemAlert…` と `handleSystemAlert` の組は、語順が違うだけで同じ単語から始まるため、かえって
  紛らわしく読めます。`systemAlertHandling` を選んだのは、対のステップの名前がすでにこの種のプロンプト
  の語彙として確立している一語を、`alertHandling` にそのまま挿し込んだ形だからです。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [x] シナリオスキーマ — `SystemAlertHandling` / `systemAlertHandling`、`alertHandling` と
      `dismissAlerts` を入力エイリアスとして維持。
- [x] config デフォルトの面 — `systemAlertHandling` キー、`alertHandling` / `dismissAlerts`
      エイリアス。
- [x] CLI フラグ — `--system-alert-handling` を `run`/`record`/`crawl` の正規に、
      `--alert-handling` と `--dismiss-alerts` を隠れた非推奨エイリアスに。ケーパビリティの
      `claude_flag`。
- [x] serve のリクエストフラグ — 正規の `systemAlertHandling` JSON キー、`alertHandling` /
      `dismissAlerts` のフォールバック連鎖。UI テンプレートは正規のキーを送信。
- [x] 旧来のどちらのキー / フラグに対する非推奨の告知（authoring / CLI 経路のみ）。
- [x] ドキュメント — `docs/` + `docs/ja/` の全言及を改名、見出し slug 変更で切れるアンカーリンクを
      修正、両エイリアスを注記、`handleSystemAlert` との対比を維持。
- [x] デモ — showcase のシナリオ / config / CI コメントが正規の綴りを使う。
- [x] テスト — 3通りの綴りすべてがパースされること、config デフォルト、3つの CLI フラグ、ダンプが
      新キーを出力、serve のフォールバック連鎖、非推奨通知。

## 参考

- [BE-0317 — dismissAlerts ガードを、却下も許可も表す alertHandling に改名する](../BE-0317-rename-dismiss-alerts-to-alert-handling/BE-0317-rename-dismiss-alerts-to-alert-handling-ja.md) —
  本項目がもう一歩先へ進める前例。同じフィールドに同じ、エイリアスを残す改名パターンを適用します。
- [BE-0316 — iOS 権限プロンプトアラート向けの明示的な中間ステップ](../BE-0316-ios-permission-alert-step/BE-0316-ios-permission-alert-step-ja.md) —
  この改名の動機となった対の `handleSystemAlert` ステップ。両者が「system alert」を明示するように
  なった今、品詞（動詞のアクションと名詞の設定）で読み分けられます。
- `bajutsu/scenario/models/scenario.py`（`AlertHandling`）、`bajutsu/config/schema.py`、
  `bajutsu/config/effective.py`、`bajutsu/config/resolve.py`、`bajutsu/cli/_shared.py`、
  `bajutsu/cli/commands/run.py`、`bajutsu/cli/commands/record.py`、
  `bajutsu/cli/commands/crawl.py`、`bajutsu/capabilities.py`、
  `bajutsu/serve/operations/dispatch.py`、`bajutsu/templates/serve.*.mjs` — 改名が触れる面。
