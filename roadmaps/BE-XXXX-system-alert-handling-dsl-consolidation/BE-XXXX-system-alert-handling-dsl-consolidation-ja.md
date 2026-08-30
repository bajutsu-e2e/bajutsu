[English](BE-XXXX-system-alert-handling-dsl-consolidation.md) · **日本語**

# BE-XXXX — systemAlertHandling のDSLを、答え方の経路ごとに1つのキーへ整理する

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-XXXX](BE-XXXX-system-alert-handling-dsl-consolidation-ja.md) |
| 提案者 | [@akiramatsuda](https://github.com/akiramatsuda) |
| 状態 | **提案** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| 実装 PR | [#1810](https://github.com/bajutsu-e2e/bajutsu/pull/1810) |
| トピック | シナリオ記述機能 |
| 関連 | [BE-0177](../BE-0177-run-behavior-target-config/BE-0177-run-behavior-target-config-ja.md), [BE-0315](../BE-0315-ios-native-system-alert-handling/BE-0315-ios-native-system-alert-handling-ja.md), [BE-0316](../BE-0316-ios-permission-alert-step/BE-0316-ios-permission-alert-step-ja.md), [BE-0317](../BE-0317-rename-dismiss-alerts-to-alert-handling/BE-0317-rename-dismiss-alerts-to-alert-handling-ja.md), [BE-0320](../BE-0320-ios-system-alert-locale-determinism/BE-0320-ios-system-alert-locale-determinism-ja.md), [BE-0327](../BE-0327-rename-alert-handling-to-system-alert-handling/BE-0327-rename-alert-handling-to-system-alert-handling-ja.md), [BE-0382](../BE-0382-system-alert-per-prompt-rules/BE-0382-system-alert-per-prompt-rules-ja.md) |
<!-- /BE-METADATA -->

## はじめに

シナリオの`systemAlertHandling`設定は、アプリ自身のアクセシビリティツリーからは見えないOSの
プロンプトを、実行中のどこで現れてもボタンをタップして片付けます。対象は通知許可の要求、
App Tracking Transparency（ATT）、プロセス間のペースト確認です。この設定がボタンに届く経路は
2つあります。1つは、SpringBoardにアラートのボタンラベルを問い合わせてタップする決定的なネイティブ
経路です。もう1つは、ネイティブ経路がボタンを名指しできないときにスクリーンショットを読む、人工知能
（AI）による視覚フォールバックです。4回にわたる変更の積み重ねによって、この設定が持つ4つのキーは、
作者が書いたキーではなく**値の型**によって2つの経路へ振り分けられる状態になりました。

本提案は、1つのキーが1つの経路だけを指すように設定を組み直し、作者の書いた宣言がすべて効き続ける
ようにします。現在の`instruction`が担っている2つの役割を、ネイティブ経路がタップする順序付きの
ボタンラベル`labels`と、視覚フォールバックだけが読む自由文`visionInstruction`へ分けます。`enabled`
キーは、スキーマがすでに受け付けているbool値の短縮形に一本化して削除します。設定がいまなお応答する
非推奨の綴り2つも削除します。そして、4つのキーが4通りに解決され、そのうち1つは別の層の宣言を丸ごと
消してしまう現在の重ね方を、キーの型で決まる2つの規則へ置き換えます。

互換性は意図的に維持しません。削除したキーはエイリアスとして残さず、置き換え先を名指しする
エラーで読み込みに失敗させます。

## 動機

この設定の実行時の表現は、本提案がスキーマへ与えようとしている形をすでに持っています。
`AlertGuardConfig`（`bajutsu/orchestrator/types.py`）は、ネイティブ経路のための`labels`と`rules`を
持ちます。視覚フォールバックのための`vision`ハンドラは、そこから分けて保持します。ガードが動く時点で
2つの経路は
すでに別のデータです。2つの経路が混ざっているのはディスク上のスキーマだけです。そこでは
`instruction`という1つのキーが両方へ流れ、どちらへ行くかは作者がリストを書いたか文字列を書いたかで
決まります。

この型による振り分けは、既定のバックエンドの上で、作者が書いた意図を黙って反転させます。
`systemAlertHandling: { instruction: "tap Allow" }`は、プロンプトを許可する指示として読めます。
しかし文字列の形が方向づけるのは視覚フォールバックだけです。ネイティブ経路は正確なラベルを必要とする
ので文字列を無視し、代わりに既定の**拒否**ラベルをタップします。
[BE-0290](../BE-0290-xcuitest-default-ios-backend/BE-0290-xcuitest-default-ios-backend-ja.md)
以降の既定であるXCUITestバックエンドでは、ネイティブのタップが先に決着するため、プロンプトは
拒否されます。ファイルには許可と書いてあり、実行は拒否します。その権限についてアサーションを
持たないシナリオには落ちる場所がないので、何も失敗しません。リストの形`instruction: ["Allow"]`
なら意図どおり許可するので、1つのキーの2つの形が逆の答えを出すことになります。
`bajutsu/scenario/models/scenario.py`のdocstringは、この事実をすでに散文で警告しています。文字列は
「そうしたバックエンドでは、以前の視覚だけの挙動をそのまま置き換えるものではない」と書かれています。

2つめの欠陥は、1つの宣言が別の宣言を消すことです。ターゲット設定の`rules`には適用の条件があります
（`bajutsu/cli/commands/run.py`）。シナリオと`--alert-instruction`のどちらからも独自の`instruction`が
与えられていないときに限られます。
そのため、リテラルなボタンを1つ名指ししただけのシナリオが、プロジェクト全体の規則をすべて落とします。
そのシナリオが一度も触れていないプロンプトへの規則まで一緒に落ちます。
[BE-0382](../BE-0382-system-alert-per-prompt-rules/BE-0382-system-alert-per-prompt-rules-ja.md)は、
プロジェクト全体の規則がシナリオ自身の答えを反転させることを防ぐために、この全か無かの優先順位を
意図して選びました。現在のスキーマの下で選べる2つの読み方のうち、より安全なのは確かにこちらです。
その代償として、作者は目の前のキーだけを見て、自分のどの宣言が実行されるのかを予測できません。
シナリオに`labels`を足すと、別のファイルにある設定のブロックが黙って無効になります。

3つめの欠陥は、スキーマでは書けるのに実行時は無視する状態の存在です。`enabled`は普通のキーなので
`{ enabled: false, rules: [...] }`は読み込みに成功し、規則は何も告げられないまま捨てられます。
2つの綴りで「無効」を表せること（素の`false`と`{ enabled: false }`）も、同じ理由で共存しています。

非推奨の綴り2つが、以上のすべてに重なります。`alertHandling`と`dismissAlerts`は、それぞれ
[BE-0317](../BE-0317-rename-dismiss-alerts-to-alert-handling/BE-0317-rename-dismiss-alerts-to-alert-handling-ja.md)
と
[BE-0327](../BE-0327-rename-alert-handling-to-system-alert-handling/BE-0327-rename-alert-handling-to-system-alert-handling-ja.md)
が置き換えた旧名で、いまも一度だけの通知とともに読み込みに成功します。`--alert-handling`と
`--dismiss-alerts`のフラグも同じです。1つの設定が、2つの面で3つの名前に応答しています。

本提案が届いたかどうかは、後から読む人が上記の反転するファイルを読み込ませれば確かめられます。現在の
`systemAlertHandling: { instruction: "tap Allow" }`は読み込みに成功し、XCUITestバックエンドでは拒否
ボタンをタップします。本変更のあとでは、同じファイルは`labels`と`visionInstruction`を名指しする
エラーで読み込みに失敗し、置き換えたファイルは「Allow」をタップします。設定のキーをどう組み合わせても、
ファイルが名指ししたボタンと反対のボタンをタップすることはなくなります。

## 詳細設計

### スキーマ

```
SystemAlertHandling ::= boolean                                   # true=既定方針で有効、false=無効
               | { rules?:             list(<SystemAlertRule>),   # プロンプト名で答える（ネイティブ経路のみ）
                   labels?:            list(string),              # ボタンラベルで答える（ネイティブ経路と、そこから導く視覚のヒント）
                   visionInstruction?: string,                    # 自由文（AI視覚フォールバックのみ）
                   pollInterval?:      number }                   # ネイティブの presence 照会の間隔（秒、既定1）

SystemAlertRule ::= { prompt: notifications | tracking | paste, choice: grant | deny }
```

```yaml
- name: onboarding — accept notifications, refuse tracking
  systemAlertHandling:
    rules:
      - { prompt: notifications, choice: grant }
      - { prompt: tracking,      choice: deny }
    labels: ["Not Now"]        # どの規則も同定しなかったアラート
  steps:
    - tap:  { id: onboarding.start }
    - wait: { for: { id: home.title }, timeout: 10 }
```

`rules`と`pollInterval`は、
[BE-0382](../BE-0382-system-alert-per-prompt-rules/BE-0382-system-alert-per-prompt-rules-ja.md)と
[BE-0315](../BE-0315-ios-native-system-alert-handling/BE-0315-ios-native-system-alert-handling-ja.md)
が与えた意味のままです。`labels`は現在の`instruction`のリストの形で、エントリが何であるかを述べる
名前に変えたものです。`visionInstruction`は現在の`instruction`の文字列の形で、それを読む唯一の経路に
ちなんだ名前です。

これで、各キーがちょうど1つの実行経路に届きます。`rules`と`labels`はネイティブ経路を、
`visionInstruction`は視覚フォールバックを方向づけます。加えて`labels`は、どの層も
`visionInstruction`を与えていないときに限り、フォールバックへ渡すヒントを導出します。これは現在の
リストの形がすでに行っている動作で、ガードは候補を「Tap the button labeled one of, in order: …」の
形に整えて渡します。どの層のものであれ`visionInstruction`は導出したヒントより優先します。ラベルから
導いたヒントはフォールバックについての表明ではなく、明示された`visionInstruction`は表明だからです。`rules`がフォールバックへ何も渡さない点は、BE-0382が記録した理由のまま変わり
ません。フォールバックへ届く経路はどれも、どの規則もアラートを同定できなかった経路です。したがって
規則のラベルは、構成上かならず別のプロンプトへの答えであり、それを渡せばシナリオが名指ししていない
プロンプトを受諾する方向へ locator を誘導してしまいます。

### 適用は2つの段階に分かれ、段階の内側では具体的な宣言が勝つ

ガードがアラートのボタンへ届く道は2つの段階に分かれ、各キーはそのどちらか一方だけに属します。先に
走るのはネイティブ経路で、自力で答えられるならそこで決着します。視覚フォールバックが走るのは、
ネイティブ経路が解決できずに残したものだけです。

```
ネイティブ経路:   rules  →  labels （labels が空なら組み込みの拒否ラベル）
                              │ 何も解決しない
                              ▼
視覚フォールバック: visionInstruction  →  labels から導いたヒント  →  locator 自身の既定
```

ネイティブ経路の内側では、規則がプロンプトを名指しし、ラベルがボタンを名指しするので、より具体的な
宣言を先に参照します。この順序はBE-0382が定めた「`rules`のあとに`instruction`」という優先順位を
置き換えるものではなく、そのまま延長したものです。

組み込みの拒否ラベルは、与えられた`labels`の後ろに継ぎ足すものではなく、`labels`がないときにその
代わりを務めるものです。ボタンを名指ししたシナリオが、そのどれも持たないアラートに出会ったときは、
ネイティブ経路では何も解決せずフォールバックへ落ちます。作者が名指ししていないボタンをタップは
しません。曖昧なセレクタが最初の一致をタップせずに失敗するのと同じ理由です。2つのボタンに同じ
ラベルが載っている候補は、単一のボタンを特定できないので飛ばして次の候補へ進みます。これは
`pick_alert_label`が現在持っている規則であり、変更しません。

ネイティブ経路の内側で衝突を決めるのは、宣言がどの層から来たかではなく具体性です。ターゲット設定にあるトラッキング
プロンプトへの規則は、シナリオが独自の`labels`を持っていてもトラッキングプロンプトに答えます。規則が
そのプロンプトを名指ししているのに対し、ラベルはどのプロンプトも名指ししていないからです。両方の宣言は
生きたままです。規則がトラッキングに答え、シナリオの`labels`がそれ以外に答えます。シナリオが設定の
規則を上書きしたいときは、同じ具体性で、そのプロンプトへの規則を自分で書きます。上書きが及ぶ範囲は
作者が名指しした1つのプロンプトであり、設定にある規則の全体ではありません。

### 層の合成は、キーの型が決める

1つの設定は最大3つの層から実行へ届きます。ターゲット設定
（[BE-0177](../BE-0177-run-behavior-target-config/BE-0177-run-behavior-target-config-ja.md)）、
コマンドライン、シナリオの3つです。キーがリストを持つかスカラを持つかで、2つの規則がすべてを覆います。

| キー | 型 | 合成 |
|---|---|---|
| `rules` | リスト | 連結する。内側の層が先（シナリオ、ターゲットの順） |
| `labels` | リスト | 連結する。内側の層が先（シナリオ、コマンドライン、ターゲットの順） |
| `visionInstruction` | スカラ | 優先順位で解決する。内側の層が勝つ（シナリオ、なければコマンドライン、なければターゲット） |
| `pollInterval` | スカラ | 優先順位で解決する。内側の層が勝つ（シナリオ、なければコマンドライン、なければターゲット） |
| 有効と無効 | スカラ | 優先順位で解決する（`--system-alert-handling`か`--no-system-alert-handling`、なければシナリオ、なければターゲット、なければ有効） |

`rules`だけが2つの層しか挙げていないのは、規則を与えるフラグがないからです。1つのエントリは
プロンプトと選択の組であり、フラグの値1つでは読みやすく運べません。ほかのキーは3つの層すべてに
届きます。

リストを連結できるのは、連結が両方の層のエントリを残すからです。シナリオの答えが先に試され、シナリオが
答えなかったものにはターゲットの答えが依然として届きます。スカラは値を1つしか持たないので合成の余地が
なく、内側の層が勝ちます。この2つの規則が、現在この設定を解決している4つの規則を置き換えます。
どの規則も、別の層の宣言を消しません。

`rules`を連結しても、BE-0382が定めた上書きの性質は保たれます。照合はプロンプトを同定できた最初の規則で
戻るので、同じプロンプトを指すシナリオの規則とターゲットの規則は、シナリオ側に解決します。1つのリストの
**内側**でのプロンプトの重複は、これまでどおり解析時のエラーのままです。2つのうち先頭を黙って採ることは、
作成時の誤りを隠してしまうからです。

方針を表すキーについては、コマンドラインの層を現在と同じくシナリオとターゲット設定のあいだに置きます。
フラグはその実行1回への意図的な上書きであり、シナリオファイルはそれよりさらに具体的だからです。
有効と無効のフラグだけは例外で、現在と同じくもっとも外側の上書きのままにします。
`--no-system-alert-handling`は、ファイルに何が書いてあってもその実行でガードを止めるために
あるからです。

### 設定の規則が、自分で答えるシナリオへ届くときの通知

合成を認めると、BE-0382が意図して取り除いた状況が戻ってきます。独自の`labels`や`visionInstruction`を
持つシナリオの内側で、ターゲットの規則がそのプロンプトに答える状況です。具体性の並びから見れば挙動は
妥当であり、BE-0382の異議は、それが**黙って**起きることに向けられていました。プロジェクト全体への編集が、
どのプロンプトも名指ししていないシナリオを変えてしまう、という異議です。

そこでガードは、構築の時点で一度だけ通知を出します。通知は、対象のシナリオと、そこで答えることに
なるターゲットの規則を名指しします。この通知は、合成を保ったまま沈黙だけを取り除きます。実装は既存の
非推奨通知の経路（`bajutsu/deprecations.py`）の`warn_once`なので、デバイスの操作もモデルの呼び出しも
発生しません。

### `enabled`の削除。有効と無効は型が担う

有効と無効を表す方法をbool値の短縮形だけにし、マッピングの形はつねに有効を意味することにします。
シナリオのフィールドの型がそれを述べます。

```python
system_alert_handling: Literal[False] | SystemAlertHandling | None
```

`false`は無効、マッピングは有効であり方針を持ち、`true`は空のマッピングへ正規化され、キーがなければ
上の層を継承します。`{ enabled: false, rules: [...] }`という、実行時が何も告げずに捨てる方針は、
そもそも表現できなくなります。曖昧なセレクタが最初の一致をタップせずに失敗するのと同じ考え方です。

### 削除したキーと空の値で、はっきり失敗させる

シナリオのモデルは`extra="forbid"`なので、削除したキーはすでに読み込みに失敗します。ただし失敗の内容は
Pydanticの汎用的な「余分なフィールドは許可されていない」であり、置き換え先を名指ししません。そこで、
削除した各キーを明示的に捕まえ、置き換え先を告げて拒否します。

| 削除するもの | エラーが名指しするもの |
|---|---|
| `instruction` | リストの形には`labels`、文字列の形には`visionInstruction` |
| `enabled` | bool値の短縮形 |
| `alertHandling`、`dismissAlerts` | `systemAlertHandling` |

現在は黙って正規化されている3つの値も、決定性を優先する同じ理由でエラーにします。空の`labels`と空の
`visionInstruction`は、上の層へ、そして既定の拒否方針へ落ちるので、打ち間違いが作者の書いたことと
反対の答えを出します。`labels`に混ざった空文字列は取り除かれ、残りのエントリはそのまま効くので、
答えを反転させる代わりに打ち間違いを隠します。すべてのエントリが空のときだけ、拒否の既定へ落ちます。
正でない`pollInterval`はすでに例外を送出しており、そのままにします。

### コマンドラインの面

フラグはスキーマと1対1で対応させます。

| フラグ | 置き換えるもの |
|---|---|
| `--alert-labels "Allow,OK"` | 新設。`--alert-instruction`のネイティブ側 |
| `--alert-vision-instruction` | `--alert-instruction`を、方向づける経路にちなんで改名 |
| `--alert-poll-interval` | 新設。`pollInterval`にはフラグがなかった |
| `--system-alert-handling`、`--no-system-alert-handling` | 変更なし |
| （なし） | `--alert-handling`と`--dismiss-alerts`を削除 |

`record`と`crawl`も、改名した`--alert-vision-instruction`を受け取ります。どちらも視覚だけのガードを
組み立てる（`_build_alert_guard`、`bajutsu/cli/_shared.py`）ので、新しい名前は旧名よりこの2つの
コマンドを正確に説明します。

Web UIは、コマンドライン自身のオプションのメタデータから起動引数を導出しており
（[BE-0134](../BE-0134-serve-cli-flag-mirror-drift/BE-0134-serve-cli-flag-mirror-drift-ja.md)）、
すべてのフラグが分類されていることをテストが表明します。そのため、追加するフラグと削除するフラグの
分類を同じ変更で行います。ディスパッチ層は、リクエストボディの別名`alertHandling`と`dismissAlerts`も
受け付けています（`bajutsu/serve/operations/dispatch.py`）。この2つも、対応するスキーマのエイリアスと
同時に削除します。

### シナリオのスキーマバージョンは1のまま

`SCHEMA_VERSION`（`bajutsu/scenario/models/scenario.py`）は、実行中のbajutsuより新しいバージョンを
宣言したファイルを止めるための仕組みです。その引き上げの基準は、モジュール自身が
「読み込みを壊す変更、つまり必須フィールドの意味を取り除く変更か、古いbajutsuが拒否ではなく誤解釈する
変更のときだけ」と定めています。本提案に沿って書いたファイルは、古いbajutsuにとって未知のキーを持ち、
`extra="forbid"`がそれを拒否します。誤解釈することはありません。したがってバージョンは1のままにします。

### 作業の分解

1. **スキーマ。** `SystemAlertHandling`への`labels`と`visionInstruction`の追加、`enabled`と
   `instruction`の削除、シナリオとターゲット設定の双方への`Literal[False]`の共用体
   （`bajutsu/scenario/models/scenario.py`、`bajutsu/config/schema.py`）、削除した各キーの明示的な拒否、
   空の値の検証。
2. **層の合成。** `_alert_guard_factory`と`_apply_system_alert_handling`
   （`bajutsu/cli/commands/run.py`）でのリストの連結と、スカラの優先順位による解決。ターゲットの
   規則を抑制する処理の削除。通知の追加。`_vision_instruction`のフィールドを2つにすること。
3. **コマンドライン。** `run`、`record`、`crawl`のフラグの追加、改名、削除、
   `resolve_system_alert_handling_flag`のエイリアス統合の削除（`bajutsu/cli/_shared.py`）、
   Web UIのフラグ分類と、2つのリクエストボディの別名。
4. **ドキュメント。** `docs/scenarios.md`、`docs/dsl-grammar.md`、`docs/configuration.md`、
   `docs/cli.md`、`docs/cookbook.md`、`docs/recording.md`の`systemAlertHandling`の節と、
   それぞれの`docs/ja/`側の対応する節、および削除したキーの移行表。
5. **デモのシナリオ。** 古いキーを持つshowcaseのシナリオと設定。
6. **テスト。** 上の各単位を覆う決定的なテスト群。

### 機械的に検査できる結末

ゲートは`make check`であり、上のすべての挙動をSimulator不要のテスト群が覆います。削除した
各キーが置き換え先を名指しするエラーを送出すること。空の`labels`、空のラベル、空の`visionInstruction`が
例外を送出すること。`{ enabled: false }`が読み込めなくなり、素の`false`は引き続きガードを無効にすること。
ガードのファクトリがシナリオ、フラグ、ターゲットの各層で規則とラベルを連結し、2つのスカラを優先順位で
解決すること。ターゲットの規則が、独自のラベルを持つシナリオの中でそのプロンプトに答え、通知を一度だけ
出すこと。視覚フォールバックが、`visionInstruction`があればそれを受け取り、なければ`labels`から導いた
ヒントを受け取り、どちらの場合も`rules`からは何も受け取らないこと。どのアサーションもモデルに依存せず、
モデルへ届く呼び出しも増えません。

## 検討した代替案

**`instruction`を残し、文字列の形がネイティブ対応のバックエンドで使われたときに警告する。** 警告の実装は
分岐1つで済み、どのファイルも壊しません。それでも採らないのは、罠の正体がキーの形そのものだからです。
作者は依然として、リストと文字列が別の経路へ行くことを知っていなければならず、警告はファイルを書いた
あとに届くのであって、書く前に防ぎません。経路をキーの名前で述べれば、その誤りは書けなくなります。

**`rules`と`labels`を、順序付きの1つの`answers`へまとめる。** 各エントリがプロンプトと選択の組か、
リテラルなラベルのどちらかを取る形です。1つのリストなら合成の規則も適用の規則も1つずつで済み、本提案が
定める2つより単純です。それでも採らないのは、安全性が作者の書く順序に戻ってしまうからです。
`{ label: "Allow" }`のエントリを`{ prompt: tracking, choice: deny }`より上に置くとトラッキングを許可
します。これはBE-0382が`rules`を作って取り除いた、あの静かな反転そのものです。2つのキーを分けたままに
すれば、「プロンプト名はボタンラベルより強い」ことがスキーマの性質になり、作者が保ち続けるべき規律では
なくなります。

**BE-0382が定めたターゲット規則の抑制を残す。** 現在のスキーマの下で選べる2つの読み方のうち、抑制の
ほうが安全なのは確かです。抑制をやめれば、プロジェクト全体の規則が、どのプロンプトも名指ししていない
シナリオを変えうることも事実です。それでも採らないのは、その代償が本提案の目的そのものだからです。
抑制がある限り、作者は目の前のキーを見て、自分のどの宣言が実行されるのかを言い当てられません。具体性の
並びは同じ保護をより細かい粒度で与えます。シナリオは、同意できない1つの規則だけを名前で上書きします。
構築時の通知は、BE-0382の異議の実体であった沈黙を取り除きます。

**削除するキーを非推奨のエイリアスとして残す。** このリポジトリはこの設定について、BE-0317とBE-0327で
まさにそれを2度行っており、どちらのエイリアスもいまだに生きています。それでも今回採らないのは、
`instruction`のエイリアスが中立ではありえないからです。このキーは意味が型で分かれるので、エイリアスは
文字列を視覚経路へ、リストをネイティブ経路へ送り続けるほかなく、本提案が取り除こうとしている欠陥を
温存します。`instruction`を壊すことが避けられない以上、残り3つの名前を運ぶことは、同じ変更がすでに
求めている移行に上乗せの負担を足すだけです。

**`visionInstruction`を丸ごとやめ、フォールバックを`labels`だけで動かす。** そうすればフォールバック用の
自由文のキーは不要になり、残るキーはすべて決定的になります。それでも採らないのは、フォールバックが
まさにネイティブ経路の名指しできないアラートのために存在するからです。そこにはネイティブ経路を
まったく持たないバックエンド上のすべてのアラートが含まれます。正確なラベルを与えられない作者でも、
意図なら述べられます。このキーを削れば、それらのアラートには組み込みの既定以外の方向づけが
なくなります。

## 進捗

> 作業の進行に合わせて最新に保ってください。チェックリストは*詳細設計*のMECEな作業分解を反映し
> （作業単位ごとに1つ）、ログには変更内容と時期を古い順に記録し、PRへのリンクを添えます。

- [ ] スキーマ — `labels`、`visionInstruction`、`Literal[False]`の共用体、削除したキーのエラー、空の値の検証。
- [ ] 層の合成 — リストの連結、スカラの優先順位、抑制の削除、通知。
- [ ] コマンドライン — フラグの追加、改名、削除と、Web UIの対応。
- [ ] ドキュメント — 6ページとその日本語側、および移行表。
- [ ] デモのシナリオ — showcaseのシナリオと設定。
- [ ] テスト — 上の各単位を覆う決定的なテスト群。

## 参考

- [BE-0315 — リアクティブなアラートガードを、BE-0316 の SpringBoard 経路を再利用して決定論的・ネイティブにする](../BE-0315-ios-native-system-alert-handling/BE-0315-ios-native-system-alert-handling-ja.md)
  — 本提案がスキーマへ移すネイティブ経路とAI視覚フォールバックの分離、および分割の対象となる
  `instruction`キー。
- [BE-0382 — リアクティブなシステムアラートガードが、対応済みのプロンプトごとに個別の規則で応答できるようにする](../BE-0382-system-alert-per-prompt-rules/BE-0382-system-alert-per-prompt-rules-ja.md)
  — 本提案が変更せずに保つ`rules`キー、具体性の並びが延長する「`rules`のあとに`instruction`」の
  優先順位、および本提案が反転させるターゲット規則の抑制。
- [BE-0316 — iOS の権限プロンプトを明示的に操作する mid-flow ステップ](../BE-0316-ios-permission-alert-step/BE-0316-ios-permission-alert-step-ja.md)
  — `rules`が再利用する`prompt`と`choice`の語彙を持つプロアクティブなステップ。
- [BE-0320 — iOS システムアラートのボタン選択を Simulator のシステム言語によらず決定論的にする](../BE-0320-ios-system-alert-locale-determinism/BE-0320-ios-system-alert-locale-determinism-ja.md)
  — 規則がラベルを解決するロケール別のラベル表と、リテラルなラベル一覧が言語に縛られる理由。
- [BE-0177 — run のテスト動作設定にターゲット config の既定値を持たせる](../BE-0177-run-behavior-target-config/BE-0177-run-behavior-target-config-ja.md)
  — 本提案がシナリオとの合成を定義し直すターゲット設定の層。
- [BE-0317 — dismissAlerts ガードを、却下も許可も表す alertHandling に改名する](../BE-0317-rename-dismiss-alerts-to-alert-handling/BE-0317-rename-dismiss-alerts-to-alert-handling-ja.md)
  — 1度目の改名と、本提案が削除する`dismissAlerts`エイリアス。
- [BE-0327 — alertHandling ガードを、扱う対象を表す systemAlertHandling に改名する](../BE-0327-rename-alert-handling-to-system-alert-handling/BE-0327-rename-alert-handling-to-system-alert-handling-ja.md)
  — 2度目の改名と、本提案が削除する`alertHandling`エイリアス。
- [BE-0134 — serve と CLI のフラグ二重管理による drift をなくす](../BE-0134-serve-cli-flag-mirror-drift/BE-0134-serve-cli-flag-mirror-drift-ja.md)
  — 追加と削除のたびにWeb UIを追従させる取り決め。
- [`docs/ja/scenarios.md`](../../docs/ja/scenarios.md) — 本提案が書き換える`systemAlertHandling`の
  リファレンス。
