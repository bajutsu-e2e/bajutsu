[English](BE-XXXX-compose-incremental-artifact-upload.md) · **日本語**

# BE-XXXX — 変わった leg だけをアップロードし、アクティブな合成を引き継ぐ

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-XXXX](BE-XXXX-compose-incremental-artifact-upload-ja.md) |
| 提案者 | [@akira-matsuda](https://github.com/akira-matsuda) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| 実装 PR | [#1386](https://github.com/bajutsu-e2e/bajutsu/pull/1386) |
| トピック | config の取得元 |
| 関連 | [BE-0268](../BE-0268-composable-upload-artifacts/BE-0268-composable-upload-artifacts-ja.md), [BE-0073](../BE-0073-serve-zip-bundle-upload/BE-0073-serve-zip-bundle-upload-ja.md), [BE-0243](../BE-0243-upload-bundle-durable-storage/BE-0243-upload-bundle-durable-storage-ja.md) |
<!-- /BE-METADATA -->

## はじめに

[BE-0268](../BE-0268-composable-upload-artifacts/BE-0268-composable-upload-artifacts-ja.md) により、Open config ではすでに `config`、`scenarios`、`binary` を 3 つの content-addressed な成果物としてアップロードし、1 本の実行可能なツリーへ合成できます。それでも実際の compose ピッカーは、毎回フル選択を強いられます。ブラウザが選んだ leg を覚えるのはモーダルが開いているあいだだけで、典型的な iOS の config の整合性チェックは参照されているすべての leg を要求します。ページを再読み込みしたあと、あるいは後からバイナリだけ差し替えるときに、すべてのゾーンを選び直すことになります。

本項目は合成を **leg 境界での差分更新** にします。compose ピッカーを開くと、合成がバインド済みなら各ゾーンをアクティブな合成から事前入力するので、変わった leg だけを差し替え、残りは引き継げます。`POST /api/compose` はリクエストボディの純関数のままです。サーバが欠けた leg をライブ状態から黙って補うことはありません。決定論的なランナー、シナリオスキーマ、ゲートには手を触れません。

## 動機

BE-0268 は、変わっていないバイトの回線コストを解決しました。sha256 がすでに格納済みの成果物は再アップロードされません。しかし **選択のコスト** は残っています。Open config の UI は、いまも 1 回のモーダルセッションのなかで完全なトリプルを組み立てるよう運用者に求めます。次の 2 つの作業が損なわれます。

1. **バインド後に 1 本の leg だけ差し替える。** ホスト型運用でよくある流れは「新しい CI バイナリ、同じ config と scenarios」です。いまは 3 つすべてを再びドロップするか、モーダルを開きっぱなしにするしかありません。sha が新しければバイナリはすでに単体で送られます。UI は、残りの 2 本をアクティブなバインドから選び直しなしで載せられるべきです。
2. **再読み込みのあとに再開する。** モーダルを閉じるかページを更新すると `composeState` が消えます。content-addressed なストアには各成果物が残っています。しかしピッカーはライブなトリプルを判別できないので、運用者は記憶から選択を組み直します。

修正は BE-0268 の決定性を弱めてはなりません。欠けた leg をサーバ状態から「推測」して補う合成は、同じ POST ボディが「直前に誰が何をバインドしたか」で別ツリーになり、directive 2 の逆です。リクエストボディは完全で明示的なトリプルのままとし、クリック前に前の leg を埋めるのは UI の役割です。

## 詳細設計

作業は互いに重ならず漏れのない（mutually exclusive and collectively exhaustive、MECE）3 単位へ分かれます。バインドの来歴、アクティブな合成を読む application programming interface（API）、compose ピッカーの UI です。

### 1. 合成バインドに leg ごとの表示名を持たせる

合成された `Upload` はすでに、供給された leg ごとの sha を `artifact_shas` として持っています。本項目は **表示用ファイル名** の兄弟マップ（`artifact_names`）を足します。成功した `POST /api/compose` のとき、リクエストの `filename` / `scenariosName`（および `binary` 用の安定したデフォルト値）から記録します。名前は UI 用の来歴であり、`materialize_composition` のレイアウトには影響しません。ただし単一の YAML ファイルの `scenarios` leg では意味があります。その経路は composition のキャッシュキーを `scenariosName` で salt するため、継承した YAML を名前なしで再合成すると salt が壊れたり、別ツリーができたりします。名前をバインドに保存すると、再開経路はピッカーが最初に送ったのと同じボディを再現できます。

従来の単一 zip バインド（[BE-0073](../BE-0073-serve-zip-bundle-upload/BE-0073-serve-zip-bundle-upload-ja.md)）と Git / ファイルシステムバインドでは、どちらのマップも未設定のままです。露出するのは合成されたトリプルだけです。

### 2. アクティブな合成の leg を読む GET

新しい admin ゲート付き GET は、ライブな合成バインドを UI の種データとして返します。合成がバインドされていなければ空のペイロードです。

```json
{
  "artifacts": {
    "config": { "sha256": "…", "filename": "bajutsu.config.yaml" },
    "scenarios": { "sha256": "…", "filename": "scenarios.zip" },
    "binary": { "sha256": "…", "filename": "binary" }
  }
}
```

規則は次のとおりです。

- **合成バインドでなければ空**：config なし、Git/fs バインド、従来の zip バインドは Hypertext Transfer Protocol（HTTP）200 で `{"artifacts": {}}`。404 にはしない。UI は空の種として扱える
- **org スコープ**：バインドは呼び出し元の org 所属が条件（BE-0243 と同じテナンシー）。別 org のアクティブな合成は不可視で応答は空
- **`/api/artifacts/exists` と同じ admin ゲート**：応答は格納済み sha256 とファイル名を開示。`required_role` に明示の早期ケースを置く（GET は POST 専用の `_ADMIN_PATHS` 集合に届かない）
- **黙って合成しない**：材料化も再バインドもしない。`state.upload` がすでに持っているものだけを報告

`POST /api/compose` の意味は変わりません。config が必要とするすべての leg は、いままでどおりボディに現れなければなりません。クライアントはそのボディを、継承した種とユーザが上書きしたゾーンから組み立てます。

### 3. compose ピッカーでの継承と上書き

Open config を開くと、compose セクションは `GET /api/compose/current` を呼び、`composeState` がまだ空のゾーンだけを埋めます。このモーダルセッションですでに選ばれたゾーンはそのままなので、種の途中更新が進行中の選択を潰しません。

埋まった各ゾーンは、選択の由来を示します。**inherited** はアクティブなバインドから、**uploaded / reused** はこのセッションでの content-addressed なスキップです。すべてのゾーンに **Clear** を置き、不要になった leg を外せます。scenarios だけのターゲットへ移るとき、古い binary sha をボディに残さないためです。**Compose & load** はこれまでどおり config leg を必須とします。scenarios と binary はワイヤ上は任意のままで、`materialize_composition` の整合性チェックが検証します。

モーダル内のヒントは、変わっていない leg がアクティブな合成から選ばれたままであることを述べ、本項目を読まなくても差分更新の流れがわかるようにします。

### 決定性、ゲート、app-agnostic

- **大規模言語モデル（LLM）なし、合否への影響なし**：継承は取得 UI とバインド来歴の読み取り。合否は機械だけが決める（directive 1）
- **compose は純関数のまま**：同じ POST ボディは常に同じツリー。欠けた leg をサーバのライブ状態から補わない（directive 2）
- **Linux でテスト可能**：新しい GET、ファイル名マップ、org スコープ、継承トリプルへの 1 leg 上書き往復は純粋な serve 配管。既存ゲート上のユニットテストで足りる
- **app-agnostic**：レイアウト権限は config の `scenarios` / `appPath`。継承経路はアプリごとに分岐しない（directive 3）

### 対象外

- **scenarios ツリー内のファイル単位マージ**：YAML 1 本のドロップも scenarios 成果物全体の置き換え（BE-0268 の契約）。既存ツリーへの個別マージは別機能
- **過去の成果物カタログの閲覧**：種にするのは *アクティブな* 合成だけ。版付きライブラリは BE-0268 と同様に対象外
- **`POST /api/upload` を 3 分割の糖衣として読み替えること**：BE-0268 から据え置き

## 検討した代替案

- **`POST /api/compose` が欠けた leg を `state.upload` から補う**：却下。同じボディがライブなバインド次第で別ツリーになり、リクエスト単体から再現できない（directive 2）。ボディを埋めるのは UI
- **compose の選択を `localStorage` に永続化する**：主手段としては却下。ブラウザとサーバのアクティブなバインドが食い違う（別 admin の付け替え、別マシンでの UI）。`GET /api/compose/current` なら Replay / Record / Crawl と揃う
- **専用エンドポイントではなく `GET /api/config` を広げる**：却下。`/api/config` は各タブがポーリングする要約であり、leg ごとの sha256 を押し込むと開示面が広がる。専用 admin GET は `/api/artifacts/exists` と揃えられる
- **BE-0268 の Progress 編集に折り込む**：却下。BE-0268 は実装済みで安定。差分再開は独自 API とテストを持つ別 UX 契約であり、閉じた項目への遡及編集ではなく `Related`

## 進捗

> 作業の進行に合わせてここを更新します。チェックリストは *詳細設計* の MECE な作業分解を写し（作業単位ごとに 1 枠）、ログは何がいつ変わったかを古い順に記録し、PR へリンクします。

- [x] 1 — 合成された `Upload` に leg ごとの表示名（`artifact_names`）を持たせ、compose リクエストから記録して来歴 / UI の種に使えるようにする。
- [x] 2 — `GET /api/compose/current`：アクティブな合成 leg を org スコープ・admin ゲート付きで読む（合成がバインドされていなければ空のペイロード）。
- [x] 3 — compose ピッカー UI：Open config 時に空のゾーンをアクティブな合成から種付けし、inherited と uploaded/reused を示し、ゾーンごとの Clear を置き、マージしたトリプルで Compose & load する。

### ログ

- 2026-07-27 — 単位 1〜3 を本提案と同じ BE 作成 PR で実装。`Upload.artifact_names`、`GET /api/compose/current`（`required_role` の admin 早期ケース）、compose ピッカーの inherit / overwrite / Clear フロー。`POST /api/compose` はリクエストボディの純関数のまま。

## 参考

- [CLAUDE.md](../../CLAUDE.md)、[DESIGN §2](../../DESIGN.md)（決定性を最優先し、推測せず失敗する）。
- [BE-0268 — config・シナリオ・アプリバイナリを独立した content-addressed な成果物として個別にアップロードし run ごとに合成する](../BE-0268-composable-upload-artifacts/BE-0268-composable-upload-artifacts-ja.md) — 本項目が差分更新にする compose ピッカーと `POST /api/compose`。
- [BE-0073 — config + シナリオ + アプリバイナリのバンドルを zip としてアップロードする](../BE-0073-serve-zip-bundle-upload/BE-0073-serve-zip-bundle-upload-ja.md) — leg ごとの継承を露出しない従来の単一 zip バインド。
- [BE-0243 — アップロードした zip の config バンドルをオブジェクトストレージへ永続化する](../BE-0243-upload-bundle-durable-storage/BE-0243-upload-bundle-durable-storage-ja.md) — 本項目の読み取り経路が尊重する org スコープのアップロードキャッシュ。
- `bajutsu/serve/operations/upload.py`（`bind_composition` / `_compose_and_bind`）、`bajutsu/serve/uploads.py`（`Upload.artifact_shas`）、`bajutsu/templates/serve.panels.mjs`（compose ピッカー）、`bajutsu/serve/authz.py`（admin GET 用の `required_role` 早期ケース）。
- [docs/configuration.md](../../docs/configuration.md)、[docs/cli.md](../../docs/cli.md#serve)。
