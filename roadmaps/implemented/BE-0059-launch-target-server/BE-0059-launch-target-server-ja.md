[English](BE-0059-launch-target-server.md) · **日本語**

# BE-0059 — run のためにターゲットサーバを起動する（`launchServer`）

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0059](BE-0059-launch-target-server-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| 実装 PR | [#169](https://github.com/bajutsu-e2e/bajutsu/pull/169) |
| トピック | Dogfood フィクスチャ（Web UI） |
| 由来 | Dogfooding |
<!-- /BE-METADATA -->

## はじめに

web（Playwright）backend の run は、アプリの `baseUrl` にアクセスしますが、そのサーバを起動する仕組みはありません。すでに待ち受けている前提になっています。この項目では `apps.<name>.launchServer` を追加します。run の前にターゲットサーバを起動し、準備が整うまで待ってから run を実行し、終わったら停止する、という一連の流れを設定で宣言できるようにするものです。run の前に `.app` を用意する iOS の `build` フックの、長時間動き続けるプロセス版にあたります。

## 動機

iOS のターゲットには、必要に応じて事前準備を行うフックがあります。`apps.<name>.build` はシェルコマンドで、バイナリが無いときに `serve` がシナリオの前に実行して `appPath` を生成します。web backend には同等のものが無いため、web のテスト環境ごとに「サーバを起動し、応答するまで待ち、run を実行し、停止する」という処理を手作業で書き直していました。serve Web UI の Dogfood（[BE-0058](../BE-0058-dogfood-web-ui/BE-0058-dogfood-web-ui-ja.md)）が分かりやすい例です。Web UI を Web UI 経由でテストするには、ターゲットとして 2 つめの serve インスタンスを動かす必要があり、これを `Makefile` が手動で起動し、ポーリングし、trap で後始末していました。`demos/web` も静的ページに対して同じことをしています。この段取りは設定側に置くべきもので、そうすれば `bajutsu run`（および、それを経由する Web UI の Run ボタン）が自己完結します。

## 詳細設計

アプリに任意のブロックを追加します。

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

`run` はシナリオ実行を次のライフサイクルで包みます（`bajutsu/runner/launch_server.py`）。

1. まず `readyUrl`（既定は `baseUrl`）を一度プローブします。すでに応答する（HTTP が `< 400`）場合は、`Makefile` や CI、手動起動など別の場所で起動済みということなので、それを再利用し、停止もしません。バイナリがすでにあるときに `build` をスキップするのと同じ考え方です。
2. 応答しない場合は `cmd` を独立したプロセスグループで起動し（後始末がサーバの子プロセスまで届くようにするため）、`readyUrl` が応答するか `readyTimeout` を過ぎるまでポーリングします。コマンドが途中で終了した場合やタイムアウトした場合は、分かりやすいメッセージを出して run を終了コード `2` で失敗させます。
3. シナリオを実行します。
4. run の `finally` で後始末します。自分で起動した場合はプロセスグループに `SIGTERM` を送り（猶予の後に `SIGKILL`）、再利用したサーバはそのまま動かし続けます。

これは prime directive の範囲に収まります。このライフサイクルはシェルコマンドと HTTP の準備確認ポーリングだけで構成される決定的なインフラであり、**LLM は使いません**。そのため Tier-2 のゲートには影響しません。準備確認はサーバが応答するまで待つ条件待ちであり、当てずっぽうの固定 sleep ではありません。`serve` はジョブを `bajutsu run` の起動として実行するため、Web UI の Run ボタンはこの挙動を自動的に引き継ぎます。Web UI を Dogfood する際の入れ子も問題ありません。外側の run が内側の serve を別ポートで起動し、それを操作し、停止します。`demos/serve-ui` の `Makefile` の `e2e` ターゲットは、`bajutsu run` 1 行にまで縮みます。

`cmd` は設定から渡される任意のシェルコマンドで、**`build` と同じ信頼モデル**です。`build` もすでに設定で宣言されたコマンドを実行します。ホスト型のマルチテナント `serve` では設定を管理者の権限の下に置くため、ここで新たな露出が増えることはありません。

## 検討した代替案

* **シナリオ単位のサーバ指定** — 却下しました。シナリオをインフラに結び付けてしまい、「同じシナリオがどこでも動く」という app 非依存の原則を壊します。ターゲットはアプリの属性なので、`baseUrl` や `build` と同様に `apps.<name>` に置きます。
* **`mockServer` の再利用** — 却下しました。`mockServer` はアプリが呼び出す依存先をスタブするものであり、`launchServer` はテスト対象のアプリそのものをホストします。役割が異なり、混ぜると両方の意味が曖昧になります。「管理されるプロセス＋準備確認」という形は共通なので、将来は両者を 1 つの仕組みに通すこともできます。
* **`servers:` のリスト化** — 見送りました（YAGNI）。サーバ 1 つで Dogfood と `demos/web` をまかなえます。frontend と API のような複数プロセスのターゲットが実際に出てきたらリストに広げます。
* **`Makefile` に任せる** — 却下しました。これはこの項目が解消する現状そのもので、放置すると新しい web テスト環境のたびに起動・ポーリング・後始末を書き直すことになります。

## 参考

* [BE-0058 — serve Web UI の Dogfood](../BE-0058-dogfood-web-ui/BE-0058-dogfood-web-ui-ja.md) — この機能を必要とした利用側です。
* [BE-0041 — web（Playwright）backend](../BE-0041-web-playwright-backend/BE-0041-web-playwright-backend-ja.md)。
* iOS の `build` / `appPath` による必要時の事前準備フック（`bajutsu/config.py`、`bajutsu/serve/jobs.py`）。
* [DESIGN.md](../../../DESIGN.md) — 決定性（条件待ち、固定 sleep の禁止）と app 非依存の原則。
