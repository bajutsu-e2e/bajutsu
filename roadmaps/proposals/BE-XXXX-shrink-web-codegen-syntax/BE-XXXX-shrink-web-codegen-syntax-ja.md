[English](BE-XXXX-shrink-web-codegen-syntax.md) · **日本語**

# BE-XXXX — web（Playwright）codegen の未対応構文の縮小

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-XXXX](BE-XXXX-shrink-web-codegen-syntax-ja.md) |
| 提案者 | [@hirosassa](https://github.com/hirosassa) |
| 状態 | **提案** |
| トピック | codegen 網羅性 |
<!-- /BE-METADATA -->

## はじめに

**Playwright（web）codegen エミッタ**が素の `// TODO` に落とす構文の範囲を減らします。XCUITest エミッタについて同じことを行う [BE-0026](../../in-progress/BE-0026-shrink-unsupported-syntax/BE-0026-shrink-unsupported-syntax-ja.md) の web 版です。

## 動機

`codegen` は、通過したシナリオを出力先フレームワークの流儀のネイティブテストに変換します。XCUITest（Swift）と、[BE-0062](../../implemented/BE-0062-playwright-codegen/BE-0062-playwright-codegen-ja.md) 以降は Playwright（TypeScript）で、いまは 1 つの共有シナリオウォークの背後にあります（[BE-0083](../../implemented/BE-0083-codegen-emitter-unification/BE-0083-codegen-emitter-unification-ja.md)）。エミッタが翻訳できない構文は失敗ではなく `// TODO` になるので、出力は常にコンパイルでき、レビュー可能です。ただし `// TODO` の 1 行ごとに人手の移植が発生します。

XCUITest エミッタのフォールバック処理は磨かれました（BE-0026）。複合セレクタは構造的に写され、忠実な形が無い構文はエンドポイントと理由を明記した**ラベル付き** `// TODO` を出します。Playwright エミッタ（`bajutsu/codegen_playwright.py`）は同じ面で遅れています。

- ネットワークの `request` アサーションと `until: { request }` 待機は、素の `// TODO`（`"network 'request' assertion (no bajutsu runtime in the emitted test)"`）を出し、**エンドポイントも正確な理由も示しません**。しかも *諦め* の文言です。Playwright には第一級のネットワーク傍受があるので、これらは web では本当はマッピング不能ではありません（後述）。
- `requestSequence` と `responseSchema` アサーションは汎用の `// TODO: unsupported assertion` に落ち、何も名指ししません。
- locator ビルダが描画できないセレクタ（`within`、描画できない glob、trait の組み合わせ）は、レビュアが対処できる理由の無い素の `// TODO: unsupported selector` を出します。

これを BE-0026 の単なる複製以上にしている要点はこうです。**XCUITest エミッタはネットワーク構文をラベル付けすることしかできませんでした（オンデバイスのランナーにネットワーク傍受の口が無いため）が、Playwright はそれらを忠実に生成できます。** つまり web エミッタは、iOS 側が文書化するしかなかった欠落を *塞ぐ* べきです。

## 詳細設計

統制ルールは BE-0026 と同じです。構文がフォールバック集合から抜けるのは、**忠実で決定的かつ AI 非依存**の構造マッピングが存在するときに限ります。そうでなければ `// TODO` のまま、ただし何をなぜ残すのかを明記したラベル付きにします。決定的な `run` / CI ゲートには変更を加えません。これは codegen の出力だけの話です。

- **ネットワーク `request` / `until: { request }` → 忠実にマッピング。** Playwright はネットワークをネイティブに観測します。リクエストマッチャ（`method` / `url` / `urlMatches` / `path` / `pathMatches` / `status`）を Playwright の `Request` / `Response` に対する述語にし、`await page.waitForResponse(r => …)`（リクエストのみを検査するなら `waitForRequest`）として出力します。`until: { request }` 待機は同じ `waitForResponse` をステップのタイムアウトで包んだものです。`status` は `response.status()` を、`bodyMatches` は `response.text()` / `request.postData()` を見ます。これはマッチャの構造的な翻訳であり推測を含まないので、ラベル付けではなく *生成* の対象です。
- **`requestSequence` → 順序付きの `waitForResponse`。** 要素ごとに 1 つの待機を順番に出します（sequence アサーションは順序が眼目で、ランタイムの検査と対応します）。
- **`responseSchema` → ラベル付き `// TODO`。** レスポンスボディを JSON Schema で検証するには、生成テストにスキーマライブラリ（生成ファイルが前提にすべきでない外部依存）が要ります。そこでこれは `// TODO` のままにしますが、素の「unsupported assertion」ではなく、BE-0026 のネットワーク TODO と同様にエンドポイントとスキーマファイルを明記したラベル付きにします。
- **未対応セレクタ → ラベル付き `// TODO`。** locator ビルダが None を返すとき（`within` の幾何的包含、描画できない glob、対応外の trait 組み合わせ）、素の「unsupported selector」ではなく、*どの* セレクタが *なぜ* Playwright locator を持たないのかを明記した `// TODO` を出します。BE-0026 が XCUITest 側に与えたのと同じ誠実な欠落の扱いです。

エンドポイント記述は `bajutsu.assertions.request_label`（ランナー・coverage・XCUITest エミッタがすでに共有しているマッチャ記述）を再利用するので、生成コメントはバックエンド間で同一表記になります。

作業は BE-0026 と同じく漸進的（1 構文 1 小 PR）で、各スライスは Linux ゲート上の golden codegen テストと共に出します（codegen は純粋で、生成テキストの検証にブラウザは不要です）。

## 検討した代替案

- **web エミッタを XCUITest と同様に扱い、ネットワーク構文を *ラベル付け* するだけにする。** 却下。Playwright のネイティブなネットワーク傍受—まさに iOS にはできない本物のアサーション生成を可能にする能力—を捨ててしまいます。ラベル付けは本当にマッピング不能なもの（`responseSchema`、描画できないセレクタ）向けのフォールバックであり、`request` / `until` 向けではありません。
- **未対応構文で `// TODO` を出す代わりに生成を失敗させる。** BE-0026 と同じ理由で却下。出力は常にコンパイルできるという codegen の約束を壊し、1 つの未マッピング構文がフローの残り全体の出力をブロックします。
- **すべての欠落をベストエフォートの推測で埋める**（例: `within` を最も近い描画可能な locator で近似）。却下。決定性に反し、誤った理由で成功するテストを生みます—BE-0026 が記録する prime directive の懸念そのものです。
- **BE-0026 に畳み込み、独立項目にしない。** BE-0026 の詳細設計は XCUITest 固有（NSPredicate / `simctl`）で、web のマッピングは本質的に異なります（Playwright が対応するネットワーク構文を *生成* します）。同じ `codegen 網羅性` トピックの兄弟項目にすれば、統制ルールを共有しつつ各エミッタの設計を読みやすく保てます。

## 参考

- [`bajutsu/codegen_playwright.py`](../../../bajutsu/codegen_playwright.py) — web エミッタと現在の `// TODO` 箇所。`bajutsu/assertions.py` の `request_label`（共有のマッチャ記述）。
- [BE-0026](../../in-progress/BE-0026-shrink-unsupported-syntax/BE-0026-shrink-unsupported-syntax-ja.md)（XCUITest 版と統制ルール）、[BE-0062](../../implemented/BE-0062-playwright-codegen/BE-0062-playwright-codegen-ja.md)（Playwright ターゲット）、[BE-0083](../../implemented/BE-0083-codegen-emitter-unification/BE-0083-codegen-emitter-unification-ja.md)（共有エミッタウォーク）。
- [docs/codegen.md](../../../docs/ja/codegen.md)。
