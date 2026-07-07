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
| トピック | プラットフォーム拡張（着手済みスライス） |
<!-- /BE-METADATA -->

## はじめに

**Playwright（Python）** を基盤とする Web（ブラウザ）プラットフォーム向けの driver です。ヘッドレス、
クロスブラウザで、`getByTestId` / `getByRole` で選択し、**意味的にクリック**（座標なし）します。これを
追加するとは、決定的コアを 1 バイトも変えずに、新しい三つ組（actuator + 環境マネージャ + id 規約）
を追加することです。これは **最優先のプラットフォーム項目**です。Web を最初に推奨するのは、Mac もエミュレータも
なしで Linux 上で動き、既存の `make check` / CI ゲートに収まり、能力モデルの大きい端を行使するからです。
コアがプラットフォーム中立であることの最低コストの証明になります。

## 動機

Web は **抽象がプラットフォーム中立であることを証明する最低コストの場所**です。他のどのプラットフォームも
同時には持たない 2 つの理由があります。

1. **macOS もデバイスエミュレータも不要。** `BrowserContext` がそのまま「デバイス」なので、バックエンドは
   Linux 上で動き、動いた日から現行の `make check` / CI ゲート（[ci](../../docs/ja/ci.md)）の *内側*に収まります。
   最初の本物の 2 つ目のプラットフォームから、最大の摩擦要因（Mac やエミュレータの用意）を取り除きます。
2. **能力モデルの大きい端を行使する。** Playwright は `semanticTap`、ネイティブ `conditionWait`（自動待機）、
   ネイティブ `network`（リクエストのスタブ化 **と** 観測を 1 つの API で）、エミュレートの `multiTouch` を
   提供します。これに対して作ることで `capabilities()` の上限を引き上げ、無改造の能力モデルが小さい端の座標
   バックエンドから大きい端の意味的端まで及ぶことを実証します。

これらが相まって、Web を **推奨される第 1 段階**のプラットフォームにし、プラットフォーム拡張トラックの最優先
項目にします。摩擦が最小で、到達範囲が最大で、コアが本当にプラットフォーム中立であることの最低コストの証明になります。

## 詳細設計

### 継ぎ目の表

| 継ぎ目 | 選択 |
|---|---|
| **actuator** | **Playwright（Python）**：`playwright` は Python パッケージ、ヘッドレス、クロスブラウザです。`getByTestId` / `getByRole` で選択し、**意味的にクリック**（座標なし） |
| **環境** | デバイスではなく **`BrowserContext`**。クリーン状態 = 新規 incognito `browser.new_context()`（`erase` 相当ですがほぼ無コスト）。「launch」= `page.goto(url)`、「deeplink」= URL、launch env = クエリパラメータ / 注入した `localStorage` / cookie |
| **id 規約** | `data-testid`（非ローカライズ、開発者付与）。ARIA `role` → `traits`、accessible name → `label` |
| **証跡 provider** | screenshot = `page.screenshot`、video = context の録画、**`network` = ネイティブの route インターセプト**（これを持つ最初のバックエンド）、`deviceLog` ≈ console ログ / page error |
| **codegen 変換先** | Playwright test（TypeScript）または `pytest-playwright` |

Playwright が能力グラデーションを先導する理由は、`semanticTap`、ネイティブ `conditionWait`（自動待機）、
`network`（リクエストのスタブ化 **と** 観測を 1 つの API で）、エミュレートの `multiTouch` を提供するためです。
これは能力モデルの上限を引き上げると同時に、Web を抽象を実証する最低コストの場所にします。

### セレクタの対応づけ

YAML のセレクタ（`{ id: settings.reindex }`）はすでにプラットフォーム中立です。変わるのは *バックエンドが
それを満たすためにアプリ側のどの属性を読むか* だけで、それは新しい Driver の内側に完全に閉じます。Web では
`Selector` フィールドは次のように対応します。

| `Selector` フィールド | iOS | Web |
|---|---|---|
| `id`（第一候補） | `accessibilityIdentifier` | `data-testid` |
| `label`（補助） | `accessibilityLabel` | accessible name / `aria-label` / テキスト |
| `traits`（役割で絞る） | UI traits（`button`、`link`…） | ARIA（Accessible Rich Internet Applications）`role`（`button`、`link`、`textbox`） |
| `value` | accessibility value | input `value` / `aria-*` |

### 能力マトリクスでの位置

Web は **グラデーションを先導**します。`semanticTap`、ネイティブ `conditionWait`（自動待機）、ネイティブ
`network`、エミュレートの `multiTouch` を提供する最初のバックエンドです。

| 能力 | idb (iOS) | adb (Android) | Playwright (Web) | fake |
|---|:--:|:--:|:--:|:--:|
| `query` / `elements` / `screenshot` | ✅ | ✅ | ✅ | ✅ |
| `semanticTap` | — | — | ✅ | ✅ |
| `conditionWait`（ネイティブ） | — | — | ✅ | ✅ |
| `network`（ネイティブ） | — | — | ✅ | — |
| `multiTouch` | — | — | ✅（エミュレート） | ✅ |

idb と Android は小さい端（座標 actuation とモックネットワーク）、Playwright は大きい端（意味的操作とネイティブ
ネットワーク）に位置します。無改造の能力モデルがこの両端をまたぐことは、抽象が成立している証拠です。

### 展開順：推奨される第 1 段階

Web は **推奨される第 1 段階**のプラットフォームで、最初の本物の 2 つ目のプラットフォームとして、Android より
先に着手します。理由は決定的です。Web は **macOS もデバイスエミュレータも不要**な唯一のプラットフォームなので、
*既存の* Linux `make check` / CI ゲート（[ci](../../docs/ja/ci.md)）に動いた日から収まります。ネイティブ network +
video + 意味的操作が `capabilities()` の **大きい端**を行使します。摩擦が最小で、到達範囲が最大で、コアがプラットフォーム
中立であることの最低コストの証明になります。Android（[BE-0007](../BE-0007-android-backend/BE-0007-android-backend-ja.md)）は
第 2 段階で続き、すでに一般化されたコアの上で小さい / 座標経路を確認します。

**Android が idb の構造的に近い双子なのに、なぜ Web を先にするか**: Web は Mac もエミュレータもなしで現行
Linux ゲートに収まる唯一のプラットフォームなので、「コアは本当にプラットフォーム中立か？」という問いのリスクを
最低コストで下げられます。Android はその後、すでに一般化されたコアの上で小さい / 座標経路を確認します。

## 検討した代替案

- **座標ベースのブラウザ actuator（Selenium 風のスクリーンショット tap）。** 却下: Playwright はすでに意味的
  クリック、ネイティブ自動待機、ネイティブ network インターセプトを提供しており、能力モデルと決定性の原則に
  直接合致します。座標経路は、Web を抽象の大きい端の証明にしているまさにその能力を捨ててしまいます。
- **Android を先に作る（idb に近い双子）。** 展開順の理由で却下: Android はエミュレータ（CI では KVM）を要する
  一方、Web は Mac もエミュレータも要さず、現行 Linux ゲートに最低コストで収まります。[BE-0007](../BE-0007-android-backend/BE-0007-android-backend-ja.md) を参照。

## 進捗

- [x] 出荷済み。上記の *実装 PR* を参照してください。

## 参考

[DESIGN](../../DESIGN.md)、`bajutsu/drivers/`、`bajutsu/backends.py`、
[drivers.md](../../docs/ja/drivers.md)、[ci.md](../../docs/ja/ci.md)、[concepts.md](../../docs/ja/concepts.md)、
[BE-0007 — Android backend](../BE-0007-android-backend/BE-0007-android-backend-ja.md)、
[BE-0008 — Flutter support](../BE-0008-flutter-support/BE-0008-flutter-support-ja.md)
