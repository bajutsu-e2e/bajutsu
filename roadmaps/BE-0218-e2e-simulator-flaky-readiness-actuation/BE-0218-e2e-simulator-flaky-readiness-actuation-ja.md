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
| トピック | プラットフォーム対応 |
| 関連 | [BE-0207](../BE-0207-xcuitest-channel-transient-retry/BE-0207-xcuitest-channel-transient-retry-ja.md) |
<!-- /BE-METADATA -->

## はじめに

`E2E (Simulator)` ゲート（`.github/workflows/e2e.yml`）が不安定でした。異なる 2 つのジョブで、別々の
「通信とタイミング」に起因する hard failure が起きていました。どちらの原因も、テスト対象のコードの
欠陥ではありません。この項目は、判定を弱めることなく両方を原因から直し、ゲートの不安定さを解消します。

1. **アプリの準備完了判定（`smoke (idb)`、支配的な不安定要因）**：`_await_ready` は、
   `bajutsu/platform_lifecycle.py` にある起動後のゲートです。このゲートが、アプリが前面に来る前に
   「準備完了」と判定してしまうことがありました。その結果、シナリオの最初のステップが遅いコールド起動と
   競合し、タイムアウトしていました。
2. **操作のタイムアウト（`xcuitest (multi-touch)`、二次的な不安定要因）**：負荷の高い CI ホスト上では、
   1 回の 2 本指ジェスチャがランナーチャネルの 15 秒のソケット窓を超えることがあります。
   [BE-0207](../BE-0207-xcuitest-channel-transient-retry/BE-0207-xcuitest-channel-transient-retry-ja.md)
   は、配信後の書き込みを意図的に再試行しません。再試行すると、二重操作の危険があるからです。そのため、
   この遅延はそのまま失敗につながっていました。

## 動機

直近 80 回の `E2E (Simulator)` 実行のうち、hard failure は 2 件でした。残りは成功か、新しい push による
`concurrency: cancel-in-progress` での `cancelled` です。2 件の hard failure は、いずれも同じ
`smoke (idb)` のシグネチャでした。

```
step 0 (wait): wait timeout: for {'id': 'stable.row.1'} (10.0s)
```

各実行の直前に記録された `bajutsu doctor` の出力が手がかりになります。画面は `Partial` / `Blocked` と
採点され、名前空間外の id として `Fitness, Watch, Contacts, Files, Safari, Messages` が並んでいました。
これらはテスト対象アプリではなく、ホーム画面（SpringBoard）のアプリアイコンです。つまり、シナリオが
始まった時点で、アプリはまだ前面に来ていませんでした。

原因は `_await_ready` にあります。`showcase-swiftui` ターゲットは `readyWhen` を宣言していません。
そのため、準備完了の判定は「ツリーに要素が 2 つ以上ある」というヒューリスティックにフォールバック
していました。遅いコールド起動では、デバイスへの問い合わせが SpringBoard のツリーを返します。このツリーには
名前空間外のアイコンが多数あり、「2 つ以上」を容易に満たします。そのため `_await_ready` は、早すぎる時点で
戻ってしまいます。すると、シナリオの最初のステップ（`stable.row.1` を待つ、タイムアウト 10 秒）が、実質的な
準備完了ゲートになります。混雑した macOS ランナー上では、コールド起動が 10 秒以内に `stable.row.1` を
描画できませんでした。シナリオに決定性の欠陥はありません。準備完了ゲートが、誤った画面を受け入れて
いました。

2 件のうち 1 件は、`xcuitest (multi-touch)` も次のように失敗しました。

```
XcuitestChannelError: runner channel POST /gesture failed: timed out
```

この実行は BE-0207（#824）のマージ後です。そのため、一時的な失敗に対する再試行は、すでに入っていました。
それでもジェスチャは hard failure になりました。BE-0207 の分類が、配信後の書き込みを（正しく）決して
再試行しないからです。ランナーチャネルは、すべての呼び出しに 1 つのソケットタイムアウト窓
（`_SOCKET_TIMEOUT_SECONDS = 15`）を使います。瞬間的に詰まった読み取りは、再試行が吸収します。単に遅い
だけの書き込みには、頼れる再試行がありません。負荷の高いホスト上で XCUITest が合成する多点タッチの
イベントが、そのような書き込みです。15 秒を超える 1 回の遅延が、実行全体を沈めてしまいます。

どちらの失敗も、通信とタイミングの脆さであって、判定のシグナルではありません。何かが本当に壊れているか
どうかを何も伝えない赤いゲートは、まさにこの項目が解消する、不安定なゲートの損失です。

## 詳細設計

1. **`_await_ready` を名前空間に基づく判定にする**：ターゲットの `idNamespaces` をゲートに渡します。
   使える `readyWhen` セレクタがない場合は、素の要素数より強いシグナルを優先します。問い合わせで得た
   要素のうち **どれか 1 つでも id が宣言済みの名前空間に属したら**（`namespace_of(id) in idNamespaces`）、
   アプリは準備完了とみなします。SpringBoard の名前空間外のアイコンはゲートを満たさなくなるので、
   ゲートはアプリ自身を待ちます。準備完了の優先順位は、次のとおりです。
   - `readyWhen` セレクタ（最も強く、明示的）です。
   - 名前空間内の要素です。
   - 既存の「2 つ以上」の要素数です。名前空間を宣言しないターゲット（たとえば `-noax` アプリや Web）のための、
     変更のないフォールバックです。
2. **XCUITest チャネルにメソッド別のソケットタイムアウトを入れる**：1 つの窓を、
   `_SOCKET_TIMEOUT_SECONDS`（読み取り、15 秒のまま）と `_ACTUATION_TIMEOUT_SECONDS`（書き込み、30 秒）に
   分けます。`_timeout_for(method)` が、どちらかを選びます。
   - 読み取りは、一時的な詰まりに対して BE-0207 の再試行に頼ります。
   - 書き込みは、配信後に再試行できません。負荷の高いホスト上での遅い操作を許容するために、より長い
     有界な窓を 1 つ与えます。
   - どちらの窓も有界のままです。本当にハングしたランナーは、ハングし続けず、試行ごとに声高に失敗
     します。
3. **決定性の維持（プライム・ディレクティブ 1 と 2）**：どちらの変更も、実際の結果には触れません。
   - アプリが最後まで前面に来なければ、準備完了は締め切りで失敗します。
   - 再試行の使い果たしや、予算を超えた書き込みは、従来どおり同じ声高な `XcuitestChannelError` を
     送出します。
   - `stale` と `not-found` は、デコードされた結果のままで、再試行しません。
   - LLM はゲートに入りません。固定の `sleep` も加えません。準備完了は条件のポーリングで、タイムアウトは
     試行ごとの上限です。
4. **デバイスなしのユニットテスト**：準備完了の変更は、スクリプト化した fake driver に対する 4 つの
   ケースで検証します。
   - SpringBoard だけの画面は、ゲートを満たしてはなりません。
   - 名前空間内の要素が 1 つあれば、ゲートを満たします。
   - 名前空間のリストが空なら、要素数のヒューリスティックを維持します。
   - 明示的な `readyWhen` は、依然として優先します。

   タイムアウトの変更は、`_timeout_for` と、`http.client` の境界を fake にしてメソッド別の窓が接続に
   届くことを確認するテストで検証します。

## 検討した代替案

- **`_await_ready` に触れず、`showcase-swiftui` ターゲットに `readyWhen` を設定する案**：ターゲットごとの
  セレクタでは、スイートが使う 2 つの起動画面を両方はカバーできません。smoke は Stable タブを起動し、
  gestures はピンチ/回転の画面を起動します。ターゲットごとのセレクタは、1 つのアプリしか手当てできません。
  名前空間のシグナルはアプリ非依存です。どのターゲットもすでに宣言している `idNamespaces` を再利用する
  からです。識別子を持つすべてのターゲットのゲートを、一度に直せます。
- **準備完了やステップ 0 のタイムアウトを単に延ばす案**：1 つの不安定さを、より遅い不安定さに置き換える
  だけです。ゲートが SpringBoard を受け入れるのは止められません。後続のステップがどのみちタイムアウト
  するまでの待ち時間を、延ばすだけです。
- **書き込み操作を再試行する案（BE-0207 の冪等性の切り分けをやめる）**：応答が単にタイムアウトしただけの
  ジェスチャを、二重に適用しうるので、ゲートが守っている決定性を静かに壊します。却下しました。再試行
  できない書き込みには、より長い有界な窓が安全な手段です。
- **`_SOCKET_TIMEOUT_SECONDS` を全体的に大きくする案**：読み取りについても、ハングしたランナーの経路を
  長くしてしまいます。読み取りは、すでに再試行を持っています。メソッド別に分ければ、読み取りは短いまま
  にして、再試行できない書き込みだけを寛容にできます。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [x] `_await_ready` を名前空間に基づく判定にする（readyWhen → 名前空間内 → 要素数）。`idNamespaces` を呼び出し側に通す
- [x] XCUITest チャネルにメソッド別のソケットタイムアウトを入れる（`_timeout_for`：読み取り 15 秒、操作の書き込み 30 秒）
- [x] 決定性を維持する（声高な失敗とデコード済みの結果は変更しない）
- [x] 両方の変更に対するデバイスなしのユニットテスト

### ログ

- [#850](https://github.com/bajutsu-e2e/bajutsu/pull/850)：`_await_ready` を名前空間に基づく判定にしました。
  名前空間外の SpringBoard 画面はゲートを満たさなくなり、名前空間内の要素が 1 つあれば満たします。
  `idNamespaces` が空なら 2 つ以上の要素数を維持し、明示的な `readyWhen` は依然として優先します。
  あわせて、XCUITest チャネルにメソッド別のタイムアウトを入れました（`_timeout_for`）。読み取りは
  BE-0207 の再試行に支えられた短い 15 秒の窓のままです。操作の書き込みは配信後に再試行できないので、
  有界な 30 秒の窓を与えました。デバイスなしのユニットテストでカバーしています。

## 参考

- [`bajutsu/platform_lifecycle.py`](../../bajutsu/platform_lifecycle.py)：`_await_ready`、起動後の準備完了ゲート
- [`bajutsu/drivers/xcuitest.py`](../../bajutsu/drivers/xcuitest.py)：`_timeout_for`、`_SOCKET_TIMEOUT_SECONDS`、`_ACTUATION_TIMEOUT_SECONDS`、`_raw_http_transport`
- [`bajutsu/doctor.py`](../../bajutsu/doctor.py)：`namespace_of`、準備完了ゲートが再利用する id から名前空間への切り分け
- [`.github/workflows/e2e.yml`](../../.github/workflows/e2e.yml)：この項目が安定化させる `E2E (Simulator)` ゲート
- [BE-0207](../BE-0207-xcuitest-channel-transient-retry/BE-0207-xcuitest-channel-transient-retry-ja.md)：このタイムアウトの分割が補完する、一時的な失敗の再試行方針
- [BE-0049](../BE-0049-determinism-flakiness-audit/BE-0049-determinism-flakiness-audit-ja.md)：両方の変更が整合する決定性の立場（通信とタイミングは許容し、判定は決して許容しない）
