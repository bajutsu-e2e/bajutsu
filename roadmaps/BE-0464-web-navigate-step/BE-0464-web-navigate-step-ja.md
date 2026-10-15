[English](BE-0464-web-navigate-step.md) · **日本語**

# BE-0464 — Web バックエンドで、シナリオの途中から URL を開く `navigate` ステップ

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0464](BE-0464-web-navigate-step-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0464") |
| 実装 PR | — |
| トピック | シナリオ作成機能 |
<!-- /BE-METADATA -->

## はじめに

Web（Playwright）バックエンドで、シナリオの途中から URL を開く `navigate` ステップを追加します。ステップは `- navigate: /settings` のように文字列を 1 つ受け取ります。`/` で始まるパスはターゲットの `baseUrl` を基準に解決し、絶対の `http` または `https` の URL はそのまま使います。ステップはページの `load` イベントを待って戻ります。HTTP ステータスは見ないので、遷移後に何が表示されるかはシナリオのアサーションで確かめます。実装は新しい `Driver.goto(url)` メソッドと、新しい `urlNavigation` capability の上に載せます。capability を宣言するのは Web バックエンドだけなので、iOS や Android で `navigate` を使うシナリオは、デバイスを確保する前の preflight で失敗します。iOS の `web:` ブロックの中に置いた `navigate` も同じく失敗します。

## 動機

現在の Web シナリオがページへ到達する手段は 2 つしかありません。実行の開始時とリセット時にブラウザーが `baseUrl` を開き、その後は目的のページが現れるまで作者が UI をタップしていきます。`back` ステップで履歴をたどれますが、任意の URL を開くステップはありません。

この欠落は、Web シナリオの作者に 3 つの困りごとを生みます。

- **深いページに着くまでの UI 操作が長くなります。** アカウント設定ページを検証するシナリオは、まずサインインとメニューのタップを再生しなければなりません。タップが増えるたびに、検証したいページとは無関係な理由でシナリオが失敗する箇所も増えます。
- **リンクではない入口を検証できません。** メールに載ったパスワード再設定のリンク、共有された項目の URL、アドレスを直接入力して到達するページには、タップできる部品がありません。シナリオはその入口から始められません。
- **リダイレクトを検証できません。** 新しい場所へ転送されるべき旧 URL や、未サインインの訪問者をサインインへ送るべき保護された URL を試すには、まさにその URL を開くステップが必要です。

観測できる変化は次のとおりです。いま 5 から 10 ステップのタップで深いページへ向かっているシナリオは、1 つの `navigate` ステップでそのページを開けるようになります。リダイレクトやメールのリンクを扱うシナリオは、初めて書けるようになります。Web デモのシナリオには、URL でページを開いて内容を確かめるものを加えます。

## 詳細設計

### 記述の形

```yaml
steps:
  - navigate: /settings                       # ターゲットの baseUrl を基準に解決する
  - navigate: https://idp.example.com/login   # 絶対 URL はそのまま使う
  - navigate: "/items/${vars.itemId}"         # ${...} トークンは他のステップと同様に展開する
  - assert: [{ exists: { id: settings.title } }]
```

`navigate` は通常のアクションです。1 つのステップが持つアクションは 1 つだけで、共通の修飾子（`name`、`capture`、`target`）も他のアクションと同じように使えます。YAML の値は素の文字列です。`url` を 1 つだけ持つ `Navigate` モデルが、`before` バリデーターで文字列を `{url: <文字列>}` に写して受け取ります。`Exists` も同じバリデーターの手法で、インライン形式を受け取っています（`bajutsu/common/scenario/models/assertions/exists.py:21`）。バリデーターはマッピングを拒否します。`{url: ...}` が文書にない公開の形になり、後で足す `waitUntil` がその形との互換を背負うことを避けるためです。

URL の解決は標準の参照解決（`urllib.parse.urljoin`）に従い、基準にはターゲットの `baseUrl` を使います。

| 値 | 結果 |
|---|---|
| `/settings` | `baseUrl` のオリジンに `/settings` を続けた URL になります。`baseUrl` にパスがあれば置き換わります |
| `settings` | ブラウザーが相対リンクを解くのと同じく、`baseUrl` のパスを基準に解決します |
| `https://other.example/x` | そのまま使います。別のオリジンも許します |
| `http` と `https` 以外のスキーム | シナリオの読み込み時に拒否します |

ほかのスキームを拒否するのは、`file:`、`data:`、`javascript:` をこのステップへ入れないためです。読み込み時の検査は、`${...}` トークンを含まない値にだけ働きます。トークンを含む値は、展開した後の実行時に改めて検査します。スキームの規則は 1 つの共有関数が持ち、2 つの検査はどちらもその関数を呼びます。`HttpRequest` が `first_duplicate_var` を読み込み時と実行時の検査で共有しているのと同じ形です（`bajutsu/common/scenario/models/actions/http_request.py:20`）。`urljoin` は `javascript:alert(1)` のような絶対参照をそのまま返すので、解決の処理そのものは防御になりません。

### 実行時の振る舞い

1. ハンドラーが文字列の `${...}` トークンを展開し、結果にスキームの規則を当てます。そのうえで、相対のこともある参照を `Driver.goto` に渡します。`baseUrl` を基準に参照を解決するのは、ハンドラーではなくドライバーです。Playwright ドライバーはすでに基準の URL を持っているので（`playwright_driver.py:57`）、オーケストレーターがターゲットの設定に新たに依存することはありません。したがって `Driver.goto` の契約は「バックエンド自身の基準で解決される参照」です。
2. Playwright ドライバーは actuation（`gesture: navigate`）を記録して `page.goto(url)` を呼び、`load` イベントが発火したところで戻ります。ステップは履歴に項目を 1 つ足します。Cookie とストレージは残るので、`navigate` はコンテキストのリセットではありません（クリーンな開始の手段は、これまでどおりクロールの `reset_context` です）。
3. ステップはレスポンスのステータスを読みません。404 のページでも読み込めればステップは成功し、続く `assert` や `wait` が判定を担います。`last_nav_status` はクロール専用の信号のままです（`bajutsu/common/drivers/playwright/_functions.py:143`）。
4. URL そのものが引き起こす遷移エラー（名前解決の失敗、接続の拒否、タイムアウト）は、Playwright のメッセージを添えてステップを失敗させます。ドライバーはこれをブラウザーの wedge（詰まり）に分類してはいけません。`_wedge_guard` は Playwright のエラーをすべて回復可能な `DeviceError` に変換し（`bajutsu/common/drivers/playwright/_functions.py:109`）、クロールのワーカーはそれにブラウザーの再起動で応えます。そのままでは、シナリオの実行が作者の打ち間違いをブラウザーの故障として扱ってしまいます。そのため `goto` には独自のエラー対応づけが要ります。

このステップは prime directive の範囲に収まります。固定の待ち時間を含まず、待つ条件はブラウザー自身の `load` であり、判定は引き続きアサーションだけから出ます。

### ドライバー、capability、preflight

| 層 | 変更 | ファイル |
|---|---|---|
| シナリオモデル | `Navigate` アクションと `Step.navigate` フィールド | `bajutsu/common/scenario/models/actions/navigate.py`（新規）、`steps/step.py` |
| Driver プロトコル | `def goto(self, url: str) -> None` | `bajutsu/common/drivers/base/driver.py` |
| capability | `URL_NAVIGATION = "urlNavigation"` | `bajutsu/common/drivers/base/capability.py` |
| preflight | `navigate` を持つステップへの `_Requirement`。`web:` ブロックの中のステップも含みます | `bajutsu/common/capability/capability_preflight.py` |
| ハンドラー | `back` の隣に `@_handler("navigate")` | `bajutsu/common/orchestrator/actions/handlers/navigation.py` |
| Web ドライバー | 独自のエラー対応づけを持つ `goto` | `bajutsu/common/drivers/playwright/playwright_driver.py` |
| Fake ドライバー | 呼び出しを記録し、capability を宣言する | `bajutsu/common/drivers/fake/fake_driver.py` |
| 他のドライバー | iOS と Android の `select_option` と同じく `UnsupportedAction` を送出する | `adb`、`xcuitest`、`xcuitest_live`、`webview` の各ドライバー |
| ランナーの集合 | 画面アクションの集合とセレクターを解決しない集合に `navigate` を加える | `bajutsu/common/orchestrator/loop/_step_runner.py:125` と `:149` |
| トレースと actuation | `_TRACED_METHODS` に `goto` を加え、`navigate` ジェスチャーと `url` チャネルを加える | `drivers/tracing.py`、`drivers/actuation/_shared.py` |

ドライバーのメソッド名を `goto` にするのは、`BackendLifecycle.navigate()` がすでに「`baseUrl` を開く」を意味し、引数を取らないからです（`bajutsu/common/drivers/base/backend_lifecycle.py:25`）。1 つのドライバーに意味の違う `navigate` が 2 つあると、読み手が混乱します。

`navigate` はセレクターを解決しないので、デバイスグループでは前面にいるメンバーに当たってしまいます。そのため `back` と同じく、セレクターを解決しない集合に入れます（BE-0447）。

### コード生成、記述支援、文書の面

- Playwright のコード生成は `await page.goto(new URL(<値>, BASE_URL).toString());` を出力します。ドライバーの解決と同じ結果になります。値は、他のリテラルと同じく生成器の `_ts()` 文字列リテラル補助関数（`bajutsu/codegen/playwright.py:74`）で描きます。URL のクエリには `'` が現れうるからです。リテラルとして描けない値は、既存の「ラベル付き `// TODO`」の形に落とします（`bajutsu/codegen/playwright.py`、376 行目付近の `back` の分岐）。XCUITest と UI Automator のコード生成は、同じラベル付き `TODO` を出力します。
- ステップの語彙は、モデルのほかにも複数の場所に列挙されています。実装では `bajutsu/common/scenario/edit.py`、`bajutsu/repl/session.py`、`app_crash_latches.py` を確かめて更新します。そのうえで、`docs/scenarios.md`、`docs/dsl-grammar.md`、`docs/drivers.md`、`docs/codegen.md` と、それぞれの `docs/ja/` の写しにアクションを加えます。

### 対象外

- iOS のディープリンクと Android の intent。`Driver.goto` という名前と capability のトークンは余地を残しますが、それぞれに別の項目が要ります（URL スキームの解決、iOS が出す確認ダイアログ、`DeviceControl` の面）。
- ホストの許可リスト。`http` ステップも任意の URL を受け付けるので、`navigate` が到達範囲を新たに広げるわけではありません（`bajutsu/common/scenario/models/actions/http_request.py`）。ホスト実行向けの方針が必要なら、セキュリティ強化の項目で両方のステップを対象に扱います。
- レスポンスのステータス検査と `waitUntil` オプション。文字列形式なら、後からマッピング形式へ広げる余地があります。
- `reset_context` や `baseUrl` による起動の置き換え。

### 作業の分解（MECE）

| # | 単位 | 完了条件 |
|---|---|---|
| 1 | シナリオモデル：`Navigate`、`Step.navigate`、文字列の短縮形、スキーム検査 | モデルのテストが文字列の短縮形を読み込み、マッピング、`javascript:`、空の値を拒否する |
| 2 | Driver プロトコル、capability トークン、preflight の要件、Web 以外のドライバーでの送出 | preflight のテストで、iOS と Android のターゲットが、`navigate` の名前を示すメッセージとともにデバイス確保の前に失敗する。iOS ターゲットの `web:` ブロックの中に置いた `navigate` も同じく失敗する。preflight は `web.steps` へ意図して再帰しないが（`capability_preflight.py:95`）、その隙間からこのトークンを確保済みのデバイスまで通さない |
| 3 | ハンドラー、トークン展開、ランナーの集合、トレース、actuation の記録 | Fake ドライバーでの実行が `navigate` を実行し、`${vars.*}` を展開し、actuation を記録する。展開すると `javascript:` や `file:` になる値は、単位 1 が読み込み時に使うのと同じスキームの規則でステップを失敗させる |
| 4 | Playwright の `goto`：URL の解決、`load` の待機、エラーの対応づけ | Web ドライバーのテストが、相対と絶対の値、回復を起こさずにステップを失敗させる名前解決の失敗、成功扱いになる 404 を網羅する |
| 5 | ドライバー適合性のテストケース | Web の適合性スイートが新しい `goto` のケースとともに通り、Web 以外のバックエンドは capability によってそれを飛ばす |
| 6 | コード生成 | `navigate` のシナリオに対する Playwright の出力がゴールデンと一致し、他の生成器はラベル付き `TODO` を出力する |
| 7 | Web デモのシナリオ | Web デモ配下のシナリオが URL でページを開いて内容を確かめ、Web の E2E レーンで通る |
| 8 | 文書と語彙の一覧 | 英語と日本語のページが `navigate` を載せ、`make check` が通る |

### レビューで見てほしい点

- **相対 URL の解決。** 標準の `urljoin` を採ったので、`/settings` は `baseUrl` のパスを落とします。`baseUrl` が `https://host/app/` のターゲットでは、`app/settings` か `/app/settings` と書く必要があります。代案は `baseUrl` のパスに続けて足す方式です。
- **エラーの分類。** 単位 4 では、不正な URL とブラウザーの故障を分けなければなりません。Playwright の `net::ERR_*` エラーを具体的にどう対応づけるかは、実ブラウザーでのテストで確かめる必要があります。
- **スキームの制限。** `http` と `https` に限る規則は、`http` ステップの検査より厳しくなっています。ホスト実行では `file:` の URL がワーカーのディスクを読んでしまうので、この規則は残します。

## 検討した代替案

| 代替案 | 採らなかった理由 |
|---|---|
| Driver プロトコルを通さない Playwright 専用のアクション | app-agnostic の方針に反します。将来 iOS や Android に広げるときは、ステップの意味を定義し直す必要があります。capability による制限は費用が小さく、ステップの意味をバックエンドに依存させません。 |
| 絶対 URL だけを受け付ける | 作者が環境ごとにシナリオを書き換えることになり、環境を決める場所が `baseUrl` の 1 か所ではなくなります。 |
| 最初からマッピング形式（`navigate: { url: ... }`）にする | 引数が現状 1 つしかないので、すべての使用箇所が冗長になります。文字列を先に受け付ければ、後で `waitUntil` のためにマッピングを開けます。 |
| 既定で `baseUrl` のオリジンに限る | 実際のシナリオが通る外部のサインイン提供元や決済ページを塞ぎます。ホスト制限のない `http` より `navigate` が厳しくなる非対称も生まれます。ホスト実行の方針なら両方を扱えます。 |
| 4xx と 5xx の応答でステップを失敗させる | より安全な既定ですが、今回は素の `load` までを担うステップにしました。エラーページを検証するシナリオには逃げ道が要ります。黙って通る 404 が多いと分かった時点で見直します。 |
| `relaunch` に URL を足す | `relaunch` はアプリのプロセスを再起動します。Web での相当物はコンテキストのリセットで、Cookie とストレージを捨てます。「別のページでセッションを続ける」は表せません。 |

## 進捗

- [x] シナリオモデル：`Navigate` と `Step.navigate`
- [x] Driver プロトコル、capability、preflight
- [x] ハンドラー、ランナーの集合、トレース、actuation の記録
- [x] URL の解決とエラーの対応づけを持つ Playwright の `goto`
- [x] ドライバー適合性のテストケース
- [x] コード生成
- [x] Web デモのシナリオ
- [x] 文書と語彙の一覧

ログ：

- 提案しました。
- 8 つの単位をすべて 1 つの変更で実装しました。現在のコードに合わせて、設計の記述から 3 点を変えています。
  1 点目として、`Navigate` は書き出すときも素の文字列に戻します。`_interp_step`、`serialize`、保存済みの run の再読み込みは、いずれもステップを書き出してから再検証します。`{url: ...}` の形で書き出すと、拒否するマッピングの形に当たるためです。
  2 点目として、読み込み時のスキーム検査は、モデルではなく `Scenario` のバリデータで行います。理由は `_http_extract.py` に書かれているものと同じで、モデルで検査すると、展開後の再検証で捕捉されない `ValidationError` になるからです。
  3 点目として、`goto` は `TargetClosedError` 以外の Playwright のナビゲーションエラーをすべて `SelectorError` に変換します。存在しないホストではステップが失敗し、閉じたページは wedge のまま残ることを、実際の Chromium を使うテストで確かめています。
  preflight が `web:` と `app:` ブロックの中まで追うのは `navigate` だけです。ほかの構文の失敗が実行時から preflight に移ることはありません。
  Playwright のコード生成は、golden ファイルではなく単体テストの期待値で検証します。golden ファイルは `smoke.spec.ts` の 1 つだけで、`smoke.yaml` に対応しているからです。
  相対 URL の解決は、設計どおり `urljoin` のままです。

## 参考

- [BE-0210](../BE-0210-android-actuation-fidelity/BE-0210-android-actuation-fidelity-ja.md)：`back` をバックエンド横断のステップとして加えた項目で、この項目のドライバーとハンドラーの経路の前例です。
- [BE-0059](../BE-0059-launch-target-server/BE-0059-launch-target-server-ja.md)：Web ターゲットの `baseUrl` の背後にあるサーバーを立ち上げます。
- `docs/ja/scenarios.md` の `selectOption` の節：capability で制限する Web 専用ステップの前例です。
