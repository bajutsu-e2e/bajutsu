[English](BE-0042-platform-backend-registry.md) · **日本語**

# BE-0042 — プラットフォーム対応の backend レジストリと選択

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0042](BE-0042-platform-backend-registry-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| 実装 PR | PR 単位の履歴より前（初期インポートの一部・単一 PR なし） |
| トピック | プラットフォーム拡張（着手済みスライス） |
<!-- /BE-METADATA -->

## はじめに

マルチプラットフォーム化（[BE-0009](../../proposals/BE-0009-cross-platform-abstractions/BE-0009-cross-platform-abstractions-ja.md)）の最初のスライスはすでに着手済みです。backend の選択は、単一のハードコードされた iOS actuator ではなく **プラットフォームレジストリ** を起点にします。`--backend` と config の `backend:` は、bare な actuator 名（`idb` など）に加えて **プラットフォームトークン**（`ios` / `android` / `web` / `fake`）を受け付けます。プラットフォームは安定度順の actuator 列に展開され、選ばれるのはこの環境で **実装済みかつ利用可能** な最初の actuator です。これは、シナリオ、config スキーマ、決定的コアに触れずに 2 つ目のプラットフォームを差し込めるようにするための、選択側の地ならしです。

## 動機

マルチプラットフォーム化とは、既存の `Driver` シームの背後に、プラットフォームごとの三点セット（actuator、environment manager、安定 id の規約）を追加することです。決定的な背骨はそのまま保ちます（[BE-0009](../../proposals/BE-0009-cross-platform-abstractions/BE-0009-cross-platform-abstractions-ja.md)）。その前段として、backend の *選択* が「iOS = idb」という前提をやめる必要がありました。すなわち (1) プラットフォームを第一級のトークンとして扱う、(2) プラットフォームを候補 actuator の順序付きリストへ対応づける（こうすれば、XCUITest のような豊富な iOS actuator を config を変えずに後から `idb` より優先できます。[BE-0019](../../proposals/BE-0019-xcuitest-backend/BE-0019-xcuitest-backend-ja.md) を参照）、(3) 認識はしているがまだ未実装のプラットフォームには、汎用の失敗ではなく *明確な*「未実装」を返す、の 3 点です。このスライスを先に入れることで、後続のプラットフォーム作業が追加的なものに収まります。

## 詳細設計

実装は [`bajutsu/backends.py`](../../../bajutsu/backends.py)。[drivers → backend 選択](../../../docs/ja/drivers.md#バックエンド選択と-actuator) も参照。

プラットフォームレジストリは、各プラットフォームトークンをその actuator 列（安定度の高い順）へ対応づけます:

```python
PLATFORMS = {
    "ios":     ("idb",),   # later: ("xcuitest", "idb")
    "android": ("adb",),
    "web":     ("playwright",),
    "fake":    ("fake",),
}
```

- **展開。** プラットフォームトークンはその actuator 列へ展開され、bare な actuator はそれ自身を表します。`--backend ios`（または `backend: [ios]`）は今日では `idb` に解決され、より豊富な iOS actuator が入れば、シナリオや config を変えずにそれを拾います。
- **選択。** `select_actuator` は展開後のリストを辿り、**実装済みかつ利用可能** な最初の actuator を返します。利用可能性は「実装済み **かつ** 実行ファイルが `PATH` 上にある」ことです（`fake` は常に利用可能で実行ファイル不要）。今日は `IMPLEMENTED = {idb, fake}` です。
- **未実装プラットフォームへの明確なエラー。** レジストリには認識されているがまだ driver のない `android` / `web` を要求すると、`"not implemented yet"`（未実装）エラーになり、プラットフォーム拡張のロードマップを指します。これは「利用可能な actuator がない」とは区別されます。`make_driver` 経由でそうした driver を構築しようとした場合も、汎用の失敗ではなく `NotImplementedError` になります。
- **前方互換。** 本当に未知のトークンは失敗させずにスキップします。古いビルドでも、将来の backend を列挙した config を実行でき、理解できる backend へフォールスルーします。

対応する config 形状は、`apps.<name>` への **`platform` ディスクリミネータ** とプラットフォーム別ターゲットフィールドです。決定的な解決順序（`defaults < app < scenario`）は変わりません:

```yaml
defaults:
  platform: ios                 # 既定値。アプリ単位で下記のように上書き

apps:
  sample-ios:
    platform: ios
    backend:  [idb]
    bundleId: com.bajutsu.sample
  sample-android:
    platform: android
    backend:  [adb]
    package:  com.bajutsu.sample          # bundleId の対応物
  sample-web:
    platform: web
    backend:  [playwright]
    baseUrl:  https://app.example.test     # bundleId の対応物
```

`platform` はどの **environment manager** と **backend レジストリ** を使うかを選びます。残りのスキーマ（namespaces、redact、setup、capture）は共有のままです。

**済んでいること / 残っていること。** 着手済みなのは、プラットフォームレジストリ、プラットフォームトークンの展開、実装済みかつ利用可能による選択、未実装プラットフォームへの明確なエラーです（今日の実装済み actuator は `idb` / `fake` のみ）。実際の 2 つ目のプラットフォームに向けて残るのは、三点セットの残りです。プラットフォーム別の **environment manager**（`simctl` の対応物）と **actuator driver**（[BE-0007](../../proposals/BE-0007-android-backend/BE-0007-android-backend-ja.md) の `adb`、[BE-0041](../../in-progress/BE-0041-web-playwright-backend/BE-0041-web-playwright-backend-ja.md) の `playwright`）、そして上記の明示的な `platform` config フィールドです。

## 検討した代替案

- **actuator だけを指す単一の `backend` 文字列**（従来の形）。「XCUITest を優先し idb にフォールバック」や「これは Web プラットフォーム」を、actuator の選択をすべての config やシナリオへ漏らさずに表現できないため却下。
- **未知トークンを即座に失敗させる。** 前方互換のため却下。将来のビルド向けに書かれた config も、理解できる backend へフォールスルーして古いビルドで動くべきです。

## 参考

[`bajutsu/backends.py`](../../../bajutsu/backends.py)、[drivers.md](../../../docs/ja/drivers.md#バックエンド選択と-actuator)、[BE-0009](../../proposals/BE-0009-cross-platform-abstractions/BE-0009-cross-platform-abstractions-ja.md)、[BE-0007](../../proposals/BE-0007-android-backend/BE-0007-android-backend-ja.md)、[BE-0041](../../in-progress/BE-0041-web-playwright-backend/BE-0041-web-playwright-backend-ja.md)、[BE-0019](../../proposals/BE-0019-xcuitest-backend/BE-0019-xcuitest-backend-ja.md)
