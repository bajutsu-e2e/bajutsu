[English](BE-0325-compose-incremental-artifact-upload.md) · **日本語**

# BE-0325 — 変わった leg だけをアップロードし、アクティブな合成を引き継ぐ

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0325](BE-0325-compose-incremental-artifact-upload-ja.md) |
| 提案者 | [@akira-matsuda](https://github.com/akira-matsuda) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0325") |
| 実装 PR | [#1386](https://github.com/bajutsu-e2e/bajutsu/pull/1386) |
| トピック | config の取得元 |
| 関連 | [BE-0268](../BE-0268-composable-upload-artifacts/BE-0268-composable-upload-artifacts-ja.md), [BE-0073](../BE-0073-serve-zip-bundle-upload/BE-0073-serve-zip-bundle-upload-ja.md), [BE-0243](../BE-0243-upload-bundle-durable-storage/BE-0243-upload-bundle-durable-storage-ja.md) |
<!-- /BE-METADATA -->

## はじめに

[BE-0268](../BE-0268-composable-upload-artifacts/BE-0268-composable-upload-artifacts-ja.md) により、Open config では
`config`、`scenarios`、`binary` を 3 つの content-addressed な成果物としてアップロードし、1 本の実行可能な
ツリーへ合成できます。それでも実際の compose ピッカーは、毎回フル選択を強いられます。ブラウザが選んだ leg を覚える
のは、モーダルが開いているあいだだけです。典型的な iOS の config の整合性チェックは、参照されているすべての leg を
要求します。ページを再読み込みしたあと、あるいは後からバイナリだけ差し替えるときに、すべてのゾーンを選び直す
ことになります。

本項目は、合成を **leg 境界での差分更新** にします。compose ピッカーを開くと、合成がバインド済みなら、各ゾーンを
アクティブな合成から事前入力します。ユーザは変わった leg だけを差し替え、残りを引き継げます。

`POST /api/compose` は、リクエストボディの純関数のままです。サーバが欠けた leg をライブ状態から黙って補うことは
ありません。決定論的なランナー、シナリオスキーマ、ゲートには手を触れません。

## 動機

BE-0268 は、変わっていないバイトの回線コストを解決しました。sha256 がすでに格納済みの成果物は再アップロード
されません。しかし **選択のコスト** は残っています。Open config の UI は、いまも 1 回のモーダルセッションのなかで
完全なトリプルを組み立てるよう運用者に求めます。次の 2 つの作業が損なわれます。

1. **バインド後に 1 本の leg だけ差し替える。** ホスト型運用でよくある流れは「新しい CI バイナリ、同じ config と
   scenarios」です。いまは 3 つすべてを再びドロップするか、モーダルを開きっぱなしにするしかありません。sha が新しければ、
   バイナリはすでに単体で送られます。UI は、残りの 2 本をアクティブなバインドから、選び直しなしで載せられるべきです。
2. **再読み込みのあとに再開する。** モーダルを閉じるかページを更新すると、`composeState` が消えます。
   content-addressed なストアには各成果物が残っています。しかしピッカーはライブなトリプルを判別できないので、
   運用者は記憶から選択を組み直します。

修正は、BE-0268 の決定性を弱めてはなりません。欠けた leg をサーバ状態から「推測」して補う合成を考えます。
同じ POST ボディが「直前に誰が何をバインドしたか」で別ツリーになり、directive 2 の逆になります。

リクエストボディは、完全で明示的なトリプルのままにします。クリックの前に、前回の leg を覚えて埋めるのは UI の役割です。

## 詳細設計

作業は、互いに重ならず漏れのない（mutually exclusive and collectively exhaustive、MECE）3 単位に分かれます。
バインドの来歴、アクティブな合成を読む application programming interface（API）、compose ピッカーの UI です。

### 1. 合成バインドに leg ごとの表示名を持たせる

合成された `Upload` は、供給された leg ごとの sha を `artifact_shas` としてすでに持っています。本項目は、
**表示用ファイル名** の兄弟マップ（`artifact_names`）を足します。成功した `POST /api/compose` のとき、
リクエストの `filename` と `scenariosName`（および `binary` 用の安定したデフォルト値）から記録します。

名前は UI 用の来歴であり、`materialize_composition` のレイアウトには影響しません。ただし、単一の YAML ファイルの
`scenarios` leg では意味があります。その経路は composition のキャッシュキーを `scenariosName` で salt するためです。
継承した YAML を名前なしで再合成すると、salt が壊れるか、別ツリーができます。名前をバインドに保存すれば、
再開経路はピッカーが最初に送ったのと同じボディを再現できます。

従来の単一 zip バインド（[BE-0073](../BE-0073-serve-zip-bundle-upload/BE-0073-serve-zip-bundle-upload-ja.md)）と
Git またはファイルシステムのバインドでは、どちらのマップも未設定のままです。露出するのは、合成されたトリプルだけです。

### 2. アクティブな合成の leg を読む GET

新しい admin ゲート付きの GET は、ライブな合成バインドを UI の種データとして返します。合成がバインドされていなければ、
空のペイロードを返します。

```json
{
  "artifacts": {
    "config": { "sha256": "…", "filename": "bajutsu.config.yaml" },
    "scenarios": { "sha256": "…", "filename": "scenarios.zip" },
    "binary": { "sha256": "…", "filename": "binary" }
  }
}
```

このエンドポイントには、次の 4 つの規則があります。

- **合成バインドでなければ空です。** config がない場合、Git またはファイルシステムのバインド、従来の zip バインドは、
  Hypertext Transfer Protocol（HTTP）200 で `{"artifacts": {}}` を返します。404 にはしません。UI は「引き継ぐものがない」
  状態を、通常の空の種として扱えます。
- **org スコープです。** バインドは、呼び出し元の org に属していなければなりません。BE-0243 がアップロードキャッシュに
  すでに適用しているテナンシーと同じです。別の org のアクティブな合成は見えず、応答は空になります。
- **`/api/artifacts/exists` と同じ admin ゲートです。** 応答は、格納済みの sha256 とファイル名を開示します。
  `required_role` に明示の早期ケースを置きます。GET は、POST 専用の `_ADMIN_PATHS` 集合に届かないためです。
- **黙って合成しません。** このエンドポイントは、材料化も再バインドもしません。`state.upload` がすでに持っているものを
  報告するだけです。

`POST /api/compose` の意味は変わりません。config が必要とするすべての leg は、これまでどおりボディに現れなければ
なりません。クライアントは、そのボディを、継承した種とユーザが上書きしたゾーンから組み立てます。

### 3. compose ピッカーでの継承と上書き

Open config を開くと、compose セクションは `GET /api/compose/current` を呼び、`composeState` がまだ空のゾーンを
埋めます。このモーダルセッションですでに選ばれたゾーンはそのままなので、種の途中更新が進行中の選択を潰すことは
ありません。

埋まった各ゾーンは、選択の由来を示します。**inherited** は、アクティブなバインドから来た選択です。
**uploaded / reused** は、このセッションでの選択で、content-addressed な既存のスキップを指します。

すべてのゾーンに **Clear** を置き、不要になった leg を外せます。scenarios だけのターゲットへ移るとき、古い binary の
sha をボディに残さないためです。

**Compose & load** は、これまでどおり config leg を必須とします。scenarios と binary は、ワイヤ上は任意のままです。
`materialize_composition` の整合性チェックが、引き続きこの 2 つを検証します。

モーダル内のヒントは、変わっていない leg がアクティブな合成から選ばれたままであることを述べます。本項目を読まなくても、
差分更新の流れがわかります。

### 決定性、ゲート、app-agnostic

- **大規模言語モデル（LLM）なし、合否への影響なし。** 継承は、取得 UI とバインド来歴の読み取りです。合否は機械だけが
  決めます（directive 1）。
- **compose は純関数のまま。** 同じ POST ボディは、常に同じツリーになります。欠けた leg を、サーバのライブ状態から
  補うことはありません（directive 2）。
- **Linux でテストできます。** 新しい GET、ファイル名マップ、org スコープ、継承トリプルへの 1 leg 上書きの往復は、
  純粋な serve 配管です。既存のゲート上のユニットテストで足ります。
- **app-agnostic です。** レイアウトの権限は、config の `scenarios` と `appPath` にあります。継承経路は、アプリごとに
  分岐しません（directive 3）。

### 対象外

- **scenarios ツリー内のファイル単位マージ。** YAML 1 本のドロップも、scenarios 成果物全体の置き換えです（BE-0268 の契約）。
  既存ツリーへの個別マージは、別の機能です。
- **過去の成果物カタログの閲覧。** 種にするのは、*アクティブな* 合成だけです。版付きライブラリは、BE-0268 と同様に
  対象外です。
- **`POST /api/upload` を 3 分割の糖衣として読み替えること。** BE-0268 から据え置きます。

## 検討した代替案

- **`POST /api/compose` が欠けた leg を `state.upload` から補う。** 却下します。同じボディがライブなバインド次第で別ツリーに
  なり、リクエスト単体から再現できません（directive 2）。ボディを埋めるのは UI で、サーバは推測しません。
- **compose の選択を `localStorage` に永続化する。** 主手段としては却下します。ブラウザとサーバのアクティブなバインドが
  食い違いうるためです。別の admin が config を付け替えたり、別のマシンで UI を開いたりします。
  `GET /api/compose/current` から種を得れば、ピッカーは Replay / Record / Crawl が実際に動かすものと揃います。
- **専用エンドポイントではなく `GET /api/config` を広げる。** 却下します。`/api/config` は、各タブがポーリングする
  パスとソースの要約です。leg ごとの sha256 を押し込むと、すべてのクライアントへの開示面が広がります。専用の admin GET
  なら `/api/artifacts/exists` と揃い、頻繁に通る経路を軽く保てます。
- **BE-0268 の Progress 編集に折り込む。** 却下します。BE-0268 は実装済みで安定しています。差分再開は、独自の API と
  テストを持つ別の UX 契約です。閉じた項目への遡及編集ではなく、`Related` として整理します。

## 進捗

> 作業の進行に合わせてここを更新します。チェックリストは *詳細設計* の MECE な作業分解を写し（作業単位ごとに 1 枠）、ログは何がいつ変わったかを古い順に記録し、PR へリンクします。

- [x] 1 — 合成された `Upload` に leg ごとの表示名（`artifact_names`）を持たせ、compose リクエストから記録して来歴 / UI の種に使えるようにする。
- [x] 2 — `GET /api/compose/current`：アクティブな合成 leg を org スコープ・admin ゲート付きで読む（合成がバインドされていなければ空のペイロード）。
- [x] 3 — compose ピッカー UI：Open config 時に空のゾーンをアクティブな合成から種付けし、inherited と uploaded/reused を示し、ゾーンごとの Clear を置き、マージしたトリプルで Compose & load する。

### ログ

- 2026-07-27：単位 1〜3 を本提案と同じ BE 作成 PR で実装。`Upload.artifact_names`、`GET /api/compose/current`（`required_role` の admin 早期ケース）、compose ピッカーの inherit / overwrite / Clear フロー。`POST /api/compose` はリクエストボディの純関数のまま。

## 参考

- [CLAUDE.md](../../CLAUDE.md)、[DESIGN §2](../../DESIGN.md)（決定性を最優先し、推測せず失敗する）。
- [BE-0268：config・シナリオ・アプリバイナリを独立した content-addressed な成果物として個別にアップロードし run ごとに合成する](../BE-0268-composable-upload-artifacts/BE-0268-composable-upload-artifacts-ja.md)：本項目が差分更新にする compose ピッカーと `POST /api/compose`。
- [BE-0073：config + シナリオ + アプリバイナリのバンドルを zip としてアップロードする](../BE-0073-serve-zip-bundle-upload/BE-0073-serve-zip-bundle-upload-ja.md)：leg ごとの継承を露出しない従来の単一 zip バインド。
- [BE-0243：アップロードした zip の config バンドルをオブジェクトストレージへ永続化する](../BE-0243-upload-bundle-durable-storage/BE-0243-upload-bundle-durable-storage-ja.md)：本項目の読み取り経路が尊重する org スコープのアップロードキャッシュ。
- `bajutsu/serve/operations/upload.py`（`bind_composition` / `_compose_and_bind`）、`bajutsu/serve/uploads.py`（`Upload.artifact_shas`）、`bajutsu/templates/serve.panels.mjs`（compose ピッカー）、`bajutsu/serve/authz.py`（admin GET 用の `required_role` 早期ケース）。
- [docs/configuration.md](../../docs/configuration.md)、[docs/cli.md](../../docs/cli.md#serve)。
