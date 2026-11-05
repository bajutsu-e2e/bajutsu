[English](BE-0041-web-playwright-backend.md) · **日本語**

# BE-0041 — Web (Playwright) backend

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0041](BE-0041-web-playwright-backend-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0041") |
| 実装 PR | [#158](https://github.com/bajutsu-e2e/bajutsu/pull/158) |
| トピック | プラットフォーム対応 |
<!-- /BE-METADATA -->

## はじめに

この項目は、**Playwright（Python）** を基盤とする Web（ブラウザ）プラットフォーム向けの driver を追加します。driver はヘッドレスでクロスブラウザに動きます。`getByTestId` と `getByRole` で要素を選択し、座標を使わずに**意味的にクリック**します。

driver を追加すると、[actuator](../../docs/ja/glossary.md#driver-backend-actuator-platform)、環境マネージャ、id 規約の新しい三つ組が加わります。決定的コアは 1 バイトも変わりません。

これは**最優先のプラットフォーム項目**です。Web を最初のプラットフォームとして推奨する理由は 3 つあります。

- Mac とエミュレータのどちらも使わずに、Linux 上で動きます。
- 既存の `make check` と CI ゲートに収まります。
- 能力モデルの大きい端を行使するので、コアがプラットフォーム中立であることを最小のコストで証明できます。

## 動機

Web は、**抽象がプラットフォーム中立であることを最小のコストで証明できる場所**です。次の 2 つの理由が同時に成り立つプラットフォームは、他にありません。

1. **macOS とデバイスエミュレータのどちらも不要です。** `BrowserContext` がそのまま「デバイス」なので、バックエンドは Linux 上で動きます。動いた日から、現行の `make check` と CI ゲート（[ci](../../docs/ja/ci.md)）の*内側*に収まります。したがって、最初の本物の 2 つ目のプラットフォームは、最大の摩擦要因である Mac やエミュレータの用意を避けられます。
2. **能力モデルの大きい端を行使します**。
   Playwright は `semanticTap`、ネイティブ `conditionWait`（自動待機）、エミュレートの `multiTouch` を提供します。
   ネイティブ `network` も提供します。ネイティブ `network` は、リクエストのスタブ化**と**観測を 1 つの API で扱えます。Playwright を相手に作ると、`capabilities()` の上限が上がります。あわせて、無改造の能力モデルが、小さい端の座標バックエンドから大きい端の意味的な端まで及ぶことも実証できます。

この 2 つの理由から、Web は**推奨される第 1 段階**のプラットフォームであり、マルチプラットフォーム対応トラックの最優先項目になります。摩擦が最小で、到達範囲が最大で、コアが本当にプラットフォーム中立であることを最小のコストで証明できます。

## 詳細設計

### 継ぎ目の表

| 継ぎ目 | 選択 |
|---|---|
| **actuator** | **Playwright（Python）**：`playwright` は Python パッケージ、ヘッドレス、クロスブラウザです。`getByTestId` / `getByRole` で選択し、**意味的にクリック**（座標なし） |
| **環境** | デバイスではなく **`BrowserContext`**。クリーン状態 = 新規 incognito `browser.new_context()`（`erase` 相当ですがほぼ無コスト）。「launch」= `page.goto(url)`、「deeplink」= URL、launch env = クエリパラメータ / 注入した `localStorage` / cookie |
| **id 規約** | `data-testid`（非ローカライズ、開発者付与）。ARIA `role` → `traits`、accessible name → `label` |
| **証跡 provider** | screenshot = `page.screenshot`、video = context の録画、**`network` = ネイティブの route インターセプト**（これを持つ最初のバックエンド）、`deviceLog` ≈ console ログ / page error |
| **codegen 変換先** | Playwright test（TypeScript）または `pytest-playwright` |

Playwright は、能力グラデーションを先導します。
`semanticTap`、ネイティブ `conditionWait`（自動待機）、エミュレートの `multiTouch` を提供するからです。
`network` も提供し、リクエストのスタブ化**と**観測を 1 つの API で扱えます。これらの能力は、能力モデルの上限を引き上げ、Web を抽象の実証に最小のコストで使える場所にします。

### セレクタの対応づけ

YAML のセレクタ（`{ id: settings.reindex }`）は、すでにプラットフォーム中立です。変わるのは、セレクタを満たすためにバックエンドが読むアプリ側の属性だけです。その属性は、新しい Driver の内側に完全に閉じます。Web では、`Selector` フィールドは次のように対応します。

| `Selector` フィールド | iOS | Web |
|---|---|---|
| `id`（第一候補） | `accessibilityIdentifier` | `data-testid` |
| `label`（補助） | `accessibilityLabel` | accessible name / `aria-label` / テキスト |
| `traits`（役割で絞る） | UI traits（`button`、`link`…） | ARIA（Accessible Rich Internet Applications）`role`（`button`、`link`、`textbox`） |
| `value` | accessibility value | input `value` / `aria-*` |

### 能力マトリクスでの位置

Web は**グラデーションを先導**します。`semanticTap`、ネイティブ `conditionWait`（自動待機）、ネイティブ `network`、エミュレートの `multiTouch` を提供する最初のバックエンドです。

| 能力 | idb (iOS) | adb (Android) | Playwright (Web) | fake |
|---|:--:|:--:|:--:|:--:|
| `query` / `elements` / `screenshot` | ✅ | ✅ | ✅ | ✅ |
| `semanticTap` | — | — | ✅ | ✅ |
| `conditionWait`（ネイティブ） | — | — | ✅ | ✅ |
| `network`（ネイティブ） | — | — | ✅ | — |
| `multiTouch` | — | — | ✅（エミュレート） | ✅ |

idb と Android は小さい端に位置し、座標 actuation とモックネットワークを使います。Playwright は大きい端に位置し、意味的操作とネイティブネットワークを使います。無改造の能力モデルがこの両端をまたぐことは、抽象が成立している証拠です。

### 推奨される第 1 段階の展開順

Web は**推奨される第 1 段階**のプラットフォームです。最初の本物の 2 つ目のプラットフォームとして、Android より先に着手します。

理由は決定的です。Web は、**macOS とデバイスエミュレータのどちらも不要**な唯一のプラットフォームです。そのため、*既存の* Linux `make check` と CI ゲート（[ci](../../docs/ja/ci.md)）に、動いた日から収まります。ネイティブ network、video、意味的操作が、`capabilities()` の**大きい端**を行使します。Web は、摩擦が最小で、到達範囲が最大で、コアがプラットフォーム中立であることを最小のコストで証明できます。

Android（[BE-0007](../BE-0007-android-backend/BE-0007-android-backend-ja.md)）は第 2 段階で続きます。すでに一般化されたコアの上で、小さい端の座標経路を確認します。

**Web を Android より先にする理由。** Android は、idb に構造的に近い双子です。それでも Web は、Mac とエミュレータのどちらもなしで現行の Linux ゲートに収まる唯一のプラットフォームです。Web から始めると、「コアは本当にプラットフォーム中立か」という問いに伴うリスクを、最小のコストで下げられます。Android はその後、すでに一般化されたコアの上で、小さい端の座標経路を確認します。

## 検討した代替案

- **座標ベースのブラウザ actuator（Selenium 風のスクリーンショット tap）。** 却下しました。Playwright は、意味的クリック、ネイティブ自動待機、ネイティブ network インターセプトをすでに提供しています。これらは、能力モデルと決定性の原則に直接合致します。座標経路は、Web を抽象の大きい端の証明にしているまさにその能力を捨ててしまいます。
- **Android を先に作る（idb に近い双子）。** 展開順の理由で却下しました。Android はエミュレータ（CI では KVM）を要します。Web は Mac とエミュレータのどちらも要さないので、現行の Linux ゲートに最小のコストで収まります。[BE-0007](../BE-0007-android-backend/BE-0007-android-backend-ja.md) を参照してください。

## 進捗

- [x] 出荷済み。上記の *実装 PR* を参照してください。

## 参考

[DESIGN](../../DESIGN.md)、`bajutsu/drivers/`、`bajutsu/backends.py`、
[drivers.md](../../docs/ja/drivers.md)、[ci.md](../../docs/ja/ci.md)、[concepts.md](../../docs/ja/concepts.md)、
[BE-0007 — Android backend](../BE-0007-android-backend/BE-0007-android-backend-ja.md)、
[BE-0008 — Flutter support](../BE-0008-flutter-support/BE-0008-flutter-support-ja.md)
