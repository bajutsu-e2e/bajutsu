[English](BE-0470-subject-group-rename.md) · **日本語**

# BE-0470 — ターゲットの app グループを subject に改名する

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0470](BE-0470-subject-group-rename-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0470") |
| トピック | Driver & backend architecture |
<!-- /BE-METADATA -->

## はじめに

ターゲットは、テストする対象を `app` というキーの下に書きます。iOS や Android のアプリには合う名前ですが、
web のターゲットは `app: { url: https://example.com }` と書くことになり、サイトはアプリではありません。
本項目は、ターゲットの config とシナリオの前提条件で、このグループを「テストの対象」を意味する
`subject` に改名します。`targets.site.subject.url` も `targets.app-sim.subject.id` も、どちらの種類の
ターゲットでも正しく読めるようになります。グループの中のフィールドは、名前も意味も変わりません。

## 動機

`app` グループは、どのプラットフォームでも同じ役割を持ちます。Bajutsu が起動してテストする対象です。
iOS と Android では `id`、`path`、`build`、`reinstall`、`launch` を持ち、web では `url` と `launch.env` を
持ちます。`startWhen` と `idNamespaces` はすべてのプラットフォームが共有します。名前が合うのは、この
一覧の片側だけです。web のターゲットを読む人は `app.url` に出会い、ここでの「アプリ」がサイトを意味すると
覚える必要があります。config のスキーマ、ドキュメント、エラーメッセージのすべてが、この語を繰り返します。

この食い違いは、Bajutsu がプラットフォームを足すほど効いてきます。次に予定している Flutter の
ターゲットには `app` が合いますが、web ビューやモバイル Safari のサイトには合いません。ソフトウェアの
種類ではなく役割を表す名前なら、プラットフォームが増えても合い続けます。

出荷後に確かめられる結果は具体的です。リポジトリのターゲットとシナリオは、グループのキーとして `app:` を
書かなくなります。`bajutsu config schema` はどのプラットフォームでも `subject` を示し、旧キーの `app` は
置き換え先のパスを示して読み込みに失敗します。

## 詳細設計

### 改名の内容

| 旧キー | 新キー |
|---|---|
| `targets.<name>.app` | `targets.<name>.subject` |
| `defaults.platforms.<platform>.app` | `defaults.platforms.<platform>.subject` |
| `preconditions.app`（`reinstall`、`launch.env`、`launch.args`、`launch.deeplink`） | `preconditions.subject` |

グループの中のフィールドは、どれも名前と意味を保ちます。`subject.id` は引き続きバンドル ID か
パッケージ名、`subject.url` は web のターゲットが開くページ、`subject.startWhen` は run が最初の
ステップの前に待つ条件です。

```yaml
targets:
  app-sim:
    platform: ios
    subject: { id: com.example.app, path: build/App.app }
  site:
    platform: web
    subject: { url: https://example.com }
```

### 移行

旧キーの `app` は読み込みに失敗し、`subject` を示します。BE-0452 が自身の移動で採ったのと同じ方法です。
ローダーが両方の書き方を受け付けることはありません。改名前に保存したシナリオのプランは、引き続き
読み込めます。そのために、次の 2 つの対応表を変えます。

| 対応表 | 変更 |
|---|---|
| `bajutsu/common/scenario/load.py` の `_PRECONDITION_MOVES` | BE-0452 より前に保存した平らなキーのために、各移動先を `app` から `subject` に移します。BE-0452 より後に保存した入れ子のグループのために、`app` から `subject` への段階も加え、保存済みのプランに限って適用します。 |
| `bajutsu/common/scenario/models/scenario/preconditions.py` の `FLAT_PRECONDITION_KEYS` | 移行のエラーが示す移動先のパスを、それぞれ `subject` に移します。これにより、ローダーの受け付けないキーをメッセージが示すことはなくなります。 |

### 変更が入る場所

- `bajutsu/common/config/schema/platform/registry.py` のプラットフォームの registry が、`Group`、
  `GROUPS`、`PlatformSchema` でグループを `subject` と名付けます。`TargetConfig`、`PlatformDefaults`、
  `json_schema.py` の JSON Schema の生成もこれに従います。
- シナリオの `Preconditions` モデルは、`app` フィールドを `subject` に改名します。
- `bajutsu/serve/batch_provider/device_farm_batch_provider.py` の 2 つの読み手は、モデルを通さずに
  グループを生の辞書として扱います。どのモデルも検出しないため、手で追随させます。
  - 起動時の環境変数の衝突ガードは、`preconditions["app"]["launch"]["env"]` を読みます。
  - Device Farm 向けにまとめる config は、注入する環境変数を `targets.<name>.app.launch.env` に書きます。
- `IosApp` や `AppCommon` のような内部のクラス名は、同じ変更で追随しても、後回しにしてもかまいません。
  config の表面には現れないからです。
- [設定](../../docs/ja/configuration.md)と英語版、[CLI](../../docs/ja/cli.md)、
  [アーキテクチャ](../../docs/ja/architecture.md)のページは、全体で `subject` を使います。
  [用語集](../../docs/ja/glossary.md)には項目を加えます。

### ほかの `runsOn` の提案との関係

本項目は、対になる提案「runsOn を実行先ごとにまとめ、シナリオの runsOn を廃止する」と
「runsOn で iOS のデバイスファミリーと、デバイスやブラウザの種類の集合を指定できるようにする」から
独立しています。触るグループが違い、照合の規則にも触れません。前者と同じリリースで入れれば、利用者に
とって config の破壊的変更は 1 回の移行で済みます。

### 原則との整合

run に LLM は入らず、振る舞いも変わりません。本項目は config のキーを改名し、差異はすべて config に
置いたままにします。

### 対象外

- シナリオの `app:` ステップブロックと `installApp` アクション。ここでの「app」はハイブリッドアプリの
  ネイティブ側の文脈を指し、`web:` の文脈と対になるので、名前は正しいままです。
- `platform` のキーとその値（`ios`、`android`、`web`、`fake`）。
- config の作成者が目にしない、内部の `Effective` のフィールド名。

### 作業分解

1. **スキーマ。** registry、`TargetConfig`、`PlatformDefaults`、シナリオの `Preconditions`、JSON Schema の出力で
   グループを改名します。旧キーの `app` は新しいパスを示して失敗させ、保存済みのシナリオのプランでは
   移します。
2. **config とシナリオ。** `demos/` の config、デモのシナリオ、テストのフィクスチャで `app` を
   `subject` に書き換えます。
3. **ドキュメント。** 上記の日英ドキュメントを更新し、用語集に項目を加えます。

各ユニットが自分のテストを持ちます。移行メッセージ、引き続き読み込める保存済みのプラン、JSON Schema の
出力です。

## 検討した代替案

| 代替案 | 退けた理由 |
|---|---|
| `app` のままにする | web のターゲットをすべて誤った名前で呼ぶことになり、プラットフォームが増えるほど食い違いが広がります。 |
| `testTarget` | Bajutsu は `targets` の下の各項目をすでにターゲットと呼び、`--target` やコードも同じです。`targets.site.testTarget` では、1 つのパスの中で同じ語が 2 つの意味を持ちます。 |
| `product` | 平易ですが、`path` や `build` と並ぶと、Xcode の Products（ビルド成果物）と読めます。 |
| `frontend` | UI の E2E テストのツールには合いますが、`frontend.build` のようにビルド設定と並ぶと不自然です。 |
| `sut`（system under test） | テストの用語としては正確ですが、初めて読む人が調べる必要のある略語です。 |
| `artifact` | web のターゲットの URL はビルド成果物ではありません。 |
| `app` と `subject` を猶予期間だけ両方受け付ける | ローダー、JSON Schema、ドキュメントに 2 つの書き方が並びます。新しいキーを示して読み込みが失敗すれば、config は 1 回の編集で移れます。 |

`subject` にも、`testTarget` を退けた理由が弱い形で当てはまります。シナリオの `email.match` は、
メールの件名を表す `subject` と `subjectMatches` をすでに受け取ります。そのため 1 つのシナリオのファイルに
`preconditions.subject` と `email.match.subject` が並びえます。この衝突は受け入れます。
2 つの用法は互いに無関係なブロックにあり、同じパスを共有しません。一方 `targets.site.testTarget` では、
1 つのパスの中で同じ語が繰り返されます。用語集の `subject` の項目では、両方の意味を示します。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [x] ユニット 1: スキーマ
- [x] ユニット 2: config とシナリオ
- [x] ユニット 3: ドキュメント

ログ：

- [#PR](https://github.com/bajutsu-e2e/bajutsu/pull/PR) — 単位 1-3。本項目全体を実装しました。古い
  `app` キーは、ターゲット、テンプレート、`defaults.platforms`、シナリオの `preconditions` のどこに
  書いても読み込みに失敗し、`subject` を示します。設計に挙がっていなかった 2 か所も合わせて改めました。
  テンプレート自身のキー検査と、テンプレートのパス付け替えの対象である `subject.path` です。実行時の
  エラーメッセージのうち `app.url`、`app.id`、`app.path` を示していたものは、`subject` のパスを示します。

## 参考

- [BE-0452](../BE-0452-target-config-restructure/BE-0452-target-config-restructure-ja.md)：本項目が改名する `app` を含む、ターゲットのグループ。
- `bajutsu/common/config/schema/platform/registry.py` と `bajutsu/common/scenario/load.py`：本項目が広げるグループの registry と、保存済みプランの移行。
