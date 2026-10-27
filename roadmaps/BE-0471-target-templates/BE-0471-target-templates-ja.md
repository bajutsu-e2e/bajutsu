[English](BE-0471-target-templates.md) · **日本語**

# BE-0471 — ターゲットテンプレート：名前付きテンプレートの継承でターゲット設定を共通化する

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0471](BE-0471-target-templates-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **承認済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0471") |
| トピック | config の取得元 |
| 関連 | [BE-0030](../BE-0030-parameterized-shared-steps/BE-0030-parameterized-shared-steps-ja.md), [BE-0174](../BE-0174-scenario-ref-path-containment/BE-0174-scenario-ref-path-containment-ja.md), [BE-0422](../BE-0422-inline-scenario-components/BE-0422-inline-scenario-components-ja.md), [BE-0452](../BE-0452-target-config-restructure/BE-0452-target-config-restructure-ja.md), [BE-0467](../BE-0467-environment-specific-settings/BE-0467-environment-specific-settings-ja.md) |
<!-- /BE-METADATA -->

## はじめに

[ターゲット](../../docs/ja/glossary.md#target-app-device)は、アプリごとの差分をすべて
`bajutsu.config.yaml` に宣言します。現在、ターゲットの設定は 2 つの層で決まります。全ターゲットに効く
`defaults` と、1 つのプラットフォームの全ターゲットに効く `defaults.platforms.<platform>` です。
「全ターゲット」と「この 1 ターゲット」のあいだには、何の層もありません。本項目はその中間の層として、
**ターゲットテンプレート**を加えます。

テンプレートは、名前を付けた部分的なターゲットブロックです。ターゲットまたは別のテンプレートが `extends`
で取り込みます。シナリオが component を取り込むのと同じ形です。テンプレートは部品であり、ターゲットでは
ありません。専用のトップレベルキー `templates:` に置くので、`--target` では選べず、`targets:` を走査する
コードにも現れません。テンプレートは config のなかに直接書くことも、独立したファイルに置くこともできます。

## 動機

同じアプリを対象にするターゲットは、数項目だけが異なり、残りは繰り返しになります。
`demos/showcase/showcase.config.yaml` は 15 個のターゲットを宣言しています。そのうち 6 個が同じ
`driver.runner.testRunner` のパスを繰り返し、14 個が `SHOWCASE_UITEST` の起動環境変数を繰り返します。
共通部分を変えるとき、たとえば runner のパスを変えるときは、すべての写しを編集しなければなりません。
直し忘れた写しは、単独では有効な設定なので、エラーにならないまま食い違っていきます。

既存の 2 つの層は、この場合に届きません。`defaults` は広すぎます。SwiftUI の 4 ターゲット向けの設定は、
Compose や web のターゲットには属しません。`defaults.platforms.ios` も、まだ広すぎます。showcase の
iOS ターゲットは、すべてが同じアプリではないからです。YAML のアンカーとマージキーも、後述の代替案に
示すとおり役に立ちません。

観測できる結果は、config そのものです。showcase の config が共通ブロックをテンプレートに移すと、各ブロックは
1 度だけ現れます。さらにテストが、15 個のすべてのターゲットについて、移行の前後で `resolve` が同一の
`Effective` を返すことを示します。後の読者は 2 点を確かめられます。ファイルの行数と、変わらない
`Effective` です。

## 詳細設計

### 振る舞い

テンプレートは、部分的なターゲットブロックです。ターゲットの 11 個のキー
（[BE-0452](../BE-0452-target-config-restructure/BE-0452-target-config-restructure-ja.md)）のどれでも
持てますが、必須のキーはありません。したがって `app.id` や `platform` を省けます。ターゲットとテンプレートは
どちらも、新しいキー `extends` を取ります。値はテンプレート名か、その一覧です。

```yaml
templates:
  showcase-ios:                    # inline：ターゲットの任意のブロック。必須のキーはない
    platform: ios
    app:
      launch: { env: { SHOWCASE_UITEST: "1" } }
    driver:
      runner: { testRunner: ../../BajutsuKit/Runner/build/dd/Build/Products/BajutsuRunner.xctestrun }
  masked-password:
    evidence: { redact: { fields: [password] } }

targets:
  showcase-swiftui:
    extends: [showcase-ios, masked-password]     # 後ろの項目が前の項目に勝つ
    app: { id: com.bajutsu.showcase.ios.swiftui }
  showcase-uikit:
    extends: ./templates/uikit.template.yaml     # ファイル参照
    app: { id: com.bajutsu.showcase.ios.uikit }
```

- **層の順序**：弱い順に `defaults`、`defaults.platforms.<platform>`、`extends` の各項目（列挙順）、
  ターゲット自身です。別のテンプレートを継承するテンプレートでは、継承元が先に来ます。したがって継承する
  側が、継承元に勝ちます。
- **マージ規則は既存のものを使う**：[Defaults](../../docs/ja/configuration.md#デフォルト値defaults) の 5 つの規則が
  すべての層に適用されます。辞書はキーごとにマージし、`null` はキーを消し、リストは置き換えます。
  `run.secrets` と各 `evidence.redact` のリストは和集合にし、和集合のリストとその親ブロックへの `null` は
  読み込みを失敗させます。リストの新しい記法は加えません。
- **名前かパスか**：`extends` の項目が `/` を含むか `.yaml` または `.yml` で終わるなら、ファイルです。
  それ以外は、inline のテンプレートの名前です。展開は、`use` がすでに共有している判定関数
  `is_component_file_ref`（`bajutsu/common/scenario/models/scenario/component.py`）を呼びます。
  したがって 2 つのキーの規則が食い違うことはありません。`schema/hooks.py` と同じく、scenario の
  モジュールは関数の中で import し、スキーマの import の経路に scenario のローダーを載せません。ファイルには
  テンプレートのブロックを 1 つだけ書きます。パスは、その `extends` を書いたファイルのディレクトリから解決し、
  [BE-0174](../BE-0174-scenario-ref-path-containment/BE-0174-scenario-ref-path-containment-ja.md) が
  求めるとおり、スイートのルートの内側に収めます。ファイルを持たない文書から読み込んだ config は、
  inline のテンプレートだけを指せます。
- **ファイル形式のテンプレートは、自分の相対パスを自分で解決する**：テンプレートファイルの中の相対パスは、component ファイルと同じく、そのファイルのディレクトリから解決します。対象は次の 3 種類です。
  - 入れ子の `extends` の項目
  - パス項目の `app.path`、`runsOn.seedPhotos`、`driver.runner.testRunner`、`paths.*`
  - `hooks` のステップが `use` で呼ぶ component。config 自身の hook の走査がたどるすべての深さが対象です。
    `setup`、各 `cleanup` ルール、各 `interrupts` の項目に加え、グループ、`if`/`else`、`forEach`、
    `web`、`app` の各ステップの `steps` を再帰的にたどります。書き換えと `schema/hooks.py` の
    `_nested_steps` は、これらの入れ物の記述を 1 つ共有します。そのため、新しい入れ物がモデルにだけ
    加わり、書き換えから漏れることはありません。

  ローダーはファイルを読む時点で、これらの値を config ファイルのディレクトリ基準へ書き換えます。そのため、マージより後の処理は 1 つの基準だけを扱います。テンプレートファイルには名前を引くための `templates:` がないので、テンプレートファイルの `extends` はファイルだけを指せます。`app.build` のようなシェルコマンドはパスではありません。`services.server.cwd`、`services.server.dockerfile`、`services.mockServer.stubs` も、現在は config ファイルのディレクトリから解決していません。ローダーはこれらを書き換えません。いずれも現在と同じ基準のままです。
- **`platform`**：テンプレートは宣言してもかまいません。ターゲットは省略して、テンプレートから
  受け継ぐこともできます。`platform` を宣言するすべての層は、ターゲット自身も含めて一致しなければなりません。
  2 つの層が食い違うと、両方を名指ししてロードが失敗します。ターゲットのキーがテンプレートのキーを
  上書きすることはありません。`platform` は `app`、`runsOn`、`driver` の形を決めるので、上書きを許すと、
  テンプレートの iOS 向けのブロックが web のターゲットに渡ってしまいます。どの層からもプラットフォームが
  得られないターゲットは、現在と同じく失敗します。
- **テンプレートはターゲットにならない**：`bajutsu run --target <name>` も serve の各エンドポイントも、
  `targets:` の下の名前だけを受け付けます。名前がテンプレートに一致するときは、エラーがそのことを述べ、
  ターゲットの一覧を示します（`'showcase-ios' is a template, not a target`）。このエラーは `resolve` が
  出します。所属を自分で調べる serve のエンドポイント（`operations/reads.py`、`doctor.py`、`codegen.py`、
  `enrich.py`、`capture.py`、`coverage/_functions.py`）も、1 つの共有ヘルパーからメッセージを組み立てます。
  したがって、どのエンドポイントにも素の `unknown target` の文言は残りません。`extends` が探すのは
  テンプレートだけなので、テンプレートとターゲットが同じ名前を持つことは許されます。
- **エラーはロードを失敗させる**：未知のテンプレート名は、既知の名前を一覧にします。循環はその経路を
  表示します（`a → b → a`）。共通の継承元のように、2 つの項目が両方たどり着くテンプレートは、深さ優先の
  順序で最初に現れる位置に、1 度だけ畳み込みます。したがって継承元は、それを継承するどのテンプレートよりも
  常に弱くなります。継承先のあとに継承元をもう一度並べても、位置は動きません。1 つの名前について、
  「継承元が先」と「後ろの項目が勝つ」は同時に成り立たないからです。
- **テンプレートはターゲットを通して検査する**：ローダーはテンプレートのキーを、ターゲットの 11 個のキーと `extends` に
  照らして検証します。グループ全体の検証は、層をマージしたあとの各ターゲットで走ります。したがって、
  どのターゲットも継承しないテンプレートは、入れ子のキーが検査されません。この隙間は、エディタ向けには `bajutsu config schema` が狭めます。`platform` を宣言したテンプレートは、そのプラットフォームのターゲットの形で、すべてのフィールドを任意として検査されます。`platform` を持たないテンプレートは、プラットフォームに依存しないグループが完全に検査され、`app`、`runsOn`、`driver` はどのプラットフォームの部分的な形でも受け付けられます。

### 実装

変更が及ぶのは、config スキーマと、その外で生の YAML に defaults のマージをかけている 1 箇所の呼び出し元です。`resolve` とその下流は、これまでどおりマージ済みのターゲットを
読みます。

| 部分 | 場所 | 変更 |
|---|---|---|
| 層の畳み込み | `bajutsu/common/config/schema/_merge.py` | `merge_defaults(defaults, target)` を、defaults を土台にして順序付きの層のリストを重ねる畳み込みにします。`defaults.platforms.<platform>` は、ターゲット自身のキーだけでなく、層から決まるプラットフォームで選びます。従来の呼び出しは層が 1 つの場合です。 |
| Device Farm の条件検査 | `bajutsu/serve/batch_provider/device_farm_batch_provider.py` の `_check_no_conditions` | ターゲットの `runsOn` の条件を探すために、現在は生の YAML に `merge_defaults` をかけています。これを展開と畳み込みに置き換え、テンプレートが与えた条件も拒否されるようにします。 |
| `extends` の展開 | 新規 `bajutsu/common/config/schema/_templates.py` | ターゲットの `extends` を、順序付きの層のリストに変えます。深さ優先で、継承元を先に、各テンプレートを 1 度ずつ並べます。循環と未知の名前を検出し、スイートのルートの下のファイルを読み、プラットフォームの衝突を報告します。 |
| パスの付け替え | `_templates.py`。パス項目の表を、`bajutsu/common/config/effective/effective.py` の `Effective.rebased` と一致させて保ちます | ファイル形式のテンプレートの相対パスを、そのファイルのディレクトリ基準から config ファイルのディレクトリ基準へ書き換えます。`Effective.rebased` は型付きの、プラットフォームごとの走査を保つので、生の YAML を書き換えるためのドット区切りの表を読めません。両者が同じ項目の集合を扱うことをテストで確かめ、片方にだけ加えたパス項目はゲートで失敗させます。 |
| 入口 | `schema/config.py` の `Config._merge_defaults` | ターゲットごとに、展開、畳み込みの順に呼びます。config に `defaults` がない場合も同じです。`Config` に `templates: dict[str, dict[str, Any]]` を加えます。 |
| ターゲット名のエラー | `bajutsu/common/config/resolve.py` の `resolve` と、上に挙げた serve の 6 つのエンドポイント | 未知のターゲットのメッセージを 1 つのヘルパーで組み立て、名前がテンプレートに一致するときはそのことを述べます。`resolve` と各エンドポイントがこのヘルパーを呼びます。 |
| エディタ向けスキーマ | `bajutsu/common/config/json_schema.py` | 各プラットフォームのターゲットに `extends` を加えます。`templates` マップも加えます。その各項目は、ターゲットの判別子付きの `oneOf` ではなく `anyOf` です。プラットフォームごとの分岐は、それぞれ自分の `platform` の値を必須にしたままです。もう 1 つの分岐は `platform` を禁じ、プラットフォームに依存するグループを、どのプラットフォームの部分的な形でも受け付けます。判別子には、`platform` を省いたテンプレートのための分岐がないからです。 |

畳み込みは、層を defaults に 1 つずつ重ねなければなりません。テンプレート同士を先に 1 つのブロックへ
マージしてはいけません。`defaults` が与える値を消すために `null` を書いたテンプレートは、適用の時点で
その値を見つけられる必要があります。先に平坦化すると `null` が落ち、`defaults` が値を復元してしまいます。

`extends` は `TargetConfig` が検証する前に取り除かれます。したがって `TARGET_KEYS` は 11 項目のままで、
未知のキーを拒む厳格な検査も変わりません。

`ai` は、`resolve` で `defaults.ai` とフィールド単位にマージする現在の方式を保ちます。テンプレートの
`ai` は、上の層の順序でターゲットの `ai` に畳み込まれ、その結果が従来どおり `defaults.ai` とマージされます。

### 対象外

| 除外するもの | 理由 |
|---|---|
| テンプレートのパラメータ化（`with:`） | 値を渡すには、置換の順序と `${secrets.X}` との境界を決める必要があります。後続の項目で加えられます。 |
| リストの追加・削除の新しい記法 | 既存の規則（置き換えと、和集合のリスト）が、すべての層にすでに適用されます。 |
| 別の config ファイルや Git リポジトリをまたぐ参照 | ファイル形式はスイートのルートの内側にとどめます。リポジトリをまたぐ共有は、別の取得元の問題です。 |
| ターゲットが別のターゲットを継承すること | ターゲットは実行できるので、編集するとそれを継承するすべてのターゲットが変わってしまいます。 |

### 作業分解

1. `merge_defaults` を、振る舞いを変えずに層の畳み込みへリファクタリングします。
2. `templates:` フィールドを加えます。ファイル形式のテンプレートのパスの付け替えを含む `extends` の展開と、そのエラーも加えます。
3. `resolve` と serve のエンドポイントに共有のテンプレート名のエラーを、JSON Schema に `extends` と `templates` を加えます。
4. テストを加えます。層の順序、`null` による消去、和集合のリスト、ファイル参照と封じ込めを検証します。さらに、ファイル形式のテンプレート内の相対パス、循環、未知の名前、プラットフォームの衝突、ターゲットとして指定されたテンプレートのエラーを検証します。あわせて、テンプレートが与えた `runsOn` の条件による Device Farm ジョブの拒否を検証します。
5. `docs/configuration.md` と `docs/ja/configuration.md` に機能を記述し、config の層を説明している `docs/architecture.md` と `DESIGN.md` を更新します。
6. showcase の config の共通ブロックをテンプレートへ移し、全ターゲットの `Effective` が変わらないことをテストで確かめます。

## 検討した代替案

| 代替案 | 採らなかった理由 |
|---|---|
| YAML のアンカーとマージキー（`<<: *base`） | コードなしで今でも使えますが、マージが浅く、ターゲット側に入れ子の `app` ブロックがあるとアンカーの `app` 全体を置き換えます。`run.secrets` が和集合になるべき場面でも、リストは置き換えられます。アンカーは 1 ファイルの内側にとどまり、誤りがあっても Bajutsu はエラーを出しません。 |
| ファイル形式のテンプレートのパスを config ファイルのディレクトリから解決する | 値の書き換えが要らないぶん単純です。しかし、テンプレートファイルの読み方が component ファイルと食い違い、テンプレートを移動しても、実行時にパスが失敗するまで壊れたことに気づけません。テンプレート自身のディレクトリから解決すれば、component ファイルがすでに従っている規則のままで済みます。 |
| `targets:` の中の目印（`abstract: true`） | ブロックがターゲットのあいだに残るので、`config.targets` を走査するすべてのコード（serve、provisioning、`resolve`）が除外処理を要し、1 つ漏れるとテンプレートが実行可能なものとして列挙されます。別キーにすれば、除外が構造で決まります。 |
| `extends` を 1 項目に限り、継承の連鎖も認めない | プラットフォームと環境のような 2 軸を合成するには、組み合わせごとにテンプレートが要ります。リストと連鎖なら合成できます。 |
| ターゲットが別のターゲットを継承する | 継承元が実行可能なので、継承元の変更が、黙って継承先を変えます。また、決して実行されないブロックを宣言する手段が残りません。 |
| `defaults` の下の名前付きグループ | 同じ発想を別の名前にしたものですが、「このターゲットは X の形をしている」という表現が `defaults.platforms` の隣にもう 1 つ増えます。`templates:` なら、`defaults` は全ターゲットに効くものに専念できます。 |

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [ ] `merge_defaults` を、振る舞いを変えずに層の畳み込みへリファクタリングする
- [ ] `templates:`、ファイル形式のテンプレートのパスの付け替えを含む `extends` の展開、ロード時のエラーを加える
- [ ] `resolve` と serve のエンドポイントに共有のテンプレート名のエラーを、JSON Schema に `extends` と `templates` を加える
- [ ] 層の順序、消去、ファイル、循環、プラットフォームの衝突のテストを加える
- [ ] `docs/` と `docs/ja/` に機能を記述し、`docs/architecture.md` と `DESIGN.md` を更新する
- [ ] showcase の config の共通ブロックをテンプレートへ移し、ターゲットごとの `Effective` を変えない

## 参考

- `bajutsu/common/config/schema/_merge.py` — 現在の 2 層マージである `merge_layer` と `merge_defaults`
- `bajutsu/common/config/schema/config.py` — 検証の前にマージを走らせる `Config._merge_defaults`
- `bajutsu/common/config/schema/target_config.py` — `TARGET_KEYS` と `TargetConfig`
- `bajutsu/common/config/resolve.py` — `resolve` と、スイートのルートを運ぶ `parse_config_dict`
- `bajutsu/common/config/json_schema.py` — `defaults.platforms` の形を含む、エディタ向けスキーマ
- `bajutsu/common/config/effective/effective.py` — config ファイルのディレクトリから解決するパス項目を持つ `Effective.rebased`
- `docs/scenarios.md` — `extends` が名前かパスかの規則を借りる、component の `use` ステップ
- `demos/showcase/showcase.config.yaml` — 最初の利用者
