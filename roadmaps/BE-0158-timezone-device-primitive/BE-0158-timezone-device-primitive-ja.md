[English](BE-0158-timezone-device-primitive.md) · **日本語**

# BE-0158 — タイムゾーンのデバイスプリミティブ

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0158](BE-0158-timezone-device-primitive-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **保留** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0158") |
| トピック | シナリオ記述機能 |
| 関連 | [BE-0052](../BE-0052-device-state-timezone-clipboard-shake/BE-0052-device-state-timezone-clipboard-shake-ja.md) |
| 由来 | MagicPod |
<!-- /BE-METADATA -->

## はじめに

本項目は、シミュレータの**タイムゾーン**の固定を扱います。タイムゾーンは、
[BE-0052](../BE-0052-device-state-timezone-clipboard-shake/BE-0052-device-state-timezone-clipboard-shake-ja.md)
が提案したデバイス状態プリミティブのひとつでした。BE-0052 の実装トリアージで、信頼できる決定的な作動
手段が見つからなかったため、本項目として切り出しました。シェイクジェスチャは塞がっている理由が別
なので、別項目で扱います。本項目は、タイムゾーンの検証済みの手段を待ちます。

## 動機

日付に依存する UI、たとえば「今日」のヘッダやカウントダウン、スケジュール画面は、テストがデバイスの
ゾーンを固定できて初めて、複数のタイムゾーンにわたり検証できます。固定できなければ、結果は CI の
実行場所によってぶれます。同じシナリオが、あるマシンでは通り、別のマシンではホストのローカル時刻
だけを理由に落ちることになります。ゾーンを決定的に固定できれば、「Maestro はできるが Bajutsu は
できない」という安易な反論も取り除けます。Maestro は幅広いデバイス制御の語彙を備えており、Bajutsu は
BE-0035 の大半のプリミティブで、すでにそれに追いついています。

本項目を妨げているのは、実装の工数ではありません。デバイスごとに決定的に作動させる手段が、存在
しないことです。実行できたように見えて、UI が誤ったゾーンを表示し続けるプリミティブは、プリミティブが
ないよりも悪い状態です。何も証明しないグリーンな run を生むからです。それはまさに、決定性優先
(prime directive #2)が禁じるものです。

## 詳細設計

提案する表面は、BE-0052 から変わりません。

```yaml
- setTimezone: { id: "Asia/Tokyo" }          # デバイスのタイムゾーンを固定する
```

**塞がっている理由。** シミュレータにはデバイスごとのタイムゾーン制御がありません。

- `simctl` に `timezone` サブコマンドはなく、`idb` にもありません。シミュレータのタイムゾーンは
  **ホスト Mac から継承**します(`/etc/localtime`)。そのため、起動中のデバイスはホストのゾーンを
  返します。
- デバイス全体を動かせる唯一の作動手段は、**ホスト Mac** のタイムゾーンを変えること
  (`sudo systemsetup -settimezone`、または `/etc/localtime` の書き換え)です。この手段には3つの
  問題があります。グローバルであること、`sudo` を要すること、全シミュレータへ波及することです。開発機や CI の時計も書き換えます。シナリオごと・デバイスごとの分離を壊すもので、
  契約外です。
- launch 時の `SIMCTL_CHILD_TZ`(または `TZ` の launch-env)は、アプリプロセスの C ライブラリの
  `localtime` だけを設定します。iOS の日付 UI の多くは `TimeZone.current` /
  `NSTimeZone.systemTimeZone`(Core Foundation)を読みます。これは **`TZ` を無視**します。
  つまり「実行」はされても UI は変わらず、最も質の悪い暗黙の no-op になります。
- シミュレータの GUI メニューにタイムゾーン項目はないため、GUI 自動化(AppleScript、RocketSim)も
  役に立ちません。この経路自体の制約は、別項目の*シェイクのデバイスプリミティブ*で扱います。

成立する手段は、ホストに触れず、コマンドラインから、1つのシミュレータのデバイス全体の
`TimeZone.current` を動かせなければなりません。現時点で、そうした手段は知られていません。

**prime directive。** 将来の実装は、次の3つを満たさなければなりません。

- run／CI ゲートを AI 非依存に保つ(#1)。
- 機械チェック可能な結果を持つ決定的な作動とし、落ち着き待ちの sleep を置かない(#2)。
- タイムゾーン id を、ツールのコードではなくシナリオや `apps.<name>` config に置く(#3)。

## 検討した代替案

- **launch 時の `SIMCTL_CHILD_TZ` で出荷する。** 却下します。libc の `localtime` だけを動かし、
  iOS の日付 UI の多くが読む `TimeZone.current` には効きません。検証したいはずの UI に対して
  暗黙の no-op になり、決定性優先に反します。
- **ホスト Mac のタイムゾーン変更で出荷する。** 却下します。変更はグローバルで `sudo` を要し、
  すべてのシミュレータに波及します。ホストや CI の時計も書き換えます。デバイスの分離を壊し、
  契約外です。
- **アプリ内から近似する(タイムゾーンを固定する launch フラグ)。** 主要な機構としては却下します。
  負担を各対象アプリへ押し付け、アプリ非依存性を壊します。launch env は、純粋にアプリ固有の
  セットアップ用として引き続き利用できます。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な作業
> 分解を反映し(作業単位ごとに1つ)、ログは何がいつ変わったかを(古い順に)PR とともに記録します。

- [ ] ホストに依存せず、デバイスごとに、ヘッドレスで `TimeZone.current` を動かせる検証済みの作動手段。

[BE-0052](../BE-0052-device-state-timezone-clipboard-shake/BE-0052-device-state-timezone-clipboard-shake-ja.md)
の実装トリアージで切り出しました。検証済みの手段を待ちます。

## 参考

[BE-0052 — デバイス状態プリミティブ: タイムゾーン・クリップボード・シェイク](../BE-0052-device-state-timezone-clipboard-shake/BE-0052-device-state-timezone-clipboard-shake-ja.md)
から分割。BE-0052 自体は [BE-0035 — デバイス制御ステップ](../BE-0035-device-control-primitives/BE-0035-device-control-primitives-ja.md)
から分割したものです。[DESIGN §6.2](../../DESIGN.md)、`bajutsu/orchestrator/actions/handlers/device.py`、`bajutsu/env.py`
