[English](../network.md) · **日本語**

# ネットワーク観測（`request` アサーション）

> アプリが送受信する HTTP(S) 通信を、step/expect のアサーションとして検証します。iOS と Android では、
> 観測は**アプリ内**で行います。アプリが個々の通信を Bajutsu の起動するコレクタに報告します。Web では
> Playwright がページの通信をネイティブに観測するため、アプリ側の報告は不要です。いずれの場合も、
> `request` アサーションが蓄積された通信を照合します。
>
> 実装:
>
> - `bajutsu/common/evidence/network.py`：モデルとコレクタ
> - `bajutsu/assertions/network.py`：`request` の評価
> - `bajutsu/common/drivers/web_network.py`：Playwright ネイティブのコレクタとモック（BE-0054）
> - アプリ内の software development kit（SDK、ソフトウェア開発キット）：iOS は
>   [`BajutsuKit`](../../BajutsuKit/README.md)、Android は
>   [`BajutsuAndroid`](../../BajutsuAndroid/README.md)
>
> Web（Playwright）バックエンドは SDK を必要としません。

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
   の後段**で行います。プロキシも認証局（CA、certificate authority）も使わないため、どの backend が
   アプリを駆動していても動作し、プログラムから読み取れます。各 POST はトークンを持っていることを
   証明します。コレクタは証明できないリクエストを 401 で拒否するので、同じマシン上の別プロセスが
   偽の通信を run の証跡に紛れ込ませることはできません。
   - Bajutsu は常に `BAJUTSU_COLLECTOR_AUTH=hmac` を知らせます。現行の BajutsuKit は、それを受けて
     各リクエストにトークンで署名します。
   - 古い BajutsuKit はトークンを `Authorization: Bearer` ヘッダとして送ります。ループバックの
     コレクタは、そのヘッダも受け付けます。
   - 実機のコレクタが受け付けるのは、署名付きのリクエストだけです
     （[iOS を実機とデバイスクラウドで動かす](ios-device-cloud.md#実機からホストへの経路の認証)を参照してください）。
3. コレクタは通信をメモリ上に保持します。step の `request` アサーションはその通信に対してリアルタイムに
   評価され、通信はシナリオの証跡として `<sid>/network.json`（マスキング済み）に書き出されます。

`--no-network` を渡すとコレクタを無効にします。SDK を持たないアプリは何も報告しないため、
コレクタは空のままです。この機能はアプリごとのオプトインです。

iOS では、同じコレクタが画面遷移イベントも別の `/transitions` エンドポイントで受け取ります
（BE-0310）。報告するのは `BajutsuKit` の `BajutsuScreen` です。`UIViewController.viewDidAppear(_:)`
を swizzle し、完了したビューコントローラの出現をそれぞれ報告します。コレクタは、その記録を上記の
ネットワーク通信とは独立した専用のストアに保持します。このシグナルは `request` アサーションの対象では
ありません。参照するのは、起動直後の readiness ゲートと `settled` 待ちです。詳細は
[run-loop](run-loop.md#待機条件待機)を参照してください。

### Android

Android も同じ仕組みで、違いは 2 点です（BE-0283）。

- アプリは [`BajutsuAndroid`](../../BajutsuAndroid/README.md) をリンクし、OkHttp クライアントに
  `BajutsuNet.interceptor()` を足します。iOS の `URLProtocol` のような単一の OS レベルの HTTP フックが
  Android にはないため、インターセプタはクライアントごとで、捕捉するのは **OkHttp** の通信です。
- エミュレータの `127.0.0.1` は、ホストではなくエミュレータ自身のループバックです。そこで Bajutsu は
  `adb reverse` でコレクタをデバイスへ橋渡しします。両側で同じポートを使うので、注入した
  `BAJUTSU_COLLECTOR` の URL はそのまま解決します。

同じポート番号を**両側**でバインドできなければならないため、Android のレーンはコレクタのポートを
OS に選ばせず、小さな予約帯から取ります。OS が選ぶポートは、ホストのエフェメラルポート範囲
（ephemeral range）に属します。エミュレータが自身のソケットへ割り当てる範囲も同じなので、ゲストが
すでに握っているポートに時折ぶつかり、橋渡しのバインドが失敗します。予約帯を使うのは Android だけです。
iOS シミュレータは Mac のループバックを共有し、橋渡しをしないので、OS が選んだポートをそのまま使います。
コレクタ、トークン検査、アサーションパイプラインは同一です。

### Web

Web（Playwright）では、このアプリ内の仕組みは一切不要です。Playwright はページが送るすべての
リクエストをすでにネイティブに観測しているためです（BE-0054）。`PlaywrightDriver.network_collector()` は、
ページの `requestfinished` イベントを同じ `NetworkExchange` モデルへ直接結び付けます。
`BAJUTSU_COLLECTOR` の起動環境変数もポートもトークンも注入する必要はありません。ドライバ自体が
コレクタです。得られる `NetworkExchange` の記録は、iOS や Android と同じ `request` アサーションで
照合できます。

> 採用したのは、アプリ内の経路です。RocketSim の GUI ネットワークインスペクタと、TLS を傍受する
> プロキシは、どちらも採用しませんでした。前者は CLI に公開されていないため、自動アサーションには
> 使えません。後者は CA のインストールを必要とし、証明書ピンニングがあると動作しなくなります。
> 設計上の判断は設計ノートを参照してください。

## `request` アサーション

`request` はアサーションの一種です（`exists` / `value` / `count` などと並びます）。照合フィールドは
AND で結合します。素の `request` は観測された**一つ**の通信に対応します。一つのブロック内に複数の
`request` アサーションがあると、それぞれが別々の通信に**一対一**で照合されるため、`request` を 2 行
書けば別々のリクエストが 2 件必要です。例外は `count` で、これは明示的な集計です。値を指定すれば
厳密な件数を、指定しなければ唯一の照合子に対して 1 件以上を要求します。

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
| `urlMatches` | URL 全体に対する正規表現/部分一致 |
| `path` | URL パスの完全一致（クエリは無視する） |
| `pathMatches` | パスに対する正規表現 |
| `query` | URL デコードしたクエリパラメータに対するキーと値の辞書。順序に依存しない（BE-0463） |
| `body` | JavaScript Object Notation（JSON）のリクエストボディに対するキーと値の辞書。入れ子の map とリストも含む（BE-0463） |
| `status` | レスポンスのステータスコード |
| `bodyMatches` | 生のリクエストボディに対する正規表現/部分一致 |
| `count` | 一致する通信の正確な件数。集計であり一対一の規則の対象外（省略すると 1 件以上） |

## 決定的なモック

シナリオの `mocks` はネットワークを決定的にします。外向きのリクエストがルールに一致すると、各プラットフォームの
コレクタは**ネットワークへ送る代わりに**あらかじめ用意したレスポンスを返します。これにより、テストは
ライブのサーバに依存せず、オフラインでも動作します。

- iOS では、BajutsuKit が URL プロトコルの内側でスタブを返します。TLS の後段で行い、プロキシも CA も
  使いません。
- Web では、`WebNetworkCollector` が Playwright の `page.route` を使ってインプロセスでスタブを返します。
- BajutsuAndroid は観測はしますが、スタブ応答はまだ返しません（BE-0283 の追随の課題です）。

iOS と Web では、スタブもなお観測の対象になります。`network.json` に `mocked` の印つきで現れ、
`request` アサーションは他の通信と同様にスタブを照合します。

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
`url` / `urlMatches` / `path` / `pathMatches` / `bodyMatches`）をそのまま使います。アプリ内のマッチャが
無視してしまうため、`query` / `body` の辞書はモックでは拒否します。

モックは観測と同じ経路に乗るため、`--network` が必要です。iOS では、ルールは `BAJUTSU_MOCKS` 起動環境変数を
介してアプリに注入します（`BAJUTSU_COLLECTOR` と同様です）。Web には起動するアプリが存在しないため、
`network_collector()` がシナリオの `mocks` を直接受け取り、インプロセスで解決します。

iOS では、`setMocks` ステップがアプリの実行中にスタブテーブルを置き換えます（BE-0365）。コレクタが新しい
テーブルをキューに積み、アプリがアプリ内制御チャネルを通してそのテーブルを受け取ります。`setMocks`
ステップは、アプリが適用を確認するまで待ちます。`setMocks` を運べるのは、ネットワーク収集が有効な
`xcuitest` バックエンドだけです。それ以外で `setMocks` を使うシナリオは、`run` が開始前に拒否します。ステップの詳細は
[scenarios](scenarios.md#シナリオの途中でモックを変えるsetmocks) にあります。

## タイミング

ネットワークは非同期なので、レスポンスが届く前に step が動くことがあります。この隙間は、レスポンスを
反映する UI への待機で埋めます（たとえば `wait: { until: settled }`、あるいはレスポンスによって現れる要素への
待機）。この待機を `request` アサーションの**前**に置きます。iOS と Android では SDK が通信の完了時に
POST し、Web では Playwright 自身の `requestfinished` イベントがそこで発火します。いずれの場合も、UI が
更新された時点で、通信はすでにコレクタに入っています。

## アプリ側の契約

**iOS：** [BajutsuKit](../../BajutsuKit/README.md) をリンクし、早い段階で
`BajutsuNet.startIfEnabled()` を呼びます。この呼び出しは `BAJUTSU_COLLECTOR` が設定されていなければ
何もせず、`URLSession` の HTTP(S) のみを捕捉し、**テスト/デバッグ専用**です。ヘッダとボディを記録する
ので、リリースには含めず `redact` を使ってください。

横取りしたリクエストは、メソッド・ヘッダ・ボディ・キャッシュポリシー・タイムアウトをそのまま保って
転送します。リダイレクトは横取り側では追わず、URL Loading System に差し戻して、アプリ自身のセッション
からあらためて発行させます。ただし
`URLProtocol` には、アプリ自身の `URLSessionConfiguration` やセッションデリゲートを取得する手段が
ありません。そのため、自前のセッションデリゲートで TLS ピンニングやクライアント証明書認証をしている
アプリは、横取り後、その検証に失敗することがあります。

**Android：** [BajutsuAndroid](../../BajutsuAndroid/README.md) をリンクし、起動時に
`BajutsuNet.configure(env)` を（起動環境変数のマップとともに）呼び、アプリの `OkHttpClient` に
`BajutsuNet.interceptor()` を足します。インターセプタも `BAJUTSU_COLLECTOR` が設定されていなければ
何もせず、**OkHttp** の HTTP(S) のみを捕捉し、同じく**テスト/デバッグ専用**です。iOS と同様にヘッダと
ボディを記録するので、リリースには含めず `redact` を使ってください。

Android ではさらに、テスト/デバッグビルドに `127.0.0.1` へのクリアテキスト例外を
`network_security_config` で設定する必要があります。コレクタの URL は平文 HTTP で、API 28 以降は既定で
遮断するためです。iOS は loopback を App Transport Security（ATS）で除外するため、例外は不要です。
例外がないと報告 POST は失敗し、ログに記録されるだけで、やり取りはコレクタに届きません。

**Web：** アプリ側の契約は一切ありません。Playwright がページの通信を観測し、モックもネイティブに
解決するため、テスト対象のアプリを変更する必要はありません。
