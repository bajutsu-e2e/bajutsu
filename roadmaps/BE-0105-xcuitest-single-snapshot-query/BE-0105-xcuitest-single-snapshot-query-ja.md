[English](BE-0105-xcuitest-single-snapshot-query.md) · **日本語**

# BE-0105 — XCUITest の要素取得を単一スナップショット化する

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0105](BE-0105-xcuitest-single-snapshot-query-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **提案** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0105") |
| トピック | バックエンド拡張（iOS actuator） |
| 関連 | [BE-0019](../BE-0019-xcuitest-backend/BE-0019-xcuitest-backend-ja.md) |
<!-- /BE-METADATA -->

## はじめに

XCUITest ランナーの要素取得を、要素ごと属性ごとの読み取りから 1 回のスナップショット走査へ置き換えます。
これにより `GET /elements` の XCUITest 往復を数百回から 1 回へ削減しつつ、BE-0019 が定めたアドレッシングの
保証（Python 側で一意に解決し、ランナーは正にその要素を操作する、曖昧は即失敗、stale は明示失敗）はそのまま
維持します。

## 動機

showcase アプリの 84 要素の画面では、`GET /elements` の 1 回に 10〜12 秒かかります。この取得が `run` の
readiness、条件待ちのポーリング、各ステップの要素解決のすべてを律速するため、実行全体がこの時間に縛られます。
すでに暫定処置として、Python ドライバのソケットタイムアウトを 30 秒へ引き上げ、リクエストが単にタイムアウト
しないようにしてあります。

原因は取得の作り方にあると見ています。ランナーの `queryElements()` は
`app.descendants(matching: .any).allElementsBoundByIndex` を走査し、各要素について `identifier`、`label`、
`value`、`frame`、`isEnabled`、`isSelected`、`elementType` を個別に読みます。1 回の読み取りが 1 往復なので、
往復回数は要素数と属性数の積に比例し、1 画面あたりおよそ 84 × 7 ≈ 600 往復に達します。加えて
`allElementsBoundByIndex` は各ノードの `XCUIElement` を実体化するので、それ自体もコストになります。

本項目は [BE-0019](../BE-0019-xcuitest-backend/BE-0019-xcuitest-backend-ja.md) の後続であり、
その蒸し返しではありません。BE-0019 はスナップショットハンドルによるアドレッシングを確立しました。
`SnapshotStore` が要素ごとにスナップショット単位のハンドルを発行し、Python が `/elements` の JSON から一意の
要素を解決し、ランナーはそのハンドルが指す正にその要素を操作します。この設計が最適化したのは**正しさ**であって、
取得の速さではありません。本項目はそこを絞り込み、いずれの保証も弱めずに取得を軽くします。

設計上の壁は実在します。速い読み取り手段である公開 API の `XCUIElement.snapshot()`（`app.snapshot()`）は、
属性つきの部分木を 1 往復で返します。ただし返るノードは `XCUIElementSnapshot` という**値**であって、タップ
できる `XCUIElement` ではありません。「1 回の軽い読み取り」と「Python が解決した正にその要素を操作する」ことを
両立させるのが、本提案の解く問題です。

## 詳細設計

変更はランナー側の `ElementProviding` 実装（UI テストターゲットが供給する実際の XCUITest 実装）と、Python の
小さなタイムアウト差し戻しに閉じます。公開プロトコル `ElementProviding`、`Router`、`SnapshotStore` のハンドル
方式、Python ドライバの解決からハンドルへの流れは、いずれも**変わりません**。`ElementSnapshot.backingElement`
はすでに不透明な `AnyObject` なので、そこが保持する中身を変えても上位には影響しません。

1. **まず計測する。** 設計を選ぶ前に、時間の所在を確定します。`allElementsBoundByIndex` の実体化か、属性の
   個別読みか、どこに何秒かかっているかを計ります。あわせて、計測環境（Simulator の機種、Xcode のバージョン、
   showcase の 84 要素の画面）を明記してベースラインを記録します。この計測は主に仮説の確定と受け入れ数値の
   基準づくりが目的です。疑わしいコストはどちらも同じ単一スナップショット方式で取り除かれるため、設計の
   方向はどちらが支配的かに左右されません。
2. **単一スナップショット取得。** `queryElements()` は `app.snapshot()` の走査を **1 回**行い、返った木から
   すべての属性を読みます。要素ごとの属性往復はありません。出力は従来と同じ正規化済みの `Element[]`
   （`identifier` / `label` / `value` / `traits` / `frame`）なので、`find_all` / `resolve_unique` と
   `/elements` の JSON 契約は変わりません。
3. **位置パスによる backing と属性の再照合。** 各ノードは、ルートからのインデックス位置パスを
   `ElementSnapshot.backingElement`（不透明なのでプロトコル変更なし）に記録します。`tap` / `gesture` の時点で、
   ランナーはその位置パスから `XCUIElement` を再導出します。これは 84 要素すべての再走査ではなく、単一要素の
   解決です。続いて、**再導出した要素の属性（`identifier` / `label` / `traits` / `frame`）が、スナップショット
   時に記録した値と一致するかを照合**します。食い違えば `stale` を返し、ドライバは要素が消えたときと同じ
   エラーを送出します。これにより stale 検出を、BE-0019 の世代ベース（古いスナップショットのハンドルは
   検知するが、同一世代内の UI 変化は検知しない）から属性一致へ格上げし、「今マッチする何かを黙って操作しない」
   という性質を保ちます。属性照合のない純粋なインデックスパスだけでは、兄弟順序が変わった後に別要素を叩く
   危険が残ります。照合こそが決定性の安全を保つ要です。
4. **ドライバのタイムアウト再調整。** 取得が目標時間内に収まったら、暫定で上げたソケットタイムアウト（現在
   30 秒）を `bajutsu/drivers/xcuitest.py` で有界な値へ差し戻します。応答しないランナーが 30 秒ぶら下がる
   のではなく、妥当な時間内に明示的に失敗するようにします。
5. **検証の切り分け。** fast gate（fake transport）は**アドレッシングの正しさ**を守ります。位置パスの再導出が
   属性の食い違いで `stale` を返すこと、曖昧なセレクタがリクエスト前に即失敗すること、を検証します。ただし
   fast gate は Swift 側の取得遅延を測れないため、受け入れの**数値**（典型画面の `/elements` が目標時間内）は
   fast gate のアサーションではなく、実機（`e2e.yml`）での計測になります。両者は意図して分けます。gate は
   新しいアドレッシングが回帰しないことを、実機経路は速さを、それぞれ証明します。

**受け入れ基準。** 典型画面の `GET /elements` が目標時間内（案として計測環境で 1〜2 秒）に収まり、かつ
BE-0019 の保証（Python 側での解決、ハンドルで正にその要素を操作、曖昧は即失敗、stale は明示失敗）を回帰
させないこと。

## 検討した代替案

- **private snapshot API（WebDriverAgent 方式）。** 最速の選択肢です。`app.snapshot()` の下層にある XCTest の
  非公開シンボル（歴史的には `_XCTElementSnapshot` / `snapshotWithError:` や、WebDriverAgent が使う
  アクセシビリティクライアント `XCAXClient_iOS`）は、木全体を 1 回のアクセシビリティ問い合わせで取得し、
  公開 API よりさらに桁で速いことがあります。primary としては却下します。非公開シンボルは Xcode の
  バージョン間で脆く、薄い依存方針（DESIGN §4）に反するためです。これは BE-0019 が WebDriverAgent の
  vendoring を見送ったのと同じ理由です。公開 `app.snapshot()` の走査が目標に届かないときだけ再検討する、
  却下済みの代替として残します。
- **走査の絞り込み（深さ・型・`hittable` のみのフィルタ）。** セレクタが一致し得る要素を落とすと決定性を
  損ないます（prime directive 2：曖昧なセレクタは失敗し、一致を取りこぼさない）。単一スナップショット走査で
  全木が十分安くなれば絞り込みは不要になるため、性能のレバーではなく「安全性を証明できない限り不採用」の
  代替として残します。
- **ハンドルの backing に `identifier` 単独を使う。** アクセシビリティ `identifier` は一意とは限らないため、
  ランナー側で再解決すると、選択が取り除いた曖昧さを呼び戻します。BE-0019 で既に却下済みです。位置パスと
  属性照合の組み合わせが、一意を保つ形です。
- **BE-0019 に畳む。** BE-0019 の詳細設計は正しさとケイパビリティを MECE に閉じており、close-out は実機検証
  です。取得性能は、独自の受け入れ数値と独自の設計代替を持つ、切り離せる計測可能な関心事です。相互 `関連`
  の後続項目として立てることで両項目の分解を素直に保て、ロードマップが性能の後続を別項目として管理して
  きた流儀にも合います。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [ ] `/elements` のベースライン計測と記録（時間の所在、計測環境の明記）。
- [ ] `app.snapshot()` による単一スナップショット `queryElements()`（正規化済み `Element[]` 出力は同一）。
- [ ] 位置パスによる backing と属性の再照合（食い違いで `stale`）。
- [ ] 暫定のドライバソケットタイムアウトを有界な値へ差し戻す。
- [ ] 検証：fast gate のアドレッシング正しさテストと、目標に対する実機での遅延計測。

## 参考

[BE-0019 — XCUITest backend](../BE-0019-xcuitest-backend/BE-0019-xcuitest-backend-ja.md)、
[DESIGN §4](../../../DESIGN.md)、`bajutsu/drivers/xcuitest.py`、
`BajutsuKit/Sources/BajutsuRunner/SnapshotStore.swift`、
`BajutsuKit/Sources/BajutsuRunner/ElementProviding.swift`
