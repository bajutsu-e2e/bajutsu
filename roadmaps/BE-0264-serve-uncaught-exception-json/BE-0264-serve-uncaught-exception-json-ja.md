[English](BE-0264-serve-uncaught-exception-json.md) · **日本語**

# BE-0264 — serve ハンドラの未捕捉例外を JSON エラーで返す

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0264](BE-0264-serve-uncaught-exception-json-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0264") |
| 実装 PR | _pending_ |
| トピック | Codebase quality & technical debt |
<!-- /BE-METADATA -->

## はじめに

serve の HTTP ハンドラ（`bajutsu/serve/handler.py`）は、各リクエストを `match path:` 文で振り分け、operation の
返す `(payload, status)` を JSON として書き出します。この振り分けはトップレベルの `try/except` で囲まれていません。
operation がエラー tuple を返すのではなく例外を*送出*すると、その例外は Python の `socketserver` まで伝播し、
socketserver はサーバコンソールにトレースバックを記録したうえで**本文なしで接続を閉じます**。ブラウザは
`await response.json()` で `Unexpected end of JSON input` という不透明な失敗を踏み、利用者には原因の手がかりのない
意味不明なメッセージだけが表示されます。

本項目は 1 つのトップレベル例外境界を追加し、未捕捉の operation エラーを読みやすいメッセージ付きの正しい JSON 500 に
変えます。これは operation が自前で扱い済みのエラーを返す既存のやり方と揃います。

## 動機

serve の operation はいずれも「想定内の失敗は `({"error": …}, code)` を返す」という約束に従います。しかし実機を伴う
operation は、返すのではなく*送出*する経路に達します。たとえば `backend: [ios]` の target で serve の Capture を実行すると
`ValueError: xcuitest backend requires a runner_port` が送出されます（この具体的な原因は姉妹提案のアクチュエータ選択で
修正します）。原因が何であれ、現状の挙動は最悪です。クライアントは空の応答を受け取り、ステータス行は使えず、失敗の記録は
運用者の端末のトレースバックだけです。

これはプロジェクトが他所では欠陥として扱うサイレント失敗の型です（BE-0150 は不正なシナリオで CLI がきれいに失敗する
ようにしました）。Web UI にも同じ保証が要ります。失敗したリクエストは、人が読めてクライアントが分岐できる応答を必ず
生むべきです。境界は既存の振り分けを 1 つの wrapper で囲むだけなので、正常に返る全リクエストでは挙動を保ち、現状クラッシュ
する経路の結末だけを変えます。

prime directive のいずれにも影響しません。これは転送層のエラー衛生であり、`run` の判定・決定性・アプリ固有の config には
関わりません。

## 詳細設計

1. **POST の振り分けを囲む。** `do_POST` の `match path:` 本体を `try/except` で囲み、未処理の例外が出たら既存の `_json`
   ヘルパー経由で `({"error": "<message>"}, 500)` を返します。既存の `json.JSONDecodeError` → 400 と、非 dict body →
   400 のガードは、より具体的なケースとしてそのまま残します。
2. **GET の振り分けを囲む。** 同じ境界を `do_GET` にも適用し、読み取り経路（シナリオ読み取りや capture のスクリーンショット
   など）が例外を出しても空本文ではなく JSON 500 を返すようにします。ただし応答のライフサイクルを自前で持ち二重書き込みを
   避けねばならないストリーミング/バイナリ経路（SSE、ファイル/zip 配信）は除きます。
3. **サーバ側のトレースバックを保つ。** 例外は引き続き記録し（`oplog` で束ねた request id 付き）、運用者には診断情報を
   残しつつ、クライアントには無害化したメッセージを返します。例外テキストをどこまでクライアントに返すかは意図的に決めます。
   利用者が対処できるだけの情報は返しつつ、必要な箇所では内部パスを漏らさないようにします。
4. **テスト。** operation が例外を送出したとき、POST と GET の両経路で（空応答ではなく）JSON の `error` 本文を伴う 500 に
   なること、およびストリーミング経路が手つかずであることを serve ハンドラのテストで確認します。

## 検討した代替案

- **例外を出す呼び出し箇所を個別に直す。** 送出そのものがバグの箇所では必要です（アクチュエータ選択の提案がまさにこれを
  行います）が、*次に*例外を出す経路が処理される保証にはなりません。1 つの境界は「想定外の送出でも読みやすい 500 になる」を
  箇所ごとの約束ではなく構造的な性質にする最後の砦です。
- **フレームワーク既定の処理に任せる。** `socketserver` の既定（記録して破棄）は、バッチ処理のクラッシュなら許容できますが、
  ここは唯一のクライアントが全応答を JSON として解釈するブラウザである対話 API です。空本文は UI にとって行き止まりです。
- **HTML のエラーページを返す。** serve API は端から端まで JSON です。HTML の 500 はクライアントの一様な
  `response.json()` 処理を壊します。

## 進捗

> 作業の進行に合わせて最新に保ってください。チェックリストは *詳細設計* の MECE な作業分解を反映し
> （作業単位ごとに 1 ボックス）、ログは変更内容と時期を（古い順に）記録し PR をリンクします。

- [x] Unit 1 — POST 振り分けを囲むトップレベル `try/except` → JSON 500。
- [x] Unit 2 — GET にも同じ境界を（ストリーミング/バイナリ経路を除く）。
- [x] Unit 3 — サーバ側トレースバックを保持し、クライアント向けメッセージを意図的に決める。
- [x] Unit 4 — POST・GET の送出経路のハンドラテスト、ストリーミングは不変。

ログ:

- _pending_ — `bajutsu/serve/handler.py`: `do_POST` / `do_GET` は `_dispatch_post` /
  `_dispatch_get` 経由で振り分けるようにし、これをトップレベルの `try/except` で囲みました。未捕捉の
  operation 例外は `_respond_uncaught` が JSON 500 に変換します（トレースバックは `oplog` の request id
  付きで記録し、クライアントには例外メッセージのみを返します）。ストリーミング/バイナリの GET 経路（SSE、
  run ファイル／zip／スクリーンショット）は `_serve_streaming_get` に切り出して境界の外で振り分け、既に送出
  済みの応答をフォールバックの `_json` が二重書き込みしないようにしました。`_respond_uncaught` 自身の書き込み
  も保護し、クライアント切断が本文なしドロップとして再伝播しないようにしています。テストは
  `tests/serve/test_http_uncaught.py`。

## 参考

- [BE-0150 — Fail cleanly on a malformed scenario in `trace --explain` and `audit`](../BE-0150-scenario-load-yaml-error-handling/BE-0150-scenario-load-yaml-error-handling-ja.md)（CLI 向けの、同じきれいな失敗の規範）
- [BE-0051 — Serve hardening for hosting](../BE-0051-serve-hardening-for-hosting/BE-0051-serve-hardening-for-hosting-ja.md)（この境界が内側に座るリクエストゲート）
- `bajutsu/serve/handler.py`（`do_POST` / `do_GET` の振り分け）
