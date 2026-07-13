[English](BE-0218-e2e-simulator-flaky-readiness-actuation.md) · **日本語**

# BE-0218 — E2E Simulator ゲートの安定化：名前空間に基づく準備完了判定と、操作用の有界なタイムアウト

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0218](BE-0218-e2e-simulator-flaky-readiness-actuation-ja.md) |
| 提案者 | [@hirosassa](https://github.com/hirosassa) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0218") |
| 実装 PR | [#850](https://github.com/bajutsu-e2e/bajutsu/pull/850) |
| トピック | プラットフォーム対応（iOS / Android / Web / Flutter） |
| 関連 | [BE-0207](../BE-0207-xcuitest-channel-transient-retry/BE-0207-xcuitest-channel-transient-retry-ja.md) |
<!-- /BE-METADATA -->

## はじめに

`E2E (Simulator)` ゲート（`.github/workflows/e2e.yml`）が不安定でした。異なる 2 つのジョブで、
それぞれ別々の「通信とタイミング」に起因する hard failure が起きており、どちらもテスト対象のコードの
欠陥ではありません。この項目は、判定を弱めることなく両方を原因から直し、ゲートの不安定さを解消します。

1. **アプリの準備完了判定（`smoke (idb)`、支配的な不安定要因）**：`_await_ready`
   （`bajutsu/platform_lifecycle.py` にある起動後のゲート）が、アプリが前面に来る前に「準備完了」と
   判定してしまうことがあり、その結果、シナリオの最初のステップが遅いコールド起動と競合してタイムアウト
   していました。
2. **操作のタイムアウト（`xcuitest (multi-touch)`、二次的な不安定要因）**：負荷の高い CI ホスト上では、
   1 回の 2 本指ジェスチャがランナーチャネルの 15 秒のソケット窓を超えることがあります。
   [BE-0207](../BE-0207-xcuitest-channel-transient-retry/BE-0207-xcuitest-channel-transient-retry-ja.md)
   は配信後の書き込みを（二重操作の危険があるため）意図的に再試行しないので、この遅延はそのまま失敗に
   つながっていました。

## 動機

直近 80 回の `E2E (Simulator)` 実行のうち、hard failure は 2 件でした（残りは成功か、新しい push による
`concurrency: cancel-in-progress` での `cancelled` です）。2 件の hard failure は、いずれも同じ
`smoke (idb)` のシグネチャでした。

```
step 0 (wait): wait timeout: for {'id': 'stable.row.1'} (10.0s)
```

各実行の直前に記録された `bajutsu doctor` の出力が手がかりになります。画面は `Partial` / `Blocked` と
採点され、名前空間外の id として `Fitness, Watch, Contacts, Files, Safari, Messages` が並んでいました。
これらはテスト対象アプリではなく、ホーム画面（SpringBoard）のアプリアイコンです。つまり、シナリオが
始まった時点で、アプリはまだ前面に来ていませんでした。

原因は `_await_ready` にあります。`showcase-swiftui` ターゲットは `readyWhen` を宣言していないため、
準備完了の判定は「ツリーに要素が 2 つ以上ある」というヒューリスティックにフォールバックしていました。
遅いコールド起動では、デバイスへの問い合わせが SpringBoard のツリー（名前空間外のアイコンが多数）を返し、
これが「2 つ以上」を容易に満たすので、`_await_ready` は早すぎる時点で戻ってしまいます。すると、シナリオの
最初のステップ（`stable.row.1` を待つ、タイムアウト 10 秒）が実質的な準備完了ゲートになり、混雑した
macOS ランナー上ではコールド起動が 10 秒以内に `stable.row.1` を描画できませんでした。これはシナリオの
決定性の欠陥ではなく、準備完了ゲートが誤った画面を受け入れていたということです。

2 件のうち 1 件は、`xcuitest (multi-touch)` も次のように失敗しました。

```
XcuitestChannelError: runner channel POST /gesture failed: timed out
```

この実行は BE-0207（#824）のマージ後なので、一時的な失敗に対する再試行はすでに入っていました。それでも
ジェスチャは hard failure になりました。BE-0207 の分類が、配信後の書き込みを（正しく）決して再試行しない
からです。ランナーチャネルは、すべての呼び出しに 1 つのソケットタイムアウト窓
（`_SOCKET_TIMEOUT_SECONDS = 15`）を使います。瞬間的に詰まった読み取りは再試行が吸収しますが、単に遅い
だけの書き込み（負荷の高いホスト上で XCUITest が合成する多点タッチのイベント）には頼れる再試行がなく、
15 秒を超える 1 回の遅延が実行全体を沈めてしまいます。

どちらも通信とタイミングの脆さであって、判定のシグナルではありません。何かが本当に壊れているかどうかを
何も伝えない赤いゲートは、まさにこの項目が解消する不安定ゲートの損失です。

## 詳細設計

1. **`_await_ready` を名前空間に基づく判定にする**：ターゲットの `idNamespaces` をゲートに渡します。
   使える `readyWhen` セレクタがない場合は、素の要素数より強いシグナルを優先します。すなわち、問い合わせで
   得た要素のうち **どれか 1 つでも id が宣言済みの名前空間に属したら**（`namespace_of(id) in idNamespaces`）、
   アプリは準備完了とみなします。SpringBoard の名前空間外のアイコンはもはやゲートを満たさないので、ゲートは
   アプリ自身を待ちます。準備完了の優先順位は、`readyWhen` セレクタ（最も強く、明示的）→ 名前空間内の要素
   → 既存の「2 つ以上」の要素数（名前空間を宣言しないターゲット、たとえば `-noax` アプリや Web のための、
   変更のないフォールバック）です。
2. **XCUITest チャネルにメソッド別のソケットタイムアウトを入れる**：1 つの窓を、
   `_SOCKET_TIMEOUT_SECONDS`（読み取り、15 秒のまま）と `_ACTUATION_TIMEOUT_SECONDS`（書き込み、30 秒）に
   分け、`_timeout_for(method)` で選びます。読み取りは一時的な詰まりに対して BE-0207 の再試行に頼りますが、
   書き込みは配信後に再試行できないので、負荷の高いホスト上での遅い操作を許容するために、より長い有界な
   窓を 1 つ与えます。どちらも有界のままなので、本当にハングしたランナーは試行ごとにハングせず声高に失敗
   します。
3. **決定性の維持（プライム・ディレクティブ 1・2）**：どちらの変更も実際の結果には触れません。アプリが
   最後まで前面に来なければ、準備完了は締め切りで失敗します。再試行の使い果たしや、予算を超えた書き込みは、
   従来どおり同じ声高な `XcuitestChannelError` を送出します。`stale` / `not-found` はデコードされた結果の
   ままで、再試行しません。LLM はゲートに入らず、固定の `sleep` も加えません（準備完了は条件のポーリング、
   タイムアウトは試行ごとの上限です）。
4. **デバイスなしのユニットテスト**：準備完了の変更は、スクリプト化した fake driver に対して検証します
   （SpringBoard だけの画面はゲートを満たしてはならない、名前空間内の要素が 1 つあれば満たす、名前空間の
   リストが空なら要素数のヒューリスティックを維持する、明示的な `readyWhen` は依然として優先する）。
   タイムアウトの変更は、`_timeout_for` と、`http.client` の境界を fake にしてメソッド別の窓が接続に届くこと
   を確認するテストで検証します。

## 検討した代替案

- **`_await_ready` に触れず、`showcase-swiftui` ターゲットに `readyWhen` を設定する**：ターゲットごとの
  セレクタでは、スイートが使う 2 つの起動画面（smoke は Stable タブ、gestures はピンチ/回転の画面）を両方は
  カバーできず、1 つのアプリしか手当てできません。名前空間のシグナルはアプリ非依存で（どのターゲットも
  すでに宣言している `idNamespaces` を再利用する）、識別子を持つすべてのターゲットのゲートを一度に直します。
- **準備完了やステップ 0 のタイムアウトを単に延ばす**：1 つの不安定さを、より遅い不安定さに置き換えるだけ
  です。ゲートが SpringBoard を受け入れるのを止められず、後続のステップがどのみちタイムアウトするまでの
  待ち時間を延ばすだけです。
- **書き込み操作を再試行する（BE-0207 の冪等性の切り分けをやめる）**：応答が単にタイムアウトしただけの
  ジェスチャを二重に適用しうるので、ゲートが守っている決定性を静かに壊します。却下しました。再試行できない
  書き込みには、より長い有界な窓が安全な手段です。
- **`_SOCKET_TIMEOUT_SECONDS` を全体的に大きくする**：すでに再試行を持つ読み取りについても、ハングした
  ランナーの経路を長くしてしまいます。メソッド別に分ければ、読み取りは短いままにし、再試行できない書き込み
  だけを寛容にできます。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [x] `_await_ready` を名前空間に基づく判定にする（readyWhen → 名前空間内 → 要素数）。`idNamespaces` を呼び出し側に通す
- [x] XCUITest チャネルにメソッド別のソケットタイムアウトを入れる（`_timeout_for`：読み取り 15 秒、操作の書き込み 30 秒）
- [x] 決定性を維持する（声高な失敗とデコード済みの結果は変更しない）
- [x] 両方の変更に対するデバイスなしのユニットテスト

### ログ

- [#850](https://github.com/bajutsu-e2e/bajutsu/pull/850) — `_await_ready` を名前空間に基づく判定にした（名前空間外の SpringBoard 画面はもはやゲートを
  満たさない。名前空間内の要素が 1 つあれば満たす。`idNamespaces` が空なら 2 つ以上の要素数を維持する。
  明示的な `readyWhen` は依然として優先する）。また、XCUITest チャネルにメソッド別のタイムアウトを入れた
  （`_timeout_for`：読み取りは BE-0207 の再試行に支えられた短い 15 秒の窓のまま、操作の書き込みは配信後に
  再試行できないので有界な 30 秒の窓を与える）。デバイスなしのユニットテストでカバーしています。

## 参考

- [`bajutsu/platform_lifecycle.py`](../../bajutsu/platform_lifecycle.py) — `_await_ready`、起動後の準備完了ゲート
- [`bajutsu/drivers/xcuitest.py`](../../bajutsu/drivers/xcuitest.py) — `_timeout_for`、`_SOCKET_TIMEOUT_SECONDS`、`_ACTUATION_TIMEOUT_SECONDS`、`_raw_http_transport`
- [`bajutsu/doctor.py`](../../bajutsu/doctor.py) — `namespace_of`、準備完了ゲートが再利用する id から名前空間への切り分け
- [`.github/workflows/e2e.yml`](../../.github/workflows/e2e.yml) — この項目が安定化させる `E2E (Simulator)` ゲート
- [BE-0207](../BE-0207-xcuitest-channel-transient-retry/BE-0207-xcuitest-channel-transient-retry-ja.md) — このタイムアウトの分割が補完する、一時的な失敗の再試行方針
- [BE-0049](../BE-0049-determinism-flakiness-audit/BE-0049-determinism-flakiness-audit-ja.md) — 両方の変更が整合する決定性の立場（通信とタイミングは許容し、判定は決して許容しない）
