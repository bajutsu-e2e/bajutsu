[English](BE-XXXX-adb-tab-bar-navigation.md) · **日本語**

# BE-XXXX — adb で tab bar を操作して Android の全タブへ到達する

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-XXXX](BE-XXXX-adb-tab-bar-navigation-ja.md) |
| 提案者 | [@hirosassa](https://github.com/hirosassa) |
| 状態 | **提案** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| トピック | Platform expansion (Android / Web / Flutter) |
| 関連 | [BE-0007](../BE-0007-android-backend/BE-0007-android-backend-ja.md), [BE-0208](../BE-0208-android-emulator-e2e-ci/BE-0208-android-emulator-e2e-ci-ja.md), [BE-0107](../BE-0107-showcase-tab-navigation-no-launch-shortcut/BE-0107-showcase-tab-navigation-no-launch-shortcut-ja.md), [BE-0114](../BE-0114-driver-conformance-suite/BE-0114-driver-conformance-suite-ja.md) |
<!-- /BE-METADATA -->

## はじめに

[BE-0107](../BE-0107-showcase-tab-navigation-no-launch-shortcut/BE-0107-showcase-tab-navigation-no-launch-shortcut-ja.md)
が `SHOWCASE_TAB` 起動ショートカットを廃止して以降、showcase の共有シナリオ集合
([`demos/showcase/scenarios/`](../../demos/showcase/scenarios)) は、起動タブ以外のすべてのタブへ
native tab bar のタップで到達します。バックエンドをまたいで共通の 1 つのセレクタ、
`tap: { label: "Log", traits: [button] }` です。iOS はこのセレクタを XCUITest バックエンドで満たします。
XCUITest は各タブを label で指定できる button として列挙するからです。一方、Android の adb バックエンド
([BE-0007](../BE-0007-android-backend/BE-0007-android-backend-ja.md)) はまだ満たせません。物理的なタップ
自体は可能ですが、この共有セレクタが `uiautomator dump` のツリーに対して解決せず、`bajutsu run` はタップに
至る前に失敗します。その直接の帰結として、Android 実機 e2e レーン
([BE-0208](../BE-0208-android-emulator-e2e-ci/BE-0208-android-emulator-e2e-ci-ja.md)) は Stable タブで
完結するシナリオだけに縮小されました。本項目は、iOS と同じセレクタで tab bar を操作してすべてのタブへ
到達する能力を adb ドライバーに与え、共有シナリオの Android への移植性を回復し、BE-0208 のシナリオ拡張の
ブロックを解消します。

## 動機

BE-0107 が `SHOWCASE_TAB` を廃止したことで、Stable 以外のタブへの到達は、どのバックエンドでも
`{ label, traits: [button] }` による tab bar のタップになりました。adb バックエンドでは、タップの機構自体は
動きます (ドライバーはセレクタを要素の frame に解決し、その中心をタップします)。しかし、解決のほうが
失敗します。`uiautomator dump` は Compose の `NavigationBarItem` を、可視テキストを `label` チャネルに載せて
出力するため「Log」は一致します。Compose が tab bar をどう描画するか (`NavigationBarItem` であって
`android.widget.Button` ではないこと) から見て、この項目の widget `class` は、ドライバーの class から trait への
マッピング ([`_norm_class`](../../bajutsu/drivers/adb.py)) が `button` trait を割り当てるものではないと見込まれます。
そうであれば、共有セレクタの要求する `button` trait は生成されず、要素は label では見つかるものの trait で棄却され、
決定性の原則のとおり、中途半端に一致しただけの要素をタップするのではなく run が失敗します。この見込みは、修正の
設計に入る前に、作業項目 1 が実機の `uiautomator dump` で確定します。

その代償は具体的です。BE-0208 の Android e2e レーンは `search`、`data_driven`、`relaunch`、`system`、
および Log/Notices タブのフロー (`components`、`modals`、`gestures`、`controls`、`notices`) を除外して
います。これは共有集合の大半にあたり、Android 実機のカバレッジは `smoke`、`firstlook`、`navigation`
(いずれも Stable タブ) にとどまります。除外されたシナリオはどれも冒頭でタブを切り替えるので、tab bar を
操作できるようになるまで 1 つも実行できません。

これはドライバー層の移植性の欠落であり、まさにプライム原則 3 (アプリ非依存) が位置づける場所です。
シナリオは一度だけ記述され、どのバックエンドでも解決されなければならず、どのアプリ側の属性がセレクタを
満たすかは常にドライバーの内側に置かれ、シナリオには置かれません。iOS が `{ label, traits: [button] }` で
到達するタブに Android が到達できないのは、共有シナリオではなく adb ドライバーの欠落です。修正は決定性の
契約を保たなければなりません。曖昧なタブの一致は最初の候補をタップするのではなく失敗し、生の座標タップに
頼る修正もしません。

## 詳細設計

本作業は、まず実機のツリーの事実に立脚し、次にドライバーを直し、その修正が解放するカバレッジを回収する、
という順序を意図的にとります。触れるのは adb ドライバーと showcase の e2e 配線であり、共有シナリオは
変更しません (そこが要点です)。どの経路にも LLM を追加しません。

### 作業分解 (MECE)

1. **実機で tab bar のツリーを診断する。** Compose (`NavigationBarItem`) と Views
   (`BottomNavigationView`) の showcase tab bar で `uiautomator dump` を採取し、各タブ項目がどのチャネル
   (`class`、`text`、`content-desc`、`selected`、`clickable`) を露出するかを正確に確定します。マッピングの
   判断は、Compose がどう平坦化するかの想定ではなく、実際のツリーに立脚させます。
2. **adb ドライバーでバックエンド共通のタブセレクタを解決する。** `{ label, traits: [button] }` を adb
   バックエンドで正しいタブ項目に解決させます。XCUITest が各タブを label で指定できる button として露出する
   のに倣い、修正はドライバーの正規化 (trait/label のマッピング) に置き、アプリ非依存で、showcase 専用の
   場合分けを設けません。曖昧さは fail-fast のままにします。1 つのセレクタに 2 つのタブが一致したら、最初の
   1 つをタップせず例外にします。
3. **Android の両ツールキットをカバーする。** Compose と Views は tab bar を `uiautomator dump` 上で異なる
   widget class として露出します。解決は両方の showcase の双子 (`showcase-compose`、`showcase-views`) で
   成り立たせるか、残る差分を文書化して明示的にスコープ外とします。
4. **ブロックされていたシナリオを e2e レーンへ戻す。** tab bar を操作できるようになったら、除外していた共有
   シナリオを [`demos/showcase/android/Makefile`](../../demos/showcase/android/Makefile) の `E2E_SCENARIOS`
   へ、実機で通るものから順に戻します。まず `search`、`data_driven`、`relaunch`、`system`、続いて CI
   エミュレータのソフトウェア描画のタイミング次第で Log/Notices のフローです。これが BE-0208 の Unit 5 を
   解消します。
5. **ドライバー適合スイートにタブ移動を追加する。** ドライバー適合の契約
   ([BE-0114](../BE-0114-driver-conformance-suite/BE-0114-driver-conformance-suite-ja.md)) に tab bar 移動の
   ケースを加え、この能力を showcase レーンで暗黙に示すだけでなく、バックエンドをまたいで検査します。

## 検討した代替案

- **Android だけ起動時のタブショートカットを残す。** 却下します。BE-0107 が取り除いたフィクスチャの忠実性の
  欠落 (Log タブへ直接起動し、ユーザーが行うタブ切り替えを一切試さない Log タブ用シナリオ) をそのまま復活させ、
  さらに共有集合で Android を iOS から分岐させます。これはアプリ/バックエンド固有の例外であり、プライム原則 3
  が禁じます。
- **showcase のタブ項目に明示的な id を与え、共有シナリオを id ベースのタブタップに切り替える。** 却下します。
  共有シナリオがタブへ `{ label, traits: [button] }` で到達するのは、まさに 1 つのシナリオが iOS と Android の
  双方を駆動するためです。セレクタを id に変えると、シナリオがバックエンドごとに分岐し、バックエンド横断の保証が
  弱まります。変えるべき層はドライバーであってシナリオではありません。
- **tab bar を座標で幾何的にタップする。** 却下します。run の経路はセレクタしか解決しません (DESIGN「決定性
  優先」)。生の座標タップは指定可能な対象を持たず、「曖昧なセレクタは失敗する」契約を壊します。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [ ] 実機で Compose / Views の tab bar ツリーを `uiautomator dump` で診断する。
- [ ] adb ドライバーで `{ label, traits: [button] }` をタブ項目に解決する（曖昧さは fail-fast）。
- [ ] Android の両ツールキット（`showcase-compose`、`showcase-views`）をカバーする、または残る差分をスコープ外として文書化する。
- [ ] 除外していた共有シナリオを Android e2e レーンへ戻す（BE-0208 Unit 5）。
- [ ] ドライバー適合スイートにタブ移動のケースを追加する（BE-0114）。

## 参考

[BE-0007 — Android バックエンド](../BE-0007-android-backend/BE-0007-android-backend-ja.md)、
[BE-0208 — CI での Android 実機 e2e](../BE-0208-android-emulator-e2e-ci/BE-0208-android-emulator-e2e-ci-ja.md)、
[BE-0107 — showcase の全タブへナビゲーションで到達する](../BE-0107-showcase-tab-navigation-no-launch-shortcut/BE-0107-showcase-tab-navigation-no-launch-shortcut-ja.md)、
[BE-0114 — ドライバー適合スイート](../BE-0114-driver-conformance-suite/BE-0114-driver-conformance-suite-ja.md)、
[`bajutsu/drivers/adb.py`](../../bajutsu/drivers/adb.py)、
[`demos/showcase/android/compose/.../RootScreen.kt`](../../demos/showcase/android/compose/src/main/java/com/bajutsu/showcase/compose/RootScreen.kt)
