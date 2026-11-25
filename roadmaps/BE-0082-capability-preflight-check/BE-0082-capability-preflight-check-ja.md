[English](BE-0082-capability-preflight-check.md) · **日本語**

# BE-0082 — run の前に capability をプリフライト検査する

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0082](BE-0082-capability-preflight-check-ja.md) |
| 提案者 | [@hirosassa](https://github.com/hirosassa) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0082") |
| 実装 PR | [#229](https://github.com/bajutsu-e2e/bajutsu/pull/229) |
| トピック | ドライバとバックエンドのアーキテクチャ |
<!-- /BE-METADATA -->

## はじめに

本項目は、`run` に**プリフライト検査**を加えます。run の開始時、デバイスに触れる前に、シナリオの action とアサーションが必要とする
capability を洗い出します。洗い出した capability を `driver.capabilities()` と突き合わせ、未対応のものがあれば、明確で集約された
メッセージとともに、ただちに決定論的に失敗させます。

どの backend も、自分に何ができるかを `Driver.capabilities()` で宣言します。これは `bajutsu/drivers/base.py` で定義された
capability トークンの集合で、`query`、`semanticTap`、`conditionWait`、`network`、`screenshot`、`elements`、`multiTouch` があります。
ところがシナリオは、選ばれた backend が対応していない action を要求できてしまいます。たとえば、idb（単一タッチのみ）での
2 本指のピンチや、`network` capability を持たない backend での `request` アサーションです。

いまは、この種の不一致のうち 1 つだけを、action が実行される瞬間に検査します。`gestures.py` が run の途中で `_require_multi_touch` を
呼び、`UnsupportedAction` を送出します。したがって、**最後の**ステップが未対応の capability を必要とするシナリオは、それより前の
ステップをすべてデバイス上で実行してから、最後に失敗します。

プリフライトは、決定論的な `run`/CI 経路に対する、決定論と診断の改善です。LLM は関わりません。prime directive #2（決定論優先：
中途半端に進めて問題を後から表に出すのではなく、速く明確に失敗する）に直接資します。

## 動機

- **失敗が遅く、action ごとに表面化する。** capability を見張っているのは
  `_require_multi_touch` だけで、それはジェスチャが実行されたときに発火します。5 つの画面をタップして進み、そこでようやく
  未対応のピンチをするシナリオを考えます。この run は、失敗する前に 5 画面分のデバイス作業を払います。失敗レポートも、
  「このシナリオはこの backend では走らせられない」ではなく、run の途中で起きたエラーのように見えます。
- **不一致は、デバイスに触れる前に分かる。** シナリオのステップとアサーションは最初からすべて分かっているし、backend の capability 集合も分かっています。検査にデバイスの状態は要らないので、純粋なプリフライトとして実行できます。この種の検査は、run の最中ではなく前に失敗すべきです。
- **これは backend ごとの問題で、backend の組み合わせは増えていく。** iOS（idb）、Web（Playwright）があり、Android も計画されています（[BE-0009](../BE-0009-cross-platform-abstractions/BE-0009-cross-platform-abstractions-ja.md)）。backend 間の capability の差は、一度きりの話ではなく、構造的で繰り返し現れる事実です。ある backend
  に向けて書いたシナリオを別の backend で走らせるとき、「この backend では未対応」という明確なメッセージが効いてきます。
  BE-0009 のプラットフォームごとの表は、すでに `capabilities()` を backend の契約の中心に据えています。
- **いまの capability の扱いはモデル化が足りない。** 実際に検査されているのは `multiTouch`
  だけです。「シナリオの構文 → 必要な capability」の対応は暗黙で、不完全でもあります。`request` /
  `event` / `requestSequence` のアサーションは `network` を必要とし、条件待ちは `conditionWait` を欲しがるかもしれません。
  この対応を明示することが本項目の中身です。

## 詳細設計

### capability 要求の対応表

シナリオの各構文が必要とする capability の対応を、1 か所で定義します。

| シナリオの構文 | 必要な capability |
|---|---|
| `pinch` / `rotate`（2 本指ジェスチャ） | `multiTouch` |
| `request` / `event` / `requestSequence` / `responseSchema` アサーション | `network` |
| `until: { request }` の待ち | `network` |
| `visual` アサーション / スクリーンショットの取得 | `screenshot` |
| すべての run（基本） | `query`、`elements` |

正確な行は、現在の action とアサーションの集合に照らして実装の中で確定します。この表が、プリフライトが強制する契約です。

### プリフライト

run の開始時、driver が選ばれたあと、最初の action の前に、runner は解決済みのシナリオを走査します。走査には、展開された
共有ステップやパラメータ化ステップ、データ駆動の行も含めます。検査が、実際に実行されるものをそのまま見るためです。
runner は必要な capability の集合を集め、`driver.capabilities()` との差を取ります。

差が空でなければ、run はただちに失敗します。1 つの集約されたエラーで、**すべての**未対応の構文と、それを照合した backend を挙げます。
エラーはシナリオ全体で 1 つであり、action ごとには出さず、デバイス作業の前に出ます。失敗がレポート上で一貫して分類されるよう、
既存の `UnsupportedAction` 例外型（いまは `_require_multi_touch` が送出している）を再利用します。

`_require_multi_touch` は、多層防御のアサーションとして残します。ジェスチャが走る時点では、不変条件はすでに成り立っているはずだからです。
ただし、**主たる**門はプリフライトになります。

### 決定論

この検査は (シナリオ, capability 集合) の純粋な関数です。デバイスも、時計も、ネットワークも要りません。毎回同じように通るか失敗するかし、
しかも非決定論的なデバイス操作の**前に**失敗します。この検査は、決定論の保証を強めます。

### 実装で確かめる未解決の点が 1 つ

`base.py` の `Capability` トークンは、いまは部分的にしか使われていません（検査されているのは
`gestures.py` の `multiTouch` だけ）。実装ではまず、どの構文がどの capability に本当に依存するかを精査する必要があります。アサーションのなかには、capability を必須とせず
穏やかに縮退できるものもあるかもしれないからです。精査によって、対応表は真に必須なものだけを門にでき、実際には走るシナリオを弾きません。

## 検討した代替案

- **これまでどおり run 時に action ごとに検査する。** 現状の弱点として却下します。run は失敗する前に中途半端なデバイス作業をし、報告も遅くなります。同じ機械的検査を前倒しするプリフライトのほうが、
厳密に優れています。
- **`run` ではなく別の `validate` / `lint` コマンドでの静的検査にする。** 補完としては有用です。ただし、未対応の
  action に対してシナリオが中途半端なデバイス作業を絶対にしないことを保証するには、検査は `run`
  自体になければなりません。別の lint は、あとで同じ対応表を再利用できます。
- **未対応の action を backend に黙って no-op させる、または近似させる。** 即座に却下します。決定論優先に反するからです。黙って通る、単一タッチで近似した「ピンチ」は、prime directive
  が禁じる「マッチした何かをタップする」式の失敗の仕方です。明確に失敗させることが要点です。
- **capability を config 層でモデル化する。** capability は backend の実装の性質であって、app
  ごとの config ではありません。config でモデル化すると、driver が実際に報告する内容と二重になり、ずれる
  おそれがあります。`driver.capabilities()` を唯一の真実の源として保ちます。

## 進捗

- [x] 出荷済み。上記の *実装 PR* を参照してください。

## 参考

- `bajutsu/drivers/base.py`（`Capability`、`Driver.capabilities()`）：本項目が強制する
  capability の契約。`bajutsu/orchestrator/actions/handlers/gestures.py`（`_require_multi_touch`）は、本項目が一般化する、既存のより狭い run 時の検査です。
- [BE-0009 — Cross-platform abstractions](../BE-0009-cross-platform-abstractions/BE-0009-cross-platform-abstractions-ja.md)：`capabilities()`
  を backend の契約とするプラットフォームごとの backend の組み合わせ。本項目は、runner
  が前もってその契約を強制するようにします。
- [CLAUDE.md](../../CLAUDE.md)：prime directive #2（決定論優先：推測したり中途半端に進めたりせず、速く失敗する）。
