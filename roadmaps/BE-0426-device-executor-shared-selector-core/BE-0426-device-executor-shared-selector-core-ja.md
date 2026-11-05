[English](BE-0426-device-executor-shared-selector-core.md) · **日本語**

# BE-0426 — RustとUniFFIで、iOSとAndroidの端末側セレクタコアを共有する

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0426](BE-0426-device-executor-shared-selector-core-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **承認済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0426") |
| トピック | Platform support |
| 関連 | [BE-0114](../BE-0114-driver-conformance-suite/BE-0114-driver-conformance-suite-ja.md)、[BE-0208](../BE-0208-android-emulator-e2e-ci/BE-0208-android-emulator-e2e-ci-ja.md)、[BE-0238](../BE-0238-ios-device-cloud-execution/BE-0238-ios-device-cloud-execution-ja.md)、[BE-0405](../BE-0405-android-identifiertool/BE-0405-android-identifiertool-ja.md)、[BE-0407](../BE-0407-step-latency-driver-internal-tuning/BE-0407-step-latency-driver-internal-tuning-ja.md)、[BE-0408](../BE-0408-step-latency-device-executor-protocol/BE-0408-step-latency-device-executor-protocol-ja.md)、[BE-0409](../BE-0409-step-latency-ios-device-executor/BE-0409-step-latency-ios-device-executor-ja.md)、[BE-0410](../BE-0410-step-latency-android-device-executor/BE-0410-step-latency-android-device-executor-ja.md) |
<!-- /BE-METADATA -->

## はじめに

[BE-0408](../BE-0408-step-latency-device-executor-protocol/BE-0408-step-latency-device-executor-protocol-ja.md)は、iOSとAndroid向けの端末側ステップ実行プロトコルを定義しています。BE-0408は、SwiftとKotlinがそれぞれ独自にホスト側のセレクタ照合ロジックを持つ必要があると述べています。さらに、2つの実装がホスト側とまったく同じ結果を返す必要があるとも述べています。

本項目は、このロジックをRustで一度だけ書くことを提案します。両プラットフォームは、[UniFFI](https://mozilla.github.io/uniffi-rs/)が生成するバインディング経由で、これを呼び出します。手書きのSwift移植と手書きのKotlin移植の代わりに、1つのコンパイル済みコアを囲む2つの薄いバインディングを置きます。

[BE-0409](../BE-0409-step-latency-ios-device-executor/BE-0409-step-latency-ios-device-executor-ja.md)のiOS実行エンジンと[BE-0410](../BE-0410-step-latency-android-device-executor/BE-0410-step-latency-android-device-executor-ja.md)のAndroid実行エンジンは、同じクレートを呼び出します。どちらも、独自に書いた`matches`、`find_all`、`resolve_unique`を持ちません。

## 動機

BE-0408自身の設計は、リスクを明確に名指ししています。端末側の2つの実装は、「ホスト側の実装と同じ要素にすべてのセレクタを解決」しなければなりません。さらに、「あいまいな一致に対しても同じように失敗」しなければなりません。

BE-0409とBE-0410は、順序付けでこのリスクに対応しています。iOS側の移植を先に行い、「この移植が明らかにするギャップを、両プラットフォームが個別に再発見しなくて済むように」する設計です。この順序付けは、ギャップを再発見するコストを下げます。しかし、2つの移植が最初から食い違うこと自体は防ぎません。

どちらの項目も、`find_all`／`resolve_unique`の移植をまだ始めていません。BE-0409はBE-0408（今は[Implemented](../BE-0408-step-latency-device-executor-protocol/BE-0408-step-latency-device-executor-protocol-ja.md)）の完了を待っており、BE-0410はBE-0409の完了を待っています。SwiftのコードにもKotlinのコードにも、この2つの関数の実装は今日の時点でまだ存在しません。だからこそ今は、1つの実装が2つの実装より安く済む時点です。

本項目が移す関数は、すでに純粋なデータ変換です。プラットフォーム固有の配線ではありません。[`bajutsu/common/drivers/base/_functions.py`](../../bajutsu/common/drivers/base/_functions.py)の`matches`、`find_all`、`resolve_unique`、`_collapse_identical_duplicates`は、`Element`のリストと`Selector`を受け取ります。どちらも、文字列・リスト・タプルだけの単純な辞書です。各関数は、真偽値、絞り込んだリスト、単一の要素、送出したエラーのいずれかを返します。

この4つの関数は、アクセシビリティツリーを読みません。タップも注入しません。ソケットも開きません。プラットフォーム固有の処理は、すべてこの関数の外側で起きます。今日はPythonドライバの中で、実装後は両方の端末実行エンジンの中で起きます。プラットフォームに依存しない関数が、独立に保守された3つのコピーとして存在する理由はありません。

独自に移植したときのリスクは、ふつうの実装ズレより深刻です。`idMatches`はPythonの`fnmatch.fnmatchcase`を使い、`labelMatches`はPythonの`re.compile(...).search`を使います。独自のSwift移植は、`NSRegularExpression`とFoundation自身のglob処理に頼るでしょう。独自のKotlin移植は、`java.util.regex`とその独自のglob処理に頼るでしょう。

すると、3つのエンジンが、シナリオ作者自身のパターンが何に一致するかを、それぞれ別のルールで決めることになります。文字クラス、アンカリング、Unicodeプロパティの扱いは、エンジンごとに違います。ホストでは通るのに端末実行エンジンでは落ちるセレクタは、大きなエラーではなく不安定なシナリオとして表面化します。別の要素に一致してしまう場合も同様です。呼び出し側から見て、誤った一致は正しい一致と区別がつきません。

Rustのエンジンを1つ共有すれば、エンジンの数は独立した3つから2つへ減ります。ホスト側では、Pythonの`re`と`fnmatch`が引き続き検証済みの基準です。Rustコアは、どちらの端末実行エンジンも走らせる、もう1つのパターンエンジンになります。

実装が終われば、後の読者はこの結果を直接確かめられます。BE-0408は、それを確かめるためのフィクスチャをすでに出荷しています。[`tests/fixtures/be0408/`](../../tests/fixtures/be0408/)には、42件のセレクタ解決ケースと9件の派生ラベルケースがあります。[`tests/test_selector_fixtures.py`](../../tests/test_selector_fixtures.py)が、これをPythonの実装と照合しています。

現行の計画では、SwiftとKotlinそれぞれの移植ができた時点で、この同じフィクスチャを別々に実行することになります。それぞれの移植は、全フィクスチャに通りながら、フィクスチャが網羅していないケースで互いに食い違う可能性があります。

新しいセレクタ規則（新しいtrait、新しいフォールバック）を追加するときの手間も変わります。共有コアなら、Rustの変更は1回で済みます。両方の実行エンジンが、次のバイナリ更新でそれを拾います。現行の計画では、記憶だけを頼りに歩調を合わせる、2回の手書きの変更が必要です。

## 詳細設計

**実装順序。** 本項目は、BE-0407→BE-0408→BE-0409→BE-0410という4項目の並びを拡張します。BE-0408は[Implemented](../BE-0408-step-latency-device-executor-protocol/BE-0408-step-latency-device-executor-protocol-ja.md)（[#1949](https://github.com/bajutsu-e2e/bajutsu/pull/1949)）です。本項目が置き換える手順は、コードとしてではなく、文章としてすでに出荷済みです。それは、[`docs/selectors.md`](../../docs/selectors.md)に新設された「端末側リゾルバの移植契約」節です。

この節は、フィールドレベルのセレクタ契約を、2つの独立したSwiftとKotlinの実装が一致できるだけ精密に述べています。契約の対象は、`within`、`idMatches`、traitの導出、重複排除のキーです。動機の節が挙げたフィクスチャ一式が、この契約を裏づけています。

本項目が置き換えるのは、まだ着手していない手順ではありません。「これを手作業で正しく再現する」という、すでに出荷済みの文章による契約を、コンパイル済みのクレートへ置き換えます。どちらの移植も、契約を手作業で再現する必要がなくなります。着手のタイミングは、BE-0408への後続作業としてであり、BE-0409またはBE-0410がプラットフォーム側の照合コードを書き始める前です。そうしないと、両者とも、本項目が無用にしようとしている契約を相手に、手作業での移植を始めてしまいます。

本項目が着地すると、BE-0409とBE-0410が互いに順序付けている理由も消えます。両者とも同じ検証済みのクレートを呼ぶだけなので、どちらかの手書き移植が先に終わるのを待つ必要がありません。本項目のクレートと2つのバインディングができたあとは、BE-0409とBE-0410は、どちらの順で進めても、並行に進めてもかまいません。

**クレートで実装し直す関数と、ホスト側だけに残す関数。** クレートは、[`bajutsu/common/drivers/base/_functions.py`](../../bajutsu/common/drivers/base/_functions.py)の9つの関数を、Rustで実装し直します。Pythonの実装は、適合スイートの基準としてそのまま残ります。9つは、`matches`、`find_all`、`resolve_unique`、`_collapse_identical_duplicates`、`contains`、`topmost_at_point`、`redirect_candidates`、`raise_if_covered`、`frame_center`です。

これらは、セレクタ解決と、`tap`の被覆判定をカバーします。`tap`は、BE-0408が端末へ移すアクチュエーション種別の1つです。動機の節で示したとおり、各関数は純粋です。`Element`と`Selector`の組（またはそのリスト）を受け取ります。返すのは、真偽値、インデックスまたはそのリスト、座標、送出したエラーのいずれかです。

FFI（foreign function interface）境界を越えること自体が理由で、シグネチャが2箇所変わります。Pythonのオブジェクト同一性には、境界の向こう側に対応物がないためです。

- **返す`Element`をインデックスに替えます。** `find_all`は、`Element`のリストの代わりに`Vec<u32>`（`elements`の中でのインデックス）を返します。`resolve_unique`は、`Element`の代わりに`u32`を1つ返します。UniFFIのレコードは、この境界を値として越えます。返された`Element`はコピーにすぎず、呼び出し元がそこから解決した先のプラットフォーム側ハンドル（`XCUIElement`や`AccessibilityNodeInfo`）へたどる手がかりを持ちません。インデックスであれば、呼び出し元は、自分が組み立てたリストから、`Element`と自分自身のハンドルの両方を同じ位置で引けます。
- **対象の`Element`を`target_index`に替えます。** `topmost_at_point`、`redirect_candidates`、`raise_if_covered`は、`Element`の代わりに`target_index: u32`を受け取ります。理由は同じです。Pythonは`target`引数を、`elements`の中からオブジェクト同一性（`is`）で探しています。等価性では探していません。これは意図的な設計です。内容が同一の2要素（既知のXCUITestの重複登録）があっても、呼び出し元が実際に手にしている一方だけを指し続ける必要があるためです。インデックスはこの区別を保ちます。値としてコピーされたレコードは、この区別を保てません。

同じ理由で、`redirect_candidates`は戻り値も`Vec<u32>`にします。呼び出し元は、選んだ子孫要素に対してアクチュエーションを行います。今日の`XcuitestDriver._tap_sole_reachable_descendant`は、`handles[id(el)]`で各候補を引いています。値としてコピーされたレコードは、この同一性ルックアップを壊します。`find_all`が返す要素と事情は同じです。

`topmost_at_point`が`Element`を返す仕様は、コピーのままで構いません。`raise_if_covered`は、その識別子とフレームをメッセージへ整形するだけです。下流の誰も、プラットフォーム側ハンドルへたどり直す必要がありません。

セレクタのパターン照合には、シグネチャの変更だけでなく、使うエンジンの名指しも必要です。`idMatches`はPythonの`fnmatch.fnmatchcase`を、`labelMatches`は`re.compile(...).search`を使っています（[`_functions.py:112-122`](../../bajutsu/common/drivers/base/_functions.py)）。クレートは、`labelMatches`に[`regex`](https://docs.rs/regex)クレートを使います。

`idMatches`では、クレートは`fnmatch`自身の翻訳処理を実装し直します。それは、シェルのglobパターンを正規表現文字列へ翻訳する処理です。Pythonの`fnmatch`モジュールも、内部で同じアプローチを取っています。クレートは、翻訳結果を同じ`regex`クレートでコンパイルします。こうすることで、クレートが抱えるパターンエンジンはちょうど1つに絞れます。

この翻訳だけでは、2つの理由で足りません。

- `fnmatch.translate`が出す終端アンカーは`\Z`です。`regex`はこれを認識せず、持っているのは`\z`だけです。
- `fnmatchcase`の先頭アンカーは、パターン自体ではなく`re.match`の呼び出しから来ています。`regex`側の照合は、アンカーなしです。

そのためクレートの`idMatches`は、翻訳済みパターンの`\Z`を`\z`へ書き換え、コンパイル前に全体を`^(?:…)\z`で包みます。これは、[`docs/selectors.md`](../../docs/selectors.md)自身の指示を再現したものです。ポートは、各フィールドについて、ポート先言語自体の既定のアンカリングではなくPythonのアンカリングを再現しなければなりません。アンカリングは、`idMatches`では完全で、`labelMatches`ではなしです。

`labelMatches`には、`idMatches`にはない独自のギャップがあります。`regex`は、Pythonの`re`が受け付ける次の構文を拒否します。

- 先読み・後読み（`(?=…)`、`(?!…)`）
- 後方参照
- 文字列終端アンカー`\Z`
- 原子グループと所有量指定子
- 条件付きパターン（`(?(id)yes|no)`）

これらのいずれかを使ったパターンは、ホストではコンパイルできますが、クレートではコンパイルできません。本項目がなくそうとしているホストと端末の食い違いが、なくなったのではなく場所を移しただけです。

単なる`$`は、拒否されるより厄介です。両方のエンジンでコンパイルできてしまい、しかも意味が違うからです。Pythonの`$`（`MULTILINE`なし）は、文字列末尾の直前にある改行の手前にも一致します。`regex`の`$`は、文字列の真の末尾にしか一致しません。`regex`には先読みがなく、Pythonの挙動を表現できません。そのためクレートは、`$`を静かに違う一致へコンパイルさせず、そのまま拒否します。

クレートは、この一覧のどの構文についても、初回使用時の明確で大きな失敗として扱います。失敗は、次節のエラー列挙型に組み込みます。静かな不一致にすると、呼び出し元は「要素が存在しない」場合と区別できません。フィクスチャ一式に追加する1件のケース（検証の節を参照）が、そのようなパターンを固定します。どちらかのエンジンへの将来の変更がこのギャップを再び開いたとき、不安定なシナリオとして表面化する前に、スイートで検出できます。

`gesture_anchor`も同じく純粋ですが、本項目の範囲外です。BE-0408が端末へ移すのは、`tap`、`type`、`swipe`、`scroll`です。`gesture_anchor`が支点を計算する2本指の`pinch`／`rotate`は移しません。この関数を今移植すると、本項目のプロトコルがまだ到達していない段階のための実装になってしまいます。

`deadline_ticks`と`wait_until`も移しません。どちらも、単発の判定の周りにホスト側のポーリングループを実装したものです。BE-0408のステージ1は、ホストにポーリングの手段を公開する代わりに、端末側で内部的にポーリングさせる設計です。端末実行エンジンに必要なのは、共有した照合関数を包む、プラットフォームごとのイベント駆動またはタイマー駆動のループです。ホストのポーリングループをそのまま移植したものは要りません。

`default_wait_for`は、すべてのバックエンドの`wait_for`が委ねる単発判定です。同じ理由から、個別の移植は要りません。この関数の中身は`find_all(...).len() >= 1`そのものです。各実行エンジン自身のネイティブなループが、共有した`find_all`に対して、この判定を直接表現できます。

`id_candidates`、`validate_id_candidates`、`permission_capability`、`native_z_from_json`もPythonに残ります。`id_candidates`が残る理由は、データモデルの節で述べます。残る3つの理由はもっと単純です。これらは、シナリオの記述やエビデンスの解析といった、端末実行エンジンが担わない作業に使うためです。

**データモデル。** [`Element`](../../bajutsu/common/drivers/base/element.py)は、フィールドごとにUniFFIの辞書型レコードになります。

- `identifier`、`label`、`value`は、オプショナルな文字列になります。
- `traits`は、文字列のリストになります。
- `frame`は、`(x, y, w, h)`の4フィールドのレコードになります。
- `nativeZ`は、オプショナルな浮動小数点数になります。

[`Selector`](../../bajutsu/common/drivers/base/selector.py)は、単純な改名だけでは済みません。

- **`id`と`idMatches`。** Pythonでは、どちらも単一の文字列かリストのどちらかを受け取ります（`str | list[str]`、BE-0221のOR候補形式）。UniFFIにはこの種の合併型がないため、レコードの境界ではどちらも`Option<Vec<String>>`になります。フィールドが不在なら`None`です。これは、Python自身のフィールド存在チェック（`"id" in sel`）と対応します。空リストを不在の代わりに使うわけではありません。
- **単一の値の包み込み。** 単一の値を1要素のリストへ包むのは、1行で済む作業です。各呼び出し元が、レコードを組み立てるときに自分で済ませます。今日同じ処理を担うPythonのヘルパー`id_candidates`が、クレートへ移らずホスト側に残る理由は、ここにあります。クレート内部の`matches`と`find_all`は、すでに正規化されたリストだけを見ます。
- **`within`。** `Selector`が自分自身の`within`フィールドを入れ子として持てるため、`Option<Box<Selector>>`になります。
- **`index`。** `u32`ではなく`Option<i32>`になります。Pythonでは、末尾からの位置を表す負の値を受け付けるためです（`_functions.py:322-325`）。
- **不在のフィールド。** Pythonの`total=False`な辞書でのフィールドの不在は、両側で同じ`None`になります。

[`Trait`](../../bajutsu/common/drivers/base/trait.py)の6つの文字列定数は、両側でそのまま文字列として渡ります。定数は、`button`、`link`、`notEnabled`、`selected`、`other`、`secureTextField`です。これは、Pythonのコードと、JavaScript object notation（JSON）のワイヤフォーマットがすでに使っている形と一致します。7つ目の定数が加わっても、歩調を合わせる新しい列挙型は要りません。

[`ElementNotFound`](../../bajutsu/common/drivers/base/element_not_found.py)、[`AmbiguousSelector`](../../bajutsu/common/drivers/base/ambiguous_selector.py)、[`ElementNotTappable`](../../bajutsu/common/drivers/base/element_not_tappable.py)は、1つのUniFFIエラー列挙型になります。列挙型には、4つ目のバリアント`UnsupportedPattern`を加えます。前節で述べたパターンエンジンのギャップのためのバリアントです。`regex`クレートがコンパイルできない`labelMatches`の値を表し、フィールド名とパターン自体を持ちます。今日のPythonには、これに相当するものはありません。`re.compile`は、この4つ目のバリアントが存在する理由となるパターンを、すべて受け付けるためです。これに当たるのは、端末実行エンジンだけです。

残る3つのバリアントは、整形済みのメッセージ文字列ではなく、型付きフィールドとして構造化された失敗の詳細を持ちます。

- `ElementNotFound`は、セレクタと、`noMatch`または`outOfRange`のいずれかを表す`reason`を持ちます。`resolve_unique`は今日、「インデックスが範囲外」（`_functions.py:324`）と「一致なし」（`:327`）という2つの異なるメッセージを送出しています。BE-0408自身のフィクスチャ一式も、すでにこの同じ区別を`resolveUnique.reason`と名付けています。このバリアントは、新しい名前を発明せず、その名前を再利用します。
- `AmbiguousSelector`は、セレクタと候補数を持ちます。
- `ElementNotTappable`は、セレクタに加えて、被覆した要素の識別子・ラベル・フレームを持ちます。`raise_if_covered`は、`covering["identifier"] or covering["label"] or "<unnamed>"`をメッセージへ整形しています（`_functions.py:452`）。このフォールバックを再現するには、識別子だけでなく、識別子とラベルの両方のフィールドが必要です。

これらのフィールドを、今日実行レポートが表示するメッセージ文へ整形するのは、ホスト側です。デバイス側ではありません。SwiftやKotlinの呼び出し元は、整形前のバリアントをそのまま既存のエビデンス経路へ渡します。端末実行エンジンで失敗したステップは、今日ホストで失敗した同じステップと、まったく同じ見え方になります。

`_collapse_identical_duplicates`は、引数を追加せずに移植します。クレート側の実装も、ホスト側とまったく同じように、許容差なしでフレームの完全一致を比較します。`docs/selectors.md`の移植契約は、この関数についてもう1つ、許容差とは別の規則を「重複排除のキー」という節に持っています。この排除は、`find_all`自身の出現順で、あるキーを最初に持った候補を残します。単純なハッシュマップには、それを保つための決まった反復順序がありません。契約自身が、Swiftの`Dictionary`を具体的な落とし穴として名指ししています。

Rustの`std::collections::HashMap`は同じ落とし穴であり、しかもさらに厄介です。既定のハッシャーはプロセスごとに再シードされます。反復順序は、プラットフォーム間だけでなく、同じバイナリの実行のあいだでも変わりえます。そのためクレート側の移植では、この排除に挿入順を保つマップを使います。たとえば、`indexmap`クレートの`IndexMap`です。ホスト側の`seen`辞書と同じキーで持たせます。`HashMap`は使いません。

`docs/selectors.md`の移植契約は、許容差そのものを分岐させたまま保つべき理由を、「守るべきであり閉じてはならない2つの相違」という節で述べています。[`PositionPath.swift`](../../BajutsuKit/Sources/BajutsuRunner/PositionPath.swift)の`resolvableMatchingIndex`と`attributesMatch`は、すでに意図的にこのモジュールから外れています。フレームの各値に1ポイントの許容差を認めます。traitを、集合ではなく順序ありの配列として比較します。

どちらの相違も、`resolvableMatchingIndex`が別の問いに答えているために存在します。その問いは、すでに記録済みの要素ハンドルを、新しいライブ読み取りで再解決してもなお同じ要素か、というものです。一方`_collapse_identical_duplicates`は、「これらの候補は1回の原子的なスナップショットから見て内容が同一か」という問いに答えます。移植契約自身の文章は、ポートが「どちらか一方もPython自身の挙動へ『修正』してはならない」と明言しています。

そのため、本項目のクレートは`_collapse_identical_duplicates`だけで止まります。`resolvableMatchingIndex`と`framesEqual`は、Swift側にそのまま残ります。本項目は、この2つに触れません。ここでのどの進捗ステップも、両者を1つに畳み込むことを提案してはなりません。

Androidの派生ラベルのフォールバックは、パイプライン中の現在の位置にそのまま留まります。それは、`_derived_label`です。[`bajutsu/common/drivers/adb/_functions.py`](../../bajutsu/common/drivers/adb/_functions.py)にあり、`_to_element`の中で適用されます。共有した照合コードが`Element`を読む前に、`Element`へラベルを計算しておく処理です。今日のPythonドライバでも、BE-0410の実装後の`BajutsuAndroidUIAutomatorServer`のKotlin側呼び出し元でも、同じ位置にあります。

共有クレート側に、Android固有の分岐は要りません。呼び出し側がそれぞれ、自分のプラットフォームの生の読み取り結果を、素の`Element`へ正規化します。そのあとで、1つの共有コアを呼びます。

ここには、手書きのKotlin移植が1つ残ります。`_derived_label`自体にはRust側の対応物がないためです。この移植は、Pythonの実装と歩調を合わせ続ける必要があります。その出力は、後続処理のない単一の文字列です。BE-0408の9件の[`android_derived_label.json`](../../tests/fixtures/be0408/android_derived_label.json)ケースが、すでにこれをカバーしています。残存リスクは、照合パス全体を独立に移植していた場合よりはるかに小さく済みます。

**iOS: `BajutsuRunner`。** パッケージマニフェストは、リポジトリのルートにある[`Package.swift`](../../Package.swift)です。`BajutsuKit/`配下のファイルではありません。SwiftPMのgit連携解決の都合で、クローンのルートに置く必要があります。各ターゲット自身の`path:`が、`BajutsuKit/Sources/`配下を指し戻します。

このマニフェストは、すでにSwift Package Managerのビルドプラグイン`OpenAPIGenerator`を使っています。プラグインは、`BajutsuRunner`のソースを、パッケージをビルドするどのホストでも、ビルドのたびに生成します。`uniffi-bindgen`が生成するSwiftファイルは、これとは別のもう1つのソース生成経路です。違いは1点です。ネイティブ側の実体が、プラグインの都度コンパイルではなく、あらかじめビルド済みの`.xcframework`成果物である点です。

`cargo build`の対象は、次の4つです。

- Simulator向けの`aarch64-apple-ios-sim`と`x86_64-apple-ios`
- [BE-0238](../BE-0238-ios-device-cloud-execution/BE-0238-ios-device-cloud-execution-ja.md)の実機対応向けの`aarch64-apple-ios`
- `aarch64-apple-darwin`。[`swift.yml`](../../.github/workflows/swift.yml)のSimulatorを使わない素のApple Silicon macOSランナーが、今日どおりこのパッケージのビルドとテストを続けられるようにするためです（`swift build --package-path .`と`swift test --package-path .`）。

`x86_64-apple-darwin`のスライスは、ビルドしません。これは、対応ホストの範囲を1つ狭めます。Intel Macは、シナリオの実行に必要なSimulatorスライスこそ持ち続けます。しかし、今日のようにパッケージをネイティブにビルド・ユニットテストできなくなります。`swift.yml`がすでにApple Siliconだけで動いているため、本項目はこの狭まりを受け入れます。

`uniffi-bindgen`が生成するのは、Swiftバインディング、ヘッダー、モジュールマップだけです。`.xcframework`のスライスを統合する機能は持ちません。それを行うのは、`lipo`（ユニバーサルSimulatorスライス用）と`xcodebuild -create-xcframework`です。この2つが、4つの`cargo build`成果物を統合して、`.xcframework`を作ります。2つのSimulatorターゲットは1つのユニバーサルスライスへまとまるため、スライスは実質3系統のプラットフォーム向けです。この`.xcframework`を、`Package.swift`にバイナリターゲットとして追加します。`BajutsuRunner`は、そのターゲットに依存します。

このマニフェストが1つしかないことは、このバイナリターゲットが無償ではない理由でもあります。`Package.swift`は、`BajutsuKit`と`BajutsuRunner`という2つのプロダクトを、1つのターゲットグラフから公開しています。`BajutsuKit`プロダクトだけに依存するアプリも、同じグラフを解決します。バイナリターゲットは、ビルド時プラグインと違い、SwiftPMのリゾルバがマニフェスト全体に対して所在確認と検証を行う対象です。消費者が実際にリンクするプロダクトだけには絞られません。そのため、`BajutsuRunner`に一切触れないアプリでも、使いもしない成果物の解決時コストを支払う可能性があります。

このコストは、「`BajutsuKit`というアプリ組み込みライブラリを、このビルド手順から自由に保つ」という本項目自身の狙いに逆行します。BE-0405が`IdentifierTool`を`BajutsuAndroid`の依存から切り離しているのと、同じ意味です。このコストをそのまま受け入れるか、`BajutsuRunner`を切り離すための独自のマニフェストを持たせるかは、開かれた論点です。本項目はここで決め切らず、実装に委ねます。進捗の節は、これを決定としてではなく、論点として名指しします。

`BajutsuRunner`は、bajutsu自身が同梱するテストランナーです。[BE-0292](../BE-0292-xcuitest-bundled-runner/BE-0292-xcuitest-bundled-runner-ja.md)のコンテンツハッシュ方式のキャッシュを通じて、開発者の手元にはビルド済みの状態で届きます。利用側プロジェクトのビルド内でコンパイルされることはありません。したがって、このビルド手順が走るのは、bajutsu自身のリリースパイプラインの中だけです。テスト対象アプリ自身のビルドの中では走りません。

**Android: `BajutsuAndroidUIAutomatorServer`。** [`BajutsuAndroidUIAutomatorServer/server/build.gradle.kts`](../../BajutsuAndroidUIAutomatorServer/server/build.gradle.kts)は、この実行エンジンが拡張するのと同じインストルメンテーションのために、すでに`androidx.test.uiautomator`に依存しています。このモジュールにネイティブの依存を加えても、増えるのは依存の「種類」です。依存を許容する度合いを、新たに広げるわけではありません。ただし、これはモジュールが届く範囲そのものへの変更でもあります。

このインストルメンテーションAPKは、今日ネイティブコードを持ちません。そのため、端末が提供するどのABI（application binary interface）にもインストールできます。`minSdk = 26`がまだ許している、32ビットの`armeabi-v7a`や`x86`も含みます。`cargo-ndk`は、`arm64-v8a`と、[BE-0208](../BE-0208-android-emulator-e2e-ci/BE-0208-android-emulator-e2e-ci-ja.md)のエミュレータレーン向けの`x86_64`へ、クレートをクロスコンパイルします。これは、ABIの範囲を初めて狭める変更です。32ビット端末は、常駐サーバをもう実行できなくなります。

本項目は、4つ全部のABI向けにビルドするのではなく、この狭まりを受け入れます。32ビットのみのAndroid端末は、2019年以降のPlayストアがアプリからすでに受け付けなくなっています。常駐サーバ自身の端末カバレッジは、今日実際のターゲットアプリが配布できる範囲に揃うことになります。

`cargo-ndk`が生成した`.so`ファイルは、`jniLibs`の下に置きます。`uniffi-bindgen`は、実行エンジンが直接呼ぶKotlinバインディングを生成します。

このビルドファイル自身のコメントは、サーバが「dependency-light（依存を軽く保つ）」だとすでに述べています。理由は、HTTPとJSONのどちらのライブラリも持たない、自己完結したインストルメンテーションだからです。このコメントは、不要な依存を避けるという方針の表明です。この実行エンジンが実際に必要とするネイティブの依存を、禁じるものではありません。

**検証。** [`tests/fixtures/be0408/`](../../tests/fixtures/be0408/)は、このクレートに必要な言語非依存のフィクスチャ一式として、すでに存在しています。中身は2つのファイルです。

- `selector_resolution.json`は、スキーマ付きの42ケースを持ちます。各ケースは、`elements`のリスト、1つの`selector`、Pythonが出す`findAll`／`resolveUnique`の結果からなります。
- `android_derived_label.json`は、9ケースを持ちます。

[`tests/test_selector_fixtures.py`](../../tests/test_selector_fixtures.py)は、今日すでに、変更のたびに両方のファイルをPythonの実装と照合しています。

小さなRustのバイナリが、同じJSONから1ケース分の`Element`リストと`Selector`を標準入力で読みます。バイナリは、一致したインデックス、またはエラーのバリアントとその構造化された詳細を、標準出力へ書きます。このバイナリは、CIホスト自身のアーキテクチャ向けにビルドします。wheelにも常駐サーバにも、同梱しません。

`test_selector_fixtures.py`には、2つ目の照合経路を加えます。新しい経路は、既存の42ケースを、このバイナリ経由で走らせます。すでにPython経由で走らせている経路と並んで動きます。双方が、独立に管理された別々のフィクスチャ一式ではなく、1つの共有フィクスチャ一式に対して検証され続けます。

このフィクスチャ一式は、そのままでは`find_all`と`resolve_unique`をカバーします。この2つは、内部で`matches`と`_collapse_identical_duplicates`を使います。一方、本項目が移植する残り5つの関数は、`selector_resolution.json`の「`elements`＋`selector`」という形では表せません。

- `contains`（2つの`Frame`）
- `frame_center`（1つの`Frame`）
- `topmost_at_point`（1つの`Point`と対象のインデックス）
- `redirect_candidates`と`raise_if_covered`（セレクタに加えて対象のインデックス）

スキーマには、この5つのためのバージョン2を加えます。ケースごとの`function`フィールドが、9関数のうちどれを検証するかを選びます。各ケースは、その関数固有の追加入力（座標、対象のインデックス、2つ目のフレーム）を、既存の`elements`リストと並べて持ちます。

`test_selector_fixtures.py`は、すでに`findAll`／`resolveUnique`の有無で分岐しているのと同じやり方で、`function`によって分岐します。この1つのフィクスチャ一式と1つの照合テストが、2つ目の仕組みへ分裂することなく、9関数すべてをカバーするよう育ちます。

BE-0409とBE-0410が実装されたあとは、同じ一式を3回目として、各プラットフォームのバインディング経由でも実行します。そこでフィクスチャが落ちたときは、「バインディング側」だとすぐに絞り込めます。共有ロジック自体を疑い直す必要はありません。

## 検討した代替案

- **BE-0408が出荷した計画を最終形のまま残す案。文章による移植契約とフィクスチャ一式、そしてそれに照らして検証する、独立に書いたSwift・Kotlinの2つの移植です。** 唯一の安全策としては採用しません。BE-0408自身がすでに名指ししているリスクは、設計で取り除かれないまま残ります。2つの独立した実装が、あいまいな一致でどの候補を報告するかまで含めて、完全に一致しなければならないというリスクです。今後セレクタ規則を追加するたびに、2つの手書きパッチが必要です。歩調を合わせる手段は、テストの失敗という事後の発見だけで、構造上は食い違いを防げません。
- **同じRustコアをPyO3経由でPythonにも広げ、3言語を1つの実装にまとめる案。** 採用しません。`bajutsu`のpipパッケージは、今日は純粋なPythonです。AI SDKとPlaywrightを基本の依存にせず、オプトインの拡張にとどめる方針を、すでに取っています（[BE-0111](../BE-0111-ai-sdk-optional-dependency/BE-0111-ai-sdk-optional-dependency-ja.md)）。基本インストールへネイティブ拡張を加えると、`pip install bajutsu`のたびに、クロスプラットフォームのwheelビルドが必要になります。[maturin](https://github.com/PyO3/maturin)のようなツールが、そのビルドを担います。上記のiOS・Androidのビルドは、bajutsu自身のリリースパイプラインの中だけで走ります。このビルドは、インストールのたびに走ります。Pythonの実装は、すでに適合スイートがRustコアを照合する、検証済みの基準です。本項目の動機は、Python側を変える理由になりません。
- **同じRustコアを、BajutsuKitとBajutsuAndroidのアプリ組み込みコレクタ（`BajutsuNet`、`BajutsuZOrder`、クリップボードのレシーバ）にも広げる案。** 採用しません。これらのコンポーネントのプラットフォームフック機構は、収集先へPOSTするJSONペイロードの形という薄い契約以外に、共有できるロジックを持ちません。片方は`URLProtocol`のswizzlingで、もう片方はOkHttpの`Interceptor`です。片方はループバックのHTTPサーバで、もう片方は`AccessibilityNodeInfo`のextra-dataです。ここに共有Rustコアを持ち込んでも、置き換わるのはその薄い契約だけです。双方で、ゼロからのプラットフォーム固有実装が引き続き必要になります。`BajutsuRunner`と`BajutsuAndroidUIAutomatorServer`はbajutsu自身のテスト基盤として動きます。これらのライブラリは、テスト対象アプリの中に同梱されます。[BE-0405](../BE-0405-android-identifiertool/BE-0405-android-identifiertool-ja.md)は、そこに置く`IdentifierTool`を、依存を持たない最小フットプリントの設計に、すでにコミットしています。同梱したRustの静的ライブラリは、その設計と噛み合いません。
- **1つの仕様言語からSwiftとKotlinの同等なソースを生成する案。両プラットフォームがリンクする1つのネイティブライブラリをコンパイルする代わりに。** 採用しません。独立にコンパイルされた2つの生成コードは、言語ごとのコード生成バックエンドがそれぞれ固有のバグを抱えれば、それでも食い違いえます。これは、本項目がなくそうとしている失敗のパターンそのものです。このリポジトリには、この水準のコード生成バックエンドの前例がありません。一方UniFFIは、Rustのコアを1つ用意し、プラットフォームごとにホスト言語のバインディングを生成する形のために存在するツールです。しかも、保守が続いています。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [ ] `rust/selector-core/`にクレートの雛形を用意します。`Element`、`Selector`、`Trait`をUniFFIレコード
  として、3つのセレクタ関連エラーと`UnsupportedPattern`を合わせて1つのUniFFIエラー列挙型として、
  それぞれ用意します。
- [ ] `idMatches`と`labelMatches`の照合を、[`regex`](https://docs.rs/regex)クレート1本の上に実装します。
  `idMatches`には`fnmatch`自身のglob→正規表現変換ロジックを移植し、その`\Z`を`\z`へ書き換えたうえで、
  コンパイル前に全体を`^(?:…)\z`で包みます（Pythonの`re.match`が担う先頭アンカーも再現するため）。
  `labelMatches`はアンカーなしでコンパイルし、先読み・後読み、後方参照、`\Z`、原子グループ、所有量
  指定子、条件付きパターンに加えて`$`も拒否し、`regex`がコンパイルできないこれらのパターンには
  `UnsupportedPattern`を返します（`regex`には先読みがなく、Pythonの`$`が持つ「末尾直前の改行を許容
  する」挙動を再現できないためです）。
- [ ] [`bajutsu/common/drivers/base/_functions.py`](../../bajutsu/common/drivers/base/_functions.py)から、
  9つのセレクタ・幾何関数を移植します。`find_all`と`redirect_candidates`は`Vec<u32>`を、
  `resolve_unique`は`u32`を（いずれも呼び出し元の`elements`へのインデックス）返し、
  `topmost_at_point`・`redirect_candidates`・`raise_if_covered`は`Element`の代わりに
  `target_index: u32`を受け取るよう変更します。`_collapse_identical_duplicates`は引数を追加せず、
  ホスト側とまったく同じフレームの完全一致で移植し、排除のキー付けには`std::collections::HashMap`
  ではなく挿入順を保つマップ（`indexmap::IndexMap`など）を使います。`HashMap`の反復順序はプラット
  フォーム依存であるだけでなく、プロセスごとに再シードされるためです。`docs/selectors.md`の移植契約は、
  フレーム許容差の相違を`resolvableMatchingIndex`から意図的に切り離しておくためのものなので、ここでの
  手順が両者を1つにまとめようとしてはなりません。
  - `matches`
  - `find_all`
  - `resolve_unique`
  - `_collapse_identical_duplicates`
  - `contains`
  - `topmost_at_point`
  - `redirect_candidates`
  - `raise_if_covered`
  - `frame_center`
- [ ] [`tests/fixtures/be0408/`](../../tests/fixtures/be0408/)のスキーマにバージョン2を加えます。
  ケースごとの`function`フィールドと、各関数固有の追加入力（座標、対象のインデックス、2つ目のフレーム）
  を持たせ、バージョン1の「`elements`＋`selector`」という形では表せない5つの関数（`contains`、
  `frame_center`、`topmost_at_point`、`redirect_candidates`、`raise_if_covered`）をカバーします。
  [`tests/test_selector_fixtures.py`](../../tests/test_selector_fixtures.py)を拡張し、`function`に
  よって分岐して新しいケースをPythonの実装と照合します。バージョン1をすでに照合しているのと同じ
  やり方です。
- [ ] [`tests/fixtures/be0408/`](../../tests/fixtures/be0408/)の1ケースをJSONで標準入力から読み、一致
  したインデックス、またはエラーのバリアントとその構造化された詳細を標準出力へ書く、CLI形式の適合
  ランナーバイナリを構築します。`test_selector_fixtures.py`に、既存のフィクスチャ一式をこのバイナリ
  経由で走らせる2つ目の照合経路を、Python経由の経路と並べて追加します。
- [ ] `cargo`、`uniffi-bindgen`、`lipo`、`xcodebuild -create-xcframework`を`BajutsuKit`のSwift Package
  ビルドに組み込みます。`BajutsuRunner`がリンクする`.xcframework`（Simulator、実機、macOS向けの4つの
  `cargo build`成果物を統合した、実質3系統のプラットフォームスライス）バイナリターゲットを生成します。
  このバイナリターゲットを追加する前に、リポジトリのルートにある`Package.swift`が1つのターゲットグラフ
  しか持たないことにより、`BajutsuKit`プロダクトだけに依存するアプリもこのターゲットを解決してしまうか
  どうかを確認し、確認できたなら、そのコストを受け入れるか、BE-0405が`IdentifierTool`を
  `BajutsuAndroid`から切り離しているのと同じように、`BajutsuRunner`へ独自のマニフェストを持たせて
  切り離すかを決めます。
- [ ] `cargo-ndk`と`uniffi-bindgen`を`BajutsuAndroidUIAutomatorServer`のGradleビルドに組み込みます。
  実行エンジンがリンクする、Kotlinバインディングと`arm64-v8a`／`x86_64`向け`jniLibs`を生成します。
- [ ] `rust/selector-core/`向けにRustのCIレーン（`cargo test`、`cargo fmt --check`、`clippy`）を追加し、
  `make check`が直接これを呼ぶか、別のワークフローに任せるかを決めます。
- [ ] `docs/selectors.md`の「端末側リゾルバの移植契約」節（および
  [日本語版](../../docs/ja/selectors.md)）を更新し、`find_all`・`resolve_unique`・
  `_collapse_identical_duplicates`が手書きのSwift・Kotlin移植ではなくこのクレートに由来すると述べます。
  「守るべきであり閉じてはならない2つの相違」節はそのまま残します。この2つの相違が説明しているのは
  `resolvableMatchingIndex`であり、本項目はこれに触れないためです。BE-0409・BE-0410の「Swift／Kotlin
  へ移植する」手順を、コンパイル済みバインディングを呼ぶ形に更新します。BE-0410の派生ラベル移植は、
  `_derived_label`が共有コアより手前で正規化を行うため、独立したステップとして残します。あわせて、
  iOS側移植を先に行うという順序付けを、BE-0409の詳細設計と、BE-0410の実装順序・「順序ステータス」の
  両方から取り除きます。BE-0409の「順序ステータス」はBE-0408ではなく本項目を指すよう、BE-0410の
  「順序ステータス」はBE-0409ではなく本項目を指すよう、それぞれ付け替えます。どちらも、互いの完了順
  ではなく本項目のクレートに依存するためです。
- [ ] `roadmap-id`ワークフローがmain上で本項目のIDを割り当てたら、BE-0408・BE-0409・BE-0410へ相互の
  「関連」リンクを反映します。

## 参考

[BE-0111 — AI SDKをオプトインの依存にする](../BE-0111-ai-sdk-optional-dependency/BE-0111-ai-sdk-optional-dependency-ja.md)、
[BE-0407 — 証拠読み取りの重複排除とドライバ内部の調整によるステップ実行の高速化](../BE-0407-step-latency-driver-internal-tuning/BE-0407-step-latency-driver-internal-tuning-ja.md)、
[BE-0114 — backend 非依存の挙動を検査する driver conformance suite](../BE-0114-driver-conformance-suite/BE-0114-driver-conformance-suite-ja.md)、
[BE-0208 — AndroidエミュレータのCI](../BE-0208-android-emulator-e2e-ci/BE-0208-android-emulator-e2e-ci-ja.md)、
[BE-0238 — iOS端末クラウド実行](../BE-0238-ios-device-cloud-execution/BE-0238-ios-device-cloud-execution-ja.md)、
[BE-0292 — XCUITestの同梱ランナー](../BE-0292-xcuitest-bundled-runner/BE-0292-xcuitest-bundled-runner-ja.md)、
[BE-0405 — Android向けIdentifierTool](../BE-0405-android-identifiertool/BE-0405-android-identifiertool-ja.md)、
[BE-0408 — ステップレイテンシのための端末側実行プロトコル](../BE-0408-step-latency-device-executor-protocol/BE-0408-step-latency-device-executor-protocol-ja.md)、
[BE-0409 — XCTest ランナー内の iOS 端末側ステップ実行機](../BE-0409-step-latency-ios-device-executor/BE-0409-step-latency-ios-device-executor-ja.md)、
[BE-0410 — Androidの端末側ステップ実行エンジン](../BE-0410-step-latency-android-device-executor/BE-0410-step-latency-android-device-executor-ja.md)、
[`bajutsu/common/drivers/base/_functions.py`](../../bajutsu/common/drivers/base/_functions.py)、
[`bajutsu/common/drivers/adb/_functions.py`](../../bajutsu/common/drivers/adb/_functions.py)、
[`BajutsuKit/Sources/BajutsuRunner/PositionPath.swift`](../../BajutsuKit/Sources/BajutsuRunner/PositionPath.swift)、
[`docs/selectors.md`](../../docs/selectors.md)、
[`tests/fixtures/be0408/`](../../tests/fixtures/be0408/)、
[`tests/test_selector_fixtures.py`](../../tests/test_selector_fixtures.py)、
[UniFFI](https://mozilla.github.io/uniffi-rs/)
