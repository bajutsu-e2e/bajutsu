[English](../network.md) · **日本語**

# ネットワーク観測（`request` アサーション）

> アプリが送受信する HTTP(S) 通信を、step/expect のアサーションとして検証します。観測は
> **アプリ内**で行います。アプリが個々の通信を Bajutsu の起動するコレクタに報告し、`request`
> アサーションが蓄積された通信を照合します。
>
> 実装: `bajutsu/evidence/network.py`（モデルとコレクタ）、`bajutsu/assertions/network.py`（`request` の評価）、
> アプリ内 SDK（software development kit、ソフトウェア開発キット） — iOS は
> [`BajutsuKit`](../../BajutsuKit/README.md)、Android は [`BajutsuAndroid`](../../BajutsuAndroid/README.md)。

関連: [scenarios](scenarios.md) · [evidence](evidence.md)

---

## 通信を観測する仕組み

Simulator のアプリはホストプロセスとして動作し、Mac のループバックを共有します。この性質を使い、
次の流れで観測します。

1. `run` の開始時に、Bajutsu が `127.0.0.1:<port>` で**コレクタ**（`NetworkCollector`）を起動します。
   その URL を `BAJUTSU_COLLECTOR`、run ごとの共有トークンを `BAJUTSU_COLLECTOR_TOKEN` として、
   いずれも起動環境変数でアプリに注入します。
2. アプリ（**BajutsuKit** をリンクしたもの）は `URLProtocol` を組み込み、各リクエストとレスポンスを
   記録してコレクタへ POST します。記録は **TLS（Transport Layer Security、トランスポート層セキュリティ）
   の後段**で行うため（プロキシも CA / certificate authority も使いません）、idb の下でも動作し、
   プログラムから読み取れます。各 POST はトークンを `Authorization: Bearer` ヘッダとして添えます。
   コレクタは一致するトークンを持たないリクエストを 401 で拒否するので、同じマシン上の別プロセスが
   偽の通信を run の証跡に紛れ込ませることはできません。
3. コレクタは通信をメモリ上に保持します。step の `request` アサーションはその通信に対してリアルタイムに
   評価され、通信はシナリオの証跡として `<sid>/network.json`（マスキング済み）に書き出されます。

`--no-network` を渡すとコレクタを無効にします。SDK を持たないアプリは何も報告しません
（コレクタは空のままです）。この機能はアプリごとのオプトインです。

同じコレクタは iOS 上で、画面遷移イベントも別の `/transitions` エンドポイントで受け取ります
（BE-0310）。報告するのは `BajutsuKit` の `BajutsuScreen` observer です。観測した
`UIAccessibility.screenChangedNotification` をそれぞれ報告し、上記のネットワーク通信とは独立した
専用のストアに保持します。このシグナルは `request` アサーションの対象ではありません。参照するのは
起動直後の readiness ゲートと `settled` 待ちです。詳細は
[run-loop](run-loop.md#待機条件待機)を参照してください。

**Android** も同じ仕組みで、違いは 2 点です（BE-0283）。アプリは
[`BajutsuAndroid`](../../BajutsuAndroid/README.md) をリンクし、OkHttp クライアントに
`BajutsuNet.interceptor()` を足します。iOS の `URLProtocol` のような単一の OS レベルの HTTP フックが
Android にはないため、インターセプタはクライアントごとで、捕捉するのは **OkHttp** の通信です。もう一つは、
エミュレータの `127.0.0.1` がホストではなくエミュレータ自身のループバックである点です。そこで Bajutsu は
`adb reverse` でコレクタをデバイスへ橋渡しします（両方向で同じポートなので、注入した `BAJUTSU_COLLECTOR`
の URL はそのまま解決します）。コレクタ、トークン検査、アサーションパイプラインは同一です。

> これはアプリ内の経路です。RocketSim の GUI ネットワークインスペクタと、TLS を傍受するプロキシは、
> どちらも採用しませんでした。前者は CLI に公開されておらず（自動アサーションには使えません）、後者は
> CA のインストールを必要とし、証明書ピンニングがあると動作しなくなるためです。設計上の判断は設計ノートを
> 参照してください。

## `request` アサーション

`request` はアサーションの一種です（`exists` / `value` / `count` などと並びます）。照合フィールドは
AND で結合します。素の `request` は観測された**一つ**の通信に対応します。一つのブロック内に複数の
`request` アサーションがあると、それぞれが別々の通信に**一対一**で照合されます。`request` を 2 行書けば
別々のリクエストが 2 件必要です。例外は `count` で、これは明示的な集計です（値を指定すれば厳密な件数、
指定しなければ唯一の照合子に対して 1 件以上を要求します）。

```yaml
expect:
  - request: { method: POST, path: /login, status: 200, bodyMatches: "\"user\"" }  # login の POST 1 件
  - request: { method: GET, urlMatches: "q=hello&n=42" }                           # *別の* リクエスト
  - request: { pathMatches: "^/items", count: 2 }                                  # 集計: ちょうど 2 件
```

| フィールド | 意味 |
|---|---|
| `method` | HTTP メソッド（大文字小文字を区別しない） |
| `url` | 完全な URL の完全一致（エンドポイント） |
| `urlMatches` | URL に対する正規表現/部分一致（クエリ文字列はここで照合する） |
| `path` | URL パスの完全一致（クエリは無視する） |
| `pathMatches` | パスに対する正規表現 |
| `status` | レスポンスのステータスコード |
| `bodyMatches` | リクエストボディに対する正規表現/部分一致 |
| `count` | 一致する通信の正確な件数。集計であり一対一の規則の対象外（省略すると 1 件以上） |

## 決定的なモック

シナリオの `mocks` はネットワークを決定的にします。外向きのリクエストがルールに一致すると、BajutsuKit は
**ネットワークへ送る代わりに**あらかじめ用意したレスポンスを返します。これにより、テストはライブのサーバに
依存しません（オフラインでも動作します）。スタブは URL プロトコルの内側で返され（TLS の後段、プロキシも CA も
使いません）、なお観測の対象になります（`network.json` に `mocked` の印つきで現れ、`request` アサーションは
他の通信と同様にこれを照合します）。モックは当面 iOS 専用です。BajutsuAndroid は観測はしますが、
スタブ応答はまだ返しません（BE-0283 の追随の課題です）。

```yaml
mocks:
  - match: { method: GET, urlMatches: "example.com" }   # リクエスト側の照合子
    respond:
      status: 418                                        # 既定は 200
      headers: { Content-Type: text/plain }
      body: "stubbed by bajutsu"
      # delayMs: 200                                     # 任意。人工的な遅延
  - match: { method: POST, pathMatches: "/login$" }
    respond: { status: 201, body: "{\"token\":\"t\"}" }
```

最初に一致したルールが採用されます。`match` はリクエスト側の照合フィールド（`method` /
`url` / `urlMatches` / `path` / `pathMatches` / `bodyMatches`）をそのまま使います。モックは観測と同じ
経路に乗るため、`--network` が必要です。ルールは `BAJUTSU_MOCKS` 起動環境変数を介してアプリに注入します
（`BAJUTSU_COLLECTOR` と同様です）。

## タイミング

ネットワークは非同期なので、レスポンスが届く前に step が動くことがあります。この隙間は、レスポンスを
反映する UI への待機で埋めます（たとえば `wait: { until: settled }`、あるいはレスポンスによって現れる要素への
待機）。これを `request` アサーションの**前**に置きます。SDK は通信の完了時に POST するので、UI が更新された
時点では通信はコレクタに入っています。

## アプリ側の契約

**iOS** — [BajutsuKit](../../BajutsuKit/README.md) をリンクし、早い段階で
`BajutsuNet.startIfEnabled()` を呼びます。これは `BAJUTSU_COLLECTOR` が設定されていなければ何もせず、
`URLSession` の HTTP(S) のみを捕捉し、**テスト/デバッグ専用**です（ヘッダとボディを記録するので、
リリースには含めず `redact` を使ってください）。

**Android** — [BajutsuAndroid](../../BajutsuAndroid/README.md) をリンクし、起動時に
`BajutsuNet.configure(env)` を（起動環境変数のマップとともに）呼び、アプリの `OkHttpClient` に
`BajutsuNet.interceptor()` を足します。これも `BAJUTSU_COLLECTOR` が設定されていなければ何もせず、
**OkHttp** の HTTP(S) のみを捕捉し、同じく**テスト/デバッグ専用**です（iOS と同様にヘッダとボディを
記録するので、リリースには含めず `redact` を使ってください）。Android ではさらに、テスト/デバッグ
ビルドに `127.0.0.1` へのクリアテキスト例外を `network_security_config` で設定する必要があります
（コレクタの URL は平文 HTTP で、API 28 以降は既定で遮断します。iOS は loopback を App Transport
Security（ATS）で除外するため不要です）。これがないと報告 POST は失敗し、ログに記録されるだけで、
やり取りはコレクタに届きません。
