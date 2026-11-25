[English](BE-0059-launch-target-server.md) · **日本語**

# BE-0059 — run のためにターゲットサーバを起動する（`launchServer`）

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0059](BE-0059-launch-target-server-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0059") |
| 実装 PR | [#169](https://github.com/bajutsu-e2e/bajutsu/pull/169) |
| トピック | Dogfood フィクスチャ（Web UI） |
| 由来 | Dogfooding |
<!-- /BE-METADATA -->

## はじめに

web（Playwright）backend の run は、アプリの `baseUrl` にアクセスしますが、そのサーバを起動する仕組みはありません。サーバがすでに待ち受けている前提になっています。

本項目は、`apps.<name>.launchServer` を追加します。run の前後でターゲットサーバを管理する、宣言的な方法です。run の前にサーバを起動し、準備が整うまで待ち、終わったら停止します。run の前に `.app` を用意する iOS の `build` フックの web 版にあたり、長時間動き続けるプロセスを管理します。

## 動機

iOS のターゲットには、必要に応じて事前準備を行うフックがあります。`apps.<name>.build` はシェルコマンドです。バイナリがないときに、`serve` がシナリオの前に実行して `appPath` を生成します。web backend には同等のものがありません。そのため、web のテスト環境ごとに、「サーバを起動し、応答するまで待ち、run を実行し、停止する」という処理を手作業で書き直していました。

serve Web UI の Dogfood（[BE-0058](../BE-0058-dogfood-web-ui/BE-0058-dogfood-web-ui-ja.md)）が、分かりやすい例です。Web UI を Web UI 経由でテストするには、ターゲットとして 2 つ目の serve インスタンスを動かす必要があります。`Makefile` がそのインスタンスを起動し、ポーリングし、trap で後始末していました。`demos/web` も、静的ページに対して同じことをしています。

この段取りは、設定側に置くべきものです。そうすれば、`bajutsu run` が自己完結します。`bajutsu run` を経由する Web UI の Run ボタンも、自己完結します。

## 詳細設計

アプリに、任意のブロックを追加します。

```yaml
apps:
  webui:
    baseUrl: "http://127.0.0.1:8799/"
    backend: [web]
    launchServer:
      cmd: "uv run bajutsu serve --config demos/web/demo.config.yaml --root demos/serve-ui --port 8799"
      readyUrl: "http://127.0.0.1:8799/"   # 既定は baseUrl
      readyTimeout: 60                       # 秒。固定 sleep ではなく条件待ち
      # 任意: cwd, env
```

`run` は、シナリオの実行を次のライフサイクルで包みます（`bajutsu/runner/launch_server.py`）。

1. まず `readyUrl`（既定は `baseUrl`）を一度**プローブ**します。すでに応答する（HTTP が `< 400`）場合は、`Makefile`、CI、手動起動など、別の場所がサーバを起動しています。run はそのサーバを**再利用**し、停止もしません。バイナリがすでにあるときに `build` をスキップするのと、同じ考え方です。
2. 応答しない場合は、`cmd` を独立したプロセスグループで**起動**します。後始末が、サーバの子プロセスまで届くようにするためです。続けて、`readyUrl` が応答するか `readyTimeout` を過ぎるまで**ポーリング**します。コマンドが途中で終了した場合とタイムアウトした場合は、分かりやすいメッセージを出し、終了コード `2` で run を失敗させます。
3. シナリオを実行します。
4. run の `finally` で**後始末**します。自分で起動した場合は、プロセスグループに `SIGTERM` を送り、猶予の後に `SIGKILL` を送ります。再利用したサーバは、そのまま動かし続けます。

このライフサイクルは、prime directive の範囲に収まります。シェルコマンドと HTTP の準備確認ポーリングだけで構成される、決定的なインフラで、**LLM は使いません**。Tier-2 のゲートには影響しません。準備確認は、サーバが応答するまで待つ**条件待ち**です。当てずっぽうの固定 `sleep` ではありません。

`serve` はジョブを `bajutsu run` の起動として実行するので、Web UI の Run ボタンは、この挙動を自動的に引き継ぎます。Web UI を Dogfood する際の入れ子も問題ありません。外側の run が、内側の serve を別ポートで起動し、操作し、停止します。`demos/serve-ui` の `Makefile` の `e2e` ターゲットは、`bajutsu run` 1 行にまで縮みます。

`cmd` は、設定から渡される任意のシェルコマンドです。信頼モデルは **`build` と同じ**で、`build` もすでに設定で宣言されたコマンドを実行します。ホスト型のマルチテナント `serve` は、設定を管理者の権限の下に置くので、`launchServer` が新たな露出を増やすことはありません。

## 検討した代替案

* **シナリオ単位のサーバ指定。** 却下しました。シナリオをインフラに結び付けてしまい、「同じシナリオがどこでも動く」という app 非依存の原則（prime directive）を壊します。ターゲットはアプリの属性なので、`baseUrl` や `build` と同様に `apps.<name>` に置きます。
* **`mockServer` の再利用。** 却下しました。`mockServer` は、アプリが呼び出す依存先をスタブします。`launchServer` は、テスト対象のアプリそのものをホストします。役割が異なり、混ぜると両方の意味が曖昧になります。両者は「管理されるプロセスと準備確認」という形を共有するので、将来は 1 つの仕組みに通せます。
* **`servers:` のリスト化。** 見送りました（YAGNI）。サーバ 1 つで、Dogfood と `demos/web` をまかなえます。frontend と API のような、複数プロセスのターゲットが実際に出てきたら、リストに広げます。
* **`Makefile` に任せる。** 却下しました。これは、本項目が解消する現状そのものです。放置すると、新しい web テスト環境のたびに、起動、ポーリング、後始末を書き直すことになります。

## 進捗

- [x] 出荷済み。上記の *実装 PR* を参照してください。

## 参考

* [BE-0058 — serve Web UI の Dogfood](../BE-0058-dogfood-web-ui/BE-0058-dogfood-web-ui-ja.md)：この機能を必要とした利用側です。
* [BE-0041 — web（Playwright）backend](../BE-0041-web-playwright-backend/BE-0041-web-playwright-backend-ja.md)。
* iOS の `build` / `appPath` による、必要時の事前準備フック（`bajutsu/config.py`、`bajutsu/serve/jobs.py`）。
* [DESIGN.md](../../DESIGN.md)：決定性（条件待ち、固定 sleep の禁止）と app 非依存の原則。
