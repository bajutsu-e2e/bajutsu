[English](BE-0019-xcuitest-backend.md) · **日本語**

# BE-0019 — XCUITest backend

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0019](BE-0019-xcuitest-backend-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装中** |
| 実装 PR | [#366](https://github.com/bajutsu-e2e/bajutsu/pull/366), [#368](https://github.com/bajutsu-e2e/bajutsu/pull/368), [#374](https://github.com/bajutsu-e2e/bajutsu/pull/374), [#376](https://github.com/bajutsu-e2e/bajutsu/pull/376), [#391](https://github.com/bajutsu-e2e/bajutsu/pull/391), [#394](https://github.com/bajutsu-e2e/bajutsu/pull/394), [#397](https://github.com/bajutsu-e2e/bajutsu/pull/397) |
| トピック | バックエンド拡張（iOS actuator） |
<!-- /BE-METADATA -->

## はじめに

idb に次ぐ 2 つ目の actuator です。安定度順ラダーの上位として登録できるようにします（抽象は既に維持済み）。

## 動機

現状、iOS の actuator は idb のみで、idb は **frame 中心への座標 tap** で操作します。semantic tap を持たないので、run ループは `query()` で要素を一意化し、その中心を叩きます。ヘッドレス CI や一般的なケースにはこれで十分ですが、実際の穴が残ります。idb は `semanticTap` も、ネイティブの `conditionWait` も、`multiTouch` も提供しません（`docs/drivers.md`）。pinch や rotate のような 2 本指ジェスチャは `UnsupportedAction` を上げ、それらの操作は codegen → XCUITest を要すると注記されています。つまり、idb では今日まったく実行できないジェスチャがあり、しかもすべての tap が、識別子で要素を叩くより本質的に脆い座標往復を経由しています。

アーキテクチャはこれを既に想定しています。DESIGN §3 は idb の隣に「（将来）XCUITest backend（決定的コード生成）」を描き、DESIGN §5 はまさに 2 つ目の iOS actuator を差し込めるようドライバ抽象を backend 非依存に保ち、`bajutsu/backends.py` はその意図した順序をコメントで既に宣言しています: `"ios": ("idb",),  # later: ("xcuitest", "idb")`。本提案の狙いは、このプレースホルダを現実のものにすることです。安定度ラダーで idb の**上位**に座る本物の 2 つ目の actuator として XCUITest を追加し、idb にできない semantic な操作と多指ジェスチャを供給する一方、XCUITest が動かないヘッドレス環境では idb をフォールバックとして残します。

## 詳細設計

XCUITest は既存の `Driver` Protocol を満たす登録済み actuator になるので、シナリオ DSL、セレクタ解決、run ループ、証跡サブシステム、レポータのいずれも変わりません。

- **レジストリでの配置。** `bajutsu/backends.py` で iOS プラットフォームを `("xcuitest", "idb")`（XCUITest が先、idb が後）に展開し、`xcuitest` を実行可否チェックとともに `IMPLEMENTED` に加えます。actuator は「順に並べた中で最初に実装済みかつ利用可能な backend」なので、`--backend ios` は、どのシナリオも config も変えずに、XCUITest が動くなら自動的にそれを優先し、動かなければ idb にフォールバックします。これはまさに、このレジストリが備える前方互換の挙動です。
- **ランナーの駆動。** サブプロセスの CLI である idb と違い、XCUITest は Simulator 上に常駐するランナーの内側から操作するので、Python とそのランナーのあいだに通信路が必要です。ランナーは run のあいだ常駐し、小さなループバック HTTP エンドポイントを提供します。Python 側のドライバはそこへ `query` / `tap` / ジェスチャの要求を送ります。これは bajutsu が既に持つループバックの仕組みを流用します。`network.py` は `ThreadingHTTPServer` を `127.0.0.1` にバインドし、起動 env がそのアドレスをテスト対象アプリ（`BajutsuKit` の `BajutsuNet`）へ注入します。同じループバックを Python からランナーへの向きで使い、操作の要求を運びます。ランナー側の最小サーバは、大きな外部依存を取り込むのではなく `BajutsuKit` 内に実装し、通信路をプロジェクトの管理下に保ちます（「検討した代替案」を参照）。ランナーをどう**配布しビルドするか**は、下の「runner の配信とビルド」で決着させます（設定で指定する事前ビルド済みの `.xctestrun` を主とし、オンデマンドの `xcodebuild` をフォールバックに）。チャネルのプロトコルは「Python ↔ runner のチャネル」で定めます。
- **より豊かな capability、同じ契約。** XCUITest ドライバの `capabilities()` は、idb が提供するものに加えて `semanticTap`、ネイティブの `conditionWait`、`multiTouch` を返します。選択が決定性の核であることは変わらないので、`tap` は依然としてちょうど 1 要素に解決します。XCUITest はそれを frame 中心の座標ではなく識別子で操作するだけで、座標往復が消えます。idb では `UnsupportedAction` を上げる `pinch` / `rotate` が、直接実行できるようになります。
- **決定性を保つ。** XCUITest がネイティブの条件待機を提供する場面でも、オーケストレータの待機は固定 sleep のない条件待機のままで、ambiguous なセレクタは依然として即時に失敗します。新しい capability は表現できることを広げるだけで、規則を緩めません。XCUITest が使われるのは `run` 時の決定的 actuator としてのみで、LLM は Tier-2 ゲートに入りません。（これは、完成したシナリオを XCUITest テストソースへ構造的にマップする `codegen` とは別物で、その経路は影響を受けません。）
- **app-agnostic、必要な所は per-app。** ドライバ自体はアプリ非依存です。XCUITest で駆動されるためにアプリが用意すべきもの（例: テストホストや起動引数）は、既存の per-app 設定と並べて `targets.<name>` の下に置くので、ツールと runner はアプリをまたいで不変です。`doctor --target` は、idb の可否を報告するのと同じ仕方で XCUITest の可否を報告します。
- **フォールバックは健在。** idb をラダーの 2 番目に残すことで、XCUITest が動かない環境（必要なホストのないヘッドレス CI）は、座標ベースの idb へなだらかに劣化します。run はどの actuator が選ばれたかを記録するので、manifest は豊かな経路とフォールバック経路のどちらを通ったかを示します。これは既存の劣化開示の規則と整合します。

各部分の実装レベルの形を以下に示します。

### registry への配置と、実装前のなだらかな扱い

`bajutsu/backends.py` は *既知* の actuator と *実装済み* の actuator をすでに分けており、`select_actuator` は「計画済みだが未実装」のトークンを次に利用可能なものへ落とします。したがって順序の入れ替えはドライバが存在する前でも安全で、変更は一行です。`PLATFORMS["ios"] = ("xcuitest", "idb")` とすれば、`KNOWN_ACTUATORS` は import 時に `PLATFORMS` から派生するので `xcuitest` は自動的に「既知」になります。別途編集する一覧はありません。`IMPLEMENTED` には入れないので `--backend ios` は引き続き idb に解決され（xcuitest は「計画済み」）、ドライバが入った日に `IMPLEMENTED` への追加と可否判定（`_EXECUTABLE` 内の `xcodebuild` 探索）を足せば有効になります。明記すべき小さな関連変更が一つあります。`capabilities_for()` は現状、計画済みの actuator に対して `NotImplementedError` を投げます。したがって BE-0082 のプリフライトがドライバ完成**前**に xcuitest を判断するには、`XcuitestDriver.CAPABILITIES`（端末なしで読めるクラス定数）を返す分岐を足す必要があります。その分岐が無ければ、capability 集合はドライバと一緒に入ります。

### runner の配信とビルド（未決事項の決着）

実現可能な道は二つあります。**設定で指定するプレビルドのテスト runner を主とし、オンデマンドビルドをフォールバック**とすることを推奨します。既存の `appPath`（プレビルド）＋ `build`（オンデマンド）の切り分けに倣い、XCUITest が新しいオーサリングモデルを持ち込まないようにします。

- **プレビルド（主）。** `targets.<name>.xcuitest.testRunner` が、ビルド済みの `*.xctestrun`（またはテストホストの `.app`）を、`appPath` が `.app` を指すのと同じ仕方で指します。run はそれを直接起動します。速く、Simulator 以外に完全な Xcode ツールチェーンが無いマシンでも唯一動く道です。
- **オンデマンド（フォールバック）。** `targets.<name>.xcuitest.build` はシェルコマンド（例 `xcodebuild build-for-testing -scheme … -destination 'platform=iOS Simulator,…' -derivedDataPath …`）で、`.xctestrun` が無いときに `serve` ／ `run` が実行して生成します。`build` が無い `appPath` を生成するのと同じ仕組みです。どちらのノブも `targets.<name>` の下に置くのでツールはアプリ非依存のままです。DESIGN §1（bajutsu はビルド済み成果物を受け取り、自分ではビルドしない）は、プレビルドを優先し `build` を明示的・任意の便宜として扱うことで尊重します。

XCUITest の **runner コード本体**は、アプリごとではなく `BajutsuKit` に同梱する小さく汎用的な XCTest ターゲットです（`BajutsuNet` と並べます）。起動時に渡された bundle id に対して `XCUIApplication` を駆動するので、一つの runner ですべての target をまかないます。

### Python ↔ runner のチャネル

idb はサブプロセス CLI ですが、XCUITest は Simulator に常駐するテストプロセスから操作するため、run のあいだ両者をつなぐチャネルが要ります。既存のループバック HTTP パターン（`bajutsu/network.py` が `127.0.0.1` に `ThreadingHTTPServer` をバインドし、アプリは `BAJUTSU_COLLECTOR` 経由で話す）を、今度は **Python → runner** 方向に再利用します。`BajutsuKit` の runner がテストメソッド内で小さな `127.0.0.1:<port>` サーバを起こして常駐し、Python の `XcuitestDriver` が操作要求を送ります。ポートは同じく起動引数で runner に渡し、ループバックに閉じます（ホストの露出を広げません）。契約は `Driver` Protocol に一対一で対応するので、ドライバより上は何も変わりません。

| Driver 呼び出し | 要求 | 応答 |
|---|---|---|
| `query()` | `GET /elements` | 正規化済み `Element[]` の JSON。idb が返すのと同じ `identifier`／`label`／`value`／`traits`／`frame` の形（なので `find_all` ／ `resolve_unique` は不変）に**加え、各要素に runner が発行する不透明なスナップショット単位の `handle`** が載り、Python はそれを `tap` ／ `gesture` に往復させます（「チャネルをまたぐ要素の指定」を参照）。`handle` は selector のフィールドではないので、照合には影響しません |
| `tap(sel)` | `POST /tap {handle}` | ok ／ not-found。Python が先に `query()` から一意の要素を解決し（選択は決定性の核のまま）、その要素の**スナップショットハンドル**を送ります。XCUITest は座標ではなくその要素そのものをタップします（「要素のアドレッシング」を参照） |
| `pinch` ／ `rotate` | `POST /gesture {handle, kind, scale\|radians}` | ok。idb が `UnsupportedAction` を投げる二本指ジェスチャ |
| `wait_for(sel)` | Python が `GET /elements` をポーリング（オーケストレータの条件待ち）、または同じ上限付き・sleep なしの契約の下で runner のネイティブ expectation | ok ／ timeout |
| `screenshot(path)` | `GET /screenshot` | PNG バイト列 |

エラーは既存の `Driver` 例外に対応します（解決は Python 側に残るので `ElementNotFound` ／ `AmbiguousSelector` も Python 側）。最小のサーバを大きな外部自動化依存ではなく `BajutsuKit` に置くことで、チャネルをプロジェクトの管理下に保ちます（「検討した代替案」を参照）。

### チャネルを越えた要素のアドレッシング

解決は Python 側に残るので、チャネルは Python が解決した要素「そのもの」を操作できなければなりません。runner 側で再解決して別の一致を選ぶことは許されません。そこで runner は `GET /elements` で返す各 `Element` に**スナップショット単位の安定したハンドル**を発行し、そのスナップショットが生きているあいだ対応関係を保持します。`POST /tap` ／ `POST /gesture` は、再問い合わせの述語ではなくこのハンドルを運びます。Python はスナップショットから一意の要素を解決し、そのハンドルを送ります。runner はハンドルを、すでに特定済みの `XCUIElement` に対応づけて操作します。アクセシビリティの `identifier` だけではハンドルとして**不十分**です。一意とは限らないので、runner 側でそれを使って再解決すると、選択がいま取り除いたばかりの曖昧さを再導入してしまいます。画面が変わってハンドルが無効になっている場合、runner は `stale` を返し、ドライバは要素が消えたときと同じエラーを上げます。黙って今の一致を叩き直すのではなく、声を上げて失敗します。

### runner のライフサイクル：準備完了、後始末、クラッシュ

常駐 runner は起動と失敗のモードを持つプロセスなので、そのライフサイクルを明示します。

- **準備完了。** ドライバは、runner のループバックサーバが接続を受け付けるまで操作要求を送りません。上限付きで sleep のない準備完了プローブ（`GET /health`）を待ちます。これは他所と同じ条件待ちの作法です（固定 sleep なし）。タイムアウトはハングせず、「runner が起動しなかった」と明確に失敗します。
- **後始末。** リースを解放すると runner（とそのループバックサーバ）を止め、アプリを終了します。デバイスプールがリース解放時にデバイスごとの collector を止めるのと同じ形で、待ち受けたままのものを残しません。
- **クラッシュ。** run の途中で runner が終了するか応答しなくなったら、次の要求が失敗し、ドライバは `Driver` エラーを上げます。run は声を上げて失敗します。クラッシュした runner を黙って「要素が見つからない」と読むことはありません。インフラの失敗はテストの結果とは別物のままです。

### 並列 run とデバイスプール（BE-0020）

デバイスプール（`runner/pool.py`）は並列シナリオのために N 台の Simulator をリースし、各台にデバイスごとのリソースを与えます。XCUITest はこのモデルにそのまま収まります。リースされた各デバイスは**自分専用のループバックポート上に自分専用の常駐 runner**を持ちます。プールがデバイスごとに network collector を自分のポートで起こすのと同じです。ポートはリースごとに割り当て、そのデバイスの runner へ起動引数で渡すので、2 つの並列 run が runner やポートを共有することはなく、リース解放はそのデバイスの runner を collector とともに止めます。グローバルな runner も共有ポートも無いので、並列 XCUITest run は並列 idb run と同じだけ隔離されます。

### XCUITest 下のネットワーク証跡（BE-0020 との相互作用）

XCUITest の capability 集合は `NETWORK` を**含みません**。より豊かな *actuator* であって、ネットワークをネイティブに観測するものではありません。iOS のネットワーク証跡は、アプリ側の collector（`BajutsuNet` が `BAJUTSU_COLLECTOR` で指されたループバック受信側へ POST する）が供給します。これは **actuator 非依存**で、アプリの起動環境に乗るものであり、アプリをどう駆動するかには依りません。したがって XCUITest 下では、runner が `BAJUTSU_COLLECTOR` を `XCUIApplication.launchEnvironment` へ、idb が自分の起動 env に注入するのとまったく同じく注入する必要があり、そうすればネットワーク捕捉は同一に働きます。アプリ側 collector が引き続きネットワーク証跡を供給するので、XCUITest を選んでも BE-0020 の read-only な**兄弟**フォールバックは発動**しません**。そのフォールバックは、同一プラットフォームのどの適格 backend も供給できない証跡種別にだけ働くものであり、ネットワークはそれに当たりません。これは BE-0020 の境界を具体化します。証跡の穴は同一プラットフォームの read-only プロバイダが埋め、*操作*は選ばれた単一 backend にとどまります。

### capability・doctor・開示

`XcuitestDriver.capabilities()`（および `capabilities_for("xcuitest")`）は `QUERY`・`ELEMENTS`・`SCREENSHOT` に加えて **`SEMANTIC_TAP`**・**`CONDITION_WAIT`**・**`MULTI_TOUCH`** を返します。idb を超えるこの三つが、識別子タップと pinch ／ rotate を解禁します。`doctor --target` は XCUITest の可否（ツールチェーンと、設定済み／ビルド可能な runner）を idb と並べて報告し、run の manifest は選ばれた actuator を記録するので、idb へのフォールバックは黙ってではなく開示されます。

### 検証

Simulator 無しの高速ゲートで証明できる部分と、端末を要する部分に分けます。

- **高速ゲート（端末なし）。** registry：`--backend ios` が利用可能なら `xcuitest` を優先し、不可なら `idb` へフォールバックすること（`select_actuator` に可否判定関数を注入して駆動）。`capabilities_for("xcuitest")` が豊かな集合を返すこと。ドライバ：操作要求の組み立てと応答の解釈を、**注入した fake な HTTP トランスポート**に対して検証します（idb のテストが fake な `run` を注入するのと同じ要領）。`tap` が一意の要素を解決してから、その要素が持つスナップショットハンドルで指すこと、`pinch` ／ `rotate` がジェスチャ要求を出すこと、`stale` 応答が要素消失のエラーを上げること、曖昧な selector はどの要求よりも前に失敗することを確認します。ライフサイクルもここで検証できます。準備完了プローブが fake トランスポートに対して待ってからきれいにタイムアウトすること、2 リースのプールが各ドライバに別々のポートを渡すことです。runner も LLM も `run` ／ CI ゲートには載せません。
- **実機（e2e 経路）。** 起動した Simulator に対する実際の `BajutsuKit` runner を、より重い `e2e.yml` 経路で。Python 側で要素を解決してそのスナップショットハンドルで操作し、idb にはできない pinch ／ rotate を行うシナリオに加え、XCUITest が使えないホストでのフォールバック run で idb へのなだらかな劣化を確認します。

## 検討した代替案

- **idb を XCUITest で丸ごと置き換える。** XCUITest はより豊かな actuator ですが、idb のヘッドレスかつ座標ベースの動作は、まさに XCUITest の完全なホストが扱いづらい CI 環境で価値があります。両方を順序付きラダーに保つことで双方の良さが得られます。XCUITest を優先し、動かなければ idb にフォールバックし、シナリオは不変のままです。
- **欠けているジェスチャを idb に後付けする。** idb の単一タッチのプリミティブから pinch/rotate を合成する手もあります。idb は本質的に単一タッチを露出するので、これは多指ジェスチャの不確実な近似にしかなりません。プロジェクトが避ける、まさに脆く非決定的な挙動です。本物の多指 backend が誠実な解です。
- **ランナーの通信路として WebDriverAgent を採用する。** WebDriverAgent は実績のある HTTP+XCTest サーバで、Python とランナーの通信路を既製で供給します。最初の一歩としては不採用です。取り込んで保守するには大きすぎる依存で、backend を絞り込んだツールへの subprocess 呼び出しで駆動するという薄い依存方針（DESIGN §4）からプロジェクトを引き離します。`network.py` / `BajutsuNet` の既存のループバックの仕組みを流用して `BajutsuKit` 内に最小のランナー側サーバを置けば、面積を小さくプロジェクトの管理下に保てます。最小サーバで不十分だと分かった場合のフォールバックとして WebDriverAgent を残します。
- **特定のジェスチャだけ XCUITest へ流し、idb を actuator のままにする。** 2 つのドライバが 1 つのデバイスを操作することは、単一 actuator の規則（DESIGN §3.3 / §5）が防ぐためにある非決定性を再導入します。actuator は run ごとに一度固定されます。*証跡*の能力差は read-only フォールバックの設計（BE-0020）で別途扱いますが、*操作*は 1 つの backend にとどまります。

## 参考

[DESIGN §5 / §3](../../../DESIGN.md)、`bajutsu/backends.py`
