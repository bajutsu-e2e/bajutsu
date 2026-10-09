[English](BE-XXXX-real-device-step-lint.md) · **日本語**

# BE-XXXX — 実機ターゲットに対するシミュレータ専用ステップの lint と、明示的な除外記法 skipOnRealDevice

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-XXXX](BE-XXXX-real-device-step-lint-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **承認済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| トピック | プラットフォーム対応 |
| 関連 | [BE-0082](../BE-0082-capability-preflight-check/BE-0082-capability-preflight-check-ja.md), [BE-0128](../BE-0128-device-step-capability-preflight/BE-0128-device-step-capability-preflight-ja.md), [BE-0212](../BE-0212-granular-device-control-capabilities/BE-0212-granular-device-control-capabilities-ja.md), [BE-0238](../BE-0238-ios-device-cloud-execution/BE-0238-ios-device-cloud-execution-ja.md) |
<!-- /BE-METADATA -->

## はじめに

iOS Simulator 向けに書いたシナリオには、実機の iPhone で動かないステップがよく含まれます。
`setLocation`、`push`、クリップボードの各ステップは `simctl` を使い、`simctl` は Simulator にしか届きません。
現状の `bajutsu lint` はターゲットの設定を読まないため、この食い違いを検出できません。

この項目では、`bajutsu lint` がシミュレータ専用の[ステップ](../../docs/ja/glossary.md#シナリオのオーサリング)と前提条件を報告するようにします。
報告するのは、選んだ[ターゲット](../../docs/ja/glossary.md#target-app-device)が実機のときです。
明示的な除外には `skipOnRealDevice` を使い、必ず理由を書きます。

- ステップ修飾子 `skipOnRealDevice: "<理由>"` を付けると、実機ではランナーがそのステップを飛ばします。
- シナリオ直下の対応表 `skipOnRealDevice: { erase: "<理由>" }` に書くと、実機ではその[前提条件](../../docs/ja/glossary.md#シナリオのオーサリング)を飛ばします。
  対応表に書けるのは `erase`、`seedPhotos`、`permissions` です。

除外のないシミュレータ専用の記述は lint で失敗し、実機の実行でもデバイスを使う前に失敗します。
例外はターゲットの `appPath` のインストールだけです。
これはシナリオではなくターゲットの設定にあるため、Bajutsu は実機で通知を出して無視します。

## 動機

シミュレータ専用のステップは、実機で遅れて発覚します。実行時の絞り込み自体はすでにあります。
`capabilities_for_run`（`bajutsu/common/backends.py:194`）は、実機のターゲットで `simctl` 依存の能力を外します。
実機のターゲットとは、`xcuitest.deviceType: device` を指定したターゲットです。
外す能力は `DEVICE_CONTROL_ALL`、iOS の権限付与、`DEVICE_GROUP` です。
そのうえで BE-0082 の capability preflight が、デバイスを使う前にシナリオ全体を対象外にします。
BE-0238 はこの preflight の判定に依存しています。

ただし、この preflight が動くのは `doctor` と `run` の経路だけです。
`bajutsu lint`（`bajutsu/cli/commands/lint.py:10`）はシナリオファイルしか受け取らず、文法だけを検査し、ターゲットを見ません。
この欠落から、次の3つの負担が生じています。

- **シナリオを移す開発者が、食い違いに遅れて気付きます。** どのステップが動かないかを知るには、実機の設定で `run` か `doctor` を実行する必要があります。
  それまでは、シナリオの途中にある `setLocation` も問題がないように見えます。
- **1つのシナリオを両方のデバイスで使えません。** ステップをシミュレータ専用と示し、実機の実行で飛ばす記法がありません。
  回避策はシナリオを複製して該当ステップを消すことで、2つの複製はやがて食い違います。
- **前提条件の失敗に、先へ進む手段がありません。** 実機では `xcuitest_environment.py:337-350` が例外を投げます。
  対象は `erase`、`appPath` のインストール、`permissions` です。
  デフォルトで例外にするのは正しい振る舞いです。`erase: true` を求めたシナリオは、まっさらな状態を前提にしているためです。
  しかし、実機では汚れた状態を受け入れると決めた作者にも、それを表す手段がありません。

この項目が入ると、変化は1つのコマンドで確かめられます。
`--config` と実機の `--target` を付けて `bajutsu lint` を実行すると、終了コード 1 でシミュレータ専用のステップと前提条件を場所付きで列挙します。
この時点でデバイスは使いません。
次に、作者が食い違いを受け入れる箇所へ `skipOnRealDevice` を付けると、lint が通り、Simulator ではすべて実行されます。
実機でも実行でき、レポートは飛ばしたステップと前提条件を理由とともに示します。

## 詳細設計

設計は、判定、2つの除外記法、lint、実行時のスキップ、インストールと起動の前提条件の5つからなります。
どれもモデルの呼び出しや固定の待機を加えず、アプリごとの差はターゲットの設定に置いたままです。

### シミュレータ専用のステップと前提条件の判定

この項目は、シミュレータ専用のステップの一覧を別に持ちません。
唯一の根拠は、`capabilities_for_run` が実機で外す能力の集合です（`backends.py:236-243`）。
新しい関数 `real_device_dropped_capabilities()` がこの集合を返し、`capabilities_for_run` と lint の両方が呼びます。
実機の絞り込みに能力を足せば、同じコミットで lint の判定も変わります。

ステップと能力の対応表はすでにあります。
`capability_preflight.py`（`bajutsu/common/capability/` 配下）の `_REQUIREMENTS` がその表です。
各デバイス制御ステップを、BE-0212 の能力トークンに対応付けています。
同じ表は `permissions` の各サービスも、場所 `scenario.permissions` としてトークンに対応付けています。
lint は `capability_preflight.unsupported` の構造化した別版を呼びます（「lint」の節を参照）。
そのうち、能力が `real_device_dropped_capabilities()` に含まれる指摘を残します。

`erase` と `seedPhotos` には能力トークンがないため、lint が直接検査します。
`erase` は `run` と同じ規則で解決します。シナリオ自身の値を使い、なければターゲットの設定の `erase` を使います。

### 2つの `skipOnRealDevice` 除外記法

`Step` に、`name` や `capture` と同じ階層の修飾子を1つ追加します。

```python
# bajutsu/common/scenario/models/steps/step.py
skip_on_real_device: str | None = Field(default=None, alias="skipOnRealDevice")
```

| 規則 | 振る舞い |
|---|---|
| 値 | 空白でない理由の文字列。`Sleep.reason` と同じ検証を使い、空や空白だけの値は読み込み時に失敗します |
| アクションがちょうど1つという検査 | `steps/_shared.py` の `_MODIFIERS` にこのフィールドを加え、アクションとして数えないようにします |
| `if`、`forEach`、`web`、`app` に付けた場合 | 内側のすべてのステップが対象になります |
| `use` と `group` に付けた場合 | `expand.py` が展開後の各ステップへ修飾子を複製します。`_no_modifiers_on_use` は、`target`（BE-0446）に続く2つ目の例外としてこの修飾子を受け入れます |
| ターゲットグループ（`target` 付きの `steps:`）に付けた場合 | `_target_group` が受け入れ、`_expand_target_groups`（`models/scenario/_targets.py`）と `expand.py` のコンポーネント経路が内側の各ステップへ複製します |
| `manual` と `setPrimaryTarget` に付けた場合 | 読み込み時に拒否します |

2つの拒否には別々の理由があります。
`manual` ステップは、人が操作を引き継いだ箇所で実行を止めるためにあり、飛ばすとその停止が隠れます。
`setPrimaryTarget` ステップは以降のステップのターゲットの解決を変えるため、飛ばすと残りのシナリオの意味が変わります。

`Scenario` には、同じキーで対応表を追加します。

```python
# bajutsu/common/scenario/models/scenario/scenario.py
skip_on_real_device: dict[Literal["erase", "seedPhotos", "permissions"], str] = Field(
    default_factory=dict, alias="skipOnRealDevice"
)
```

各値は空白でない理由で、ステップ修飾子と同じ検証を使います。未知のキーは読み込み時に失敗します。

### lint

`lint_text` と `lint_diagnostics`（`bajutsu/common/lint.py`）にキーワード引数を追加します。

```python
def lint_text(
    text: str, *, real_device: RealDeviceLint | None = None, source: Path | None = None
) -> list[str]: ...
def lint_diagnostics(
    text: str, *, real_device: RealDeviceLint | None = None, source: Path | None = None
) -> list[Diagnostic]: ...
```

`RealDeviceLint` はターゲット名をキーにします。
ターゲットごとに、実行時の能力集合、解決した `erase` のデフォルト値、実機かどうかを持ちます。
`source` はシナリオファイルのパスです。実機向けの検査はこのパスを起点にコンポーネントを展開するため、`real_device` を渡すときは `source` も必須です。
デフォルトの `real_device=None` では、どちらの関数も現状と同じに振る舞います。
値を渡すと、文法の検査が通ったあとで次の3つを検査します。

| 検査 | 指摘する条件 |
|---|---|
| ステップ | `unsupported` が示すステップの場所を、どの `skipOnRealDevice` 修飾子も対象にしていない |
| 前提条件 | `erase`、`seedPhotos`、`permissions` のいずれかのサービスが有効で、シナリオの対応表に名前がない |
| 飛ばすステップの `extract` | `skipOnRealDevice` の対象のステップが `extract` を持つ |

3つ目の検査は、後続のステップが `extract` の設定する変数を読むことがあるためです。
実機では飛ばしたステップがその変数を設定しないため、後続のステップは見当違いの原因を示すエラーで失敗します。
`capture` は許可します。飛ばしたステップは証跡を残しませんが、証跡に依存する後続のステップはありません。

実機向けの検査は、実行の preflight と同じく展開後のシナリオに対して行います。
現状の lint は展開前のファイルを検証し、preflight の走査が入るのは `if` と `forEach` の内側だけです。
そのため、`group:` の内側や `use:` が取り込むコンポーネントの中の `setLocation` は、lint を通ったあとで実機の実行に失敗します。
展開後を検査すれば、2つの判定は一致します。

そこで、すべてのステップが自身の出どころを記録します。
展開の有無にかかわらず、各ステップは非公開の `source_loc` を持ちます。これはシナリオファイル中の呼び出し位置を示す YAML の場所です。
規則は次のとおりです。

- 展開から生じていないステップは、自身の位置を記録します。
- コンポーネントの中のステップは、そのコンポーネントを取り込んだ `use:` のステップを記録します。
- `group:` の中のステップは、平らにする前の自身の位置を記録します。
- ターゲットグループの子は、`_expand_target_groups` の中で場所を受け取ります。この関数は読み込み時に `Scenario` の検証の中で、`expand.py` より先に動きます。
- すでに `source_loc` を持つステップは、展開が再び動いてもその値を保ちます。

ターゲットグループを平らにすると後続のステップの番号がずれるため、lint が信頼する場所は `source_loc` だけです。

`lint_diagnostics` には、指摘ごとに構造化された場所が必要です。
`unsupported()` が返すのは `"step 3 > if > then[0]: setLocation …"` のような読みやすい文字列で、既存の YAML ノードの走査はこれを読めません。
そこで `capability_preflight` に、`(ステップ, covering_reason, 理由)` の3つ組を返す `unsupported` の別版を追加します。
`covering_reason` は、そのステップ自身か、それを囲む `if`、`forEach`、`web`、`app` のステップのうち、もっとも近い `skipOnRealDevice` の理由です。
対象外のステップでは `None` です。ステップの検査と `extract` の検査は、後述する実行の preflight と同様にこの値を読みます。
lint は各ステップの `source_loc` を読み、既存の走査（`_resolve_line`、`lint.py:154`）がそれを行番号に対応付けます。
コンポーネントの中の指摘は、そのコンポーネントを取り込んだ `use:` の行に付きます。

コマンドラインインタフェース（CLI）には、`bajutsu/cli/commands/lint.py` で `--config <path>` と `--target <name>` を追加します。
lint は、実行時と同じ方法でターゲットを解決します。

- 自身の `targets` を宣言するシナリオには `--target` は不要です。lint は宣言された各ターゲットを設定から解決します。
- 何も宣言しないシナリオは、`--target` で指定したターゲットを使います。

`xcuitest_targets_real_device(eff)` が真のターゲットを実機とみなします。この関数は `bajutsu/common/config/accessors.py:79` にあります。
検査は、実行の preflight と同じくターゲットごとに行います（`_preflight_targets`、`pipeline.py:336-357`）。
各ターゲットは、そこへ振り分けられたステップ（`_steps_for_target`）について、自身の能力集合で判定されます。
Simulator のターゲットへ振り分けられたステップは、同じシナリオの別のターゲットが実機でも指摘しません。
前提条件と `permissions` はシナリオ単位で、ランナーは宣言されたすべてのターゲットに適用します（`_lease_targets`）。
そのため前提条件の検査は、宣言されたターゲットのどれか1つが実機であれば動きます。
解決したターゲットに実機が1つもなければ、lint は新しい指摘を出しません。
serve のエディタの lint（`bajutsu/serve/operations/lint.py`）は保存前のテキストを扱うため、`/api/lint` のリクエスト本文に2つの入力を追加します。
1つ目はエディタで選択中のターゲット名で、serve はリクエストに結び付いた設定でこれを解決します。
2つ目はシナリオファイルのパスで、`load_expanded_scenarios` がディスク上のファイルに対して行うのと同じように、コンポーネントの参照の起点になります。
serve は既存の `_scenario_path(scenarios_dir, p)`（`bajutsu/serve/helpers.py:549`）でこのパスを解決します。
解決の基準は、リクエストに結び付いた設定のシナリオディレクトリです。
そのディレクトリを包含の `root` として渡し、外側のパスは拒否します。
このため、展開の処理にはテキストを入力とする入口を追加します。
ターゲットの指定がなければ、エディタの lint は新しい指摘を出しません。

検査を実機のターゲットに限るのは意図した設計です。
`demos/showcase/` のいくつかのシナリオはデバイス制御ステップを使い、Simulator だけを対象にしています。
ターゲットを問わない検査にすると、実機を使わないチームにも、それらすべてへの注釈を求めることになります。

### 実行時のスキップ

実機のターゲットでは、実行の preflight（`bajutsu/common/runner/pipeline.py:356` と `:539`）も同じ3つを検査します。
除外記法の対象になった場所を除くのは、実機のターゲットのときだけです。
`unsupported` の他の呼び出し元は現状の振る舞いを保ちます。Simulator の実行、他のバックエンド、`doctor`、アクチュエータの選択（`backends.py:402`）です。
そのため、`skipOnRealDevice` を付けた `setLocation` も、Android のターゲットでは飛ばす仕組みがないため、従来どおり早期に失敗します。

ステップのループは、振り分け先のターゲットが実機のとき、`skipOnRealDevice` 付きのステップを飛ばします。
判定はステップを実行する直前に1回だけ行います。
Simulator のターゲットへ振り分けられたステップは、ほかのターゲットが実機のシナリオでも通常どおり実行します。

`StepOutcome`（`bajutsu/common/orchestrator/types/step_outcome.py`）には、現状スキップの状態がありません。
持つのは `ok: bool` と `reason` だけです。
ここに `skipped: bool = False` を追加し、`reason` に修飾子の文字列を入れます。
スキップしたステップはアサーションを行わないため、失敗するシナリオを合格に変えることはありません。
各レポート形式は、その形式が持つ粒度でスキップを描きます。

| 形式 | 描き方 |
|---|---|
| マニフェスト | 各ステップの記録が `skipped` と理由を持ちます |
| HTML レポート | ステップの行を合格ではなくスキップとして、理由とともに示します |
| JUnit | 判定は変えません。シナリオごとに1つの `<testcase>` のままです（`junit_xml`、`report/manifest.py:320-335`） |
| Common Test Report Format（CTRF） | テストの状態は変えず、ステップの記録（`report/ctrf.py`）の状態を `skipped` にし、理由を `extra` に入れます |

JUnit にはステップの記録がないため、スキップしたステップを状態として示せません。
`<testcase>` 全体を `<skipped>` にすると、ほかのステップやアサーションを実行したシナリオを誤って報告します。
そこでシナリオは自身の判定を保ちます。
`<properties>` に `bajutsu.skippedSteps` と `bajutsu.skippedPreconditions` を加え、`<system-out>` に飛ばした各ステップと理由を列挙します。
CTRF はステップごとの記録をすでに持つため、飛ばしたステップはステップの状態として示せます。飛ばした前提条件は、テストの `extra` 欄に記録します。

### インストールと起動の前提条件

実機では、XCUITest 環境の開始処理が現状は例外を投げます（`xcuitest_environment.py:337-350`）。
同じ箇所のコメントが理由を述べています。適用できない指定は、黙って何もしないのではなく、明示的に失敗させます。
この項目はこのデフォルトを保ち、除外記法を追加します。

| 前提条件 | 実機で除外がない場合 | 実機でシナリオの対応表に名前がある場合 |
|---|---|---|
| `preconditions.erase` | lint と実行の preflight で失敗します | 飛ばし、通知を出し、理由をレポートに残します |
| `preconditions.seedPhotos` | lint と実行の preflight で失敗します | 飛ばし、通知を出し、理由をレポートに残します |
| `permissions` | lint と実行の preflight で失敗します | 飛ばし、通知を出し、理由をレポートに残します |
| ターゲットの `appPath` のインストールと `reinstall` | 通知を出して飛ばします | 該当しません |

除外記法を求める理由は、前提条件を飛ばすとシナリオの検査内容が変わることにあります。
実機は実行をまたいでアプリのデータを保つため、`erase` を飛ばすと前の実行の状態が次の実行に持ち込まれます。
`permissions` を飛ばすと各権限はデバイスの現状のままになり、権限ダイアログが出ることもあります。
理由を書いた作者は、実機に限ってこの引き換えを受け入れたことになります。
`seedPhotos` が `erase: true` を要求する読み込み時の規則（`preconditions.py:38`）は残します。

前提条件はステップではないため、`StepOutcome.skipped` では記録できません。
`RunResult`（`bajutsu/common/orchestrator/types/run_result.py`）に `skipped_preconditions: dict[str, str]` を追加します。
マニフェストには `skippedPreconditions` として書き出し、各前提条件の名前を理由に対応付けます。
HTML レポート、JUnit、CTRF は、この欄を飛ばしたステップと同じ方法で描き、判定は変えません。

`appPath` のインストールには除外記法を求めません。ターゲットの設定にあるため、シナリオからは名指しできないからです。
BE-0238 の実機のターゲットは、アプリの事前インストールをすでに前提にしています。

`erase_precondition_supported`（`backends.py:247`）は、実機では引き続き `False` を返します。
この関数はクラッシュ後の再試行で `erase` を強制してよいかを判定するもので、別の問題を扱っています。

### 対象外

- **Android の実機。** Android のターゲット設定には、エミュレータと実機を区別するフィールドがありません。
  そのため lint には判定の手がかりがありません。後続の項目がフィールドを追加すれば、この設計を再利用できます。
- **BE-0238 のライブ WebDriver の経路。** `xcuitest_live.py:87` は別の理由で `erase` を拒否しています。
  この項目が変えるのは、ローカルの `deviceType: device` の経路だけです。
- **実機向けの代替の分岐。** 実機で別のステップを実行するには `if` の拡張が必要です。
  この拡張は除外の記法より広いため、別の項目で扱います。
- **`web:` と `app:` ブロックの内側のステップ。** preflight は現状、これらのブロックの内側を走査しません（`capability_preflight.py:93-99`）。
  この項目はこの欠落を引き継ぎ、走査の範囲は広げません。

## 検討した代替案

| 代替案 | 概要 | 採らなかった理由 |
|---|---|---|
| ターゲットを問わずに失敗させる | 注釈のないシミュレータ専用ステップを、すべての lint で指摘します | シミュレータ専用の showcase のシナリオすべてに、実機を使わないチームからも注釈が必要になります |
| オプトインのフラグ（`--portable`） | フラグを付けたときだけ指摘します | フラグを付け忘れると検出されません。検査のきっかけは実機のターゲットであり、フラグではありません |
| YAML のコメント（`# bajutsu: skip`） | `# noqa` 型の抑制です | パーサーがコメントを捨てるため、編集や `record` の往復で消えます。機械可読な宣言になりません |
| ブロック形式 `simulatorOnly: {reason, steps}` | `group` のような囲みのブロックです | 新しい制御構文には、codegen、エディタ、入れ子の規則の追加が要ります。ステップ単位の修飾子で要件を満たせます |
| シミュレータ専用の一覧を別に持つ | 能力トークンとは別の表を持ちます | 一覧と実機の絞り込みが食い違っていきます |
| スキップせず lint だけ黙らせる | 指摘は消し、実行時の失敗は残します | 1つのシナリオを両方のデバイスで使えないままです |
| 前提条件を自動で無視する | `erase`、`seedPhotos`、`permissions` を実機では通知だけで飛ばします | 通知は判定を変えません。前の実行が残した状態によって、シナリオの合否が変わります |
| 飛ばすステップの `extract` を許す | 変数を未設定のままにし、後の参照で失敗させます | 後の参照は、見当違いの原因を示すエラーで失敗します |

ターゲットを問わない検査は、Android が設定で実機を表せるようになった時点で再検討する価値があります。

## 進捗

> 作業の進行に合わせて更新してください。チェックリストは「詳細設計」の MECE な作業分解
> （作業単位ごとに 1 つのチェックボックス）を反映し、ログには変更内容と日付を古い順に記録して PR へのリンクを付けます。

- [ ] `StepOutcome` に `skipped` を追加し、マニフェスト、HTML、CTRF ではステップごとに、JUnit ではシナリオの判定を保ったままメタデータとして描画する
- [ ] ステップ修飾子 `skipOnRealDevice` と読み込み時の規則を追加する（`_no_modifiers_on_use` と `_target_group` の例外を含む）
- [ ] `use`、`group`、ターゲットグループの展開で、展開後のステップへ修飾子を複製する
- [ ] `_expand_target_groups` と `expand.py` で全ステップに `source_loc` を記録し（再展開でも保持）、展開の処理にテキストを入力とする入口を追加する
- [ ] シナリオ直下の対応表 `skipOnRealDevice` と読み込み時の規則を追加する
- [ ] `real_device_dropped_capabilities()` を切り出し、`capabilities_for_run` と共有する
- [ ] `(ステップ, covering_reason, 理由)` の3つ組を返す `unsupported` の別版を追加する
- [ ] 実機向けの検査（ステップ、前提条件、飛ばすステップの `extract`）を、展開後のシナリオに対して行うよう `lint_text` と `lint_diagnostics` に追加する
- [ ] `bajutsu lint` に `--config` と `--target` を追加し、宣言されたターゲットを解決して、各ターゲットを振り分けられたステップで検査する
- [ ] serve のエディタの `/api/lint` リクエストで選択中のターゲットとファイルのパスを送り、パスを結び付いたシナリオディレクトリの中で解決する
- [ ] 実機のターゲットに限り、実行の preflight で実機向けに検査する
- [ ] 実機のターゲットで、ステップのループが `skipOnRealDevice` 付きのステップを飛ばす
- [ ] 実機で、名前のある前提条件を飛ばし、`appPath` のインストールを無視する（それぞれ通知を出す）
- [ ] `RunResult` に `skippedPreconditions` を追加し、マニフェスト、HTML、JUnit、CTRF の各レポートで描画する
- [ ] 2つの除外記法と lint を `docs/` と `docs/ja/` に記載する（`dsl-grammar.md`、`scenarios.md`、`cli.md`、`drivers.md`）。
  実機の経路について `DESIGN.md` と `docs/architecture.md` を更新する

## 参考

- `bajutsu/common/backends.py:194-243`：`capabilities_for_run` と実機の絞り込み
- `bajutsu/common/capability/capability_preflight.py`：`_REQUIREMENTS` と `unsupported()`
- `bajutsu/common/runner/pipeline.py:356,539`：実行の preflight の呼び出し箇所
- `bajutsu/common/lint.py`、`bajutsu/cli/commands/lint.py`：設定を読まない現状の lint
- `bajutsu/common/config/accessors.py:79`：`xcuitest_targets_real_device`
- `bajutsu/common/orchestrator/types/step_outcome.py`：`StepOutcome`
- `xcuitest_environment.py:337-350`：実機で前提条件に投げる例外。
  ファイルは `bajutsu/common/platform_lifecycle/environments/xcuitest/` にあります
- `bajutsu/common/scenario/models/steps/step.py`、`steps/_shared.py`：`Step`、その検証、`_MODIFIERS`
