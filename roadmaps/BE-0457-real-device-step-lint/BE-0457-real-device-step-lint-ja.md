[English](BE-0457-real-device-step-lint.md) · **日本語**

# BE-0457 — ターゲットの capability で実行できないステップの lint とスキップ、明示的な除外記法 skipIfUnsupported

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0457](BE-0457-real-device-step-lint-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装中** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0457") |
| トピック | プラットフォーム対応 |
| 関連 | [BE-0082](../BE-0082-capability-preflight-check/BE-0082-capability-preflight-check-ja.md), [BE-0128](../BE-0128-device-step-capability-preflight/BE-0128-device-step-capability-preflight-ja.md), [BE-0212](../BE-0212-granular-device-control-capabilities/BE-0212-granular-device-control-capabilities-ja.md), [BE-0238](../BE-0238-ios-device-cloud-execution/BE-0238-ios-device-cloud-execution-ja.md) |
<!-- /BE-METADATA -->

## はじめに

iOS Simulator 向けに書いたシナリオには、実機の iPhone で動かないステップがよく含まれます。
`setLocation`、`push`、クリップボードの各ステップは `simctl` を使い、`simctl` は Simulator にしか届きません。
現状の `bajutsu lint` はターゲットの設定を読まないため、この食い違いを検出できません。

この項目では、選んだ[ターゲット](../../docs/ja/glossary.md#target-app-device)で実行できない[ステップ](../../docs/ja/glossary.md#シナリオのオーサリング)と前提条件を、`bajutsu lint` が報告するようにします。
判定に使うのはターゲット自身の capability の集合で、run の preflight がすでに読んでいるものと同じです。
実機は `simctl` に依存する capability をすべて持たないため、この項目のきっかけになった代表例です。
明示的な除外には `skipIfUnsupported` を使い、必ず理由を書きます。

- ステップ修飾子 `skipIfUnsupported: "<理由>"` を付けると、振り分け先のターゲットがそのステップに必要な capability を持たないとき、ランナーはステップを飛ばします。
- シナリオ直下の対応表 `skipIfUnsupported: { erase: "<理由>" }` に書くと、ターゲットが適用できないときに、その[前提条件](../../docs/ja/glossary.md#シナリオのオーサリング)を飛ばします。
  対応表に書けるのは `erase`、`seedPhotos`、`permissions` です。

この名前は、デバイスの種類ではなく、条件と動作を表します。
ステップを実行できるかどうかは環境の capability で決まるため、除外記法はその依存関係をそのまま名前にしています。
除外のない不足は lint で失敗し、run でもデバイスを使う前に失敗します。
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
次に、作者が食い違いを受け入れる箇所へ `skipIfUnsupported` を付けると、lint が通り、Simulator ではすべて実行されます。
実機でも実行でき、レポートは飛ばしたステップと前提条件を理由とともに示します。

## 詳細設計

設計は、判定、2つの除外記法、lint、実行時のスキップ、インストールと起動の前提条件の5つからなります。
どれもモデルの呼び出しや固定の待機を加えず、アプリごとの差はターゲットの設定に置いたままです。

### ターゲットが実行できないステップと前提条件の判定

この項目は、実機で動かないステップの一覧を別に持ちません。
唯一の根拠は、`capabilities_for_run` が各ターゲットに返す capability の集合です（`backends.py:270`）。
実機では、この集合からすでに `simctl` 依存のトークンが外れています。
lint と run はどちらも、この集合に照らしてステップを判定します。
そのため、どのターゲットの絞り込みにトークンを足しても、同じコミットで両方の判定が変わります。
外れるトークンのうち `DEVICE_GROUP` だけは、`unsupported` が扱いません。`_REQUIREMENTS` に項目がないためです。
デバイスグループは `_preflight_targets`（`pipeline.py:339`）が `unsupported` より先に、独自に拒否します。
lint もこの拒否を再現し、`DEVICE_GROUP` を持たないターゲットをメンバーに含む `deviceGroups:` の宣言をエラーにします。
デバイスグループがなければ実行も進められないため、除外記法は用意しません。

ステップと能力の対応表はすでにあります。
`capability_preflight.py`（`bajutsu/common/capability/` 配下）の `_REQUIREMENTS` がその表です。
各デバイス制御ステップを、BE-0212 の能力トークンに対応付けています。
同じ表は `permissions` の各サービスも、場所 `scenario.permissions` としてトークンに対応付けています。
lint と run は `capability_preflight.unsupported` の構造化した別版を呼びます（「lint」の節を参照）。
別版は、ターゲット自身の capability の集合に対するすべての指摘を返します。

`erase` と `seedPhotos` には能力トークンがないため、lint が直接検査します。
検査の基準は、その経路が前提条件を適用できるかどうかです。現状では、ローカルの実機 XCUITest の経路が適用できません。
`erase` は `run` と同じ規則で解決します。シナリオ自身の値を使い、なければターゲットの設定の `erase` を使います。

### 2つの `skipIfUnsupported` 除外記法

`Step` に、`name` や `capture` と同じ階層の修飾子を1つ追加します。

```python
# bajutsu/common/scenario/models/steps/step.py
skip_if_unsupported: str | None = Field(default=None, alias="skipIfUnsupported")
```

| 規則 | 振る舞い |
|---|---|
| 値 | 空白でない理由の文字列。`Sleep.reason` と同じ検証を使い、空や空白だけの値は読み込み時に失敗します |
| アクションがちょうど1つという検査 | `steps/_shared.py` の `_MODIFIERS` にこのフィールドを加え、アクションとして数えないようにします |
| `if`、`forEach`、`web`、`app` に付けた場合 | ブロックが 1 つの skip 単位になります。内側のどこかに未対応のステップが 1 つあれば、ブロック全体を飛ばします |
| `use` と `group` に付けた場合 | `expand.py` は、理由と共通の skip 単位の ID を展開後のすべてのステップに付けます。ステップ自身の理由が同じなら受け入れ、異なれば拒否します。これは `_merge_target`（`expand.py:77`）が `target` にすでに適用している規則です。`_no_modifiers_on_use` は、`target`（BE-0446）に続く2つ目の例外としてこの修飾子を受け入れます |
| ターゲットグループ（`target` 付きの `steps:`）に付けた場合 | `_target_group` が受け入れ、`_expand_target_groups`（`models/scenario/_targets.py`）と `expand.py` のコンポーネント経路が、内側の各ステップに同じ単位を付けます |
| `manual` と `setPrimaryTarget` に付けた場合 | 読み込み時に拒否します |
| `manual` ステップを含む `if`、`forEach`、`web`、`app` ブロックに付けた場合 | 読み込み時に拒否します。`use` と `group` では、複製された修飾子が展開時に内側の `manual` に届き、そこで拒否します |

2つの拒否には別々の理由があります。
`manual` ステップは、人が操作を引き継いだ箇所で実行を止めるためにあり、飛ばすとその停止が隠れます。
`setPrimaryTarget` ステップは以降のステップのターゲットの解決を変えるため、飛ばすと残りのシナリオの意味が変わります。
`_targets.py:320` は `setPrimaryTarget` をトップレベルのステップに限っているため、ブロックがこれを覆うことはありません。
`manual` にはこの制限がないため、`manual` を含むブロックへの付与も拒否します。
拒否しなければ、ブロックがステップ単位の規則を迂回し、明示的な停止が黙った合格に変わります。

**skip 単位**は、run がまとめて飛ばす範囲です。
capability の不足は 1 つのステップについての事実ですが、作者の意図は複数のステップにまたがることがよくあります。
`setLocation` と、それが設定した場所を読む `assert` は一体です。
visual 以外の単独の `assert` は capability を必要としないため、`assert` だけに修飾子を付けても働きません。
2つを 1 つの `group:` にまとめて修飾子を付けると、`assert` は原因のステップと一緒に飛びます。
展開はこの単位を隠しフィールド `Step.skip_unit_id` に記録します。
`report_group_id` と同じく、run が自身の `scenario.yaml` を読み直しても失われません。
`group`、`use`、ターゲットグループの外にある修飾子付きのステップは、それだけで 1 つの単位になります。

`Scenario` には、同じキーで対応表を追加します。

```python
# bajutsu/common/scenario/models/scenario/scenario.py
skip_if_unsupported: dict[Literal["erase", "seedPhotos", "permissions"], str] = Field(
    default_factory=dict, alias="skipIfUnsupported"
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
| ステップ | `unsupported` が示すステップの場所を、どの `skipIfUnsupported` 修飾子も対象にしていません。`scenario.permissions` の場所はここでは除き、次の行に任せます。シナリオ単位のフィールドは、ステップ修飾子では覆えないためです |
| 前提条件 | `erase`、`seedPhotos`、または `permissions` のいずれかのサービスが有効で、シナリオの対応表に名前がありません |
| 飛ばすステップの `extract` | `skipIfUnsupported` の対象のステップが `extract` を持ちます |

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
そこで `capability_preflight` に、各指摘をそれを覆う skip 単位とともに返す `unsupported` の別版 `unsupported_findings` を追加します。
単位は、そのステップ自身か、それを囲む `if`、`forEach`、`web`、`app` のステップのうち、もっとも外側の `skipIfUnsupported` です。囲む単位が優先されるためです。
対象外のステップでは `None` です。ステップの検査と `extract` の検査は、後述する実行の preflight と同様にこの値を読みます。
lint は各ステップの `source_loc` を読み、既存の走査（`_resolve_line`、`lint.py:154`）がそれを行番号に対応付けます。
コンポーネントの中の指摘は、そのコンポーネントを取り込んだ `use:` の行に付きます。

コマンドラインインタフェース（CLI）には、`bajutsu/cli/commands/lint.py` で `--config <path>` と `--target <name>` を追加します。
lint は、実行時と同じ方法でターゲットを解決します。

- 自身の `targets` を宣言するシナリオには `--target` は不要です。lint は宣言された各ターゲットを設定から解決します。
- 何も宣言しないシナリオは、`--target` で指定したターゲットを使います。
  このようなシナリオで `--config` だけを渡し `--target` を省くと、指摘なしで通すのではなく、使い方の誤りとして終了コード 2 で終わります。
  フラグの付け忘れで指摘が隠れることはありません（`doctor` もすでに `--target` を必須にしています。`doctor.py:208`）。

検査は、実行の preflight と同じくターゲットごとに行います（`_preflight_targets`、`pipeline.py:339`）。
lint は各ターゲットを、そこへ振り分けられたステップ（`_steps_for_target`）について、自身の能力集合で判定します。
振り分け先のターゲットが対応しているステップは、同じシナリオの別のターゲットに capability がなくても指摘しません。
前提条件と `permissions` はシナリオ単位で、ランナーは宣言されたすべてのターゲットに適用します（`_lease_targets`）。
そのため前提条件の検査は、宣言されたターゲットのどれか1つが適用できない前提条件を報告します。
`--config` がない場合、lint は新しい指摘を出しません。
serve のエディタの lint（`bajutsu/serve/operations/lint.py`）は保存前のテキストを扱うため、`/api/lint` のリクエスト本文に2つの入力を追加します。
1つ目はエディタで選択中のターゲット名で、serve はリクエストに結び付いた設定でこれを解決します。
2つ目はシナリオファイルのパスで、`load_expanded_scenarios` がディスク上のファイルに対して行うのと同じように、コンポーネントの参照の起点になります。
serve は既存の `_scenario_path(scenarios_dir, p)`（`bajutsu/serve/helpers.py:549`）でこのパスを解決します。
解決の基準は、リクエストに結び付いた設定のシナリオディレクトリです。
そのディレクトリを包含の `root` として渡し、外側のパスは拒否します。
このため、展開の処理にはテキストを入力とする入口を追加します。
ターゲットの指定がなければ、エディタの lint は新しい指摘を出しません。

検査を解決したターゲットに限るのは意図した設計です。
`demos/showcase/` のいくつかのシナリオはデバイス制御ステップを使い、Simulator だけを対象にしています。
Simulator はそれらのステップに対応しているため、注釈がなくても lint を通ります。
ターゲットを問わない検査にすると、実機を使わないチームにも注釈を求めることになります。

### 実行時のスキップ

実行の preflight も、`capability_preflight.run_preflight` を通して同じように検査します。
`bajutsu/common/runner/pipeline.py` の 2 つの呼び出し箇所（ターゲットごとの経路と単一ターゲットの経路）がどちらもそうします。
どの単位にも覆われない指摘は、これまでどおりデバイスを使う前にシナリオを失敗させます。
覆われた指摘は、その単位を step loop が飛ばす集合に加えます。
そのため、単位の中に未対応のステップが 1 つあれば、単位のステップをすべて飛ばします。
バックエンドがシナリオを実行できるかどうかだけを問う 2 つの呼び出し元は、引き続き `unsupported` を使います。`doctor` とアクチュエータの選択（`backends.py`）です。

ステップのループは、skip 単位がその集合に含まれるステップを飛ばします。
判定はステップを実行する直前に1回だけ行います。飛ばしたステップは、ドライバを動かさず、結果だけを記録します。
修飾子が付いていても、振り分け先のターゲットが対応しているステップは通常どおり実行します。
capability の不足だけが飛ばす条件なので、実行して失敗したステップは失敗のままです。

`StepOutcome`（`bajutsu/common/orchestrator/types/step_outcome.py`）には、現状スキップの状態がありません。
持つのは `ok: bool` と `reason` だけです。
ここに `skipped: bool = False` を追加し、`reason` に修飾子の文字列を入れます。
`assert` ステップを飛ばすと、確認は満たされるのではなく取り除かれます。
`setLocation` が設定した場所のように、Simulator でしか作れない状態を読む確認こそ、この項目が扱う場面です。
そうした `assert` は、原因のステップと skip 単位を共有することで一緒に飛びます。
取り除いた確認が合格と読まれないよう、次の3つの規則を置きます。

- 飛ばしたアサーションを合格として数えません。レポートには理由とともにスキップとして示します。
- run がすべての `assert` ステップを飛ばしたシナリオは、何も確認せずに合格とせず、失敗にします。シナリオ直下の `expect` もここではアサーションに数え、どの skip も `expect` を取り除きません。run がすべてのステップを飛ばしたシナリオも、同じく失敗にします。
- 解決したターゲットで飛ばされる各 `assert` ステップを、lint が注意の行として示し、そのターゲットの実行で落ちる確認をレビュアーに見せます。この注意で lint は失敗しません。

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

- **Android の実機での絞り込み。** Android のターゲット設定には、エミュレータと実機を区別するフィールドがありません。
  そのため、実機でも Android の capability の集合はまだ絞り込まれません。
  除外記法は、Android のターゲットが持たない capability にはすでに働きます。
  後続の項目がフィールドを追加して集合を絞り込めば、この設計を変えずに済みます。
- **BE-0238 のライブ WebDriver の経路。** `xcuitest_live.py:87` は別の理由で `erase` を拒否しています。
  この項目が変えるのは、ローカルの `deviceType: device` の経路だけです。
- **実機向けの代替の分岐。** 実機で別のステップを実行するには `if` の拡張が必要です。
  この拡張は除外の記法より広いため、別の項目で扱います。
- **`web:` と `app:` ブロックの内側のステップ。** preflight は現状、これらのブロックの内側を走査しません（`capability_preflight.py:93-99`）。
  シミュレータ専用の判定については、この項目はこの欠落を引き継ぎ、走査の範囲は広げません。
  例外は `extract` の検査です。`web:` や `app:` のステップに付けた `skipIfUnsupported` は、実行時にブロック全体を飛ばします。
  そのためこの検査は `web.steps` と `app.steps` も走査し、ブロック内で飛ばされる `extract` もほかと同じく報告します。

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
| 実機を条件にする（`skipOnRealDevice`） | この項目が最初に提案した名前です | 理由ではなくデバイスの種類を名前にしています。ライブ WebDriver の経路、Android、web で同じ不足が起きると、別の名前が必要になります |
| 真偽値のフラグ（`runOnlySupported: true`） | 対応している環境でだけ実行する印です | レビュアーとレポートに理由が残りません。`false` はデフォルトと同じ意味しか持ちません。ブロックに付けると「対応している子だけを実行する」と読め、skip 単位の規則と逆になります |
| 飛ばすステップの `extract` を許す | 変数を未設定のままにし、後の参照で失敗させます | 後の参照は、見当違いの原因を示すエラーで失敗します |

ターゲットを問わない検査は、Android が設定で実機を表せるようになった時点で再検討する価値があります。

## 進捗

> 作業の進行に合わせて更新してください。チェックリストは「詳細設計」の MECE な作業分解
> （作業単位ごとに 1 つのチェックボックス）を反映し、ログには変更内容と日付を古い順に記録して PR へのリンクを付けます。

- [x] `StepOutcome` に `skipped` を追加し、マニフェスト、HTML、CTRF ではステップごとに、JUnit ではシナリオの判定を保ったままメタデータとして描画する
- [x] ステップ修飾子 `skipIfUnsupported` と読み込み時の規則を追加する（`_no_modifiers_on_use` と `_target_group` の例外を含む）
- [x] `use`、`group`、ターゲットグループの展開で、展開後のステップへ修飾子と共通の skip 単位を付ける
- [ ] `_expand_target_groups` と `expand.py` で全ステップに `source_loc` を記録し（再展開でも保持）、展開の処理にテキストを入力とする入口を追加する
- [ ] シナリオ直下の対応表 `skipIfUnsupported` と読み込み時の規則を追加する
- [x] ~~`real_device_dropped_capabilities()` を切り出し、`capabilities_for_run` と共有する~~
  - 不要になりました。検査は各ターゲット自身の `capabilities_for_run` の集合を読むため、切り出すべき実機用の集合がありません。
- [x] 各指摘を、それを覆う skip 単位とともに返す `unsupported` の別版（`unsupported_findings`）を追加する。失敗させる指摘と飛ばす単位は `run_preflight` が分ける
- [ ] ターゲットに対する検査（ステップ、前提条件、飛ばすステップの `extract`、デバイスグループ）を `lint_text` と `lint_diagnostics` に追加し、展開後のシナリオに対して行う
- [ ] `bajutsu lint` に `--config` と `--target` を追加し、宣言されたターゲットを解決して、各ターゲットを振り分けられたステップで検査する
- [ ] serve のエディタの `/api/lint` リクエストで選択中のターゲットとファイルのパスを送り、パスを結び付いたシナリオディレクトリの中で解決する
- [x] すべてのターゲットで、実行の preflight が覆われた指摘を除き、その skip 単位を step loop に渡す
- [x] ステップのループが覆われたステップを飛ばし、すべてのアサーションを飛ばしたシナリオを失敗にする
- [ ] ターゲットが適用できない、名前のある前提条件を飛ばし、実機では `appPath` のインストールを無視する（それぞれ通知を出す）
- [ ] `RunResult` に `skippedPreconditions` を追加し、マニフェスト、HTML、JUnit、CTRF の各レポートで描画する
- [ ] 2つの除外記法と lint を `docs/` と `docs/ja/` に記載する（`dsl-grammar.md`、`scenarios.md`、`cli.md`、`drivers.md`）。
  実機の経路について `DESIGN.md` と `docs/architecture.md` を更新する

ログ：

- PR_PLACEHOLDER — 単位 1〜3、6、7、11、12。
  除外記法の名前を `skipOnRealDevice` から `skipIfUnsupported` に改めました。実機かどうかではなく、capability が足りないかどうかで働きます。
  修飾子を付けた `group:`、`use:`、ターゲットグループは 1 つの単位として飛ぶようになり、`assert` を依存先のステップと一緒に扱えます。
  ステップ修飾子は実行時に実際にステップを飛ばし、すべてのレポートで skipped として表示されます。
  修飾子の規則は `dsl-grammar.md`、`scenarios.md`、`reporting.md` に記載しました。

## 参考

- `bajutsu/common/backends.py:270-323`：`capabilities_for_run` と実機の絞り込み
- `bajutsu/common/capability/capability_preflight.py`：`_REQUIREMENTS` と `unsupported()`
- `bajutsu/common/runner/pipeline.py`：実行の preflight の呼び出し箇所（`_preflight_targets` と単一ターゲットの経路）
- `bajutsu/common/lint.py`、`bajutsu/cli/commands/lint.py`：設定を読まない現状の lint
- `bajutsu/common/config/accessors.py:79`：`xcuitest_targets_real_device`
- `bajutsu/common/orchestrator/types/step_outcome.py`：`StepOutcome`
- `xcuitest_environment.py:337-350`：実機で前提条件に投げる例外。
  ファイルは `bajutsu/common/platform_lifecycle/environments/xcuitest/` にあります
- `bajutsu/common/scenario/models/steps/step.py`、`steps/_shared.py`：`Step`、その検証、`_MODIFIERS`
