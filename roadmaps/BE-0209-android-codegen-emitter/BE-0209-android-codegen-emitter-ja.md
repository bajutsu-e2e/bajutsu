[English](BE-0209-android-codegen-emitter.md) · **日本語**

# BE-0209 — Android codegen エミッタ（Espresso / UI Automator）

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0209](BE-0209-android-codegen-emitter-ja.md) |
| 提案者 | [@hirosassa](https://github.com/hirosassa) |
| 状態 | **提案** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0209") |
| トピック | codegen coverage |
| 関連 | [BE-0007](../BE-0007-android-backend/BE-0007-android-backend-ja.md), [BE-0083](../BE-0083-codegen-emitter-unification/BE-0083-codegen-emitter-unification-ja.md) |
<!-- /BE-METADATA -->

## はじめに

Bajutsu はシナリオを 2 つのターゲットのネイティブテストにトランスパイルします。iOS 向けの
XCUITest と、web 向けの Playwright です（`bajutsu/codegen_emit.py` の
`EMIT_TARGETS = ("xcuitest", "playwright")`）。Android バックエンド
（[BE-0007](../BE-0007-android-backend/BE-0007-android-backend-ja.md)）には codegen ターゲットが
なく、シナリオをネイティブの Android テストとして出力できません。本項目は 3 つ目のターゲットと
して Android エミッタ（Kotlin、Espresso または UI Automator）を追加し、iOS と Android の同等性の
ギャップのうち codegen 側を埋めます。

## 動機

[BE-0083](../BE-0083-codegen-emitter-unification/BE-0083-codegen-emitter-unification-ja.md) は、
新しいエミッタが自身のターゲット固有の行構文だけを供給すればよいように、共通のシナリオ walk を
すでに抽出しています。そしてその動機となる 3 つ目のエミッタとして「Android codegen ターゲットの
追加」を名指ししていました。統合の作業は済んでいるので、本項目は小さく構造的な残りです。新しい
走査ではなく、既存の walk の上に載る行単位のエミッタです。codegen は決定論的で LLM を使わない経路
（シナリオからソースへの構造的なマッピング）なので、`run`/CI ゲートの外にとどまり、prime
directive の枠内に収まります。

## 詳細設計

### ターゲットの方言

Espresso（`onView(withId(...)).perform(...)`）と UI Automator
（`device.findObject(By.res(...)).click()`）がどちらも候補です。UI Automator は adb ドライバの
座標／id モデルと、アプリをプロセス外からブラックボックスとして見る視点に、より直接的に対応する
ので、バックエンドが実際に行っていることの近い双子です。Espresso は、既知のアプリに対する
インストルメンテーションテストとしてはより自然に読めます。方言の選択がここでの唯一の設計判断で
あり、エミッタを実装する時点で決めます。

### 作業分解（MECE）

1. **方言を選ぶ**（UI Automator か Espresso か）。根拠を記録します。
2. **行単位のエミッタ**（`bajutsu/codegen_espresso.py`、あるいは UI Automator にちなんだ名前の
   モジュール）を BE-0083 の共通 walk の上に作ります。起動行、各ステップ（`tap`／`type`／`swipe`／
   system back／deeplink）、`expect` ブロックを、選んだ Kotlin の方言で出力します。
3. **セレクタのマッピング**を `resource-id`／`text`／`content-desc` に対応させ、adb ドライバ自身の
   マッピングをなぞることで、生成されたテストがドライバと同じ方法で要素を解決するようにします。
4. **ターゲットを登録する**。`EMIT_TARGETS` と `codegen_emit` のディスパッチに登録し、Android
   ターゲットに限定します（Playwright ターゲットが web ターゲットに限定されているのと同じです）。
5. **検証**。シナリオ fixture に対する golden 出力テストを byte-for-byte で行います。XCUITest と
   Playwright のエミッタと同じく、実機のいらない純粋な fast ゲートのチェックです。

## 検討した代替案

- **Espresso か UI Automator か**。上記のとおり方言の判断として扱い、別項目としては切り出しません。
- **Appium クライアントの codegen ターゲット（Python／Java）**。先送りします。これは BE-0007 が
  同じく先送りしている Appium アクチュエータの代替案と対になります。最初の Android エミッタは
  出荷済みの adb バックエンドのモデルに合わせるので、UI Automator／Espresso が自然な最初の
  ターゲットです。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [ ] 方言を選ぶ（UI Automator か Espresso か）。根拠つき。
- [ ] BE-0083 の共通 walk の上に載る行単位のエミッタ（`bajutsu/codegen_espresso.py`）。
- [ ] `resource-id`／`text`／`content-desc` へのセレクタのマッピング。
- [ ] `EMIT_TARGETS`／`codegen_emit` へのターゲット登録。Android ターゲットに限定。
- [ ] 検証：シナリオ fixture に対する byte-for-byte の golden 出力テスト。

## 参考

[BE-0007 — Android バックエンド](../BE-0007-android-backend/BE-0007-android-backend-ja.md)、
[BE-0083 — codegen エミッタを共通のシナリオ walk の背後に統合する](../BE-0083-codegen-emitter-unification/BE-0083-codegen-emitter-unification-ja.md)、
`bajutsu/codegen_emit.py`
