[English](BE-0264-serve-uncaught-exception-json.md) · **日本語**

# BE-0264 — serve ハンドラの未捕捉例外を JSON エラーで返す

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0264](BE-0264-serve-uncaught-exception-json-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0264") |
| 実装 PR | [#1089](https://github.com/bajutsu-e2e/bajutsu/pull/1089) |
| トピック | Codebase quality & technical debt |
<!-- /BE-METADATA -->

## はじめに

serve の HTTP ハンドラ（`bajutsu/serve/handler.py`）は、各リクエストを `match path:` 文で振り分けます。この文は operation を呼び出し、operation の返す `(payload, status)` を JSON として書き出します。この振り分けを、トップレベルの `try/except` は囲んでいません。

operation が、エラー tuple を返すのではなく例外を*送出*すると、その例外は Python の `socketserver` まで伝播します。`socketserver` は、サーバコンソールにトレースバックを記録したうえで、**本文なしで接続を閉じます**。ブラウザは、`await response.json()` で `Unexpected end of JSON input` という不透明な失敗を踏みます。利用者には、原因の手がかりのない、意味不明なメッセージだけが表示されます。

本項目は、トップレベルの例外境界を 1 つ追加します。これにより、未捕捉の operation エラーは、読みやすいメッセージ付きの正しい JSON 500 になります。operation が自前で扱い済みのエラーを返す既存のやり方と揃います。

## 動機

serve の operation は、いずれも「想定内の失敗は `({"error": …}, code)` を返す」という約束に従います。しかし、実機を伴う operation は、返すのではなく*送出*する経路に達します。たとえば、`backend: [ios]` の target で serve の Capture を実行すると、`ValueError: xcuitest backend requires a runner_port` が送出されます。この具体的な原因は、姉妹提案のアクチュエータ選択が修正します。

原因が何であれ、現状の挙動は最悪です。クライアントは空の応答を受け取ります。ステータス行は使えません。失敗の記録は、運用者の端末のトレースバックだけです。

プロジェクトは、このサイレント失敗の型を、他所では欠陥として扱っています。BE-0150 は、不正なシナリオで CLI がきれいに失敗するようにしました。Web UI にも、同じ保証が要ります。失敗したリクエストは、人が読めて、クライアントが分岐できる応答を、必ず生むべきです。

境界は、既存の振り分けを 1 つの wrapper で囲むだけです。そのため、正常に返るすべてのリクエストでは、挙動を保ちます。変わるのは、現状クラッシュする経路の結末だけです。

プライムディレクティブのいずれにも、影響しません。この変更は、転送層のエラー衛生であり、`run` の判定、決定性、アプリ固有の config には関わりません。

## 詳細設計

1. **POST の振り分けを囲みます。** `do_POST` の `match path:` 本体を `try/except` で囲みます。未処理の例外が出たら、ハンドラは、既存の `_json` ヘルパー経由で `({"error": "<message>"}, 500)` を返します。既存の `json.JSONDecodeError` → 400 と、非 dict body → 400 のガードは、より具体的なケースとして、そのまま残します。
2. **GET の振り分けを囲みます。** 同じ境界を `do_GET` にも適用します。読み取り経路（シナリオ読み取りや capture のスクリーンショットなど）が例外を出したときも、空本文ではなく JSON 500 を返します。ストリーミングとバイナリの経路（SSE、ファイルと zip の配信）は除きます。これらの経路は、応答のライフサイクルを自前で持つため、二重書き込みを避けなければなりません。
3. **サーバ側のトレースバックを保ちます。** 例外は、引き続き記録します。`oplog` で束ねた request id も付けます。運用者には診断情報が残り、クライアントには無害化したメッセージを返します。例外テキストをどこまでクライアントに返すかは、意図的に決めます。利用者が対処できるだけの情報を返しつつ、必要な箇所では内部パスを漏らさないようにします。
4. **テストを書きます。** serve ハンドラのテストで、次の 2 つの挙動を確認します。
   - operation が例外を送出したとき、空応答ではなく、JSON の `error` 本文を伴う 500 になります。POST と GET の両経路で確認します。
   - ストリーミング経路は手つかずのままです。

## 検討した代替案

- **例外を出す呼び出し箇所を個別に直す。** 送出そのものがバグである箇所では必要で、アクチュエータ選択の提案がまさにこれを行います。ただし、*次に*例外を出す経路が処理される保証にはなりません。1 つの境界は、「想定外の送出でも読みやすい 500 になる」ことを、箇所ごとの約束ではなく構造的な性質にする、最後の砦です。
- **フレームワーク既定の処理に任せる。** `socketserver` の既定（記録して破棄）は、バッチ処理のクラッシュなら許容できます。しかし、ここは、唯一のクライアントが全応答を JSON として解釈するブラウザである、対話 API です。空本文は、UI にとって行き止まりです。
- **HTML のエラーページを返す。** serve API は、端から端まで JSON です。HTML の 500 は、クライアントの一様な `response.json()` 処理を壊します。

## 進捗

> 作業の進行に合わせて最新に保ってください。チェックリストは *詳細設計* の MECE な作業分解を反映し
> （作業単位ごとに 1 ボックス）、ログは変更内容と時期を（古い順に）記録し PR をリンクします。

- [x] Unit 1 — POST 振り分けを囲むトップレベル `try/except` → JSON 500。
- [x] Unit 2 — GET にも同じ境界を（ストリーミング/バイナリ経路を除く）。
- [x] Unit 3 — サーバ側トレースバックを保持し、クライアント向けメッセージを意図的に決める。
- [x] Unit 4 — POST・GET の送出経路のハンドラテスト、ストリーミングは不変。

ログ:

- [#1089](https://github.com/bajutsu-e2e/bajutsu/pull/1089) で、`bajutsu/serve/handler.py` を変更しました。
  - `do_POST` と `do_GET` は、`_dispatch_post` と `_dispatch_get` 経由で振り分けるようにし、トップレベルの `try/except` で囲みました。未捕捉の operation 例外は、`_respond_uncaught` が JSON 500 に変換します。トレースバックは `oplog` の request id 付きで記録し、クライアントには例外メッセージのみを返します。
  - ストリーミングとバイナリの GET 経路（SSE、run ファイル、zip、スクリーンショット）は、`_serve_streaming_get` に切り出しました。境界の外で振り分けるため、すでに送出済みの応答を、フォールバックの `_json` が二重書き込みすることはありません。
  - `_respond_uncaught` 自身の書き込みも保護し、クライアント切断が、本文なしドロップとして再伝播しないようにしました。
  - テストは `tests/serve/test_http_uncaught.py` にあります。
- [#1089](https://github.com/bajutsu-e2e/bajutsu/pull/1089)（レビューのフォローアップ）で、生ボディのアップロードとアーティファクトの経路（`_handle_upload` と `_handle_artifact_upload`）にも、境界を広げました。これらの経路は、`do_POST` の `try/except` より前で振り分けられます。`ops.bind_upload_config` や `ops.bind_artifact` からの送出も、`_respond_uncaught` を通すようにしました。これらの経路も、本項目が解消しようとする本文なしドロップに陥りません。

## 参考

- [BE-0150 — Fail cleanly on a malformed scenario in `trace --explain` and `audit`](../BE-0150-scenario-load-yaml-error-handling/BE-0150-scenario-load-yaml-error-handling-ja.md)（CLI 向けの、同じきれいな失敗の規範）
- [BE-0051 — Serve hardening for hosting](../BE-0051-serve-hardening-for-hosting/BE-0051-serve-hardening-for-hosting-ja.md)（この境界が内側に座るリクエストゲート）
- `bajutsu/serve/handler.py`（`do_POST` / `do_GET` の振り分け）
