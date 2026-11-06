[English](BE-0019-xcuitest-backend.md) · **日本語**

# BE-0019 — XCUITest backend

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0019](BE-0019-xcuitest-backend-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0019") |
| 実装 PR | [#366](https://github.com/bajutsu-e2e/bajutsu/pull/366), [#368](https://github.com/bajutsu-e2e/bajutsu/pull/368), [#374](https://github.com/bajutsu-e2e/bajutsu/pull/374), [#376](https://github.com/bajutsu-e2e/bajutsu/pull/376), [#391](https://github.com/bajutsu-e2e/bajutsu/pull/391), [#394](https://github.com/bajutsu-e2e/bajutsu/pull/394), [#397](https://github.com/bajutsu-e2e/bajutsu/pull/397), [#433](https://github.com/bajutsu-e2e/bajutsu/pull/433), [#672](https://github.com/bajutsu-e2e/bajutsu/pull/672) |
| トピック | プラットフォーム対応 |
| 関連 | [BE-0105](../BE-0105-xcuitest-single-snapshot-query/BE-0105-xcuitest-single-snapshot-query-ja.md) |
<!-- /BE-METADATA -->

## はじめに

idb に次ぐ 2 つ目の actuator として、XCUITest を追加します。抽象はすでに維持されているので、レジストリは XCUITest を安定度順ラダーの最上位に置けます。

## 動機

現状、iOS の actuator は idb だけです。idb は、**frame 中心への座標 tap** で操作します。idb は semantic tap を持たないので、run ループは `query()` で要素を一意にし、その中心を叩きます。ヘッドレス CI と一般的なケースにはこれで足りますが、実際の穴が残ります。

idb は `semanticTap` も、ネイティブの `conditionWait` も、`multiTouch` も提供しません（`docs/drivers.md`）。pinch や rotate のような 2 本指のジェスチャは、`UnsupportedAction` を上げます。これらの操作は、codegen → XCUITest を要すると注記されています。そのため、idb ではまったく実行できないジェスチャがあります。すべての tap も座標の往復を経由し、識別子で要素を叩くより本質的に脆くなります。

アーキテクチャは、2 つ目の actuator をすでに想定しています。

- DESIGN §3 は、idb の隣に「（将来）XCUITest backend（決定的コード生成）」を描いています。
- DESIGN §5 は、2 つ目の iOS actuator を差し込めるよう、ドライバ抽象を backend 非依存に保っています。
- `bajutsu/backends.py` は、意図した順序をコメントで宣言しています。`"ios": ("idb",),  # later: ("xcuitest", "idb")` です。

本提案は、このプレースホルダを現実にします。安定度ラダーで idb の**上位**に座る、本物の 2 つ目の actuator として、XCUITest を追加します。XCUITest は、idb にできない semantic な操作と多指ジェスチャを供給します。XCUITest が動かないヘッドレス環境のために、idb をフォールバックとして残します。

## 詳細設計

XCUITest は、既存の `Driver` Protocol を満たす登録済みの actuator になります。シナリオ DSL、セレクタ解決、run ループ、証跡サブシステム、レポータのいずれも変わりません。

- **レジストリでの配置。** `bajutsu/backends.py` で、iOS プラットフォームを `("xcuitest", "idb")` に展開します。XCUITest が先で、idb が後です。`xcuitest` は、実行可否のチェックとともに `IMPLEMENTED` に加えます。actuator とは、順に並べた中で最初に実装済みかつ利用可能な backend です。そのため `--backend ios` は、XCUITest が動くなら優先し、動かなければ idb にフォールバックします。シナリオも config も変わりません。これは、このレジストリが備える前方互換の挙動そのものです。
- **ランナーの駆動。** idb はサブプロセスの CLI です。XCUITest は違い、Simulator 上に常駐するランナーの内側から操作します。そのため Python とそのランナーのあいだに、通信路が必要です。ランナーは run のあいだ常駐し、小さなループバック HTTP エンドポイントを提供します。Python 側のドライバは、そこへ `query`、`tap`、ジェスチャの要求を送ります。

  これは、Bajutsu がすでに持つループバックの仕組みを流用します。`network.py` は `ThreadingHTTPServer` を `127.0.0.1` にバインドし、起動 env がそのアドレスをテスト対象アプリ（`BajutsuKit` の `BajutsuNet`）へ注入します。同じループバックを Python からランナーへの向きで使い、操作の要求を運びます。ランナー側の最小サーバは、大きな外部依存を取り込まず、`BajutsuKit` 内に実装します。通信路をプロジェクトの管理下に保つためです（「検討した代替案」を参照）。ランナーをどう**配布しビルドするか**は、下の「runner の配信とビルド」で決着させます。設定で指定する事前ビルド済みの `.xctestrun` を主とし、オンデマンドの `xcodebuild` をフォールバックにします。チャネルのプロトコルは、「Python ↔ runner のチャネル」で定めます。
- **より豊かな capability、同じ契約。** XCUITest ドライバの `capabilities()` は、idb が提供するものに加えて、`semanticTap`、ネイティブの `conditionWait`、`multiTouch` を返します。選択が決定性の核であることは変わらないので、`tap` はちょうど 1 要素に解決されます。XCUITest はその要素を、frame 中心の座標ではなく識別子で操作します。これで座標の往復がなくなります。idb では `UnsupportedAction` を上げる `pinch` と `rotate` が、直接実行できるようになります。
- **決定性を保つ。** XCUITest はネイティブの条件待機を提供します。それでも、オーケストレータの待機は固定 sleep のない条件待機のままで、曖昧なセレクタは即座に失敗します。新しい capability は、表現できることを広げるだけで、規則を緩めません。XCUITest が使われるのは、`run` 時の決定的な actuator としてだけで、LLM は Tier-2 ゲートに入りません。`codegen` は別の経路です。完成したシナリオを XCUITest のテストソースへ構造的にマッピングするもので、本提案の影響を受けません。
- **app-agnostic で、必要な所は per-app。** ドライバ自体はアプリ非依存です。XCUITest で駆動されるためにアプリが用意すべきもの（例: テストホストや起動引数）は、既存の per-app 設定と並べて `targets.<name>` の下に置きます。ツールとランナーは、アプリをまたいで変わりません。`doctor --target` は、idb の可否を報告するのと同じ方法で、XCUITest の可否を報告します。
- **フォールバックは健在。** idb は、ラダーの 2 番目に残します。必要なホストのないヘッドレス CI のように、XCUITest が動かない環境では、座標ベースの idb へなだらかに劣化します。run は、どの actuator が選ばれたかを記録します。そのため manifest は、豊かな経路とフォールバックの経路のどちらを通ったかを示します。これは既存の劣化開示の規則と整合します。

各部分の実装レベルの形を、以下に示します。

### registry への配置と、実装前のなだらかな扱い

`bajutsu/backends.py` は、*既知*の actuator と*実装済み*の actuator をすでに分けています。`select_actuator` は、計画済みだが未実装のトークンを、次に利用可能なものへ落とします。したがって順序の入れ替えは、ドライバが存在する**前**でも安全で、変更は一行です。

`PLATFORMS["ios"] = ("xcuitest", "idb")` とすれば、`xcuitest` は自動的に既知になります。`KNOWN_ACTUATORS` は import 時に `PLATFORMS` から派生するので、別に編集する一覧はありません。`xcuitest` は `IMPLEMENTED` には入れません。そのため `--backend ios` は、引き続き idb に解決されます（xcuitest は計画済みだからです）。ドライバが入った日に、2 つを足せば有効になります。`IMPLEMENTED` への `xcuitest` の追加と、可否の判定（`_EXECUTABLE` 内の `xcodebuild` の探索）です。

明記すべき小さな関連変更が 1 つあります。現状の `capabilities_for()` は、計画済みの actuator に対して `NotImplementedError` を投げます。BE-0082 のプリフライトがドライバの完成**前**に xcuitest を判断するには、`capabilities_for()` に分岐を足し、`XcuitestDriver.CAPABILITIES` を返す必要があります。これはデバイスなしで読めるクラス定数です。その分岐がなければ、capability 集合はドライバと一緒に入ります。

### runner の配信とビルド（未決事項の決着）

実現できる道は 2 つあります。推奨は、設定で指定するビルド済みのテスト runner を主とし、オンデマンドのビルドをフォールバックにする案です。既存の `appPath`（ビルド済み）と `build`（オンデマンド）の切り分けに倣います。これで XCUITest は、新しいオーサリングモデルを持ち込みません。

- **ビルド済み（主）。** `targets.<name>.xcuitest.testRunner` が、ビルド済みの `*.xctestrun`（またはテストホストの `.app`）を指します。`appPath` がビルド済みの `.app` を指すのと同じ方法です。run はそれを直接起動します。速く、Simulator はあっても完全な Xcode ツールチェーンがないマシンでは、唯一の道でもあります。
- **オンデマンド（フォールバック）。** `targets.<name>.xcuitest.build` はシェルコマンドです。`.xctestrun` がないときに、`serve` または `run` が実行して生成します。例は `xcodebuild build-for-testing -scheme … -destination 'platform=iOS Simulator,…' -derivedDataPath …` です。`build` が、ない `appPath` を生成するのと同じ仕組みです。

どちらのノブも `targets.<name>` の下に置くので、ツールはアプリ非依存のままです。DESIGN §1 は、bajutsu がビルド済みの成果物を受け取り、自分ではビルドしないと定めています。この設計は、その規則を守ります。ビルド済みの経路を優先し、`build` は明示的で任意の便宜として扱います。

XCUITest の **runner コード本体**は、小さく汎用的な XCTest ターゲットです。`BajutsuKit` に同梱し（`BajutsuNet` と並べます）、アプリごとには持ちません。起動時に渡された bundle id に対して `XCUIApplication` を駆動するので、1 つの runner がすべての target をまかないます。

### Python ↔ runner のチャネル

idb はサブプロセスの CLI です。XCUITest は、Simulator に常駐するテストプロセスから操作します。そのため、run のあいだ両者をつなぐチャネルが要ります。チャネルは、既存のループバック HTTP パターンを再利用します。`bajutsu/network.py` が `127.0.0.1` に `ThreadingHTTPServer` をバインドし、アプリは `BAJUTSU_COLLECTOR` 経由でそれと話します。今回の向きは **Python → runner** です。

`BajutsuKit` の runner は、テストメソッドの中で小さな `127.0.0.1:<port>` サーバを起こして常駐します。Python の `XcuitestDriver` が、そこへ操作の要求を送ります。ポートも同じ方法で runner に渡します。起動引数または env 引数です。チャネルはループバックに閉じ、ホストの露出を広げません。

契約は `Driver` Protocol に一対一で対応するので、ドライバより上は何も変わりません。

| Driver 呼び出し | 要求 | 応答 |
|---|---|---|
| `query()` | `GET /elements` | 正規化済みの `Element[]` の JSON。idb が返すのと同じ `identifier`／`label`／`value`／`traits`／`frame` の形なので、`find_all` と `resolve_unique` は変わりません。**各要素には、不透明なスナップショット単位の `handle` も載ります**。`handle` は runner が発行し、Python が `tap` と `gesture` に往復させます（「要素のアドレッシング」を参照）。`handle` は selector のフィールドではないので、照合には影響しません |
| `tap(sel)` | `POST /tap {handle}` | ok ／ not-found。Python が先に `query()` から一意の要素を解決するので、選択は決定性の核のままです。そのうえで、その要素の**スナップショットハンドル**を送ります。XCUITest は、座標ではなくその要素そのものをタップします（「要素のアドレッシング」を参照） |
| `pinch` ／ `rotate` | `POST /gesture {handle, kind, scale\|radians}` | ok。idb が `UnsupportedAction` を投げる 2 本指のジェスチャです |
| `wait_for(sel)` | Python が `GET /elements` をポーリング（オーケストレータの条件待ち）、または同じ上限付きで sleep なしの契約の下で、runner のネイティブ expectation | ok ／ timeout |
| `screenshot(path)` | `GET /screenshot` | PNG バイト列 |

エラーは、既存の `Driver` 例外に対応します。解決は Python 側に残るので、`ElementNotFound` と `AmbiguousSelector` も Python 側に残ります。最小のサーバは、大きな外部の自動化依存ではなく、`BajutsuKit` に置きます。これでチャネルは、プロジェクトの管理下にとどまります（「検討した代替案」を参照）。

### チャネルを越えた要素のアドレッシング

解決は Python 側に残ります。そのためチャネルは、Python が解決した要素「そのもの」を操作できなければなりません。runner 側で再解決して、別の一致を選ぶことは許されません。

runner は、`GET /elements` で返す各 `Element` に、**スナップショット単位の安定したハンドル**を発行します。そのスナップショットが生きているあいだ、対応関係を保持します。`POST /tap` と `POST /gesture` は、再問い合わせの述語ではなく、このハンドルを運びます。Python はスナップショットから一意の要素を解決し、そのハンドルを送ります。runner はハンドルを、すでに特定済みの `XCUIElement` に対応づけて操作します。

アクセシビリティの `identifier` だけでは、ハンドルとして**不十分**です。`identifier` は一意とは限りません。runner 側で `identifier` を使って再解決すると、選択がいま取り除いたばかりの曖昧さを、再導入してしまいます。

画面が変わってハンドルが無効になっている場合、runner は `stale` を返します。ドライバは、要素が消えたときと同じエラーを上げます。失敗は明示的で、runner が今の一致を黙って叩き直すことはありません。

### runner のライフサイクル：準備完了、後始末、クラッシュ

常駐する runner は、起動と失敗のモードを持つプロセスです。そのため、ライフサイクルを明示します。

- **準備完了。** ドライバは、runner のループバックサーバが接続を受け付けるまで、操作の要求を送りません。上限付きで sleep のない準備完了プローブ（`GET /health`）を待ちます。これは他の箇所と同じ条件待ちの作法です（固定 sleep なし）。タイムアウトしたときは、ハングせず、「runner が起動しなかった」と明確に失敗します。
- **後始末。** リースを解放すると、runner とそのループバックサーバを止め、アプリを終了します。これは、デバイスプールがリースの解放時にデバイスごとの collector を止めるのと同じ形です。待ち受けたままのものは残りません。
- **クラッシュ。** run の途中で runner が終了するか、応答しなくなったとします。次の要求が失敗し、ドライバは `Driver` エラーを上げるので、run は明示的に失敗します。クラッシュした runner を、黙って「要素が見つからない」と読むことはありません。インフラの失敗は、テストの結果とは別物のままです。

### 並列 run とデバイスプール（BE-0020）

デバイスプール（`runner/pool.py`）は、並列シナリオのために N 台の Simulator をリースし、各台にデバイスごとのリソースを与えます。XCUITest は、このモデルにそのまま収まります。リースされた各デバイスは、**自分専用のループバックポート上に、自分専用の常駐 runner** を持ちます。プールは、デバイスごとの network collector を、同じ形で自分のポート上に起こしています。

ポートはプールがリースごとに割り当て、そのデバイスの runner へ起動引数で渡します。2 つの並列 run が、runner やポートを共有することはありません。リースを解放すると、そのデバイスの runner が collector とともに止まります。グローバルな runner も共有ポートもないので、並列の XCUITest run は、並列の idb run と同じだけ隔離されます。

### XCUITest 下のネットワーク証跡（BE-0020 との相互作用）

XCUITest の capability 集合は `NETWORK` を**含みません**。XCUITest はより豊かな *actuator* であり、ネットワークをネイティブに観測するものではありません。

iOS のネットワーク証跡は、アプリ側の collector が供給します。`BajutsuNet` が、`BAJUTSU_COLLECTOR` で指されたループバックの受信側へ POST します。この collector は **actuator 非依存**です。アプリの起動環境に乗るものであり、アプリをどう駆動するかには依りません。したがって XCUITest 下では、runner が `BAJUTSU_COLLECTOR` を `XCUIApplication.launchEnvironment` へ注入する必要があります。idb が自分の起動 env に注入するのと、まったく同じです。そうすれば、ネットワークの捕捉は同一に働きます。

アプリ側の collector が、引き続きネットワーク証跡を供給します。そのため XCUITest を選んでも、BE-0020 の read-only な**兄弟**フォールバックは発動**しません**。このフォールバックが働くのは、同一プラットフォームのどの適格な backend も供給できない証跡の種別に限ります。ネットワークは、その種別に当たりません。

これで BE-0020 の境界が具体的になります。証跡の穴は同一プラットフォームの read-only の provider が埋め、*操作*は選ばれた単一の backend にとどまります。

### capability・doctor・開示

`XcuitestDriver.capabilities()`（および `capabilities_for("xcuitest")`）は、`QUERY`、`ELEMENTS`、`SCREENSHOT`、**`SEMANTIC_TAP`**、**`CONDITION_WAIT`**、**`MULTI_TOUCH`** を返します。idb を超える 3 つが、識別子による tap と、pinch ／ rotate を解禁します。

`doctor --target` は、XCUITest の可否（ツールチェーンと、設定済みまたはビルドできる runner）を、idb と並べて報告します。run の manifest はすでに選ばれた actuator を記録しているので、idb へのフォールバックは、黙ってではなく開示されます。

### 検証

検証は、Simulator なしの高速ゲートで証明できる部分と、デバイスを要する部分に分けます。

**高速ゲート（デバイスなし）。**

- registry：`--backend ios` は、`xcuitest` が利用可能なら優先し、不可なら `idb` へフォールバックします。テストは、可否の判定関数を注入した `select_actuator` を駆動します。
- `capabilities_for("xcuitest")` は、豊かな集合を返します。
- ドライバ：操作の要求の組み立てと、応答の解釈を、**注入した fake の HTTP トランスポート**に対して検証します。idb のテストが fake の `run` を注入するのと同じ要領です。確認する挙動は 4 つあります。
  - `tap` は、一意の要素を解決してから、その要素が持つスナップショットハンドルで指します。
  - `pinch` と `rotate` は、ジェスチャの要求を出します。
  - `stale` の応答は、要素消失のエラーを上げます。
  - 曖昧な selector は、どの要求よりも前に失敗します。
- ライフサイクル：準備完了プローブは、fake のトランスポートに対して待ってから、きれいにタイムアウトします。2 リースのプールは、各ドライバに別々のポートを渡します。

runner も LLM も、`run` ／ CI ゲートには載せません。

**デバイス上（e2e 経路）。** より重い `e2e.yml` 経路で、起動した Simulator に対して実際の `BajutsuKit` の runner を動かします。対象は 2 つです。

- Python 側で要素を解決し、そのスナップショットハンドルで操作するシナリオ。idb にはできない pinch または rotate も行います。
- XCUITest が使えないホストでのフォールバックの run。idb へのなだらかな劣化を確認します。

## 検討した代替案

- **idb を XCUITest で丸ごと置き換える案。** XCUITest は、より豊かな actuator です。しかし idb のヘッドレスで座標ベースの動作は、XCUITest の完全なホストが扱いづらい CI 環境で、まさに価値を持ちます。順序付きのラダーなら、双方の良さが得られます。XCUITest を優先し、動かなければ idb にフォールバックして、シナリオは変えません。
- **欠けているジェスチャを idb に後付けする案。** idb の単一タッチのプリミティブから、pinch と rotate を合成する手もあります。idb は本質的に単一タッチを露出するので、多指ジェスチャの不確実な近似にしかなりません。それは、プロジェクトが避ける、脆く非決定的な挙動そのものです。本物の多指 backend が、誠実な解です。
- **ランナーの通信路として WebDriverAgent を採用する案。** WebDriverAgent は、実績のある HTTP と XCTest のサーバです。Python とランナーの通信路を、既製で供給できます。最初の一歩としては不採用です。理由は 2 つあります。取り込んで保守するには大きすぎる依存です。また、backend を絞り込んだツールへの subprocess 呼び出しで駆動するという、薄い依存の方針（DESIGN §4）から、プロジェクトを引き離します。`network.py` と `BajutsuNet` にある既存のループバックの仕組みを流用し、`BajutsuKit` 内に最小のランナー側サーバを置けば、面積を小さくプロジェクトの管理下に保てます。最小のサーバで不十分だと分かった場合に備えて、WebDriverAgent はフォールバックとして残します。
- **特定のジェスチャだけ XCUITest へ流し、idb を actuator のままにする案。** 2 つのドライバが 1 つのデバイスを操作すると、単一 actuator の規則（DESIGN §3.3 と §5）が防ごうとする非決定性が、再導入されます。actuator は、run ごとに一度固定されます。*証跡*の能力差は、read-only フォールバックの設計（BE-0020）が別に扱います。*操作*は、1 つの backend にとどまります。

## 進捗

- [x] バックエンドの登録。`PLATFORMS["ios"] = ("xcuitest", "idb")` とし、`xcuitest` を `IMPLEMENTED` に入れ、idb をフォールバックにします（`bajutsu/backends.py`）。
- [x] ランナー側サーバー。`BajutsuKit` の汎用 XCTest ターゲット（`Sources/BajutsuRunner/RunnerServer.swift`）が、ループバックの `/elements` / `/tap` / `/gesture` / `/swipe` / `/type` エンドポイントを公開します（[#374](https://github.com/bajutsu-e2e/bajutsu/pull/374)、[#376](https://github.com/bajutsu-e2e/bajutsu/pull/376)）。
- [x] Python とランナーのチャネル、および `XcuitestDriver`。`bajutsu/drivers/xcuitest.py` が、スナップショットごとに安定したハンドルで要素を操作します（[#366](https://github.com/bajutsu-e2e/bajutsu/pull/366)、[#368](https://github.com/bajutsu-e2e/bajutsu/pull/368)）。
- [x] 実行ライフサイクルの配線と事前条件。リースごとのランナーの起動・終了と、事前条件の転送（[#391](https://github.com/bajutsu-e2e/bajutsu/pull/391)、[#394](https://github.com/bajutsu-e2e/bajutsu/pull/394)）。
- [x] ケイパビリティ、doctor、開示。`doctor --target` が idb の検査を併合し、xcuitest 選択時に idb へフォールバックします（[#397](https://github.com/bajutsu-e2e/bajutsu/pull/397)）。
- [x] ランナーのテストターゲット。`XCUIApplication` を走査して正規化した `Element`（identifier、label、value、traits、frame）へ変換し、スナップショットのハンドルで操作する（tap、gesture、swipe、type、screenshot）、具象の XCUITest 版 `ElementProviding` です。XCTest と `BajutsuRunner` を取り込み、`RunnerServer.startFromEnvironment()` を呼んで run のあいだ常駐する薄い UI テストターゲット（`BajutsuKit/Runner/`）として実装しました。
- [x] アプリ非依存の起動。テスト対象の bundle id をランナーへ転送します（既存の `BAJUTSU_LAUNCH_*` に並べる `BAJUTSU_BUNDLE_ID`）。`XcuitestEnvironment` が値を設定し、`RunnerServer.forwardedBundleId` がそれを公開し、ターゲットは `XCUIApplication(bundleIdentifier:)` で起動します。XCUITest がビルド時に要求するターゲットアプリは、プロジェクト内の最小のホストアプリで満たします。
- [x] ランナーのパッケージングと設定例。`make -C demos/showcase runner-build` が汎用ランナーを `*.xctestrun` にビルドし、2 つの `-noax` showcase target が `xcuitest.testRunner`（オンデマンドのフォールバックとして `build`）でそれを指します。
- [x] 実機検証。idb にはできない pinch ／ rotate のシナリオを、`e2e.yml` 経路でフルの `run` 経路を通して確認しました。`xcuitest (multi-touch)` ジョブが、`SHOWCASE_GESTURES` 起動 env で差し替わる平坦でスクロール不要な `GestureView`（BE-0114 の `SHOWCASE_CONFORMANCE` に倣った作り）に対して `scenarios/gestures_multitouch.yaml` を `--backend xcuitest` で走らせ、緑になっています（[#672](https://github.com/bajutsu-e2e/bajutsu/pull/672)）。範囲は単一デバイスに絞りました。並列デバイスプールは BE-0020 自身のテストと実機の conformance ジョブが担保し、idb へのなだらかな劣化のフォールバックはレジストリの高速ゲートのユニットテスト（可用性判定を差し込んだ `select_actuator`）と idb の `smoke` ジョブが担保します。macOS ランナーには必ず xcodebuild があるため、実機で xcuitest が idb へ実際に劣化する様子は観測できません。
- [x] クエリ性能。個別プロパティごとの `/elements` 読み取り（各プロパティが個別の XCUITest 往復で、84 要素の画面で約12秒。このためドライバのソケットタイムアウトを 30 秒へ引き上げていました）を、`XCUIApplication` の単一スナップショット走査へ置き換えました（[BE-0105](../BE-0105-xcuitest-single-snapshot-query/BE-0105-xcuitest-single-snapshot-query-ja.md)、[#670](https://github.com/bajutsu-e2e/bajutsu/pull/670)）。

作業は、次のスライスに分けて構築しました。

- #366 と #368：ドライバとチャネル。
- #374 と #376：ランナーのサーバとエンドポイント。
- #391 と #394：実行ライフサイクルの配線と、事前条件。
- #397：doctor のフォールバック。
- #670：単一スナップショットのクエリ（BE-0105）。

**調査（2026-07-02）。** iPhone 17 Pro、iOS 26.1（fb-idb 1.1.7）で確認しました。`idb ui describe-all` は、UIKit の `UITabBarController` も SwiftUI の `TabView` も、子を持たない 1 つの不透明な「Tab Bar」グループとしてしか見せません。一方 XCUITest は、各タブを label で解決できるボタンとして列挙し、`tabBars.buttons["…"].tap()` でタブが切り替わりました。結果は、`-noax` の showcase アプリ 2 つのどちらでも同じで、どちらもアクセシビリティ識別子を持ちません。

これが、ランナーのテストターゲットで解けるようになる具体例です。idb からは見えないタブが、セレクタで解決できるようになります。そのため xcuitest 経由の crawl は、vision のタブロケータを必要としません。

**ランナーのテストターゲットの着地（2026-07-02）。** 具象の provider と UI テストターゲットを実装し、実機でエンドツーエンドに検証しました。`showcase-uikit-noax` に対する `bajutsu run --backend ios` は、`tap: {label: "Search", traits: [button]}` を一意に解決し、タブを切り替えました。`exists` アサーションは通り、マニフェストは `backend: "xcuitest"` を記録しました。

実機では、ライフサイクルの穴も見つかりました。`xcodebuild` は、自身のプロセス環境を Simulator のテストランナーへ渡しません。そこで `XcuitestEnvironment` は、run の `BAJUTSU_*` 変数を、`.xctestrun` の各ターゲットの `TestingEnvironmentVariables` に書き込んだ私的なコピーを作って起動します。コピーはビルド成果物の隣に置くので、`__TESTROOT__` は引き続き解決できます。

**クエリ性能の解決（2026-07-05）。** [BE-0105](../BE-0105-xcuitest-single-snapshot-query/BE-0105-xcuitest-single-snapshot-query-ja.md)（[#670](https://github.com/bajutsu-e2e/bajutsu/pull/670)）が、プロパティごとの `/elements` 読み取りを、1 回の `app.snapshot()` 走査に置き換えました。これで、このボックスに残っていた最後の障害がなくなりました。BE-0105 は、BE-0019 のスナップショットハンドルによるアドレッシングを、次のとおりそのまま保ちます。

- Python が、一意の要素を解決します。
- ランナーは、まさにそのハンドルの要素を操作します。
- 曖昧なセレクタは、即座に失敗します。
- 無効になったハンドルは、明示的に失敗します。

**実機のジェスチャ検証の配線（2026-07-05）。** 4 つを追加しました。

- 平坦でスクロール不要な `GestureView`。`log.pinch` と `log.rotate` の 2 本指ターゲットを持ち、`SHOWCASE_GESTURES` 起動 env で差し替わります。BE-0114 の `SHOWCASE_CONFORMANCE` に倣った、SwiftUI だけのテスト用の仕掛けです。
- `scenarios/gestures_multitouch.yaml`。ターゲットを pinch と rotate し、ミラーした各 a11y value が変わることをアサートします。
- `e2e.yml` の `xcuitest (multi-touch)` ジョブ。このシナリオを、フルの `run` 経路（`--backend xcuitest`）で走らせます。
- idb の smoke 経路から、このシナリオを外す判断。idb は単一タッチのため `UnsupportedAction` になり、実行できません。

当初は、Log タブの Form の奥にターゲットを置きました。専用画面に変えたのは、XCUITest の座標スワイプが、SwiftUI の Form を画面外の行まで十分にスクロールしなかったからです。このスワイプによるスクロールの制約は、本項目のアクチュエーション検証とは別の問題で、別途フォローアップに値します。ジョブが緑になった時点で、項目を実装済みへ移します。

## 参考

[DESIGN §5 / §3](../../DESIGN.md)、`bajutsu/backends.py`
