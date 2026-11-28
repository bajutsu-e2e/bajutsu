[English](BE-XXXX-web-browser-context-auth-injection.md) · **日本語**

# BE-XXXX — web のブラウザコンテキストに Cookie と storageState とスコープ付きヘッダを注入する

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-XXXX](BE-XXXX-web-browser-context-auth-injection-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **承認済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| トピック | プラットフォーム対応 |
| 関連 | [BE-0031](../BE-0031-data-driven-scenarios/BE-0031-data-driven-scenarios-ja.md)、[BE-0041](../BE-0041-web-playwright-backend/BE-0041-web-playwright-backend-ja.md)、[BE-0076](../BE-0076-web-cross-browser-engines/BE-0076-web-cross-browser-engines-ja.md)、[BE-0130](../BE-0130-default-network-secret-redaction/BE-0130-default-network-secret-redaction-ja.md)、[BE-0174](../BE-0174-scenario-ref-path-containment/BE-0174-scenario-ref-path-containment-ja.md)、[BE-0228](../BE-0228-web-device-mode-emulation/BE-0228-web-device-mode-emulation-ja.md)、[BE-0452](../BE-0452-target-config-restructure/BE-0452-target-config-restructure-ja.md)、[BE-0464](../BE-0464-web-navigate-step/BE-0464-web-navigate-step-ja.md)、[BE-0491](../BE-0491-target-command-hooks/BE-0491-target-command-hooks-ja.md)、[BE-0501](../BE-0501-scenario-command-hooks/BE-0501-scenario-command-hooks-ja.md) |
<!-- /BE-METADATA -->

## はじめに

この項目は、web ターゲットのシナリオを、ログイン済みの状態で始められるようにします。
`browser.context` ブロックに、Cookie、Playwright の storageState ファイル、追加のリクエストヘッダを書きます。
web backend は、最初のステップが走る前に、このブロックをブラウザコンテキストへ適用します。
ヘッダには scope を付けられ、デフォルトでは、アプリ自身の origin にだけ送ります。

[BE-0041](../BE-0041-web-playwright-backend/BE-0041-web-playwright-backend-ja.md) は、事前に入れた `localStorage` と Cookie を web backend の起動入力として挙げています。
しかし、それを適用するコードはありません。今は、ログイン後の画面へ入るために、UI でログイン画面を操作するしかありません。

## 動機

決定的なテストが、UI でのログインを毎回こなせるとは限りません。
Single Sign-On（SSO）、外部の ID プロバイダ、多要素認証、CAPTCHA の後ろにあるログインは、ビルドのたびには動かせません。
本番に近い環境への自動ログインを、組織として禁じている場合もあります。
そうしたチームは、スクリプトでテスト用のセッショントークンを別途発行し、Cookie としてブラウザに渡します。

Bajutsu には、そのトークンをブラウザへ渡す手段がありません。
web ドライバの `new_context` に渡すのは、`reduced_motion` とデバイスの記述子（[BE-0228](../BE-0228-web-device-mode-emulation/BE-0228-web-device-mode-emulation-ja.md)）だけです。
`add_cookies`、`storage_state`、`extra_http_headers` を呼ぶコードは、どこにもありません。

ヘッダには、もう 1 つの需要があります。
アプリは、リクエストヘッダで振る舞いを切り替えます。機能フラグ、A/B の割り当て、テナント、モバイル版を出すかどうかの判定などです。
ヘッダを context 全体に付けると、サードパーティへのリクエストにも付いてしまいます。地図タイル、解析タグ、Web Worker の fetch が例です。
別 origin への独自ヘッダは、Cross-Origin Resource Sharing（CORS）のプリフライトを起こし、リクエストを壊すことがあります。
内部のヘッダが、見せてはならないホストへ漏れもします。そこで、ヘッダの scope は、デフォルトでアプリ自身の origin に絞る必要があります。

この項目が出荷されると、2 つの違いを確かめられます。
ログイン手順を持たないシナリオが、認証済みのページから始まり、通ります。
レポートには、注入した Cookie とヘッダの名前が出て、値は出ません。
`network.json` には、注入したヘッダを付けた交換に印が付くので、別 origin へのリクエストに付かなかったことを、実行後に確かめられます。

## 詳細設計

### browser.context ブロック

ブロックは、[BE-0452](../BE-0452-target-config-restructure/BE-0452-target-config-restructure-ja.md) が web ターゲットに定める `runsOn.browser` グループへ置きます。置き場所は、`engine` と `version` の隣です。
シナリオでは `preconditions.runsOn.web.browser.context` に書きます。1 つのフィールドは、どちらの階層でも同じ綴りになります。

```yaml
# targets.web.runsOn.browser.context、または preconditions.runsOn.web.browser.context
context:
  cookies:
    - name: session
      value: "${hooks.session.token}"
      domain: app.example.test          # 既定は app.url のホスト
      path: /                           # 既定は /
      secure: true
      httpOnly: true
      sameSite: Lax
  storageState: "${hooks.session.statePath}"   # Playwright の storageState の JSON ファイル
  headers:
    values: { X-Feature-Variant: mobile }
    scope: sameOrigin                   # sameOrigin（既定）| all | ["https://api.example.test"]
```

| フィールド | 意味 |
|---|---|
| `cookies` | Playwright の `add_cookies` で追加する Cookie。`name`、`value`、`domain`、`path`、`secure`、`httpOnly`、`sameSite`、`expires` を取ります。 |
| `storageState` | Cookie と origin ごとの `localStorage` を持つ、Playwright の storageState ファイルのパス。 |
| `headers.values` | 条件に合うリクエストへ足すヘッダの名前と値。 |
| `headers.scope` | ヘッダを付ける先。`sameOrigin`、`all`、または厳密な origin の一覧。 |

`cookies` と `storageState` は組み合わせられます。
context はまず状態ファイルから始まり、その上に、列挙した Cookie を重ねます。
`domain` を省いた Cookie は、ターゲットの `app.url` のホストを使います。
解釈できない `domain` や origin は、フィールド名を示すロードエラーです。
`headers.values` に Fetch 仕様が禁じるヘッダ名（`Cookie` や `Host` など）を書くと、ロードエラーです。Playwright はその上書きを黙って捨てるためです。Cookie は `cookies` に書きます。

### 2 つの階層の統合

ターゲットが土台を決め、シナリオが上書きします。アカウントはシナリオごとに変わるからです。
Cookie は `name`、`domain`、`path` の組で統合し、シナリオの項目が勝ちます。
`headers.values` は、キーごとに統合します。
シナリオが `headers.scope` か `storageState` を書くと、ターゲットの値を丸ごと置き換えます。
これは、BE-0452 の `launch.env` の統合規則に従います。

BE-0228 は、デバイスのエミュレーションをターゲット単位に限っています。この項目は、その点で異なります。
エミュレーションは、1 つのターゲットの全シナリオで同じです。認証情報は、そうではありません。

### フックの実行後に値を解決する

値には、`${hooks.<name>.<key>}`、`${secrets.<name>}`、`${row.<column>}` のトークンを使えます。
[BE-0501](../BE-0501-scenario-command-hooks/BE-0501-scenario-command-hooks-ja.md) は、`beforeScenario` のコマンドを、デバイスの貸し出しと起動の前に実行します。
その出力は、ブラウザコンテキストができる前に存在します。
runner は、コマンドの後、`new_context` の前に、ブロックを解決します。
セッショントークンを発行するコマンドの出力を、そのまま Cookie に使えます。

ローダは、`${hooks.*}` のトークンを、ステップと同じように、見える `beforeRun` と `beforeScenario` のエントリに対して静的に検査します。
見えるエントリを指さないトークンは、ロードエラーです。
`storageState` のパスは、トークンの置換後に、suite のルートを基準に解決します。

解決後のパスを置ける場所は 2 つです。
シナリオに直接書いたパスは、suite のルートの内側に限ります（[BE-0174](../BE-0174-scenario-ref-path-containment/BE-0174-scenario-ref-path-containment-ja.md)）。
コマンドが生成する状態ファイルは、シナリオごとの作業ディレクトリに置きます。
Bajutsu は、シナリオの `beforeScenario` のコマンドより前にこのディレクトリを作り、そのパスを `BAJUTSU_SCRATCH_DIR` としてコマンドに渡します。シナリオの `afterScenario` のコマンドの後で、このディレクトリを削除します。
作業ディレクトリは suite のツリーとも実行ディレクトリとも別の場所にあるので、生きたトークンが git の作業ツリーや証跡に残りません。

[BE-0031](../BE-0031-data-driven-scenarios/BE-0031-data-driven-scenarios-ja.md) は、`data` の行を、ロード時に 1 本ずつのシナリオへ展開します。
展開された各シナリオは、自分のブラウザコンテキストを起動します。
行ごとの Cookie は `${row.token}` で入れられ、分離のための作業は増えません。行どうしで状態が混ざることもありません。

### コンテキストへの適用

web ドライバは、解決したブロックを、デバイス記述子の隣に保持します。
コンテキストを作る場所では、必ず適用します。スターターの最初のコンテキスト、`_new_context`、`reset_context`、`relaunch` です。
`storage_state` は `new_context` の引数に入れ、`add_cookies` は、コンテキストができた直後に呼びます。
解決した値は、その実行のあいだ、ドライバのメモリにだけ置きます。
BE-0228 の不変条件は保たれます。新しいコンテキストは、置き換えたコンテキストと同じ素性を持ちます。

このブロックはシナリオの `runsOn` の `web` キーの下に置くので、BE-0452 の `runsOn` の規則により、web ターゲットを駆動しない実行では効果を持ちません。
web 以外のターゲットの `runsOn` には `browser` グループがないため、そこに書いた `browser.context` はロード時に失敗し、フィールド名を示します。

### ヘッダの scope

scope が、どのリクエストにヘッダを付けるかを決めます。

| scope | ヘッダを付けるリクエスト |
|---|---|
| `sameOrigin` | ターゲットの `app.url` の origin と同じ origin へのリクエスト。 |
| `all` | すべてのリクエスト。 |
| 一覧 | 列挙した厳密な origin（スキーム、ホスト、ポートの組）へのリクエスト。 |

実装は、context レベルの route ハンドラです。
条件に合うリクエストには、ヘッダを足して送ります（リダイレクトの追い方は次の節で述べます）。それ以外には `route.fallback()` を呼びます。
scope を持たない `set_extra_http_headers` は使いません。
route を有効にするとブラウザの HTTP キャッシュが無効になるので、`headers` を設定したターゲットはキャッシュなしで動きます。manifest は、scope と並べてこのことを記録します。

web のネットワークコレクタは、シナリオがモックを宣言すると、page レベルのハンドラをすでに登録します。
そのハンドラは `route.continue_()` で終わり、`continue_()` はハンドラの連鎖を止めます。
モックされないリクエストは、context レベルのハンドラに届きません。
そこで、モックのハンドラは、モックされないリクエストに `route.fallback()` を呼ぶよう変えます。
モックされたリクエストはブラウザの外へ出ないので、注入したヘッダを持たず、証跡には従来どおりモック済みと記録します。

### リダイレクトの扱い

素朴な `route.continue_(headers=…)` では、リダイレクトをまたいで scope を守れません。
Playwright は `headers` の指定を、route したリクエストと、そのリクエストが起こすすべてのリダイレクトに適用します。
route ハンドラが見るのも、リダイレクトの連鎖の最初の宛先だけです。
同じ origin へのリクエストが外部の ID プロバイダへリダイレクトすると、注入したヘッダはすべてそこへ届きます。ハンドラは、その遷移を見ません。

そこで、ハンドラはリダイレクトを自分で追います。
条件に合うリクエストには、元のリクエストヘッダに注入するヘッダを（名前を小文字にそろえ、ページが送る同名のヘッダを置き換えます）重ねて `route.fetch(headers={**request.headers, **{k.lower(): v for k, v in values.items()}}, max_redirects=0)` を呼び、応答を `route.fulfill` で返します。
`3xx` の応答は、そのままブラウザへ返します。
ブラウザは `Location` を新しいリクエストとして追い、そのリクエストは再びハンドラに届いて、自分の origin で判定されます。
別 origin への遷移は `route.fallback()` に回り、注入したヘッダを持たずに出ていきます。

この設計は、ハンドラが fulfill したリダイレクトが、どのエンジンでも再び route ハンドラに届くという振る舞いに依存します。
spike の作業単位は、この振る舞いを Chromium、Firefox、WebKit で測り、実装に進むかどうかの関門になります。
fulfill したリダイレクトがハンドラを通らないエンジンがあれば、この項目はそこで止まります。そのエンジンで `sameOrigin` を主張する前に、設計へ戻ります。

この設計には、もう 1 つの代償があります。
`route.fetch` は Playwright 自身のリクエストコンテキストからリクエストを送り、`route.fulfill` が返す前に応答を最後まで読みます。タイムアウトのデフォルトは 30 秒です。
同じ origin からの流れ続ける応答は、ページへ一度に届くか、届かないかのどちらかになります。Server-Sent Events、long poll、chunked の応答、大きなダウンロードが該当します。
spike は、これらの応答も測ります。ブロックが無ければ動くページが壊れるなら、リダイレクトと同じく、この項目は設計へ戻ります。

### 証跡とマスク

manifest には、注入した Cookie の名前、注入したヘッダの名前、scope を残し、値は残しません。
`network.json` の各交換には、`injectedHeaders` のリストが加わります。ハンドラがそのリクエストへ足したヘッダの名前です。
リストがないか空なら、足していません。そのため、古い証跡も有効なままです。
scope が守られたかは、別 origin の宛先で、リストが空でない交換を探して確かめます。

値を証跡に残さないための規則は 3 つです。

| 規則 | 内容 |
|---|---|
| ヘッダ名のマスク | 注入したヘッダの名前を、その実行のマスク対象に加えます（[BE-0130](../BE-0130-default-network-secret-redaction/BE-0130-default-network-secret-redaction-ja.md)）。値は、デフォルトで `network.json` でマスクされます。 |
| Cookie の値のマスク | 既存の `cookie` と `set-cookie` のデフォルトが、各交換に出る Cookie の値をマスクします。 |
| storageState の扱い | storageState ファイルは生きたトークンを含むため、実行ディレクトリへ複写しません。 |

### 設計方針との関係

ブロックは、最初のステップの前に適用する固定のデータです。
ブロックは、待機も分岐も Large Language Model（LLM）の呼び出しも含みません。実行は決定的なままで、判定は assertion が持ちます。
値は、テスト対象のアプリに属し、config とシナリオに置くので、ツールはアプリ非依存のままです。

### この設計が扱わないこと

ブラウザの状態を、ファイルへ保存し戻すことはしません。必要なら、後続の項目で `saveState` ステップを足せます。
`document.cookie` への書き込みや、ページ内でのスクリプト実行も、扱いません。
`headers` を設定しているあいだ、条件に合う同じ origin からの応答は、ページへ少しずつは届かず、最後まで読んでからまとめて届きます。理由は「リダイレクトの扱い」に書いたとおりです。

## 検討した代替案

**UI でログインする。** SSO、多要素認証、CAPTCHA の後ろでは成り立ちません。
成り立つ場合でも、全シナリオに、時間と壊れる箇所を足します。しかも、そのシナリオが試したい部分ではありません。

**http ステップでログインし、その Cookie を渡す。** `http` ステップは、読んだ値を変数に入れます。
しかし、その値をブラウザコンテキストへ移す経路がありません。その経路を作ると、この項目の機能を別の名前で作ることになります。

**スクリプトのステップで `document.cookie` を書く。** スクリプトのステップはありません。
ページ内のスクリプトは、`httpOnly` の Cookie も入れられません。セッション Cookie の多くは、その形です。

**storageState ファイルだけを受け付ける。** コマンドがファイルを書き、config がそのパスを指します。
変更は最小で、この項目は、それを 1 つのフィールドとして残します。ただし、ヘッダを運べず、シナリオごとの上書きもできないので、動機の 2 つの場面が残ります。

**context の `extra_http_headers` でヘッダを付ける。** 呼び出しは 1 行で済みますが、scope がありません。
すべてのヘッダをすべての origin へ送るので、動機に書いた漏洩が起きます。

**scope のデフォルトを `all` にする。** 多くのテストツールのデフォルトと合います。
デフォルトを誤ると、秘密がアプリの origin の外へ出ます。`sameOrigin` をデフォルトにした場合、`all` を望む作者の手間は 1 行です。そのため、デフォルトは安全な側に倒します。

## 進捗

> 作業の進行に合わせて更新してください。チェックリストは「詳細設計」の MECE な作業分解（作業単位ごとに
> 1 つのチェックボックス）に対応し、ログには変更内容と時期を古い順に、PR へのリンクとともに記録します。

- [ ] spike（実装に進むかの関門）：fulfill したリダイレクトが route ハンドラに再び届くか、流れ続ける応答と長く続く応答が動くかを確かめ、Web Worker と Service Worker も、Chromium、Firefox、WebKit で測る。
- [ ] スキーマ：`browser.context` の定義、統合規則、トークンの静的検査。
- [ ] コンテキストへの配線：シナリオごとの作業ディレクトリ、`storage_state`、`add_cookies`、`reset_context` と `relaunch` での再適用。
- [ ] リダイレクトを自分で追う scope 付きヘッダのハンドラと、モックのハンドラの `fallback()` への切り替え。
- [ ] 証跡：manifest の名前、`injectedHeaders`、デフォルトのマスク。
- [ ] テストと、ログイン手順を持たない実 backend のシナリオ、両言語のドキュメント。

## 参考

- `bajutsu/common/drivers/playwright/playwright_driver.py`（`_new_context`）
- `bajutsu/common/drivers/web_network.py`（`WebNetworkCollector._on_route`）
- [Playwright `BrowserContext` リファレンス](https://playwright.dev/python/docs/api/class-browsercontext)
