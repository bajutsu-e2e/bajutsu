**English** · [日本語](cookbook.md)

# シナリオクックブック

> 「〇〇をしたい」という目的別のレシピ集です。[scenarios](scenarios.md) が文法のリファレンス
> （すべてのステップ種別、待機、アサーション）であるのに対し、このページは実例です。以下のレシピは
> どれも、リポジトリ自身の CI が実際に走らせているファイルから抜き出したもので、説明のためだけに
> 作った架空の例ではありません。各レシピの下のリンクから、ここでは省いた部分も含む全文を見られ
> ます。

関連: [scenarios](scenarios.md) · [selectors](selectors.md) · [network](network.md) · [Getting started](getting-started/index.md)

showcase アプリをビルドするか web デモを配信すれば（[Getting started](getting-started/index.md) 参照）、
以下のどれでも自分で実行できます。

```bash
uv run bajutsu run --scenario <path-to-file> --target showcase-swiftui --backend ios --udid booted --no-erase
```

---

## 画面を移動して値の変化を検証する

もっとも単純で有用な形です。画面へ移動し、1 つの要素を操作し、結果をアサーションで確かめます。
これは showcase 自身のガイドツアーで使われているシナリオそのもので、
[`demos/tour/demo.sh`](../../demos/tour/demo.sh) がこれを実行したあと、わざと壊してみせます
（まずアサーションを壊して機械アサーションが検知する様子を、続いてセレクタを壊して `triage` が
原因を突き止める様子を見せます）。

```yaml
- name: favorite a horse
  preconditions:
    launchEnv: { SHOWCASE_UITEST: "1" }
  steps:
    - wait: { for: { id: stable.row.3 }, timeout: 10 }
    - tap: { id: stable.row.3 }
    - wait: { for: { id: horse.favorite }, timeout: 5 }
    - tap: { id: horse.favorite }
  expect:
    - value: { sel: { id: horse.favorite.value }, equals: "on" }
```

`tap` の前の `wait` はどれも**条件待ち**であり、固定の sleep ではありません。Bajutsu は対象の id が
現れるまでポーリングし、現れなければタイムアウトしてはっきり失敗します。全文は
[`demos/showcase/scenarios/menu/tour.yaml`](../../demos/showcase/scenarios/menu/tour.yaml) にあります。

## 一覧を検索して絞り込む

フィールドに入力し、結果件数をアサーションで確かめます。あわせて、ハッピーパスと同じくらい大切な「該当なし」のケースも載せます。

```yaml
- name: filter narrows the catalog
  preconditions:
    launchEnv: { SHOWCASE_UITEST: "1" }
  steps:
    - tap: { label: "Search", traits: [button] }
    - wait: { for: { id: search.field }, timeout: 10 }
    - type: { text: "Horse 3", into: { id: search.field } }
    - wait: { for: { id: search.row.3 }, timeout: 5 }
  expect:
    - count: { sel: { idMatches: "search.row.*" }, equals: 1 }
    - value: { sel: { id: search.count }, equals: "1" }
    - exists: { id: search.results-empty, negate: true }

- name: no match shows the empty state
  preconditions:
    launchEnv: { SHOWCASE_UITEST: "1" }
  steps:
    - tap: { label: "Search", traits: [button] }
    - wait: { for: { id: search.field }, timeout: 10 }
    - type: { text: "zzz", into: { id: search.field } }
    - wait: { for: { id: search.results-empty }, timeout: 5 }
  expect:
    - exists: { id: search.results-empty }
    - count: { sel: { idMatches: "search.row.*" }, equals: 0 }
```

`idMatches` は id に対する glob マッチで、個々の行を名指しせずに「行が何件あるか」を検証するのに
使えます。全文（プラットフォームをまたぐ id 候補のリスト形式も含む）は
[`demos/showcase/scenarios/search.yaml`](../../demos/showcase/scenarios/search.yaml) にあります。

## システムの権限ダイアログを許可する

実行時の権限プロンプト（通知、位置情報など）は、アプリ自身の UI ではなく**プロセス外のシステム
アラート**です。iOS バックエンドはこれを直接タップできません。`dismissAlerts` は、そのタップだけを AI の
アラートガードに任せます。ガードがプロンプトを見張って「Allow」をタップする一方で、その前後の
アサーションはすべて機械チェックのままです。

```yaml
- name: grant notification permission
  tags: [permission, system]
  dismissAlerts: { instruction: "tap Allow" }
  preconditions:
    launchEnv: { SHOWCASE_UITEST: "1" }
  steps:
    - tap: { label: "Permissions", traits: [button] }
    - wait: { for: { id: perm.requestNotif }, timeout: 10 }
    - assert:
        - value: { sel: { id: perm.notif.value }, equals: "notDetermined" }
    - tap: { id: perm.requestNotif }
    - wait: { for: { id: perm.notif.authorized }, timeout: 10 }
  expect:
    - value: { sel: { id: perm.notif.value }, equals: "authorized" }
```

`dismissAlerts` はアラートの**ハンドラ**であって、アサーションではありません。合否の判定には
一切関与せず、iOS バックエンドからは見えないアラートでステップが止まってしまうのを防ぐだけです。Android では
同じシナリオがプロンプトなしで走ります（target の config が権限を事前付与しているため）。その場合
ガードは何もしないまま待機するだけです。1 つのシナリオが 2 つのプラットフォームで分岐なく動きます。
プラットフォーム間の整合性についての注記を含む全文は
[`demos/showcase/scenarios/permission.yaml`](../../demos/showcase/scenarios/permission.yaml) にあります。

## ネットワークをモックする

`mocks` はリクエストをプロトコル内で捕まえ、決定的に応答します。実サーバは不要で、ネットワークの
不安定さもありません。`request` アサーションで、そのモックされた呼び出しが実際に起きたことを
確認できます。

```yaml
- name: log submit answered by a mock, toast appears and clears
  preconditions:
    launchEnv: { SHOWCASE_UITEST: "1" }
  mocks:
    - match: { method: POST, pathMatches: "/post$" }
      respond: { status: 201, body: "{\"ok\":true}" }
  steps:
    - tap: { label: "Log", traits: [button] }
    - wait: { for: { id: log.submit }, timeout: 10 }
    - tap: { id: log.submit }
    - wait: { until: { request: { method: POST, path: /post, status: 201 } }, timeout: 6 }
    - wait: { for: { id: log.toast }, timeout: 4 }
    - wait: { until: { gone: { id: log.toast } }, timeout: 5 }
  expect:
    - request: { method: POST, path: /post, status: 201 }
    - value: { sel: { id: log.status }, equals: "done" }
```

`wait: { until: { gone: … } }` は要素が**消える**までポーリングします。こういう一時的なトースト
通知の検証に使えます。全文（このリクエストが運ぶ `Authorization` ヘッダと `password` フィールドを
`redact` ポリシーが証跡上でマスクする様子も含む）は
[`demos/showcase/scenarios/network_mock.yaml`](../../demos/showcase/scenarios/network_mock.yaml) に
あります。

## 同じシナリオをデータ表に沿って繰り返す

`data` は 1 つのシナリオ本体を行ごとに 1 回ずつ、それぞれ独立したクリーンな環境で実行し、
`${row.*}` トークンを置換します。ここでは入力するクエリとアサーションする id の両方で使っており、
各行が「検索した馬をちょうど見つけた」ことを証明します。

```yaml
- name: search finds the seeded horse
  data:
    - { q: "Horse 1", n: "1" }
    - { q: "Horse 3", n: "3" }
    - { q: "Horse 5", n: "5" }
  preconditions:
    launchEnv: { SHOWCASE_UITEST: "1" }
  steps:
    - tap: { label: "Search", traits: [button] }
    - wait: { for: { id: search.field }, timeout: 10 }
    - type: { into: { id: search.field }, text: "${row.q}" }
    - wait: { for: { id: "search.row.${row.n}" }, timeout: 5 }
  expect:
    - value: { sel: { id: search.count }, equals: "1" }
    - exists: { id: "search.row.${row.n}" }
```

各トークンのプラットフォームをまたぐ id 候補のリスト形式を含む全文は
[`demos/showcase/scenarios/data_driven.yaml`](../../demos/showcase/scenarios/data_driven.yaml) に
あります。

## ステップ列をコンポーネントとして再利用する

**コンポーネント**はパラメータ付きの再利用可能なステップ列で、シナリオ DSL のマクロです。読み込み
時に展開され、run の結果には自分自身のステップとしては現れません。まず定義します。

```yaml
# _components/search_for.yaml
params: [query]
steps:
  - tap: { label: "Search", traits: [button] }
  - wait: { for: { id: search.field }, timeout: 10 }
  - type: { into: { id: search.field }, text: "${params.query}" }
```

これをどのシナリオからでも `use` / `with` で呼び出せます。

```yaml
- name: search finds a horse by name
  preconditions:
    launchEnv: { SHOWCASE_UITEST: "1" }
  steps:
    - use: { component: _components/search_for.yaml, with: { query: "Horse 3" } }
    - wait: { for: { id: search.row.3 }, timeout: 5 }
  expect:
    - value: { sel: { id: search.count }, equals: "1" }
```

コンポーネントファイル：
[`demos/showcase/scenarios/menu/_components/search_for.yaml`](../../demos/showcase/scenarios/menu/_components/search_for.yaml)。
呼び出し側の例：
[`demos/showcase/scenarios/menu/features.yaml`](../../demos/showcase/scenarios/menu/features.yaml)。

## web backend でも同じシナリオの形

ここまでのレシピはどれも iOS の showcase を対象に書きましたが、step/expect の文法自体に iOS 固有の
ものは何もありません。変わるのはセレクタの背後にある属性だけです（`accessibilityIdentifier` が
web の `data-testid` に変わりますが、同じセレクタ解決コアを通ります）。web デモの 2 つ目のシナリオも、
同じ形をしています。画面を移動し、操作を繰り返し、最終的な値をアサーションで確かめ、その上で証跡を
取得します。

```yaml
scenarios:
  - name: onboard, log in, and increment the counter three times
    steps:
      - tap: { id: onboarding.start }
      - type: { text: "a@b.com", into: { id: auth.email } }
      - type: { text: "pw", into: { id: auth.password } }
      - tap: { id: auth.submit }
      - wait: { for: { id: home.title }, timeout: 5 }
        capture: [deviceLog, video]
      - tap: { id: counter.increment }
      - tap: { id: counter.increment }
      - tap: { id: counter.increment }
    expect:
      - exists: { id: home.title }
      - value: { sel: { id: counter.value }, equals: "3" }
```

実行するには `uv run bajutsu run --scenario demos/web/scenarios/counter.yaml --target web --backend
web --config demos/web/demo.config.yaml`（[web トラック](getting-started/web.md) 参照）。全文は
[`demos/web/scenarios/counter.yaml`](../../demos/web/scenarios/counter.yaml) にあります。

---

## これらの出どころ

showcase 一式
（[`demos/showcase/scenarios/`](../../demos/showcase/scenarios/)）には、ジェスチャ、マルチタッチ、
デバイス制御、ビジュアルリグレッション、relaunch と状態の永続化などを扱うシナリオがさらに 25 本
ほどあります。各画面が公開する識別子は [showcase](showcase.md) にカタログがあります。web デモ
（[`demos/web/scenarios/`](../../demos/web/scenarios/)）と、web UI 自身のドッグフーディング一式
（[`demos/serve-ui/scenarios/`](../../demos/serve-ui/scenarios/)）も見る価値があります。後者は
ある程度複雑な単一ページアプリをテストする実例としても参考になります。ここに出てきたすべての
レシピの背後にある step / wait / アサーションの文法全体は [scenarios](scenarios.md) に、形式的な
EBNF は [dsl-grammar](dsl-grammar.md) にあります。
