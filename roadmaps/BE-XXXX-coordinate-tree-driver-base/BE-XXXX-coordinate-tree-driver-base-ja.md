[English](BE-XXXX-coordinate-tree-driver-base.md) · **日本語**

# BE-XXXX — idb と adb 向けに共有の CoordinateTreeDriver 基底クラスを抽出する

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-XXXX](BE-XXXX-coordinate-tree-driver-base-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **提案** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| トピック | コードベース品質・技術的負債 |
<!-- /BE-METADATA -->

## はじめに

`IdbDriver`（`bajutsu/drivers/idb.py`）と `AdbDriver`（`bajutsu/drivers/adb.py`）は、座標ベースの
デバイスバックエンド二つです。どちらもデバイスからアクセシビリティツリーをダンプし、それを
`Element` へ正規化したうえで、解決したフレームの中心をタップして操作します。この二つが持つ
決定性に直結する読み取り経路、つまり一時的な空ツリーへのリトライ、安定待ちのループ、安定キーへの
射影、未検出時の解決ループは、二つのファイルのあいだでほぼ一字一句同一です。本項目は、この共有
ロジックを一つの基底クラス `CoordinateTreeDriver` へ抽出し、各バックエンドが自分自身のツリー
取得方法とアクチュエーターだけを供給すればよい形にすることを提案します。

## 動機

二つのバックエンドは、ドライバ層のなかでも決定性がもっとも重要な部分、つまり読み取った直後の
ツリーが本物の画面なのか、それともデバイスの遷移途中に生じた一時的なものなのかを判断するロジック
に、90 行ほどのほぼ一字一句の重複を抱えています。

- 6 つのチューニング定数のうち 5 つ（`_READY_MIN` / `_EMPTY_RETRIES` / `_EMPTY_BACKOFF_S` /
  `_EMPTY_BACKOFF_MAX_S` / `_SETTLE_MAX_POLLS`）は値もコメントも同一です
  （`bajutsu/drivers/idb.py:213-218`、`bajutsu/drivers/adb.py:202-215`）。`_SETTLE_POLL_S` は
  その後乖離しており（idb は `0.05`、adb は `0.0`。BE-0234 で adb は約 2.4 秒の読み取り自体が
  ループを律速するため poll を 0 に調整しました）、共有基底ではこの 1 つだけをバックエンドごとの
  値として残します。
- 型エイリアス `_StableKey` も同一です（`bajutsu/drivers/idb.py:24`、`bajutsu/drivers/adb.py:64`）。
- `query()` のリトライループは、包んでいる describe 呼び出し以外は一字一句同一です
  （`bajutsu/drivers/idb.py:229-246`、`bajutsu/drivers/adb.py:248-263`）。
- `_is_transient_empty`、`_empty_backoff`、`_settle`、`_stable_key`、`_resolve` はいずれも
  同一のロジックです。`AdbDriver._settle` のドキュメンテーション文字列自体が「idb's logic」と
  書いており（`bajutsu/drivers/adb.py:292-311`）、この重複を自ら言い当てています。

このロジックが共有されず重複したままだと、一時的な空ツリーの判定を直す作業、たとえば新しい
フレーキーな挙動が判明してバックオフの上限を締め直す、`_READY_MIN` を調整するといった修正が、
どちらかのバックエンドを触っている人によって二つのファイルへ二重に加えられることになります。
二回目の修正を強制する仕組みは何もなく、二つの読み取り経路がアプリ側の事情と無関係に静かに
乖離していく可能性があります。あるシナリオが、アプリの違いに由来しない理由で iOS と Android で
異なる挙動を示すことすら起こり得ます。これはドライバ層のなかでもっとも規模が大きく、もっとも
正しさに直結する重複であり、一箇所へ引き上げることで「二重に直すことを忘れない」から「一度直せば
両方のバックエンドが引き継ぐ」へと変わります。

## 詳細設計

作業は挙動を変えない純粋な引き上げであり、独立した単位に分けられます。

1. **`bajutsu/drivers/` に `CoordinateTreeDriver` を導入する。**（`base.py` と並ぶ独自の
   モジュール、たとえば `coordinate_tree.py` に置きます。）現在共有している 5 つのチューニング
   定数（`_SETTLE_POLL_S` はバックエンドごとの値として残します）、`_StableKey` エイリアス、
   `query()`、`_settle`、`_stable_key`、`_is_transient_empty`、
   `_empty_backoff`、`_resolve` をすべてここに持たせます。これらは現状コンストラクタが管理する
   状態（`_max_seen`、`_last_stable_key`）を閉じ込めているため、その状態も基底クラス側が持ちます。
2. **基底クラスに、抽象フックを一つだけ与える（`_describe()`）。** (1) の処理はすでにいずれも
   `_describe()` を呼び出しています（`idb.py:285-286`、`adb.py:265-266`）。これを抽象メソッド
   （または `NotImplementedError` を送出するスタブ）にすることが、共有ロジックとバックエンド
   固有の argv・パース処理とのあいだの唯一の境界になります。各サブクラスは自分自身の describe
   だけを実装します。idb なら `ui describe-all` と JSON パース（`parse_describe_all`）、adb なら
   `uiautomator dump` と XML パース（`parse_hierarchy`）です。
3. **`IdbDriver` と `AdbDriver` を基底クラスの子クラスに付け替え**、それぞれから共有部分を
   削除し、本当にバックエンド固有のものだけを残します。idb 側は tap・swipe・text の argv
   ビルダーと gRPC 経由のコンパニオン text 処理、adb 側はスクロールしての表示範囲内取得の
   リトライ（`_scroll_into_view` / `_scroll_toward`。idb には対応する処理がありません）、
   sendevent によるダブルタップ処理、そして自分自身のアクチュエーターです。`_resolve` は
   共有のまま残します。adb の `_resolve_frame_and_screen` はこの上にスクロールしての表示範囲内
   取得を重ねていますが、それは adb 固有の層であり、共有の `_resolve` を重複させるのではなく
   その上に組み合わさっているだけだからです。
4. **`XcuitestDriver` と `PlaywrightDriver` には手を入れない。** どちらも自分自身の読み取り
   モデルを持っています。XCUITest はネイティブの条件待ち機能、Playwright は DOM クエリで、
   どちらも一時的な空ツリー・安定待ちに相当するヒューリスティックを持たないため、この引き上げの
   対象外とし、新しい基底クラスへ無理に載せるべきではありません。
5. **両方のバックエンドで共有の挙動が同一であることを確認するテストを追加する。** 一時的な
   空ツリーへのリトライ（劣化したツリーの後により豊かなツリーが続く場合）、指数バックオフの
   スケジュール、安定待ちループのキャッシュヒット・キャッシュミス双方の経路について、
   `IdbDriver` と `AdbDriver` のフェイクをまたいでパラメータ化する（あるいは何らかの形で
   共有する）ことで、基底クラスへの将来の変更が、各ドライバが自分自身に持つテストのコピーでは
   なく、両方のサブクラスに対してまとめて検証されるようにします。

決定性についての補足です。`_resolve` の意味、つまり一意な一致が必要であり、曖昧な一致（2 件
以上）は `AmbiguousSelector` によって即座に失敗するという性質は、この引き上げによって変わり
ません。本項目はコードを移動するのであって、「見つかった」「曖昧である」の意味を変えるのでは
ありません。どちらのバックエンドの読み取り経路についても、既存のテストは変更なしに新しい基底
クラスに対して通るはずであり、それ自体がこの引き上げが挙動を保っていることの裏付けになります。

## 検討した代替案

- **基底クラスではなくミックスインにする。** 共有する状態とメソッドは、どちらの形でも持たせ
  られます。ミックスインであれば、`IdbDriver` / `AdbDriver` が将来何か別の基底クラスと組み合わせる
  必要が生じたときに柔軟です。本項目では、抽象フック `_describe()` が「サブクラスが完成させる
  テンプレートメソッド」として自然に読めることから、素直な基底クラスを選んでいます。ただし、
  将来的に多重継承が明らかに有利になる事情が生じれば、ミックスインも妥当な代替案です。
- **規約によって重複の乖離を防ぐ（お互いを指すコメントを置くだけ）。** 却下しました。これは
  まさに現状そのものです。`AdbDriver._settle` のドキュメンテーション文字列はすでに「idb's
  logic」というコメントレベルの言及を持っていますが、本項目が問題視している乖離のリスクは、
  まさに規約だけでは二回目の修正を強制できないという点にあります。
- **重複を残したまま、バックエンド横断のテストだけを追加する。** 却下しました。テストは事後的に
  乖離を検出できますが、乖離の原因そのものである二重修正の負担は取り除けません。引き上げと
  テストは代替関係ではなく補完関係にあります（詳細設計の手順 5 で、テストを別項目に切り出さず
  本項目に含めているのはこのためです）。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [ ] 共有の定数、`query()`、`_settle`、`_stable_key`、`_is_transient_empty`、`_empty_backoff`、
      `_resolve` を持つ `CoordinateTreeDriver` を導入する。
- [ ] 基底クラスに抽象フック `_describe()` を一つだけ与える。
- [ ] `IdbDriver` と `AdbDriver` を基底クラスへ付け替え、共有部分を削除する。
- [ ] `XcuitestDriver` と `PlaywrightDriver` には手を入れないことを確認する（対象外）。
- [ ] 両方のバックエンドで共有の一時的空ツリー・安定待ちの挙動が同一であることを確認する
      テストを追加する。

## 参考

- [`bajutsu/drivers/idb.py:209-321`](../../bajutsu/drivers/idb.py) — `IdbDriver` の読み取り
  経路。重複している片方のコピーです。
- [`bajutsu/drivers/adb.py:194-335`](../../bajutsu/drivers/adb.py) — `AdbDriver` の読み取り
  経路。もう片方のコピーです。
- [`bajutsu/drivers/base.py`](../../bajutsu/drivers/base.py) — `resolve_unique` / `find_all`。
  `_resolve` が呼び出す共有のセレクター解決の中核であり、本項目はここには手を入れません。
- [BE-0118 — Unify the wait_for polling contract across
  drivers](../BE-0118-wait-for-contract-unification/BE-0118-wait-for-contract-unification.md)
  — `Driver.wait_for` のみを single-shot 契約へ統一した、範囲の狭い先行事例です。本項目はより広く、共有の
  `query`／安定待ち／`_resolve` の仕組みを引き上げるもので、これに依存するのではなく補完する関係にあります。
- 2026 年 7 月のコードベース分析（技術的負債の棚卸し）に由来します。
