[English](BE-XXXX-capability-preflight-check.md) · **日本語**

# BE-XXXX — run の前に capability をプリフライト検査する

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-XXXX](BE-XXXX-capability-preflight-check-ja.md) |
| 提案者 | [@hirosassa](https://github.com/hirosassa) |
| 状態 | **Proposal** |
| トラック | Proposals |
| トピック | Platform expansion (Android / Web / Flutter) |
<!-- /BE-METADATA -->

## Introduction

どの backend も、自分に何ができるかを `Driver.capabilities()` で宣言する。これは
`bajutsu/drivers/base.py` で定義された capability トークンの集合で、`query`、`semanticTap`、`conditionWait`、`network`、`screenshot`、`elements`、`multiTouch`
がある。ところがシナリオは、選ばれた backend が対応していない action を要求できてしまう。idb（単一タッチのみ）での 2 本指のピンチや、`network` capability を持たない backend での
`request` アサーションがそうだ。いまはこの種の不一致が 1 つしか検査されておらず、しかも action が実行される瞬間にしか見ない。`gestures.py` が run の途中で
`_require_multi_touch` を呼び、`UnsupportedAction` を送出する。つまり、**最後の**ステップが未対応の
capability を必要とするシナリオは、それより前のステップをすべてデバイス上で実行してから、最後に失敗する。本項目では**プリフライト検査**を加える。run の開始時、デバイスに触れる前に、シナリオの action とアサーションが必要とする
capability を洗い出し、`driver.capabilities()` と突き合わせ、未対応のものがあれば、明確で集約されたメッセージとともに、ただちに決定論的に失敗させる。

これは決定論的な `run`/CI 経路に対する、決定論と診断の改善である。LLM は関わらない。そして prime directive #2（決定論優先：中途半端に進めて問題を後から表に出すのではなく、速く明確に失敗する）に直接資する。

## Motivation

- **失敗が遅く、action ごとに表面化する。** capability を見張っているのは
  `_require_multi_touch` だけで、それはジェスチャが実行されたときに発火する。5 つの画面をタップして進み、そこでようやく未対応のピンチをするシナリオは、失敗する前に 5 画面分のデバイス作業を払う。時間の無駄であり、失敗レポートも「このシナリオはこの
  backend では走らせられない」ではなく、run の途中で起きたエラーのように見える。
- **不一致は、デバイスに触れる前に分かる。** シナリオのステップとアサーションは最初からすべて分かっているし、backend の capability 集合も分かっている。検査にデバイスの状態は要らないので、純粋なプリフライトとして実行できる。まさに、run の最中ではなく前に失敗すべき類のものだ。
- **これは backend ごとの問題で、backend の組み合わせは増えていく。** iOS（idb）、Web（Playwright）があり、Android も計画されている（[BE-0009](../BE-0009-cross-platform-abstractions/BE-0009-cross-platform-abstractions-ja.md)）。backend
  間の capability の差は、一度きりの話ではなく、構造的で繰り返し現れる事実だ。ある backend
  に向けて書いたシナリオを別の backend で走らせるとき——まさにそこで、「この backend
  では未対応」という明確なメッセージが効いてくる。（BE-0009 のプラットフォームごとの表は、すでに
  `capabilities()` を backend の契約の中心に据えている。）
- **いまの capability の扱いはモデル化が足りない。** 実際に検査されているのは `multiTouch`
  だけだ。「シナリオの構文 → 必要な capability」の対応は暗黙で、不完全でもある。`request` /
  `event` / `requestSequence` のアサーションは `network` を必要とし、条件待ちは
  `conditionWait` を欲しがるかもしれない、といった具合に。この対応を明示することが本項目の中身である。

## Detailed design

### capability 要求の対応表

シナリオの各構文が必要とする capability の対応を、1 か所で定義する。

| シナリオの構文 | 必要な capability |
|---|---|
| `pinch` / `rotate`（2 本指ジェスチャ） | `multiTouch` |
| `request` / `event` / `requestSequence` / `responseSchema` アサーション | `network` |
| `until: { request }` の待ち | `network` |
| `visual` アサーション / スクリーンショットの取得 | `screenshot` |
| すべての run（基本） | `query`、`elements` |

（正確な行は、現在の action とアサーションの集合に照らして実装の中で確定する。この表が、プリフライトが強制する契約である。）

### プリフライト

run の開始時、driver が選ばれたあと、最初の action の前に、runner は解決済みのシナリオ（展開された共有・パラメータ化ステップやデータ駆動の行も含む。検査が実際に実行されるものをそのまま見るように）を走査し、必要な
capability の集合を集め、`driver.capabilities()` との差を取る。差が空でなければ、run
はただちに失敗する。1 つの集約されたエラーで、**すべての**未対応の構文と、それを照合した backend
を挙げる。action ごとに 1 つのエラーを出すのでもなく、中途半端にデバイス作業をしたあとでもない。失敗がレポート上で一貫して分類されるよう、既存の
`UnsupportedAction` / `UnsupportedCapability` 例外型を再利用する。

`_require_multi_touch` は多層防御のアサーションとして残す（ジェスチャが走る時点では、不変条件はすでに成り立っているはずだ）が、もはや**主たる**門ではない。プリフライトが主になる。

### 決定論

この検査は (シナリオ, capability 集合) の純粋な関数である。デバイスも、時計も、ネットワークも要らない。毎回同じように通るか失敗するかし、しかも非決定論的なデバイス操作の**前に**失敗する。決定論の保証を曲げるのではなく、強める。

### 実装で確かめる未解決の点が 1 つ

`base.py` の `Capability` トークンは、いまは部分的にしか使われていない（検査されているのは
`gestures.py` の `multiTouch` だけ）。実装ではまず、どの構文がどの capability に本当に依存するかを精査する必要がある。アサーションのなかには、capability を必須とせず穏やかに縮退できるものもあるかもしれない。対応表が真に必須なものだけを門にし、実際には走るシナリオを弾かないようにするためだ。

## Alternatives considered

- **これまでどおり run 時に action ごとに検査する。** 現状の弱点として却下する。失敗する前に中途半端なデバイス作業をし、報告も遅い。同じ機械的検査を前倒しするプリフライトのほうが、厳密に優れている。
- **`run` ではなく別の `validate` / `lint` コマンドでの静的検査にする。** 補完としては有用だが、未対応の
  action に対してシナリオが中途半端なデバイス作業を絶対にしないことを保証するには、検査は `run`
  自体になければならない。別の lint は、あとで同じ対応表を再利用できる。
- **未対応の action を backend に黙って no-op させる、または近似させる。** 即座に却下する。決定論優先に反するからだ。黙って通る、単一タッチで近似した「ピンチ」は、まさに prime directive
  が禁じる「マッチした何かをタップする」式の失敗の仕方である。明確に失敗させることが要点だ。
- **capability を config 層でモデル化する。** capability は backend の実装の性質であって、app
  ごとの config ではない。config でモデル化すると、driver が実際に報告する内容と二重になり、ずれる
  risk がある。`driver.capabilities()` を唯一の真実の源として保つ。

## References

- `bajutsu/drivers/base.py`（`Capability`、`Driver.capabilities()`）——本項目が強制する
  capability の契約。`bajutsu/orchestrator/actions/handlers/gestures.py`（`_require_multi_touch`）——本項目が一般化する、既存のより狭い run 時の検査。
- [BE-0009 — Cross-platform abstractions](../BE-0009-cross-platform-abstractions/BE-0009-cross-platform-abstractions-ja.md)——`capabilities()`
  を backend の契約とするプラットフォームごとの backend の組み合わせ。本項目は、それを runner
  が前もって強制するようにする。
- [CLAUDE.md](../../../CLAUDE.md)——prime directive #2（決定論優先：推測したり中途半端に進めたりせず、速く失敗する）。
