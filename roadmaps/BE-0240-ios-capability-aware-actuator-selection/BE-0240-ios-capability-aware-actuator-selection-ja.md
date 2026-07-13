[English](BE-0240-ios-capability-aware-actuator-selection.md) · **日本語**

# BE-0240 — iOS の能力に応じた actuator 自動選択（idb/XCUITest の透過化）

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0240](BE-0240-ios-capability-aware-actuator-selection-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0240") |
| 実装 PR | [#981](https://github.com/bajutsu-e2e/bajutsu/pull/981) |
| トピック | プラットフォーム対応 |
| 関連 | [BE-0019](../BE-0019-xcuitest-backend/BE-0019-xcuitest-backend-ja.md)、[BE-0020](../BE-0020-multi-backend-evidence-fallback/BE-0020-multi-backend-evidence-fallback-ja.md)、[BE-0082](../BE-0082-capability-preflight-check/BE-0082-capability-preflight-check-ja.md)、[BE-0107](../BE-0107-showcase-tab-navigation-no-launch-shortcut/BE-0107-showcase-tab-navigation-no-launch-shortcut-ja.md)、[BE-0223](../BE-0223-adb-tab-bar-navigation/BE-0223-adb-tab-bar-navigation-ja.md) |
<!-- /BE-METADATA -->

## はじめに

iOS には idb と XCUITest という 2 つの actuator があり、`bajutsu/backends.py` はすでにこれらを順序付きの梯子として宣言しています（`PLATFORMS["ios"] = ("xcuitest", "idb")`、BE-0019）。理屈のうえでは、シナリオ作者は `platform: ios`（あるいは `backend: [ios]`）とだけ書けば actuator を意識せずに済むはずです。ところが実際には、すべての実設定が `backend: [idb]` を固定し、XCUITest の豊かな能力を必要とするごく一部のシナリオを振り分けるためだけに、もう 1 つのシナリオディレクトリ（`demos/showcase/ios/scenarios-xcuitest/`）が丸ごと存在しています。本提案は、シナリオ自身がすでに自分のステップを通じて暗示している能力要求から、どの iOS actuator を必要とするかを**シナリオ自身に決めさせる**ものです。作者が書くもの（プラットフォーム）と実際にそれを動かしたもの（actuator）のあいだにある溝を、シナリオファイルの配置や CLI フラグへ漏れ出させたままにせず、実装の詳細として閉じ込めます。

## 動機

### 設計された梯子と、実際の使われ方の乖離

BE-0019 は、プラットフォームトークンを安定度順の actuator 列へ展開し、最初に利用可能なものを選ぶ `select_actuator` を作りました。したがって `--backend ios` は本来、XCUITest を優先し、そのツールチェイン（`xcodebuild`）が使えないときだけ idb にフォールバックするはずでした。ところが `demos/showcase/showcase.config.yaml` は、どの実ターゲットについてもこのデフォルトの解決を使っていません。

```yaml
defaults:
  backend: [idb]
  ...
  showcase-swiftui-noax:
    ...
    backend: [idb]   # idb + vision (BE-0176): crawl drives this -noax app with no XCUITest runner
    scenarios: demos/showcase/ios/scenarios-xcuitest
    # XCUITest backend (BE-0019) stays wired for explicit `--backend xcuitest`: the generic runner
    # reaches elements idb's tree can't (e.g. a TabView's tabs) by label, where idb needs vision.
```

すべてのターゲットが `backend: [idb]` を明示的に固定し、さらに 2 つの `-noax` ターゲットは、まったく別の `scenarios` ディレクトリを抱えています。その唯一の存在理由は、idb の `describe-all` が `UITabBarController` / `TabView` を、子を持たない 1 つの不透明な "Tab Bar" グループへ潰してしまうことです（BE-0107、および BE-0019 のオンデバイス調査記録に記載済み）。そのためタブの切り替えには `--backend xcuitest` と、XCUITest の要素到達方式に沿って書かれたシナリオが要ります。シナリオ作者、あるいは今は config の作者が、あるシナリオがどちらの backend を必要とするかを事前に把握し、対応するディレクトリへ振り分け、対応するフラグで起動しなければなりません。これはまさに「プラットフォームは 1 つのインターフェースの背後にある backend である」という原則（[DESIGN §1](../../DESIGN.md)）が作者の手から遠ざけようとしていた、backend への意識そのものです。それが単に、実行時の CLI フラグから、ディレクトリ単位・ターゲット単位の config の慣習へと居場所を変えただけで、透過性は少しも増していません。

### idb の能力は xcuitest の完全な部分集合であり、その優先理由は能力ではなくコストです

2 つの driver が宣言する `CAPABILITIES` を並べて読むと、本提案が本来避けて通れないはずだった問いに決着がつきます。

```python
# bajutsu/drivers/idb.py
CAPABILITIES = frozenset({QUERY, ELEMENTS, SCREENSHOT}) | DEVICE_CONTROL_ALL

# bajutsu/drivers/xcuitest.py
CAPABILITIES = frozenset({QUERY, ELEMENTS, SCREENSHOT, SEMANTIC_TAP, CONDITION_WAIT, MULTI_TOUCH}) | DEVICE_CONTROL_ALL
```

xcuitest の能力集合は idb の完全な上位集合です。idb が支えるデバイス制御操作(`setLocation` / クリップボード / `push` / keychain クリア / バックグラウンド・フォアグラウンド化 / ステータスバー)は、xcuitest も同じ `simctl` 駆動の Simulator ライフサイクルを共有するため、すべて支えます（BE-0128 / BE-0212）。したがって、idb が対応していて xcuitest が対応していない構文は存在せず、**idb でなければならないシナリオはありません**。idb の強みは能力ではなくコストにあります。必要なのは `idb` / `idb_companion` クライアントだけで Xcode のツールチェインは要らず、ビルド済みテストランナー(`xcuitest.testRunner` / `xcuitest.build` という、ターゲットごとの追加のセットアップ手順)も要らず、リースしたデバイスごとの常駐プロセスも要らず、しかも長年の実運用による枯れ具合(週次の `idb-monitor.yml` 互換性監視)があります。BE-0019 の能力優先の梯子(利用可能なら XCUITest を優先し、使えないときだけ idb にフォールバック)は能力を最適化してこのコストの差を無視しており、それこそがすべての実設定が idb 固定へ差し戻している理由です。あるべきデフォルトはその逆で、安い actuator を優先し、そのシナリオ自身のステップが本当に必要とするときにだけ、豊かな actuator へ昇格させることです。

### preflight の仕組みはすでに必要なものを計算済みで、今は棄却にしか使っていません

`bajutsu/capability_preflight.py`(BE-0082)はすでに、シナリオのステップツリー全体を(`if` / `forEach` の中まで再帰しながら)歩き、`(シナリオ, 能力集合)` の純粋関数として、その集合に欠けた能力を必要とする構文をすべて導出しています。`pinch` / `rotate` は `multiTouch` を、`visual` アサーションは `screenshot` を、各デバイス制御ステップは自分の `deviceControl.*` トークンを必要とします。今日の唯一の呼び出し元である `runner/pipeline.py` は、**すでに選ばれた** actuator がそれをカバーできないとき、`unsupported(scenario, self.caps)` を使ってデバイス作業の前にシナリオを失敗させるためだけにこれを使っています。同じ計算を、固定した 1 つの集合ではなく候補となる actuator ごとの能力集合に対して走らせれば、それがそのまま「このプラットフォームの梯子のなかで、このシナリオが実際に動かせる最も安い actuator はどれか」を求める解決器になります。新しい能力モデルも、解析の重複も要らず、すでに存在する関数の呼び出し元が増えるだけです。

## 詳細設計

### コスト順で能力をチェックする解決器

あるプラットフォームの actuator 候補とシナリオを受け取り、**コスト順**(安いものから)で、利用可能かつ十分な(`capability_preflight.unsupported(scenario, capabilities_for(candidate)) == []` が成り立つ)最初の候補を返す解決関数を足します(名前は仮に `select_actuator_for_scenario` とします)。安い候補が preflight を通らないときだけ、より豊かな次の候補へ昇格します。iOS のコスト順は `("idb", "xcuitest")` で、BE-0019 が持つ既存の能力優先の安定度順 `("xcuitest", "idb")` とは逆になります。この 2 つの順序は「どちらがより能力豊かか」と「どちらが安く動かせるか」という別々の問いに答えるものであり、actuator が 3 つ以上ある将来のプラットフォームでは常に単純な逆順になるとは限りません。そのため、これは安定度の梯子を暗黙に `reversed()` するのではなく、`PLATFORMS` と並ぶ独立した順序(たとえば `COST_ORDER` という並行のテーブル)として明示すべきです。`select_actuator`(シナリオを見ない、利用可能性だけの選択)はそのままにし、まだシナリオを手にしていないすべての呼び出し元(`doctor`、pool の事前セットアップ、actuator を 1 つに明示指定した場合)を今までどおり支えます。

actuator を 1 つに明示指定したリクエストは、既存のルール(`--backend <one>` は `--udid` と同様に、固定したらフォールバックしません。DESIGN §3.3)と整合する形で、フォールバックのない固定指定のままにします。この解決器は、要求された `backend` リストが iOS の actuator を 2 つ以上へ展開するとき、すなわち `backend: [ios]` や明示的な `backend: [idb, xcuitest]` のときにだけ働きます。これはそのまま `showcase.config.yaml` の移行手順にもなります。`backend: [idb]` の固定をこの梯子に置き換えれば、あとはシナリオごとにこの解決器が引き取ります。

### 選択を「実行 1 回につき 1 度」から「シナリオ 1 本につき 1 度」へ動かす

今日の `device_pool()`(`bajutsu/runner/pool.py:106`)は `select_actuator` をちょうど 1 回だけ呼び、その結果を pool の以後の生涯にわたるすべての `lease()` 呼び出しがクロージャで捕まえています。1 回の CLI 実行に含まれるすべてのシナリオは、並列であっても、同じ固定された actuator で動きます(`pipeline.py` の `_ScenarioRunner.actuator` は、スレッドプール全体で使い回される 1 つの不変フィールドです)。この選択を能力に応じたものにするとは、**シナリオごと**にすることを意味しますが、そのための継ぎ目は見た目より近くにあります。`lease(eff, scenario)` はすでにシナリオを引数として受け取っており(`pool.py:147`)、`launch_driver(udid, eff, actuator, ...)` が呼ばれるのはまさにこの `lease()` の内側です。解決器の呼び出しを `device_pool()` の一度きりのセットアップから `lease()` の中へ、シナリオがすでに手元にあるその場所で actuator を解決するように動かせば、単一 actuator という不変条件は今日と寸分違わぬ厳格さのまま保たれます。BE-0019 / BE-0020 の「actuator は 1 度確定し、run の間じゅう固定される」は、「実行全体の間じゅう」ではなく「そのシナリオの間じゅう」を意味するようになります。これは規則の単位を狭めるだけであって、緩めるものではありません。どの瞬間を切り取っても、リースしたデバイスを操作する actuator は依然としてただ 1 つです。

この移動に伴って動かす必要があるものもあります。`pool_env` / `environment_for(actuator, ...)`、証跡 provider の解決(BE-0020)、デバイスカタログは、今日は `lease()` の外側で pool 全体の 1 つの actuator から一度だけ計算されています。idb と xcuitest のあいだで意味のある違いが出る部分(なかでも、actuator が常駐ランナーの起動・後始末を必要とするかどうかは、BE-0019 がすでに lease ごとのスコープにしています)は、`lease()` の中へ動かし、シナリオごとに解決する必要があります。これは新しい下位システムではなく、`pool.py` 内側の実質を伴った、範囲の定まった手術です。lease ごとの環境・lease ごとの driver・lease ごとの証跡接続という形は、まさにこのためにすでに存在しています。

### 開示にスキーマ変更は要りません

`bajutsu/report/manifest.py:37-44` はすでにこれを見越しています。`_run_backend()` が run のシナリオ群にまたがる `RunResult.backend` の異なる値を join するのは、各 `RunResult` がすでに自分自身の `backend` フィールドを持っている*からこそ*です。「actuator は run につき 1 つに固定されるので、これは普段は単一の名前だが、もしシナリオ間で異なれば join する」。この継ぎ目がこれまで使われていなかったのは、すべてのシナリオがこれまで常に同じ actuator しか得てこなかったからにすぎません。本提案は、それらを実際に異ならせる最初の変更であり、manifest はすでにそれをどう報告すればよいかを知っています。`doctor --target` にも小さな拡張を足します。既存の idb/XCUITest 利用可能性チェックの隣に、そのターゲットのシナリオのうちいくつが、どちらの actuator へ解決するかの要約(デバイス不要の純粋な `capability_preflight` の実行だけで済みます)を出します。新しいゲートではなく、情報としての開示です。

### showcase config の移行

解決器が入れば、`demos/showcase/showcase.config.yaml` の `-noax` ターゲットは `backend: [idb]` の固定を外し(`backend: [ios]` や `[idb, xcuitest]` に置き換え)、`ios/scenarios-xcuitest/` は共有の `scenarios/` ディレクトリへ畳み戻せます。タブ切り替えのシナリオは自分で `xcuitest` へ解決し、それ以外は同じディレクトリのまま `idb` にとどまり、起動コマンドのどこにも `--backend` フラグは要りません。これが、*動機*で述べた溝が実際に閉じたことの、dogfood による具体的な証明になります。

### 検証

- **速いゲート(デバイス不要)。** 解決器は `(候補, シナリオ, available)` の純粋関数です。単体テストで小さな fake シナリオ(素朴なタップだけのシナリオ、`pinch` を含むシナリオ、デバイス制御だけのシナリオ)を組み立て、コスト優先の選択、安い actuator の `unsupported()` が空でないときだけ昇格すること、actuator を 1 つに明示指定したリクエストは決して昇格しないことをアサートします。`_run_backend()` の「異なるときに join する」振る舞いにも、異なる `backend` 値を持つ 2 つの `RunResult` を使ったテストを足します。これはコメントの中の仮定ではなく、実際に到達可能なケースになります。
- **オンデバイス(e2e 経路)。** 移行後の showcase `-noax` ターゲットを `--backend ios` で走らせ、manifest が通常のシナリオには `idb` を、タブ切り替えのシナリオには `xcuitest` を、別々のシナリオディレクトリなしに同じ実行の中で記録することを確認します。

## 検討した代替案

- **既存の能力優先の梯子をそのまま使い、上書きをやめる(利用可能なら常に XCUITest を優先する)。** 不採用です。これは能力を最適化してコストを無視しており、まさにそれこそが今日すべての実設定が idb を固定している理由です。必要とする一部のシナリオだけでなく、すべてのシナリオで軽いデフォルトを重いものへ差し替えることになります。
- **1 回のシナリオ実行の内側でステップ単位に actuator を受け渡す。** 「1 ファイル単位ではなく、やり取りごとに bajutsu に選ばせる」というもっとも文字どおりの読み方であり、正面から検討しました。不採用です。idb の能力集合は xcuitest の完全な部分集合なので(*動機* を参照)、idb にしかできないステップと xcuitest にしかできないステップが混在するシナリオはそもそも存在せず、混在したシナリオが必要とするものはすべて、シナリオ全体を xcuitest で動かせば満たせます。実行時の受け渡し(シナリオ途中で生きた driver を入れ替えながらの状態継承、厳密に排他的な逐次アクセス)を作ることは、能力面での利益がゼロのまま実質的な決定性のリスクを持ち込みます。しかもこれは、すでに明示的に 2 度下された判断を覆すことになります。BE-0019 の検討した代替案は「idb を actuator のままにして特定のジェスチャだけを XCUITest へ回す」を、「単一 actuator の規則(DESIGN §3.3/§5)が防ごうとしている非決定性を再導入する」として退けており、BE-0020 の検討した代替案も「あるステップで『より良い』フォールバック backend に操作させる」を同じ理由で退けています。本提案はこの規則をそのまま保ちます。規則が適用される*単位*を(1 回の CLI 実行全体から、1 本のシナリオの実行へ)狭めるだけで、2 つの actuator が同時に 1 台のデバイスを操作することは一度も許しません。
- **idb 自身のタブバーへの視えなさを直接直す(BE-0223 の Android 方式を iOS に適用する)。** 実在する、補完的な選択肢です。BE-0223 は backend を切り替えるのではなく、adb という単一の actuator に共有のタブバーセレクタを解決させました。ただし idb のタブバーの穴は `describe-all` のツリー自体の限界であって、adb の修正が扱ったようなパース側の穴ではなく、idb の multi-touch の穴はアーキテクチャ上のもの(そもそも単一タッチしか扱えない)です。昇格が必要な範囲を狭めることは価値ある follow-up ですが、それでも残る穴(何よりまず `multiTouch`。idb 側の修正では決して埋まりません)に対する、能力に応じた自動選択の必要性そのものはなくなりません。
- **ステップから導くのではなく、シナリオ側で `requires: [multiTouch]` のように宣言する。** 二重の真実の源になるため不採用です。`capability_preflight.py` はすでにステップそのものから同じ集合を導出しており、別に宣言を持たせれば、シナリオが実際にしていること(使っている以上を宣言する、あるいは以下しか宣言しない)とずれても、それを捕まえるものが何もありません。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解(作業の単位ごとに 1 つ)に対応し、ログには変更内容と時期(古い順)を PR へのリンクと
> ともに記録します。

- [x] `bajutsu/backends.py` に、コスト順の解決器 `select_actuator_for_scenario` を足しました(明示的な `COST_ORDER` テーブルつき)。各候補の能力集合に対して `capability_preflight.unsupported` を再利用する、速いゲートで単体テスト済みの純粋関数です。
- [x] シナリオごとの解決を `runner/pool.py` の `lease()` に配線しました(`device_pool()` の一度きりのセットアップから移動)。単一 actuator という不変条件を「シナリオの実行ごと」へ狭め、決して緩めていません。パイプラインの preflight(`runner/pipeline.py`)も同じ純粋関数をシナリオごとに解決するので、どの利用可能な actuator でも走らせられないシナリオは、デバイスに触れる前に速く失敗します。
- [x] actuator に依存する pool レベルの状態を、pool につき一度ではなく lease ごとに解決するようにしました。`environment_for` と証跡 provider の解決を `lease()` の内側へ移し、`launch_driver` に `environment` 引数を足して、lease を**起動した**インスタンスがそれを**片付ける**インスタンスと同一になるようにしました。これは、シナリオごとの XCUITest 選択が常用パスになると顕在化する、XCUITest の常駐ランナーのライフサイクルの潜在バグ(env インスタンスが 2 つに分かれていた)を直すものです。
- [x] `doctor --target` に、シナリオごとの actuator 解決の開示を足しました(情報提供であり、新しいゲートではありません)。
- [x] `demos/showcase/showcase.config.yaml` の移行は **アクセシビリティ ON のターゲットのみ**。`showcase-swiftui` / `showcase-uikit` を `backend: [ios]` にし、共有の `gestures_multitouch.yaml`(multiTouch)がシナリオごとの昇格を実地に示す例になります。`-noax` ターゲットは意図的に**移行せず**、`ios/scenarios-xcuitest/` も畳み戻していません。これらが XCUITest を必要とする理由はアプリ側の性質(アクセシビリティ id が無い、idb の `describe-all` がタブバーを潰す)であって、能力モデルが表現できるものではありません。`[ios]` のラダーは、これらの(能力の面では clean な)タブ越えシナリオを idb へ誤ルーティングしてしまいます。この隙間は BE-0223 型の actuator 強化の領分で、本提案の *検討した代替案* もそこへ割り当てています。理由は config のコメントに記録しました。
- [ ] オンデバイス検証(後続作業。Simulator とビルド済みの XCUITest runner が要るため、速いゲートの外)。移行後の `make -C demos/showcase run-swiftui` が、1 つの manifest の中で通常のシナリオには `idb` を、`gestures_multitouch` には `xcuitest` を記録することを、`run` の経路全体を通して確かめます。

## 参考

[DESIGN §1 / §3.3 / §5](../../DESIGN.md)、`docs/ja/drivers.md`、`docs/ja/multi-platform.md`。`bajutsu/backends.py`(`PLATFORMS`、`select_actuator`、`capabilities_for`)、`bajutsu/capability_preflight.py`(`unsupported`)、`bajutsu/runner/pool.py`(`device_pool`、`lease`)、`bajutsu/runner/pipeline.py`(`_ScenarioRunner.actuator`)、`bajutsu/report/manifest.py`(`_run_backend`)、`bajutsu/drivers/idb.py` / `bajutsu/drivers/xcuitest.py`(`CAPABILITIES`)、`demos/showcase/showcase.config.yaml`。

**依存・関連項目：** [BE-0019](../BE-0019-xcuitest-backend/BE-0019-xcuitest-backend-ja.md)(本提案が手を伸ばせるようになる actuator そのもの)、[BE-0020](../BE-0020-multi-backend-evidence-fallback/BE-0020-multi-backend-evidence-fallback-ja.md)(本提案があえて手を触れない姉妹の仕組み。証跡フォールバックは read-only のまま、操作は単一のままです)、[BE-0082](../BE-0082-capability-preflight-check/BE-0082-capability-preflight-check-ja.md)(本提案が解決器の入力としてそのまま再利用する関数を供給しています)、[BE-0107](../BE-0107-showcase-tab-navigation-no-launch-shortcut/BE-0107-showcase-tab-navigation-no-launch-shortcut-ja.md)(本提案が自動的に迂回する idb のタブバーの穴を記録しています)、[BE-0223](../BE-0223-adb-tab-bar-navigation/BE-0223-adb-tab-bar-navigation-ja.md)(同種の穴を backend の切り替えではなく単一 actuator の強化で直した Android 側の先例。iOS にとって競合ではなく補完の方向です)。
