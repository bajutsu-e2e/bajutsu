[English](BE-0157-shake-device-primitive.md) · **日本語**

# BE-0157 — シェイクのデバイスプリミティブ

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0157](BE-0157-shake-device-primitive-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **提案（保留）** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0157") |
| トピック | Candidates from competitive research (MagicPod / Autify) |
| 関連 | [BE-0052](../BE-0052-device-state-timezone-clipboard-shake/BE-0052-device-state-timezone-clipboard-shake-ja.md) |
| 由来 | MagicPod |
<!-- /BE-METADATA -->

## はじめに

**シェイク**ジェスチャのトリガです。
[BE-0052](../BE-0052-device-state-timezone-clipboard-shake/BE-0052-device-state-timezone-clipboard-shake-ja.md)
が提案したデバイス状態プリミティブのひとつでした。BE-0052 の実装トリアージで、信頼できる決定的な
ヘッドレスの作動手段が見つからなかったため、本項目として切り出しました。タイムゾーンは塞がっている
理由が別であるため、別項目で扱い、本項目はシェイクの検証済みの手段を待ちます。

## 動機

一部のアプリは「取り消し」やデバッグメニューをシェイクジェスチャに結びつけていますが、現状これを
トリガする手段がありません。この欠落を埋めれば、「Maestro はできるが Bajutsu はできない」という
安易な反論も取り除けます。Bajutsu は BE-0035 の大半のプリミティブで、Maestro が備える幅広いデバイス
制御の語彙にすでに追いついているからです。

これを妨げているのは、実装の工数ではなく、ヘッドレスに動く決定的な作動手段が存在しないことです。
GUI ウィンドウを開いて権限を手動で許可しなければ動かないプリミティブは、GUI なしの素の `simctl boot`
だけで動く他のデバイス制御一式とは、根本的に種類が異なります。何が欠けているかを正確に記録しておけば、
将来ヘッドレスな手段が現れたときに道を開いたままにでき、GUI 依存の経路を今採用するかどうかの判断を
迫らずに済みます。

## 詳細設計

提案する表面は BE-0052 から変わりません。

```yaml
- shake: {}                                   # シェイクジェスチャ
```

**塞がっている理由。** シェイクジェスチャにはヘッドレスな作動手段がありません。

- `simctl` に `shake` サブコマンドはなく、`idb` にも shake コマンドはありません。シェイクは
  シミュレータの GUI メニュー項目(Device ▸ Shake)です。
- **GUI 自動化**でトリガはできます。AppleScript／System Events でメニューをクリックする方法や、
  RocketSim のような第三者ツールを使う方法です。この経路は決定的で(LLM を使いません)、prime
  directive #1 には反しません。ただし、シミュレータの **GUI アプリが起動していること**と、操作する
  プロセスへの**アクセシビリティ権限**が必要です。メニューのロケールやレイアウトに脆く、**フォーカス
  中**のシミュレータに作用するため複数デバイス起動時は非決定的で、**ヘッドレス CI では動きません**
  (GUI なしの `simctl boot`)。とりわけ RocketSim は、シェイクもタイムゾーンも CLI に公開しておらず
  (公開されているのは tap／swipe／type／button／inspect のみ)、Mac アプリとアクセシビリティを要し、
  さらに有償・クローズドソースの依存を `run` パスに加えます。この用途で素の AppleScript を上回るもの
  ではありません。

成立する手段は、ヘッドレスに(GUI もアクセシビリティ権限もなしに)シェイクをトリガし、特定のデバイス
を対象にできなければなりません。現時点でそうした手段は知られていません。将来、明示的でオプトインの、
ローカル限定の escape hatch として GUI 自動化でシェイクを採用する場合は、GUI やアクセシビリティ権限が
ないところではきれいに失敗し(既存のデバイス制御ステップが fake ドライバや並列実行でそうするのと同
様)、ヘッドレス CI では利用不可であると明記しなければなりません。

**codegen。** シェイクは `simctl` を持たない(あるいは GUI 専用の)ままです。そのため、他のデバイス
制御ステップと同様、[BE-0026](../BE-0026-shrink-unsupported-syntax/BE-0026-shrink-unsupported-syntax-ja.md)
と整合する形で、将来の実装は忠実な XCUITest ステップではなく、コマンドを明記したラベル付き `// TODO`
を出力することになります。

**prime directive。** 将来の実装は、run／CI ゲートを AI 非依存に保ち(#1)、機械チェック可能な結果を
持つ決定的な作動で落ち着き待ちの sleep を置かず(#2)、アプリごとのコードを持ち込まない(#3)ものでなけ
ればなりません。

## 検討した代替案

- **AppleScript／RocketSim の GUI 自動化で今すぐ出荷する。** 採用せず保留します。シミュレータの GUI
  とアクセシビリティ権限を要し、ヘッドレス CI で利用できず、RocketSim の場合は有償の第三者依存を
  `run` パスに加えます。将来採用する場合は、ヘッドレスなプリミティブではなく、明示的できれいに失敗
  するローカル限定の escape hatch でなければなりません。
- **アプリ内から近似する(シェイクを模すデバッグ deeplink)。** 主要な機構としては却下します。負担を
  各対象アプリへ押し付け、アプリ非依存性を壊します。launch env は、純粋にアプリ固有のセットアップ用
  として引き続き利用できます。
- **アラートガードの vision パス(スクリーンショット + タップ)でシステムを操作する。** 却下します。
  このパスは AI フォールバックであり、決定的な run ゲートには決して入れてはなりません(prime
  directive #1)。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な作業
> 分解を反映し(作業単位ごとに1つ)、ログは何がいつ変わったかを(古い順に)PR とともに記録します。

- [ ] 特定のデバイスを対象にできるヘッドレスな検証済みの作動手段(あるいは、明示的できれいに失敗する、CI では利用不可と明記したローカル限定の GUI 自動化 escape hatch)。

[BE-0052](../BE-0052-device-state-timezone-clipboard-shake/BE-0052-device-state-timezone-clipboard-shake-ja.md)
の実装トリアージで切り出しました。検証済みの手段を待ちます。

## 参考

[BE-0052 — デバイス状態プリミティブ: タイムゾーン・クリップボード・シェイク](../BE-0052-device-state-timezone-clipboard-shake/BE-0052-device-state-timezone-clipboard-shake-ja.md)
から分割。BE-0052 自体は [BE-0035 — デバイス制御ステップ](../BE-0035-device-control-primitives/BE-0035-device-control-primitives-ja.md)
から分割したものです。[DESIGN §6.2](../../../DESIGN.md)、`bajutsu/orchestrator/actions/handlers/device.py`、`bajutsu/env.py`
