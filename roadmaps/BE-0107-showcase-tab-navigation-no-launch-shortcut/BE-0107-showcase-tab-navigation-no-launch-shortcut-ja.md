[English](BE-0107-showcase-tab-navigation-no-launch-shortcut.md) · **日本語**

# BE-0107 — showcase の各タブへ、起動時の近道ではなくナビゲーションで到達する

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0107](BE-0107-showcase-tab-navigation-no-launch-shortcut-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **提案** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0107") |
| トピック | Dogfood フィクスチャ（デモアプリ） |
| 関連 | [BE-0079](../BE-0079-consolidate-demos-on-showcase/BE-0079-consolidate-demos-on-showcase-ja.md), [BE-0019](../BE-0019-xcuitest-backend/BE-0019-xcuitest-backend-ja.md) |
| 由来 | Dogfooding |
<!-- /BE-METADATA -->

## はじめに

[BE-0079](../BE-0079-consolidate-demos-on-showcase/BE-0079-consolidate-demos-on-showcase-ja.md)
は、showcase で画面や状態へ起動時に直行する近道を取り除き始めました。データを注入する `SHOWCASE_SEED`
（カタログは固定）と、deeplink による*詳細画面の push*（馬や通知は行をタップしてのみ到達）を廃止しています。
一方で近道を 1 つだけ残しました。起動時に初期タブを選ぶ `SHOWCASE_TAB` です。これは、代替手段（シナリオが
タブバーをタップしてタブ間を移動する）が idb backend ではまだ安定しないためです。本アイテムはその残りを片付け、
すべてのタブを**タブバーの操作**で到達可能にし、`SHOWCASE_TAB` を退役させます。

## 動機

**本物のテストは、出荷する UI を操作するのであって、ナビゲーションを飛び越えて瞬間移動しません。**
`SHOWCASE_TAB` があると、Log タブ向けのシナリオは Log タブ*の上に*起動し、そこへ辿り着くためにユーザが行う
ナビゲーションを一切行使しません。これはフィクスチャの忠実度の欠落です。第一級の操作であるタブ切り替え自体が
テストされず、そこにリグレッションが入っても気付かれません。

**idb はネイティブのタブバーをタップできず、それこそが `SHOWCASE_TAB` が存在する理由です。** SwiftUI の
`TabView` / UIKit の `UITabBarController` は idb の `describe-all` では子を持たない不透明な `Tab Bar` グループ 1 つに
潰れ（実機で確認済み）、各タブはセレクタで指定できません。座標タップも選択肢にありません（run 経路はセレクタしか
解決しません。DESIGN の「決定性優先」）。`SHOWCASE_TAB` はその決定的な回避策です。したがってこれを外すには、タブに
到達*できる* backend が要ります。

**[BE-0019](../BE-0019-xcuitest-backend/BE-0019-xcuitest-backend-ja.md) がその backend です。**
その XCUITest runner はネイティブの各タブをラベルで指定できるボタンとして列挙するので、
`tap: {label: "Log", traits: [button]}` でネイティブのタブバー上のタブを切り替えられます（`demos/showcase/scenarios-xcuitest/tabs.yaml`
が `-noax` プロダクトで既に実演しています）。本アイテムは、BE-0019 が showcase の機能シナリオを走らせられる程度に
安定していることを前提とします。BE-0079 の時点では runner の `/elements` クエリが遅く、環境によってはタイムアウトや
接続切断が起きました。これは BE-0019 自身が残課題として挙げている性能・安定性の作業です。安定する前にこれへ乗ると
showcase のオンデバイス経路が不安定になり、決定性の指令に反します。

## 詳細設計

- **`SHOWCASE_TAB` を退役させる。** アプリは常に Stable タブで起動し（両ツールキット）、`AppModel` の env 解釈を
  落とします。`SHOWCASE_UITEST`（アニメーション無効化は決定性の補助であって画面の近道ではない）は残します。タブを
  選択する deeplink（`…://search` など）の扱いを決めます。あわせて落とす（起動時のタブジャンプはどれも近道）か、
  deeplink 機能のデモとしてのみ残すか、のいずれかです。
- **タブをまたぐシナリオを XCUITest backend へ移す。** Stable タブを離れるシナリオは先頭に
  `tap: {label: "<タブ>", traits: [button]}` を足し、`--backend ios` で走らせます。a11y プロダクトは識別子契約を
  保つので、タブ内では従来どおり `id` で要素を指定し、タブ切り替えだけをラベルで指定します。
- **idb と XCUITest の分担を確定する。** どの showcase シナリオを idb に残し（idb 互換性モニタが必要とする
  Stable 中心の smoke/golden）、どれを XCUITest へ移すか（タブをまたぐもの全部）を決め、`e2e.yml` / `idb-monitor.yml`
  を配線します。ここが要で、idb 互換性のシグナル（BE-0005 / BE-0006）を後退させてはいけません。
- **ナビゲーションで到達する golden を録り直し**、`SPEC.md` §3〜§5（英日両方）を更新します。`SHOWCASE_TAB` は無くなり、
  タブはタップで到達します。

## 検討した代替案

- **ボタン式のカスタムタブバー**（各タブを `tab.<name>` id 付きの `Button` にする。showcase の segmented control と
  同じ）。本アイテムでは却下します。idb でタブをタップできるようにはなりますが、フィクスチャが行使するために存在する
  *ネイティブ*の `TabView` / `UITabBarController` を手製の代替物に置き換えてしまい、忠実度が落ちます。ネイティブのバーを
  XCUITest で操作するほうがフィクスチャを正直に保てます。
- **`SHOWCASE_TAB` を残し続ける。** 却下します。タブのナビゲーションが恒久的に未テストのまま残り、BE-0079 が掲げた
  「画面へ起動時に直行する近道を持たない」目標に反します。
- **タブバー領域を座標でタップする。** 却下します。生の座標は、セレクタのみ・決定性優先の run 経路に反します。

## 進捗

- [ ] `SHOWCASE_TAB` を退役（両ツールキット）。deeplink のタブ選択の扱いを確定。
- [ ] タブをまたぐシナリオを `--backend ios` へ移し、idb/XCUITest の分担と CI 配線を確定。
- [ ] ナビゲーション到達の golden を録り直し、`SPEC.md` §3〜§5（英日）を更新。

## 参考

- [BE-0079 — デモ／dogfood 用アプリを showcase 群へ統合する](../BE-0079-consolidate-demos-on-showcase/BE-0079-consolidate-demos-on-showcase-ja.md) — seed / deeplink-push の近道を除去し、タブの近道は本アイテムへ先送りしました
- [BE-0019 — XCUITest backend](../BE-0019-xcuitest-backend/BE-0019-xcuitest-backend-ja.md) — ネイティブのタブバーに到達する backend。その性能・安定性が本アイテムの前提です
- [BE-0006 — idb 要素ツリーの正規化](../BE-0006-idb-element-tree-normalization/BE-0006-idb-element-tree-normalization-ja.md) · [BE-0005 — idb バージョン監視](../BE-0005-idb-companion-version-monitoring/BE-0005-idb-companion-version-monitoring-ja.md) — 分担が保つべき idb 互換性シグナル
