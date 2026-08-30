[English](BE-XXXX-run-alert-guard-drop-vision-fallback.md) · **日本語**

# BE-XXXX — run のリアクティブなシステムアラートガードから AI vision フォールバックを廃止する

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-XXXX](BE-XXXX-run-alert-guard-drop-vision-fallback-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **提案** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| トピック | AI プロバイダ設定 |
| 関連 | [BE-0177](../BE-0177-run-behavior-target-config/BE-0177-run-behavior-target-config-ja.md), [BE-0269](../BE-0269-ios-alert-guard-early-wait-intervention/BE-0269-ios-alert-guard-early-wait-intervention-ja.md), [BE-0315](../BE-0315-ios-native-system-alert-handling/BE-0315-ios-native-system-alert-handling-ja.md), [BE-0316](../BE-0316-ios-permission-alert-step/BE-0316-ios-permission-alert-step-ja.md), [BE-0382](../BE-0382-system-alert-per-prompt-rules/BE-0382-system-alert-per-prompt-rules-ja.md), [BE-0394](../BE-0394-ai-provider-none-kill-switch/BE-0394-ai-provider-none-kill-switch-ja.md) |
<!-- /BE-METADATA -->

## はじめに

`run` の**リアクティブなシステムアラートガード**（`systemAlertHandling`）は、アプリ自身のアクセシビリ
ティツリーからは見えない OS のプロンプトを閉じます。通知許可の要求、App
Tracking Transparency（ATT）、クロスプロセスのペースト同意などです。
[BE-0315](../BE-0315-ios-native-system-alert-handling/BE-0315-ios-native-system-alert-handling-ja.md)
以降、このガードは iOS の XCUITest バックエンドで決定論的な**ネイティブ経路**を優先します。SpringBoard
にアラートのボタンを問い合わせ、方針で名指しされたボタンをモデル呼び出しなしで押します。ネイティブ
経路が動けない場合（非対応のバックエンド、問い合わせが列挙できない画面、方針がどのボタンも名指しでき
ない場合）、現状は `bajutsu/agents/alerts.py` の `ClaudeAlertLocator` にフォールバックします。スクリー
ンショットを Claude vision に送り、モデルが返した座標をガードが押します。

本提案は、この vision フォールバックを `run` から完全に取り除きます。ガードの決定論的な半分（ネイティ
ブな SpringBoard 照会、`rules`、`instruction` によるラベル方針、ツリー内での dismiss 経路）はそのまま
残します。画面をスクリーンショットでモデルに読ませて答えを得る半分だけを、`run` の経路から削除します。
ネイティブ経路が動けない場合、ガードはもう何もしません。ブロックされていたステップや `wait` は、
ガードが設定されていない場合とまったく同じように自身のタイムアウトへ向かって進みます。最終的なタイ
ムアウトのメッセージには、見えたアラートがあればそれを名指しします。原因不明の要素未検出として読め
てしまう今のメッセージとは違う形です。`record` と `crawl` はこの変更の対象外です。どちらもすでに Tier 1 の
AI 主導な執筆経路であり、prime directive 1 が定める決定論的なゲートの関心事の外にあります。人間または
エージェントがシナリオを組み立てているあいだ、予期しないプロンプトを同じ vision ロケータで閉じる動作
はそのまま残ります。

## 動機

`run` は Bajutsu の決定論的なゲートです。prime directive 2 は決定性を最優先に置き、prime directive 1
は大規模言語モデル（LLM）を合否判定の経路から完全に締め出します。vision フォールバックは合否を決めま
せん。`bajutsu/orchestrator/waits.py` の `AlertGuardConfig.__call__` と `_AlertGuardGate` は、これを
wait 自身の条件判定とは別の、回復を早める補助としてのみ扱います。それでも、実行中の端末に対しては実際
に作用します。スクリーンショットを撮り、ホスト型のモデルへ送り、モデルの答えが指す座標を押すからです。
そのため、同じビルドに対する同じシナリオの 2 回の実行でも、名前のないプロンプトから画面がいつ、あるい
はそもそも回復するかが揺れ得ます。どの 1 回の実行の結果もモデルには判定させていないとしてもです。
`run --system-alert-handling` は現状、決定論的なゲートの内側で唯一 LLM に届く経路でもあります。
`bajutsu/capabilities.py` はすでに `--system-alert-handling` を、`run` を Claude フリーの分類
（BE-0101）から外すフラグとして記録しており、`bajutsu/ai/__init__.py` のモジュール docstring も、
このガードを `run` の内側から広く到達可能な AI 経路の唯一の入り口として名指ししています。フォールバッ
クを取り除けば、この最後の経路がふさがります。「`run` は構造上 Claude フリーである」という主張は、
フラグ依存の例外なしに成り立ちます。

[BE-0394](../BE-0394-ai-provider-none-kill-switch/BE-0394-ai-provider-none-kill-switch-ja.md) は
すでに、`ai: { provider: none }` によってこのフォールバックを無効化する選択肢をプロジェクトに与えて
います。その提案自身の「動機」の節は、フォールバックを消す必要が生じ得る理由として同じ 3 つ
の性質を挙げています。マスクされないスクリーンショットが端末の外へ出ること、ネイティブ照会なら名前付
きのボタンに解決できるはずのタップが座標頼みであること、そしてモデルへの往復が実行のクリティカルパス
に乗ることです。あの提案の貢献は、認証情報を設定しないという環境上の欠落を、プロジェクトが自ら選び取
る、レビュー可能な明示の設定へと変えることでした。ただし、コード自体はそのまま残り、デフォルトで有効
なままです。そのため、`ai.provider` を一度も設定しないプロジェクトは、ネイティブ経路が名指しできないケース
について、アラートの回復を AI vision の呼び出しに委ねたシナリオ一式を、今も出荷し続けます。本提案は、
その裏返しに当たります。フォールバックを無効化するもう1つの手段を足すのではなく、`run` からフォール
バックそのものを取り除きます。決定論的な挙動を得るのに設定は要らず、どんな設定でもモデル呼び出しを呼
び戻せなくなります。

フォールバックが実際にカバーする範囲は、書かれた当時から狭まってもいます。今それを無効化に留めず削除
する判断が正しい理由です。ネイティブの `HANDLE_SYSTEM_ALERT` capability を一度も広告したことのない 2
つのバックエンドは、そもそもよくあるプロンプトの処理を vision に頼っていません。Web（Playwright）バッ
クエンドは、ガードが動く前に、固定された非破壊的な方針で JavaScript（JS）ダイアログを自動的に閉じます
（`bajutsu/drivers/playwright.py:381`）。Android（adb）バックエンドは、システムの権限ダイアログを通常
の `tap` がすでに届く同じウィンドウダンプの内側に表示します（`bajutsu/drivers/adb.py:1589`）。iOS の
XCUITest バックエンドでは、
[BE-0382](../BE-0382-system-alert-per-prompt-rules/BE-0382-system-alert-per-prompt-rules-ja.md) が、
シナリオが対象のプロンプトごとに自分の rule を名指しできるようにしました。そのため、
`bajutsu/scenario/system_alerts.py` が今日カバーする 3 つのプロンプトは、ボタンのラベルを共有していて
もネイティブに解決できます。フォールバックに残された答えるべき相手は、rule、ラベル、ツリー内 dismiss
のいずれも名指しできない、正真正銘予期しないプロンプトだけです。それはまさに、赤くなった実行を調べる
人間が、最善を尽くした当て推量ではなく読める失敗メッセージを必要とする場面です。

自由テキスト形式の `instruction` は、フォールバックが消えると同時に効かなくなる、今の設定面のなかで
唯一のものです。黙って無視するより大きな声で拒否するほうが、著者にとって親切です。
`bajutsu/scenario/models/scenario.py:81` の `SystemAlertHandling.instruction` は、すでに 2 つの形式を
文書化しています。ネイティブ経路が決定論的に解決する候補ラベルのリスト形式と、vision ロケータだけが解
釈してきた「自由テキストの文字列…レガシー形式」です。同じ docstring によれば、ネイティブ経路は「その
文字列を無視し、デフォルトの dismissive なラベルを代わりに押す」とされています。したがって、シナリオ、
ターゲット設定、`--alert-instruction` フラグのいずれかに自由テキスト形式で書いた instruction は、対応
済み
の iOS バックエンドでは今すでに何の効果も持ちません。ネイティブ capability を持たないバックエンドでは、
この変更が入る前の時点で、自由テキスト形式がフォールバックを動かす唯一の手段になっています。フォール
バックを取り除けば、この形式は iOS だけでなくあらゆるバックエンドで無反応になります。BE-0382 自身の
「動機」の節が、同じ `rules` というフィールドについて長さを割いて排除した、まさにその種の
黙った誤答という結果です。自由テキストの `instruction` を、どのシナリオの端末作業にも入る前、`run`
の実行開始時点で拒否すれば、拒否の理由は読める形のまま残り、著者を今も効くリスト形式や `rules` 形式へ
案内できます。

## 詳細設計

作業は、ガード自身の 2 つの呼び出し箇所、それを組み立てる CLI（コマンドラインインタフェース）配線、
自由テキストの検証、そして `run` が Claude へ届かなくなることに追随する分類とドキュメントに分かれま
す。以下の各ユニットは `run` に限った変更です。`bajutsu/agents/alerts.py`（`ClaudeAlertLocator`、
`SystemAlertGuard`）とそのテスト一式は変更しません。`record` と `crawl` が引き続きそれらを組み立てて
使うためです。

### 1. `AlertGuardConfig` から `vision` フィールドを外す

`AlertGuardConfig`（`bajutsu/orchestrator/types.py:315`）は `BlockedHandler` です。呼び出すとブロッ
キング状態のアラートを閉じるか `None` を返します。必須の `vision: BlockedHandler` フィールドと、ネイ
ティブ方針（`labels`、`rules`、`poll_interval`）から組み立てられています。`__call__` はまず
`self.probe_native` を呼び、`"dismissed"` 以外の結果になると `self.vision(driver)` へフォールスルー
します。本ユニットは `vision` フィールドを完全に取り除きます。`__call__` は `probe_native` 自身の結果
をそのまま返す形になります。`"dismissed"` なら `AlertEvent` を、`"incapable"`、`"absent"`、
`"unhandled"` なら `None` を返し、2 回目の呼び出しはありません。`NativeAlertState` の 4 つの値、
`pick_alert_label`、`match_alert_rule` は変更しません。呼び出し元がモデル呼び出しに交換できなくなる
状態が変わるだけです。

### 2. `_AlertGuardGate` は vision を呼ぶ代わりに未処理のアラートを記録する

`_AlertGuardGate`（`bajutsu/orchestrator/waits.py:189`）は、BE-0269 が追加した wait 途中のトリガーで
す。各ポーリングのツリーを `observe` 経由で受け取り、ネイティブ対応のバックエンドではネイティブ照会
（`_observe_native`）を優先し、それ以外では画面がブロックされているように見えるかどうかを崩壊ツリー
のプロキシ（`_observe_vision`、`shows_app_ui`）で見張ります。どちらの経路も現状は `_fire_vision_bounded`
にたどり着き、cooldown と wait ごとの試行上限のもとでモデル呼び出しを費やしたのち、諦めて警告を記録し
ます。本ユニットは、このモデル呼び出しと、それを制限していた仕組み（`_fire_vision_bounded`、cooldown、
試行上限）を取り除きます。代わりに、作用せず記録するだけのメモへ置き換えます。

- ネイティブ対応のバックエンドでは、`probe_native` の `"unhandled"` 状態（アラートは出ているが、どの
  rule と候補ラベルのどちらもそのボタンを名指しできない）を、アラート自身のボタンラベルとして記録する。
- ネイティブ capability を持たないバックエンド、またはネイティブ対応バックエンドの `"absent"` ポーリ
  ング（アクションシートや WKWebView のダイアログのように問い合わせが列挙できない非 SpringBoard の
  画面）では、vision を起動していた debounce 済みの崩壊ツリー信号を使い、代わりに
  「画面はブロックされているように見えるが名指しできるボタンラベルはない」ことを記録する。

ゲートはこのメモを、wait のループがタイムアウトを報告しようとする瞬間にだけ参照するメソッド越しに公開
します。そのため、一時的な、あるいは自然に解消したブロックがタイムアウトのメッセージに紛れ込むことは
ありません。既存の debounce（`_GUARD_DEBOUNCE_POLLS`）がすでに一時的な崩壊フレームを取り除いており、
このメモはゲートの直近の観測を反映するものであって、wait の早い段階から残った固定フラグではありません。

### 3. wait タイムアウトのメッセージが未処理のアラートを名指しする

`_wait` の `for` 分岐と `screenChanged` 分岐（`bajutsu/orchestrator/waits.py:608` と `:663`）が、タイ
ムアウトを報告し得る、ガード対象の 2 つの分岐です。`gone` と `request` はガード対象外であり、`settled`
は失敗しません。どちらもすでに `f"wait timeout: for {target} ({timeout}s)"` のような単純な文字列を組
み立てています。本ユニットは、タイムアウトの瞬間にゲートのメモ（ユニット 2）が存在すれば、それをこの
文字列へ追記します。認識できないシステムプロンプトの裏で失敗する実行は、たとえば
`wait timeout: for #submit (10.0s) — an unhandled system alert is blocking the screen (buttons:
Allow, Don't Allow)` のように読めるようになります。ついに現れなかった要素だけを名指しする今のメッセ
ージとは違う形です。ネイティブ非対応の場合は、ラベルなしの控えめな言い回しでカバーします。
`… the screen appears blocked, possibly by a system alert or another overlay outside the app's
view` です。

### 4. `run` の CLI 配線が vision ロケータを組み立てなくなる

`_alert_guard_factory` と `_vision_instruction`（`bajutsu/cli/commands/run.py:420`–`525`）は、共有の
`ClaudeAlertLocator` を組み立て、`SystemAlertGuard` に束ね、各シナリオの `AlertGuardConfig` が持つ
`vision` コールバックとして包みます。本ユニットは `_vision_instruction`、ロケータと guard の組み立て、
シナリオごとの `vision` クロージャを削除します。`_alert_guard_factory` は、`labels`、`rules`、
`poll_interval` だけから各シナリオの `AlertGuardConfig` を組み立てるようになります。それらへ渡してい
たフィールド自体を、ユニット 1 ですでに外しているためです。`_build_alert_locator` と
`_build_alert_guard`（`bajutsu/cli/_shared.py:460`–`519`）は変更しません。`record` と `crawl` は引き
続きこれらを直接呼び出します。

### 5. 自由テキストの `instruction` は `run` 実行時の検証エラーになる

`SystemAlertHandling.instruction`（`bajutsu/scenario/models/scenario.py:125`）は
`str | list[str] | None` フィールドのままです。型自体は変わりません。`record` と `crawl` は、変更しな
い自分たちの vision guard のために、引き続き自由テキスト形式を受け付けるからです。変わるのは `run` に
よる解決のしかたです。シナリオ自身の `instruction`、ターゲット設定の
`run_defaults.system_alert_handling.instruction`、CLI の `--alert-instruction`
（`bajutsu/cli/commands/run.py:998`。常に素の文字列であり、空でない値はすべて自由テキスト形式に当た
る）の 3 か所を、`_alert_guard_factory` が検証します。検証は、各シナリオの実効方針を解決する時点、
つまりどのシナリオの端末作業が始まるよりも前に行います。解決された値が `list[str]` ではなく、かつ空でもない `str` である場
合、`run` の実行全体を止め、該当するシナリオまたはフラグを名指しし、リスト形式（`instruction: [...]`）
または `rules` 形式へ誘導するメッセージを添えます。これは、
`SystemAlertHandling.resolved_locale` が未対応のロケールに対してすでに使っている早期検証の形、つまり
「そのシナリオの端末作業より前でここから送出する」という形をなぞるものであり、新しい失敗の形を持ち込
むものではありません。検証はどのシナリオについても `run` がどれか 1 つでも実行を始める前に走るため、
シナリオ一式は全体として受理されるか拒否されるかのどちらかになり、実行の途中で拒否されることはありま
せん。

### 6. Claude 境界の分類とドキュメントの追随

- `bajutsu/capabilities.py` の
  `Capability("run", uses_claude=False, claude_flag="--system-alert-handling")` から `claude_flag`
  を外す。`run` はどのフラグを立てても Claude に届かなくなるため、エントリは
  `Capability("run", uses_claude=False)` になり、分類を変えるフラグを持たない他の Claude フリーなコマ
  ンドと同じ形に揃う。
- `bajutsu/ai/__init__.py` のモジュール docstring は、`run --system-alert-handling` のアラートガード
  を、`run` の内側から広く到達可能な AI seam の唯一の入り口（vision ロケータを組み立てるかどうかを決
  める `credential_gap` の参照）として名指ししている。この一文と、docstring の Tier 1 経路一覧にあ
  る `--system-alert-handling` の記載を、対応するコード経路とあわせて削除する。
- `docs/architecture.md` の「DSL（ドメイン固有言語）system-alert and tip handling」節と、その
  `docs/ja/architecture.md` の対訳は、`systemAlertHandling` の dismiss を「ネイティブ経路が名指しでき
  ない相手に対しては AI vision guard が fallback へ降格する」と説明している。この一節を、未処理のア
  ラートは wait 自身のタイムアウトメッセージに現れ、自由テキストの `instruction` は `run` の実行時に
  拒否される、という新しい挙動の説明へ書き換える。
- `CLAUDE.md`、`CONTRIBUTING.md` / `CONTRIBUTING.ja.md`、`SECURITY.md` / `SECURITY.ja.md` は、いずれも
  `ANTHROPIC_API_KEY` を必要とする経路を「`record`、`run --system-alert-handling`」（またはその日本語
  訳）として列挙している。本変更が入れば `run` はどのフラグでも AI の認証情報を必要としなくなるた
  め、4 ファイルすべてでこの一覧から `run --system-alert-handling` を外す。

### 対象外

- **`record` と `crawl` は vision guard を残す。** どちらもすでに Tier 1 の AI 主導な執筆経路であ
  り（`record` は執筆ループ全体で Claude と対話し、`crawl` はサイト探索のために対話する）、予期しな
  いプロンプトを同じ手段で閉じても prime directive 1 への負担はなく、実在する執筆上の利便性を保てる。
- **Web や Android 向けの新しいネイティブなアラート処理 capability は追加しない。** どちらのバック
  エンドも今日 `HANDLE_SYSTEM_ALERT` を広告しておらず、本提案もそれを追加しない。上の「なぜつくるの
  か」で示したとおり、どちらもすでに vision なしでよくあるプロンプトを処理できている。どちらかのバ
  ックエンドで予期しないプロンプトに出会った場合は、iOS で列挙できない画面と同じように、ユニット 3 の
  控えめでラベルなしのタイムアウトのメモとして現れる。
- **vision に代わる新しいヒューリスティックは追加しない。** 固定座標や、アラートらしき最前面の要素
  を推測する類いである。
  [BE-0315](../BE-0315-ios-native-system-alert-handling/BE-0315-ios-native-system-alert-handling-ja.md)
  自身の「検討した代替案」節が、固定オフセットのタップをすでに同じ理由で退けている。当て推量の答え
  は、prime directive 2 が曖昧なセレクタについて別の場所で退けているのと同じ種類の非決定性であり、
  1 段下で再導入してよい性質ではない。
- **`iosTipKitHandling`（BE-0389）には触れない。** アラートガードと同じステップ末尾の再試行の形を共
  有しているが、そもそもモデルを呼んでいないため、本提案の関心の外にある。

## 検討した代替案

| 案 | 概要 | 採らなかった理由 |
|---|---|---|
| 現状維持。`ai: { provider: none }`（BE-0394）だけを無効化の手段とする | フォールバックはそのままデフォルトで有効にしておき、プロジェクトごとに無効化できるようにする | `ai.provider` を一度も設定しないプロジェクトは、ネイティブ経路が名指しできないケースについて、アラートの回復を AI vision の呼び出しに委ねたシナリオ一式を今も出荷し続ける。取り除く理由（決定論的なゲートの内側にモデル呼び出しがあること自体）は、個々のプロジェクトの設定に依存しない |
| 未解決のケースに向けて、モデルを使わないヒューリスティックな代替（固定オフセット、アラートらしき最前面の要素）を足す | どの rule もラベルも名指しできないプロンプトに対しても、何らかの自動回復を残す | BE-0315 が固定オフセットのタップを退けたのと同じ理由で採らない。当て推量の答えは、prime directive 2 が曖昧なセレクタについて別の場所で退けているのと同じ非決定性である |
| `record` と `crawl` も含め、vision guard をあらゆる場所から取り除く | モジュール（`agents/alerts.py`）を丸ごと削除でき、考えるべき挙動の種類が 1 つ減る | 本提案の壁打ちのなかで退けた。`record` と `crawl` はすでに Tier 1 の AI 主導な経路であり、prime directive 1 が定める決定論的なゲートの関心事の外にある。両者の guard を取り除いても決定性は上がらず、実在する執筆上の利便性だけを後退させる |
| `run` 上の自由テキストの `instruction` を黙って無視する（デフォルトの dismissive な方針として扱う） | 後方互換になる。レガシー形式で書かれた既存のシナリオは、名指ししていた答えを失うだけで動き続ける | 採らない。`instruction: "tap Allow"` と書いて権限を許可するつもりのシナリオが、代わりに黙って拒否するようになってしまう。BE-0382 自身の「動機」の節が同じフィールドについて挙げた、まさにその黙った誤答という結果である |

## 進捗

- [ ] 未着手。

## 参考

- [`bajutsu/agents/alerts.py`](../../bajutsu/agents/alerts.py) — vision ロケータ。本提案では変更しな
  い。
- [`bajutsu/orchestrator/types.py`](../../bajutsu/orchestrator/types.py) — `AlertGuardConfig`、
  `NativeAlertState`、`pick_alert_label`、`match_alert_rule`。
- [`bajutsu/orchestrator/waits.py`](../../bajutsu/orchestrator/waits.py) — `_AlertGuardGate`、
  `_wait`。
- [`bajutsu/cli/commands/run.py`](../../bajutsu/cli/commands/run.py) — `_alert_guard_factory`、
  `_vision_instruction`。
- [`bajutsu/cli/_shared.py`](../../bajutsu/cli/_shared.py) — `_build_alert_locator`、
  `_build_alert_guard`（変更しない。`record` / `crawl` が引き続き使う）。
- [`bajutsu/scenario/models/scenario.py`](../../bajutsu/scenario/models/scenario.py) —
  `SystemAlertHandling`。
- [`bajutsu/capabilities.py`](../../bajutsu/capabilities.py) — Claude 境界の分類。
