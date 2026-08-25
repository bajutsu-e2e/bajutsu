[English](BE-XXXX-ai-provider-none-kill-switch.md) · **日本語**

# BE-XXXX — AI 経路をすべて無効化するプロバイダ none を追加する

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-XXXX](BE-XXXX-ai-provider-none-kill-switch-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **提案** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| トピック | AI プロバイダ設定 |
| 関連 | [BE-0047](../BE-0047-ai-data-sovereignty/BE-0047-ai-data-sovereignty-ja.md), [BE-0104](../BE-0104-vendor-neutral-ai-backend/BE-0104-vendor-neutral-ai-backend-ja.md), [BE-0177](../BE-0177-run-behavior-target-config/BE-0177-run-behavior-target-config-ja.md), [BE-0315](../BE-0315-ios-native-system-alert-handling/BE-0315-ios-native-system-alert-handling-ja.md), [BE-0382](../BE-0382-system-alert-per-prompt-rules/BE-0382-system-alert-per-prompt-rules-ja.md) |
<!-- /BE-METADATA -->

## はじめに

Bajutsu には Claude を呼ぶ経路が複数あります。`record` と `crawl` はシナリオを執筆し、`triage`
は失敗を調査します。そしてリアクティブなシステムアラートガードが、アプリ自身のアクセシビリティ
ツリーからは見えない OS のプロンプトを処理します。決定的な判定を担う `run` の内側で動く AI 経路は、
このガードだけです。iOS XCUITest バックエンドでは、ガードはプロンプトをネイティブに処理します。
ネイティブ経路が対処できなかったときにかぎり、端末のスクリーンショットを Claude vision へ送り、
モデルが示した座標をタップします。

端末のスクリーンショットを外へ出したくないプロジェクトには、その方針を表明する手段がありません。
`systemAlertHandling: false` で設定ごと切ると、残したいほうの決定的なネイティブ経路まで失われます。
残る手段はプロバイダの認証情報を環境に置かないことだけです。しかし認証情報がないという状態は、シェル
の偶然であって、リポジトリに書かれた方針ではありません。

そこで本提案では、[BE-0104](../BE-0104-vendor-neutral-ai-backend/BE-0104-vendor-neutral-ai-backend-ja.md)
が導入したプロバイダレジストリへ、`none` という名前のプロバイダを登録します。設定へ
`ai: { provider: none }` と書けば、すべての AI 経路がまとめて無効になります。vision フォールバックは
何もしなくなる一方で、ネイティブ経路はプロンプトを処理し続けます。Claude を必要とするコマンドは
設定名を挙げて終了します。そもそも、どの経路からも AI バックエンドを構築できなくなります。
プロバイダの `factory` がバックエンドを返さずに例外を送出するからです。本提案の貢献は、ツールに
欠けていた機能を足すことではありません。鍵を設定しない運用でも、似た挙動はすでに得られます。貢献は、
その挙動を環境変数の不在から、リポジトリに残る fail-closed な表明へ変えることにあります。

## 動機

リアクティブなガードは、ステップやガード付きの待機がブロックされている最中に発火します。
`AlertGuardConfig`（`bajutsu/orchestrator/types.py:304`）は、まずネイティブ経路を試します。ネイティブ
経路は [BE-0316](../BE-0316-ios-permission-alert-step/BE-0316-ios-permission-alert-step-ja.md) が
追加した SpringBoard クエリでアラートのボタンを読み、シナリオの `rules` や `instruction` が指定した
ラベルをタップします。一致するルールがなく、候補ラベルも当たらないときは、`SystemAlertGuard.dismiss`
（`bajutsu/agents/alerts.py`）へフォールバックします。クエリが列挙できない表面（アクションシート、
Web ビューのダイアログ、`HANDLE_SYSTEM_ALERT` を持たないバックエンド）でも同じです。フォール
バックは端末のスクリーンショットを撮り、`resolve_alert` ツールとともに Claude へ送ります。そして
モデルが返した座標をタップします。

このフォールバックには、プロジェクトによっては排除しておきたい性質が 3 つあります。そして現状では、
そのどれも排除できません。

1 つめは、スクリーンショットそのものです。Bajutsu はモデルへ送るテキストについては秘匿値を伏せます。
`--alert-instruction` は、要求へ届く前に `Redactor` を通ります（[BE-0047](../BE-0047-ai-data-sovereignty/BE-0047-ai-data-sovereignty-ja.md)）。
しかし隣に並ぶ画像には同じ処理を適用できず、ガードの実装自身がその穴を書き残しています。本番相当の
データを持つ検証環境に対する実行や、撮影した画面を第三者へ送ることを契約が禁じている案件が必要と
するのは、経路が存在しないという保証です。めったに発火しないという観測では足りません。

2 つめは、非決定性です。フォールバックはボタンを座標の推定で決めます。ネイティブ経路が答えられない
場面では、次に何が起きるかをモデルのタップが決めることになります。誰も処理を教えていないプロンプト
で失敗するはずだった実行が、タップの開いた分岐へそのまま進んでしまいます。prime directive 2 は
決定性を最優先に置きます。再現できない救済よりも、読める失敗を選びたいプロジェクトはあります。

3 つめは、費用と待ち時間です。ガードは待機の最中に発火するため、往復がそのまま実行のクリティカル
パスに乗ります。ロケータが Opus ではなく Sonnet を選んでいる理由も、実装のコメントに同じ形で書かれて
います。継続的インテグレーションでスイート全体を回せば、往復は実時間と課金の両方に積み上がります。
しかもネイティブ経路を持つバックエンドでは、めったに必要にならないフォールバックのために積み
上がります。

現状で使える 2 つの手段は、どちらも 3 つの性質を排除できません。`systemAlertHandling: false` は
ガードを丸ごと無効にします。ネイティブ経路も止まるため、決定的な半分だけを使いたいシナリオは、
これまでネイティブ経路が処理していた通知プロンプトで失敗し始めます。

もう 1 つの手段が認証情報です。`_build_alert_locator`（`bajutsu/cli/_shared.py:450`）はプロバイダの
認証ギャップを読み、認証できないときは `None` を返します。フォールバックは何もせず、ネイティブ経路
はそのまま動きます。`ANTHROPIC_API_KEY` を設定しないだけで、プロジェクトが望む形にはなります。
とはいえ、不在は方針の置き場所として貧しいものです。不在はリポジトリのどこにも現れないため、設定を
読んだレビュアーには、スクリーンショットが送られないことを確かめようがありません。新しい継続的
インテグレーションのランナーがこの方針を継ぐのも、単に整備されていないからにすぎません。さらに、
不在はすべての AI 経路で共有されるので、いちばん困る方向へ壊れます。`record` でシナリオを書くために
鍵を export した開発者は、同じシェルで走るすべての `run` の vision フォールバックを、気づかないまま
有効に戻してしまいます。

`none` プロバイダが入ったあとであれば、鍵を export したまま AI の通信が発生しないことを見て、変更が
届いたと判断できます。設定に `ai: { provider: none }` を持つスイートを `run` で回すと、AI の使用量
台帳（[BE-0196](../BE-0196-ai-usage-cost-ledger/BE-0196-ai-usage-cost-ledger-ja.md)）にはイベントが
1 件も記録されません。アラートガード自身の注記も `ai.provider: none` を挙げます。設定のない同じ
実行では、ネイティブ経路が解決できなかったガード付き待機のたびにトークンを消費します。

## 詳細設計

### プロバイダ `none`

```yaml
defaults:
  ai:
    provider: none      # AI 経路をすべて禁じる。決定的なネイティブのアラート処理は影響を受けない
```

新しいアダプタモジュール `bajutsu/ai/disabled.py` を追加します。`bajutsu/ai/registry.py` の
組み込みアダプタと並べて、`none` という名前で `Adapter` を登録します。アダプタの契約が許すもっとも小さな
実装であり、意図的に何もしません。

| フック | 振る舞い |
|---|---|
| `factory` | `ai.provider: none` を理由に挙げて `RuntimeError` を送出する。`AiBackend` は決して構築されない |
| `credential_gap` | トークン `"ai-disabled"` を返す |
| `announce` | デフォルトのプロバイダとモデルの行を `🤖 AI: disabled (ai.provider: none)` に置き換える。プロバイダを開示する面が、使われないモデル名を挙げずに済む |

例外を送出する `factory` こそが、この設定を静かなだけの設定ではなく fail-closed にします。
`create_backend` は、すべてのエージェントが `ClaudeBackedAgent`
（`bajutsu/agents/claude_backed.py`）を通じて到達する唯一の構築点です。認証チェックを飛ばした呼び出しがあっても、黙って往復するのでは
なく構築の時点で例外になります。ロケータの失敗が実行を落とさないようにアラートガードが持っている
`except Exception` も、この送出を要求へ変えることはできません。ガードは警告を記録して何もタップせず、
そのとき何も送信されていないからです。

### 設定フィールドではなく認証ギャップに乗せる理由

`credential_gap` を読む側は、値が `None` でなければ、すでに fail-closed に振る舞います。したがって
プロバイダを登録するだけで、各面はそれぞれ分岐を足さず、意図どおりに振る舞います。

| 箇所 | `provider: none` のときの振る舞い |
|---|---|
| `_build_alert_locator`（`bajutsu/cli/_shared.py:450`） | `None` を返し、vision フォールバックは何もしない。ネイティブ経路には触れない |
| `_require_ai_credential`（`bajutsu/cli/_shared.py:172`） | 終了コード 2 で止まり、`record`、`crawl`、`triage --ai` は機能を落として動くのではなく起動を拒む |
| `doctor` の `_claude_readiness`（`bajutsu/cli/commands/doctor.py:286`） | 任意項目としての Claude の行が未設定と読める。環境の故障を示す ✗ にはならない |
| `serve` の設定（`bajutsu/serve/operations/config.py:508`） | 変化なし。`provider_info` はターゲットの設定ではなく組織の保存済み選択からプロバイダを解決するため、`claudeAvailable` はこの設定を見ない（後述の *`serve`* を参照） |
| `serve` の enrich と triage（`bajutsu/serve/operations/enrich.py:64`、`bajutsu/serve/operations/triage.py:73`） | ジョブを起動する前に HTTP 400 を返す |

`ai.enabled: false` というフィールドを足す場合、認証ギャップを読むすべての箇所で、ギャップに加えて
そのフィールドも読む必要が生じます。しかも `{ enabled: false, provider: api-key }` という、答えが
2 つある設定を許してしまいます。プロバイダ名なら、値 1 つで同じ情報を表せます。

### メッセージ

`"ai-disabled"` トークンのための分岐を、2 つのメッセージ表に足します。既存の文言のままでは、壊れて
いない環境を直しに行くよう読者を促してしまうからです。

| 箇所 | 文言 |
|---|---|
| `bajutsu/agents/availability.py` の `message()`（`doctor` と `serve` が描画） | このターゲットでは AI が無効です（`ai.provider: none`）。AI 経路を使うにはプロバイダを選んでください |
| `_credential_gap_message`（`bajutsu/cli/_shared.py:147`。`record`、`crawl`、`triage --ai` が終了前に表示） | 設定名（`ai.provider: none`）と、AI 経路を再び使うための手順。設定が書かれた*ファイル*まで挙げるには `--config` のパスをメッセージまで引き回す必要がある。`Effective`（`bajutsu/config/effective.py:153`）は解決済みの設定だけを持ち、出所への参照を持たないため、本項目では扱わない |
| `_build_alert_locator` の注記（鍵が未設定である旨の現在の文と置き換え） | vision アラートガードは無効です（`ai.provider: none`）。iOS XCUITest バックエンドではネイティブ経路が一般的なプロンプトを処理し続けます |

### 優先順位

`resolve_provider`（`bajutsu/agents/ai_config.py:78`）は、まず設定の `ai.provider` を読みます。
設定がフィールドを持たないときにかぎり、`BAJUTSU_AI_PROVIDER` へ落ちます。したがって、コミットされた
`provider: none` を環境変数で覆すことはできません。環境を誰も制御できない継続的インテグレーションの
ランナー上でも方針が保たれるのは、この順序があるからです。設定に何も書かれていないマシンでの
一時的な実行には、`BAJUTSU_AI_PROVIDER=none` が引き続き使えます。

`_merge_ai`（`bajutsu/config/resolve.py:43`）は `defaults.ai` と `targets.<name>.ai` をフィールド
単位で統合し、ターゲット側を優先します。新しいプロバイダに、この規則の例外は要りません。リポジトリ
は `defaults` で AI を無効にしたうえで、あるターゲットだけ有効に戻せます。戻す判断は、誰にも見えない
環境変数ではなく、同じファイルの中でレビュアーが読める 1 行になります。

### `serve`

`serve` にだけは例外が要ります。とはいえ特別扱いを足すのではなく、選択肢から差し引く形の例外です。
`none` を登録はしますが、**選択可能にはしません**。レジストリに `selectable_providers()` を足します。
`known_providers()` から無効化用のプロバイダを除いたものです。`serve` が現在 `known_providers()` を
読んでいるのは 3 箇所あります。`bajutsu/serve/operations/config.py:378` と `:392` の読み込み経路、
`:864` の `set_provider` の書き込み経路です。この 3 箇所を、そちらへ向けます。Settings のドロップダウンに `none` は並びません。

理由は、組織の Settings での選択がジョブへ届く経路が環境変数だけだからです。`provider_env`
（`bajutsu/serve/operations/config.py:456`）は、選択した名前を `BAJUTSU_AI_PROVIDER` として出します。
dispatch は、その辞書をジョブの環境オーバーレイとして渡します。`resolve_provider` は設定を先に読むので、
ドロップダウンで `none` を選んだ運用者は、`ai.provider` を設定に書いているプロジェクトに対して何の
効果も得られません。ジョブはモデルを呼び続け、そのことは誰にも知らされません。「オフ」と書かれた
スイッチが黙って入ったままである状態は、鍵を設定しない運用について *動機* が批判した失敗そのもので、
ドロップダウンへ並べれば気づきにくさが増すだけです。このキルスイッチは、リポジトリがコミットする
表明であって、組織ごとのトグルではありません。

ここから制限が 1 つ残ります。本項目は、その制限を閉じずに受け入れます。`provider_info`
（`bajutsu/serve/operations/config.py:491`）が読むのは、組織の保存済み選択です。選択が無ければ、
`AiConfig` を渡さない `resolved_provider()` へ落ちます。enrich と triage のハンドラとは違い、
`resolve(config, target).ai` を読みません。したがって `ai.provider: none` を設定したリポジトリでも
`claudeAvailable: true` が返り、Web UI の record と crawl のタブは有効なままです。キルスイッチ自体は
効いています。タブから起動したジョブはコマンドラインの呼び出しであり、リポジトリの設定を自分で解決
して、モデルへ届く前に終了コード 2 で止まります。欠けているのは、タブをあらかじめ灰色にするための
事前の合図だけです。`provider_info` にターゲットの `ai` を読ませる変更は、`serve` がすべての
プロバイダについて到達性を報告する仕方を変えます。本項目の継ぎ目からは導かれないため、後続の項目に
委ねます。

### 本提案が変えないこと

ネイティブのアラート経路、`systemAlertHandling` のスキーマ、
[BE-0177](../BE-0177-run-behavior-target-config/BE-0177-run-behavior-target-config-ja.md) が定めた
解決順序は、いずれもそのままです。とくに、ネイティブ経路が処理できなかったアラートは、
`provider: none` のもとでも黙って見送られます。認証情報のない今日の挙動と同じです。この場合を
ステップの失敗へ変えれば、動機の 3 つめに挙げた決定性が判定に現れます。しかし、これまで
vision のタップが救っていた実行が赤くなるため、独自の移行を伴う別の判断になります。後続の項目で
扱うことはできますが、本提案のスイッチはその判断に依存しません。

スイッチが個々のシナリオ単位ではなく全体に効くことも、意図した設計です。シナリオごとの
`systemAlertHandling.vision: false` は、アラートガードの動機だけに答えるものです。`record`、`crawl`、
triage は到達可能なまま残ります。スクリーンショットを送らないという方針が必要とするものとは、向きが
逆になります。

### 作業分解

以下の単位は相互に排他で、設計全体を覆います。*進捗* のチェックリストは 1 対 1 で対応します。

1. **アダプタ。** 3 つのフックを持つ `bajutsu/ai/disabled.py` の追加と、`bajutsu/ai/registry.py` の
   `_ensure_builtins` への登録（`known_providers()` から見えるようにするため）。ユニットテストで
   確かめるのは、`credential_gap` がトークンを返すこと、`create_backend` が例外を送出すること、
   `known_providers()` が名前を含むことの 3 点。
2. **`serve` からの除外。** `bajutsu/ai/registry.py` への `selectable_providers()` の追加と、
   `bajutsu/serve/operations/config.py` にある 3 箇所の `known_providers()` の読み替え。テストでは、
   `set_provider` が `none` を 400 で拒むことの確認。環境変数としてしかジョブへ届かないスイッチを、
   ドロップダウンが差し出さないようにするため。
3. **メッセージ。** `bajutsu/agents/availability.py` と `_credential_gap_message` への
   `"ai-disabled"` の分岐の追加、および `_build_alert_locator` の注記の書き換え。
4. **実行経路のテスト。** 環境に鍵があり、設定が `provider: none` のときの確認。`run` がロケータを
   構築しないこと、ガードの `vision` ハンドラが何もしないこと、ネイティブ経路がアラートを処理する
   こと、使用量台帳が空のままであること。
5. **拒否のテスト。** `record`、`crawl`、`triage --ai` の終了コード 2 での停止と設定名の表示。
   enrich と triage がジョブの起動前に返す HTTP 400。加えて、ターゲット設定が `provider: none` の
   ときの `serve` の設定エンドポイントの `claudeAvailable: true` での固定。*`serve`* の節が記録した
   制限を、偽の主張へ変わらないよう据え置くため。
6. **ドキュメント。** `docs/configuration.md` の `ai:` の節と `docs/ja/` 側への、プロバイダ、
   優先順位の規則、`serve` からの除外の追記。あわせて、AI 経路を走らせない旨を 1 行で表明できる
   ようになったことの、`docs/ai-boundary.md` と `docs/ja/ai-boundary.md` への記録。

## 検討した代替案

| 案 | 概要 | 採らなかった理由 |
|---|---|---|
| シナリオごとの `systemAlertHandling.vision: false` | `rules` や `instruction` と並ぶ真偽値を足し、BE-0177 の解決順序に乗せる | アラートガードにしか答えられません。スクリーンショットを第三者へ送らないという方針は `record`、`crawl`、triage も覆う必要があり、シナリオ単位のスイッチでは表明できません。あるシナリオでは vision を切り、別のシナリオでは使いたいという要求が出たときに再検討する価値はありますが、今回の動機にその要求はありません |
| `ai.enabled: false` | `AiSettings` に真偽値のフィールドを追加する | `{ enabled: false, provider: api-key }` という、答えが 2 つある設定を許します。認証ギャップを読むすべての箇所に新しい分岐も要ります。同じ情報はプロバイダ名だけで表せます |
| `ai.provider: disabled` | 同じアダプタを長い名前で登録する | `none` はプロバイダ名の自然な反対語として読め、他のツールの語彙とも揃います。YAML では null ではなく文字列 `none` として解釈されるため、紛らわしさは読み手の側にとどまります。誰も必要としない別名を増やさず、綴りを 1 つに保ちます |
| コマンドラインのフラグのみ（`--no-vision-alert-handling`） | 設定キーを持たず `run` のフラグだけを足す | 方針がリポジトリの外に置かれるため、呼び出し側が毎回覚える必要があり、レビュアーからは見えません。利用者からフラグを受け取らない `serve` のジョブ経路にも届きません |
| 鍵を設定しない運用を正式な答えとして文書化する | コードは変えず、既存の挙動を説明する節を追加する | 動機に挙げた失敗がそのまま残ります。`record` のために export した鍵が `run` のフォールバックを黙って有効に戻し、意図はリポジトリのどこにも記録されません |

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [ ] `none` アダプタと登録、ユニットテスト
- [ ] `selectable_providers()` と `serve` からの除外、`set_provider` が拒むことのテスト
- [ ] `"ai-disabled"` のメッセージ分岐と、アラートガードの注記の書き換え
- [ ] 実行経路のテスト（ロケータを構築しない、台帳にイベントが残らない、ネイティブ経路は不変）
- [ ] `record`、`crawl`、`triage --ai`、enrich の拒否テストと、`claudeAvailable: true` の固定
- [ ] `docs/configuration.md` と `docs/ai-boundary.md` の両言語での記述
- [ ] BE-0047、BE-0104、BE-0315 への相互の `関連` 行の追加（CI が本項目の ID を採番したあと）

## 参考

- [BE-0104 — ベンダー中立な AI バックエンド](../BE-0104-vendor-neutral-ai-backend/BE-0104-vendor-neutral-ai-backend-ja.md) — 本項目が拡張するプロバイダレジストリ。
- [BE-0047 — AI のデータ主権](../BE-0047-ai-data-sovereignty/BE-0047-ai-data-sovereignty-ja.md) — 本項目が再利用する `ai` ブロック、`keyEnv` の規則、認証ギャップ。
- [BE-0315 — ネイティブなリアクティブアラート処理](../BE-0315-ios-native-system-alert-handling/BE-0315-ios-native-system-alert-handling-ja.md) — AI を切っても動き続ける決定的な経路。
- [BE-0382 — システムアラートガードのプロンプト別ルール](../BE-0382-system-alert-per-prompt-rules/BE-0382-system-alert-per-prompt-rules-ja.md) — ネイティブ経路がプロンプトに答えるためのルール。
- [BE-0196 — AI のトークン使用量と費用の記録](../BE-0196-ai-usage-cost-ledger/BE-0196-ai-usage-cost-ledger-ja.md) — 検証可能な成果が読む台帳。
- [`docs/ja/configuration.md`](../../docs/ja/configuration.md) — `ai:` ブロックとその解決順序。
- [`docs/ja/ai-boundary.md`](../../docs/ja/ai-boundary.md) — どの経路がモデルに到達してよく、どの経路が到達してはならないか。
